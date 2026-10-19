"""Service Control surface: Kernel health, start/stop/restart actions."""

import asyncio
import logging

from fastapi import APIRouter, Request

from clashflow.kernel import KernelManager

log = logging.getLogger(__name__)

router = APIRouter()

type StatusBody = dict[str, str | int]


async def _settle(kernel: KernelManager, secret: str, timeout: float = 5.0) -> None:
    """Bounded wait after an action: Controller answers, or the Kernel dies."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if not kernel.alive():
            return
        if await kernel.probe(secret):
            return
        await asyncio.sleep(0.05)


async def _status(request: Request) -> StatusBody:
    app = request.app
    up = await app.state.kernel.probe(app.state.secret)
    kernel_status = app.state.kernel.status()
    active = app.state.store.get_active()
    return {
        "kernel": "up" if up else "down",
        "state": kernel_status["state"],
        "respawns": kernel_status["respawns"],
        "active_config": active or "",
    }


@router.get("/api/service/health")
async def service_health(request: Request) -> StatusBody:
    return await _status(request)


@router.post("/api/service/start")
async def service_start(request: Request) -> StatusBody:
    kernel: KernelManager = request.app.state.kernel
    if not kernel.alive():
        try:
            kernel.start(kernel.active_config_path(), request.app.state.secret)
        except OSError as exc:
            log.error("kernel failed to start: %s", exc)
    await _settle(kernel, request.app.state.secret)
    return await _status(request)


@router.post("/api/service/stop")
async def service_stop(request: Request) -> StatusBody:
    kernel: KernelManager = request.app.state.kernel
    kernel.stop()
    return await _status(request)


@router.post("/api/service/restart")
async def service_restart(request: Request) -> StatusBody:
    kernel: KernelManager = request.app.state.kernel
    try:
        kernel.restart()
    except OSError as exc:
        log.error("kernel failed to restart: %s", exc)
    await _settle(kernel, request.app.state.secret)
    return await _status(request)
