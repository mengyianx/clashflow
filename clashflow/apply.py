"""Reusable Config apply pipeline: `mihomo -t` validation, atomic swap, and
forced hot-apply through the Controller. Shared by the save endpoint, Active
Config switching, and (per the plan) the Subscription refresh pipeline.

A refused hot-apply leaves the Kernel on the previous Config, so the write
paths put the pre-write state back on disk (`hot_apply_or_restore`): disk and
the running state stay consistent, and the next Kernel restart does not boot
bytes the Controller refused. The pre-write state is the file's bytes, or its
absence when the file was gone before the write — absent restores as absent.

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
    - the bytes the file carried: a refusal puts them back;
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
            config_path.write_bytes(previous)
            log.info("restored the previous Config bytes on %s", config_path.name)
        raise


def _upstream_message(resp: httpx.Response) -> str:
    try:
        message = resp.json().get("message")
    except ValueError:
        message = None
    if isinstance(message, str) and message:
        return message
    return resp.text
