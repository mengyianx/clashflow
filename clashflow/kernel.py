"""Kernel (mihomo) process management: spawn with CLI pins, probe health,
stop, and supervise with exponential-backoff respawn."""

import asyncio
import logging
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

CONTROLLER_HOST = "127.0.0.1"
CONTROLLER_PORT = 9090
CONTROLLER_ADDR = f"{CONTROLLER_HOST}:{CONTROLLER_PORT}"

POLL_INTERVAL = 0.05


class KernelManager:
    def __init__(self, kernel_bin: str, data_dir: Path) -> None:
        self._bin = kernel_bin
        self._data_dir = data_dir
        self._proc: subprocess.Popen[bytes] | None = None
        self._secret = ""
        self._active_config: Callable[[], Path] | None = None
        self._respawns = 0
        self._give_up = False
        self._stop_requested = False
        self._retry_at: float | None = None
        self._wake = asyncio.Event()
        self._supervisor: asyncio.Task[None] | None = None
        self.respawn_base_delay = float(os.environ.get("CLASHFLOW_RESPAWN_BASE_DELAY", "1.0"))
        self.max_respawns = int(os.environ.get("CLASHFLOW_MAX_RESPAWNS", "5"))

    @property
    def pid(self) -> int | None:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc.pid
        return None

    @property
    def binary(self) -> str:
        """The mihomo binary this manager spawns; the apply pipeline runs the
        same binary for `mihomo -t` validation."""
        return self._bin

    @property
    def data_dir(self) -> Path:
        """The Kernel home dir (`-d`); validation reuses it so GEO downloads
        and caches land in the data volume."""
        return self._data_dir

    def active_config_path(self) -> Path:
        if self._active_config is None:
            raise RuntimeError("kernel is not supervised")
        return self._active_config()

    def start(self, config_path: Path, secret: str) -> None:
        if self.alive():
            raise RuntimeError("kernel already running")
        self._stop_requested = False
        self._give_up = False
        self._respawns = 0
        # Wake the supervisor first: if the spawn itself fails (missing binary)
        # the supervisor picks the dead Kernel up and backs off instead of
        # leaving it silently down.
        self._wake.set()
        self._spawn(config_path, secret)

    def _spawn(self, config_path: Path, secret: str) -> None:
        if self.alive():
            return
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
        self._secret = secret
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
        self._stop_requested = True
        self._give_up = False
        self._respawns = 0
        self._retry_at = None
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

    def restart(self) -> None:
        """Stop and respawn through the panel with the current Active Config."""
        if self._active_config is None:
            raise RuntimeError("kernel is not supervised")
        self.stop()
        self.start(self._active_config(), self._secret)

    def status(self) -> dict[str, str | int]:
        if self.alive():
            state = "up"
        elif self._give_up:
            state = "degraded"
        elif self._retry_at is not None:
            state = "respawning"
        else:
            state = "down"
        return {"state": state, "respawns": self._respawns}

    def supervise(self, active_config: Callable[[], Path], secret: str) -> asyncio.Task[None]:
        """Run the supervision loop: an unexpected Kernel exit respawns with
        the Active Config path and exponential backoff; the loop parks on a
        user stop and gives up visibly (degraded) after max_respawns."""
        self._active_config = active_config
        self._secret = secret
        self._supervisor = asyncio.create_task(self._supervise_loop())
        return self._supervisor

    async def _supervise_loop(self) -> None:
        while True:
            if self.alive():
                await asyncio.sleep(POLL_INTERVAL)
                continue
            if self._stop_requested or self._give_up or self._active_config is None:
                self._retry_at = None
                self._wake.clear()
                await self._wake.wait()
                continue
            if self._respawns >= self.max_respawns:
                self._give_up = True
                log.error(
                    "kernel crashed %d consecutive times; giving up until a manual start",
                    self._respawns,
                )
                continue
            self._respawns += 1
            delay = self.respawn_base_delay * 2 ** (self._respawns - 1)
            log.warning(
                "kernel exited unexpectedly; respawning in %.2fs (attempt %d/%d)",
                delay,
                self._respawns,
                self.max_respawns,
            )
            self._retry_at = asyncio.get_running_loop().time() + delay
            try:
                await asyncio.sleep(delay)
            finally:
                self._retry_at = None
            if self.alive() or self._stop_requested or self._give_up:
                continue
            try:
                self._spawn(self._active_config(), self._secret)
            except OSError as exc:
                log.error("kernel respawn failed: %s", exc)
