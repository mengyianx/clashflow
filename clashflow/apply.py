"""Reusable Config apply pipeline: render, `mihomo -t` validation on the
rendered bytes, atomic writes of the Source and Runtime Configs, and hot-apply
through the Controller. Every Config write enters through
`commit_rendered_config`: the save endpoint, the Active Config switch, the
Subscription refresh, the copy that only stores bytes, and an Overlay change
that re-renders the Active Config.

Two files, one contract (ADR-0004):
- the Source Config is user content and is written verbatim — never rendered
  text, never a merged document;
- the Runtime Config is the rendered artifact the Kernel actually runs, so
  `mihomo -t` validates THAT, and the Kernel's `-f` only ever points at it.

A failure before the writes (a render stage or the Kernel's config-test) leaves
both files byte-identical. A failure after them (a refused hot-apply, a write
that fails halfway) puts both back — the pre-write bytes, or absence when a
file was gone, absent restoring as absent — so disk and the running Kernel
stay consistent and the next restart does not boot bytes the Controller
refused.

Pinned Kernel (v1.19.32) facts this module encodes:
- `mihomo -d <home> -t -f <file>` exits 0 = valid, 1 = invalid; the error
  banner goes to STDOUT (logrus writes to os.Stdout), stderr stays empty —
  both streams are captured and surfaced unmodified.
- `-t` on a missing path auto-creates a default config and exits 0, so
  validation refuses missing candidates instead of trusting the exit code.
- `-t` may download GEO data into `<home>` when a rule needs it; pointing
  `-d` at the shared data volume keeps that side effect inside `/data`.
- `PUT /configs?force=true` rebuilds listeners/ports/allow-lan and applies a
  changed log level without a restart; the CLI-pinned Controller settings
  survive it.
"""

import asyncio
import logging
import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx

from clashflow.kernel import CONTROLLER_ADDR
from clashflow.render import DEFAULT_STAGES, Stage, render

log = logging.getLogger(__name__)

CONTROLLER_URL = f"http://{CONTROLLER_ADDR}"

TMP_SUFFIX = ".tmp"


class KernelRejected(Exception):
    """`mihomo -t` refused the candidate; the message is the Kernel's own
    output (stdout + stderr), verbatim."""

    def __init__(self, output: str) -> None:
        super().__init__(output)
        self.output = output


