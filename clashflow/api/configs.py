"""Config registry surface: named Configs under `/api/panel/configs`.

Routing convention (ticket #14): the Kernel Controller owns `/api/configs`,
so Management Panel CRUD mounts under `/api/panel/...`; this router is
registered in clashflow/main.py before the Controller reverse proxy.

Filename derivation: the server assigns `<name>.yaml` under `/data/configs`
(db.config_filename); names are validated at registration.

Empty Config semantics: creation writes the YAML empty mapping `{}`, which
the pinned Kernel (v1.19.32) loads as a defaults-only config — a zero-byte
file is refused by the Kernel ("configuration file … is empty").

Blank template semantics (ticket #11): `POST` with `template: true` writes
the minimal skeleton (`mixed-port: 7890`, `allow-lan: true`, plus the GEO
auto-update keys) instead, so a new Hand-written Config is immediately
runnable when made Active. Copy (`/copy`) duplicates ANY Config's bytes into
a new Hand-written Config through the same validate + atomic-swap pipeline;
the copy is a fully independent file that refreshes never touch.
"""

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from clashflow.apply import (
    ConfigFileMissing,
    HotApplyError,
    KernelRejected,
    atomic_save,
    hot_apply,
    validate_config,
)
from clashflow.db import (
    DEFAULT_CONFIG_KIND,
    ConfigAlreadyExists,
    ConfigIsActive,
    ConfigNotFound,
    config_filename,
    validate_config_name,
)
from clashflow.kernel import KernelManager

log = logging.getLogger(__name__)

router = APIRouter()

EMPTY_CONFIG_BODY = "{}\n"
# The blank template for new Hand-written Configs (ticket #11): the plan's
# minimal skeleton (§5) — a mixed proxy port and allow-lan — plus
# geo-auto-update/geo-update-interval. US21 wants GEO databases updating
# automatically, and a Hand-written Config made Active REPLACES the boot
# Config the Kernel runs — a template without these keys would silently
# turn GEO auto-update off on every switch. The earlier decision to keep GEO
# keys out of this template is superseded: panel-AUTHORED template content is
# not covered by ADR-0001's rewrite ban (that protects user/Subscription-
# supplied content, which stays verbatim); the boot template in
# clashflow.main.DEFAULT_CONFIG_BODY carries the same GEO keys.
TEMPLATE_CONFIG_BODY = (
    "mixed-port: 7890\nallow-lan: true\ngeo-auto-update: true\ngeo-update-interval: 24\n"
)

type ConfigEntry = dict[str, str | bool]


class NewConfig(BaseModel):
    name: str
    # The panel API creates Hand-written Configs only; Subscription-derived
    # Configs are registered by the Subscription add flow, never by the user.
    # true = write the blank template skeleton; false (default) = the YAML
    # empty mapping, the established empty-Config semantics from ticket #9.
    template: bool = False


class CopyConfig(BaseModel):
    name: str


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
        request.app.state.store.create_config(body.name, DEFAULT_CONFIG_KIND)
    except ConfigAlreadyExists as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if path.exists():
        # A stale file under a free name: refuse instead of clobbering, and
        # roll the fresh registration back.
        request.app.state.store.remove_config(body.name)
        raise HTTPException(status_code=409, detail=f"config file already exists: {path.name}")
    path.write_text(TEMPLATE_CONFIG_BODY if body.template else EMPTY_CONFIG_BODY)
    log.info("created config %s (%s)", body.name, DEFAULT_CONFIG_KIND)
    return {
        "name": body.name,
        "kind": DEFAULT_CONFIG_KIND,
        "filename": path.name,
        "active": False,
    }


@router.get("/api/panel/configs")
async def list_configs(request: Request) -> list[ConfigEntry]:
    return request.app.state.store.list_configs()


