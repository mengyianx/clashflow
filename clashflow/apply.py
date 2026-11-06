"""Reusable Config apply pipeline: `mihomo -t` validation, atomic swap, and
forced hot-apply through the Controller. Every Config write enters through
`commit_config`, which restores through `hot_apply_or_restore`: the save
endpoint, the Active Config switch, the Subscription refresh, and the copy
that only stores bytes.

A refused hot-apply leaves the Kernel on the previous Config, so the write
paths put the pre-write state back on disk (`hot_apply_or_restore`): disk and
the running state stay consistent, and the next Kernel restart does not boot
bytes the Controller refused. The pre-write state is the file's bytes — put
back with the same atomic swap every other Config write uses — or its
absence when the file was gone before the write: absent restores as absent.

Pinned Kernel (v1.19.32) facts this module encodes:
- `mihomo -d <home> -t -f <file>` exits 0 = valid, 1 = invalid; the error
  banner goes to STDOUT (logrus writes to os.Stdout), stderr stays empty —
  both streams are captured and surfaced unmodified.
- `-t` on a missing path auto-creates a default config and exits 0, so
  validation refuses missing candidates instead of trusting the exit code.
- `-t` may download GEO data into `<home>` when a rule needs it; pointing
  `-d` at the shared data volume keeps that side effect inside `/data`.
"""

import asyncio
import logging
import os
from collections.abc import Callable
from pathlib import Path

import httpx

from clashflow.kernel import CONTROLLER_ADDR

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
    """The marker for "the caller wrote nothing to the Config file".

    The Active Config switch only asks the Kernel to load a Config that is
    already on disk: it writes no bytes, so a refused hot-apply has nothing
    to restore and the file stays exactly as it is.
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


async def atomic_save(kernel_bin: str, data_dir: Path, target: Path, content: bytes) -> None:
    """Validate the candidate on a sibling temp file, then swap it in with
    `os.replace` (same filesystem -> atomic). On ANY failure the temp file is
    deleted and the previous file stays byte-identical."""
    tmp = target.with_name(target.name + TMP_SUFFIX)
    tmp.write_bytes(content)
    try:
        await validate_config(kernel_bin, data_dir, tmp)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def _restore_bytes(config_path: Path, previous: bytes) -> None:
    """Put the pre-write bytes back with the same swap discipline as every
    other Config write: a sibling temp file plus `os.replace`, so a reader
    never sees a half-written Config."""
    tmp = config_path.with_name(config_path.name + TMP_SUFFIX)
    try:
        tmp.write_bytes(previous)
        os.replace(tmp, config_path)
    finally:
        tmp.unlink(missing_ok=True)


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


async def hot_apply_or_restore(
    client: httpx.AsyncClient,
    secret: str,
    config_path: Path,
    previous: bytes | None | Unwritten = UNWRITTEN,
) -> None:
    """Forced config reload through the Controller, with the pre-write state
    put back when the Controller refuses it.

    A refusal means the Kernel never left the old Config, so the stored file
    must not keep the refused bytes either: the pre-write state goes back on
    disk and the refusal is re-raised for the caller to surface verbatim.

    `previous` is that pre-write state:
    - the bytes the file carried: a refusal puts them back — with the same
      atomic swap every other Config write uses;
    - None: the file was absent before the write — there are no bytes to put
      back then, and absent restores as absent: the refused bytes are
      removed, so the file is gone again;
    - UNWRITTEN (the default): the caller wrote nothing (the Active Config
      switch), so a refusal leaves the file exactly as it is.

    Both write paths use this one implementation: the save endpoint and the
    Subscription refresh. In both the Kernel keeps running the previous
    Config, so the stored Config has to match it.
    """
    try:
        await hot_apply(client, secret, config_path)
    except HotApplyError:
        if isinstance(previous, Unwritten):
            log.info("nothing was written to %s; left as it is", config_path.name)
        elif previous is None:
            config_path.unlink(missing_ok=True)
            log.info("removed the refused Config bytes on %s (it was absent)", config_path.name)
        else:
            _restore_bytes(config_path, previous)
            log.info("restored the previous Config bytes on %s", config_path.name)
        raise


async def commit_config(
    *,
    kernel_bin: str,
    data_dir: Path,
    client: httpx.AsyncClient,
    secret: str,
    target: Path,
    content: bytes | Unwritten,
    should_reload: Callable[[], bool] | None,
) -> None:
    """The one path every Config change takes: validate, write, reload, restore.

    `content` is the candidate body. It is validated on a sibling temp file
    and only then swapped in, so a rejected candidate leaves the target
    byte-identical. `content=UNWRITTEN` means the caller wrote nothing — the
    Active Config switch loads a file that is already on disk — so the file is
    validated as it stands and a refused reload leaves it alone.

    `should_reload` is asked at the moment of the reload, not when the request
    arrived: validation can take seconds (the config-test may download GEO
    data), and a Kernel that died inside that window must not be asked to
    serve a Config it cannot. It answers false for a Config that is not
    Active, for a caller that only stores bytes (`None`), and for a Kernel
    that is down — the bytes are stored and the next start picks them up.

    Failures propagate as their own classes — `KernelRejected`,
    `ConfigFileMissing`, `HotApplyError` — so each caller maps them onto its
    own surface (HTTP status, Failure Class) without re-implementing the
    restore rules.
    """
    previous: bytes | None | Unwritten = UNWRITTEN
    if isinstance(content, Unwritten):
        await validate_config(kernel_bin, data_dir, target)
    else:
        # The pre-write bytes, read BEFORE the swap: a refused hot-apply puts
        # them back, so the stored Config stays the one the Kernel keeps
        # running.
        previous = target.read_bytes() if target.is_file() else None
        await atomic_save(kernel_bin, data_dir, target, content)
    if should_reload is not None and should_reload():
        await hot_apply_or_restore(client, secret, target, previous)


def _upstream_message(resp: httpx.Response) -> str:
    try:
        message = resp.json().get("message")
    except ValueError:
        message = None
    if isinstance(message, str) and message:
        return message
    return resp.text
