"""Config registry surface: named Configs under `/api/panel/configs`.

Routing convention (ticket #14): the Kernel Controller owns `/api/configs`,
so Management Panel CRUD mounts under `/api/panel/...`; this router is
registered in clashflow/main.py before the Controller reverse proxy.

Filename derivation: the server assigns `<name>.yaml` under `/data/configs`
(db.config_filename); names are validated at registration.

Empty Config semantics: creation writes the YAML empty mapping `{}`, which
the pinned Kernel (v1.19.32) loads as a defaults-only config — a zero-byte
file is refused by the Kernel ("configuration file … is empty").
"""

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from clashflow.db import (
    DEFAULT_CONFIG_KIND,
    ConfigAlreadyExists,
    ConfigIsActive,
    ConfigNotFound,
    config_filename,
    validate_config_name,
)

log = logging.getLogger(__name__)

router = APIRouter()

EMPTY_CONFIG_BODY = "{}\n"

type ConfigEntry = dict[str, str | bool]


class NewConfig(BaseModel):
    name: str
    kind: str = DEFAULT_CONFIG_KIND


def _configs_dir(request: Request) -> Path:
    return request.app.state.configs_dir


@router.post("/api/panel/configs", status_code=201)
async def create_config(request: Request, body: NewConfig) -> ConfigEntry:
    try:
        validate_config_name(body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    path = _configs_dir(request) / config_filename(body.name)
    try:
        request.app.state.store.create_config(body.name, body.kind)
    except ConfigAlreadyExists as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if path.exists():
        # A stale file under a free name: refuse instead of clobbering, and
        # roll the fresh registration back.
        request.app.state.store.remove_config(body.name)
        raise HTTPException(status_code=409, detail=f"config file already exists: {path.name}")
    path.write_text(EMPTY_CONFIG_BODY)
    log.info("created config %s (%s)", body.name, body.kind)
    return {
        "name": body.name,
        "kind": body.kind,
        "filename": path.name,
        "active": False,
    }


@router.get("/api/panel/configs")
async def list_configs(request: Request) -> list[ConfigEntry]:
    return request.app.state.store.list_configs()


@router.put("/api/panel/configs/{name}/active")
async def set_active_config(request: Request, name: str) -> ConfigEntry:
    """Record the Active Config; the Kernel (re)start picks it up (ticket #10
    wires the validate + hot-apply pipeline behind this seam)."""
    try:
        request.app.state.store.activate_config(name)
    except ConfigNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return request.app.state.store.get_config(name)


@router.delete("/api/panel/configs/{name}")
async def delete_config(request: Request, name: str) -> dict[str, str]:
    try:
        request.app.state.store.remove_config(name)
    except ConfigNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConfigIsActive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    (_configs_dir(request) / config_filename(name)).unlink(missing_ok=True)
    log.info("deleted config %s", name)
    return {"deleted": name}
