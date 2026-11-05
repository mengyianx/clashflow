"""Runtime update surface under `/api/panel/updates`.

Manual by design: `check` asks GitHub for the latest release of the three
pinned artifacts, `update` installs it (the Kernel restarts itself), `rollback`
returns to the version that was running before. Updates and rollbacks are
serialised per component: a second one answers 409 instead of queueing. Checks
run independently, so pressing update while a check is in flight is not refused.
"""

import logging

from fastapi import APIRouter, HTTPException, Request

from clashflow.updates import COMPONENTS, UpdateBusy, UpdateError

log = logging.getLogger(__name__)

router = APIRouter()


def _component(name: str) -> str:
    if name not in COMPONENTS:
        raise HTTPException(status_code=404, detail=f"unknown component: {name}")
    return name


@router.get("/api/panel/updates")
async def list_updates(request: Request) -> list[dict[str, object]]:
    """Current / previous / last-known-latest versions; no network access."""
    return request.app.state.updates.state()


@router.post("/api/panel/updates/check")
async def check_updates(request: Request) -> list[dict[str, object]]:
    """Query GitHub Releases for all three components (one round trip each)."""
    try:
        return await request.app.state.updates.check()
    except UpdateError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/api/panel/updates/{name}/update")
async def update_component(request: Request, name: str) -> list[dict[str, object]]:
    component = _component(name)
    try:
        return await request.app.state.updates.update(component)
    except UpdateBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except UpdateError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/api/panel/updates/{name}/rollback")
async def rollback_component(request: Request, name: str) -> list[dict[str, object]]:
    component = _component(name)
    try:
        return await request.app.state.updates.rollback(component)
    except UpdateBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except UpdateError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
