"""Reusable Config apply pipeline: `mihomo -t` validation, atomic swap, and
forced hot-apply through the Controller. Shared by the save endpoint, Active
Config switching, and (per the plan) the Subscription refresh pipeline.

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


def _upstream_message(resp: httpx.Response) -> str:
    try:
        message = resp.json().get("message")
    except ValueError:
        message = None
    if isinstance(message, str) and message:
        return message
    return resp.text