class ConfigFileMissing(Exception):
    """The candidate file does not exist; validating it would let the Kernel
    auto-create a default config and report success."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"config file missing: {path.name}")
        self.path = path


class HotApplyError(Exception):
    """The Controller refused or never received the forced reload; the message
    carries the upstream error text verbatim when one was returned."""


class Unwritten:
    """The marker for "the caller wrote no Source Config bytes".

    The Active Config switch and an Overlay change both re-render a Source
    Config that is already on disk: they write the Runtime Config only, and a
    failure leaves the Source Config exactly as it is.
    """


UNWRITTEN = Unwritten()


async def validate_config(kernel_bin: str, data_dir: Path, config_file: Path) -> None:
    """Run the Kernel config-test on an existing file; raise KernelRejected
    with the Kernel's verbatim output when it fails."""
    if not config_file.is_file():
        raise ConfigFileMissing(config_file)
    proc = await asyncio.create_subprocess_exec(
        kernel_bin,
        "-d",
        str(data_dir),
        "-t",
        "-f",
        str(config_file),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode == 0:
        return
    raise KernelRejected(
        stdout.decode("utf-8", errors="replace") + stderr.decode("utf-8", errors="replace")
    )


def _write_atomic(target: Path, content: bytes) -> None:
    """Write through a sibling temp file plus `os.replace` (same filesystem ->
    atomic), so a reader never sees a half-written Config."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + TMP_SUFFIX)
    try:
        tmp.write_bytes(content)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def _restore(target: Path, previous: bytes | None) -> None:
    """Put the pre-write state back: the bytes when there were any, absence
    when the file was gone before the write."""
    if previous is None:
        target.unlink(missing_ok=True)
    else:
        _write_atomic(target, previous)


async def hot_apply(client: httpx.AsyncClient, secret: str, config_path: Path) -> None:
    """Forced config reload through the Controller: `force=true` rebuilds
    listeners/ports/allow-lan; the CLI-pinned Controller settings survive it."""
    try:
        resp = await client.put(
            f"{CONTROLLER_URL}/configs",
            params={"force": "true"},
            json={"path": str(config_path)},
            headers={"Authorization": f"Bearer {secret}"},
        )
    except httpx.HTTPError as exc:
        raise HotApplyError("kernel unreachable") from exc
    if not resp.is_success:
        raise HotApplyError(_upstream_message(resp))
    log.info("hot-applied config %s", config_path)


def render_runtime_config(
    source_target: Path,
    runtime_target: Path,
    overlay: Mapping[str, Any],
    *,
    stages: Sequence[tuple[str, Stage]] = DEFAULT_STAGES,
) -> bytes:
    """Render a Source Config on disk into its Runtime Config and write it.

    Boot and Kernel respawns use this: the Kernel's `-f` only accepts a
    Runtime Config, and deleting the artifact must never lose user content, so
    the file is rebuilt from the Source Config + Overlay. No config-test here —
    the Kernel's own start is the next check, and boot must not wait for an
    extra `mihomo -t`."""
    runtime_bytes = render(source_target.read_bytes(), overlay, stages=stages)
    _write_atomic(runtime_target, runtime_bytes)
    return runtime_bytes


async def commit_rendered_config(
    *,
    kernel_bin: str,
    data_dir: Path,
    client: httpx.AsyncClient,
    secret: str,
    source_target: Path,
    runtime_target: Path,
    content: bytes | Unwritten,
    overlay: Mapping[str, Any],
    should_reload: Callable[[], bool] | None,
    stages: Sequence[tuple[str, Stage]] = DEFAULT_STAGES,
) -> bytes:
    """The one path every Config change takes: render, validate the Runtime
    Config, write both files, reload, restore on failure.

    `content` is the candidate Source Config body; it is stored verbatim.
    `content=UNWRITTEN` means the Source Config is already on disk (the Active
    Config switch, an Overlay change) and only the Runtime Config is rebuilt.

    The Render Pipeline runs first and pure: a render failure (`RenderFailed`,
    naming its stage) touches neither file. Then the RENDERED bytes are
    validated by the Kernel — the Source Config is never what the Kernel
    reads, so validating it would test the wrong document. Only then are the
    files swapped in; a refused hot-apply puts both pre-write states back.

    `should_reload` is asked at the moment of the reload, not when the request
    arrived: validation can take seconds (the config-test may download GEO
    data), and a Kernel that died inside that window must not be asked to
    serve a Config it cannot. It answers false for a Config that is not
    Active, for a caller that only stores bytes (`None`), and for a Kernel
    that is down — the bytes are stored and the next start picks them up.

    Failures propagate as their own classes — `RenderFailed`,
    `KernelRejected`, `ConfigFileMissing`, `HotApplyError` — so each caller
    maps them onto its own surface (HTTP status, Failure Class) without
    re-implementing the restore rules. Returns the rendered bytes.
    """
    if isinstance(content, Unwritten):
        if not source_target.is_file():
            raise ConfigFileMissing(source_target)
        source_bytes = source_target.read_bytes()
    else:
        source_bytes = content

    runtime_bytes = render(source_bytes, overlay, stages=stages)

    previous_source = source_target.read_bytes() if source_target.is_file() else None
    previous_runtime = runtime_target.read_bytes() if runtime_target.is_file() else None

    # Validate the candidate on a sibling temp file: the Kernel's config-test
    # must see the exact bytes that would be swapped in, under the name they
    # would have, so its error text points at the real candidate.
    runtime_target.parent.mkdir(parents=True, exist_ok=True)
    runtime_tmp = runtime_target.with_name(runtime_target.name + TMP_SUFFIX)
    runtime_tmp.write_bytes(runtime_bytes)
    try:
        await validate_config(kernel_bin, data_dir, runtime_tmp)
    finally:
        runtime_tmp.unlink(missing_ok=True)

    wrote_source = False
    wrote_runtime = False
    try:
        _write_atomic(runtime_target, runtime_bytes)
        wrote_runtime = True
        if not isinstance(content, Unwritten):
            _write_atomic(source_target, source_bytes)
            wrote_source = True
        if should_reload is not None and should_reload():
            await hot_apply(client, secret, runtime_target)
    except BaseException:
        # One mistake must not damage the config that is running: both files
        # go back to their pre-write state, absence included.
        if wrote_source:
            _restore(source_target, previous_source)
        if wrote_runtime:
            _restore(runtime_target, previous_runtime)
        raise
    return runtime_bytes


def _upstream_message(resp: httpx.Response) -> str:
    try:
        message = resp.json().get("message")
    except ValueError:
        message = None
    if isinstance(message, str) and message:
        return message
    return resp.text