@router.post("/api/panel/configs/{name}/copy", status_code=201)
async def copy_config(request: Request, name: str, body: CopyConfig) -> ConfigEntry:
    """Copy ANY existing Config (Hand-written or Subscription-derived) into
    a NEW Hand-written Config: same content, separate name and file, fully
    independent — refreshes touch only the Config they own, so the copy is
    the escape hatch for editing a Subscription-derived Config safely. The
    content is written through the save pipeline (validate + atomic swap)
    exactly like the source file itself; an invalid source is refused with
    the Kernel's error verbatim and nothing is created."""
    try:
        validate_config_name(body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    store = request.app.state.store
    try:
        store.get_config(name)
    except ConfigNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    source = _configs_dir(request) / config_filename(name)
    if not source.is_file():
        raise HTTPException(status_code=404, detail=f"config file missing: {source.name}")
    target = _configs_dir(request) / config_filename(body.name)
    try:
        store.create_config(body.name, DEFAULT_CONFIG_KIND)
    except ConfigAlreadyExists as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if target.exists():
        # A stale file under a free name: refuse instead of clobbering, and
        # roll the fresh registration back.
        store.remove_config(body.name)
        raise HTTPException(status_code=409, detail=f"config file already exists: {target.name}")
    kernel: KernelManager = request.app.state.kernel
    try:
        await atomic_save(kernel.binary, kernel.data_dir, target, source.read_bytes())
    except KernelRejected as exc:
        store.remove_config(body.name)
        raise HTTPException(status_code=400, detail=exc.output) from exc
    log.info("copied config %s -> %s", name, body.name)
    return store.get_config(body.name)


@router.get("/api/panel/configs/{name}")
async def get_config_content(request: Request, name: str) -> dict[str, str | bool]:
    """The Editor read side: the registered entry plus the raw file content
    verbatim (the file is the source of truth; the panel never rewrites it)."""
    try:
        entry = request.app.state.store.get_config(name)
    except ConfigNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    path = _configs_dir(request) / config_filename(name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"config file missing: {path.name}")
    return {**entry, "content": path.read_text()}


@router.put("/api/panel/configs/{name}")
async def save_config(request: Request, name: str) -> ConfigEntry:
    """Validate the raw YAML body with the Kernel config-test, atomically swap
    it into the Config's file, and hot-apply it when this Config is Active
    (same process, no restart). On validation failure the stored file stays
    byte-identical and the Kernel's error output is returned verbatim."""
    store = request.app.state.store
    try:
        entry = store.get_config(name)
    except ConfigNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    target = _configs_dir(request) / config_filename(name)
    kernel: KernelManager = request.app.state.kernel
    content = await request.body()
    try:
        await atomic_save(kernel.binary, kernel.data_dir, target, content)
    except KernelRejected as exc:
        raise HTTPException(status_code=400, detail=exc.output) from exc
    if entry["active"] and kernel.alive():
        await _hot_apply_or_502(request, target)
    log.info("saved config %s (%d bytes)", name, len(content))
    return store.get_config(name)


@router.put("/api/panel/configs/{name}/active")
async def set_active_config(request: Request, name: str) -> ConfigEntry:
    """Switch the Active Config through the apply pipeline: validate the
    target file, hot-apply it, and record the new Active ONLY on success — on
    failure the previous Active stays recorded and running. A stopped Kernel
    records the choice for the next (re)start instead of hot-applying."""
    store = request.app.state.store
    try:
        store.get_config(name)
    except ConfigNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    target = _configs_dir(request) / config_filename(name)
    kernel: KernelManager = request.app.state.kernel
    try:
        await validate_config(kernel.binary, kernel.data_dir, target)
    except ConfigFileMissing as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KernelRejected as exc:
        raise HTTPException(status_code=400, detail=exc.output) from exc
    if kernel.alive():
        await _hot_apply_or_502(request, target)
    store.activate_config(name)
    log.info("switched Active Config to %s", name)
    return store.get_config(name)


async def _hot_apply_or_502(request: Request, target: Path) -> None:
    try:
        await hot_apply(request.app.state.api_client, request.app.state.secret, target)
    except HotApplyError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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
