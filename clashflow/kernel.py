"""Kernel (mihomo) process management: spawn with CLI pins, probe health, stop."""

import logging
import subprocess
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

CONTROLLER_HOST = "127.0.0.1"
CONTROLLER_PORT = 9090
CONTROLLER_ADDR = f"{CONTROLLER_HOST}:{CONTROLLER_PORT}"


class KernelManager:
    def __init__(self, kernel_bin: str, data_dir: Path) -> None:
        self._bin = kernel_bin
        self._data_dir = data_dir
        self._proc: subprocess.Popen[bytes] | None = None

    def start(self, config_path: Path, secret: str) -> None:
        if self._proc is not None and self._proc.poll() is None:
            raise RuntimeError("kernel already running")
        self._proc = subprocess.Popen(
            [
                self._bin,
                "-d",
                str(self._data_dir),
                "-f",
                str(config_path),
                "-ext-ctl",
                CONTROLLER_ADDR,
                "-secret",
                secret,
            ]
        )
        log.info("spawned kernel pid=%d config=%s", self._proc.pid, config_path)

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    async def probe(self, secret: str) -> bool:
        """True when the pinned Controller answers with the pinned secret."""
        if not self.alive():
            return False
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"http://{CONTROLLER_ADDR}/version",
                    headers={"Authorization": f"Bearer {secret}"},
                    timeout=2.0,
                )
                return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def stop(self) -> None:
        if self._proc is None:
            return
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=5)
        self._proc = None
