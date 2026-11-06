"""Config registry surface: named Configs under `/api/panel/configs`.

Routing convention (ticket #14): the Kernel Controller owns `/api/configs`,
so Management Panel CRUD mounts under `/api/panel/...`; this router is
registered in clashflow/main.py before the Controller reverse proxy.

Filename derivation: the server assigns `<name>.yaml` under `/data/configs`
(db.config_filename); names are validated at registration.

Empty Config semantics: creation writes the YAML empty mapping `{}`, which
the pinned Kernel (v1.19.32) loads as a defaults-only config — a zero-byte
file is refused by the Kernel ("configuration file … is empty").

Save semantics (ADR-0004): render (Source + Overlay) → triple validation of
the RENDERED bytes (YAML parses, `proxies` or `proxy-providers` present, the
Kernel's `-t` accepts it) → atomic writes of the Source Config (verbatim) and
the Runtime Config → hot-apply when the Config is the Active one. A render
failure answers 400 with a message that begins with the failing stage; a
render with nothing to proxy answers 400 too (issue #36: an empty Subscription
must never overwrite a working Config); a Kernel rejection answers 400 with the
Kernel's own output; each leaves both files untouched. A refused hot-apply
answers 502 with the Controller's refusal text and puts both pre-save states
back — the pre-save bytes, or absence when a file was gone (absent restores as
absent) — so the stored Config stays the one the Kernel keeps running (the
same restore the Subscription refresh uses).

The Runtime Config is read-only: it is a build artifact previewable at
`GET .../runtime` and diffable at `GET .../runtime/diff`, never writable.

Blank template semantics (ticket #11): `POST` with `template: true` writes
the minimal skeleton (`mixed-port: 7890`, `allow-lan: true`, plus the GEO
auto-update keys) instead, so a new Hand-written Config is immediately
runnable when made Active. Copy (`/copy`) duplicates ANY Config's bytes into
a new Hand-written Config through the same validate + atomic-swap pipeline;
the copy is a fully independent file that refreshes never touch.
"""

import logging
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from clashflow.api.failures import raise_for_commit_failure
from clashflow.apply import (
    UNWRITTEN,
    ConfigFileMissing,
    HotApplyError,
    KernelRejected,
    RenderedConfigInvalid,
    Unwritten,
    commit_rendered_config,
)
from clashflow.db import (
    DEFAULT_CONFIG_KIND,
    SUBSCRIPTION_DERIVED_KIND,
    ConfigAlreadyExists,
    ConfigIsActive,
    ConfigNotFound,
    config_filename,
    runtime_config_path,
    validate_config_name,
)
from clashflow.kernel import KernelManager
from clashflow.overlay import diff as overlay_diff
from clashflow.overlay import overridden_keys
from clashflow.render import RenderFailed, load_document, render

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


async def _commit(
    request: Request,
    name: str,
    *,
    content: bytes | Unwritten,
    should_reload: Callable[[], bool] | None,
) -> None:
    """Commit a Config write through the shared pipeline and map its failures
    onto the panel's status codes: a missing Config file is 404, a render-stage
    failure is 400 naming the stage, a Kernel refusal is 400 carrying the
    Kernel's own text, a refused reload is 502 carrying the Controller's."""
    kernel: KernelManager = request.app.state.kernel
    try:
        await commit_rendered_config(
            kernel_bin=kernel.binary,
            data_dir=kernel.data_dir,
            client=request.app.state.api_client,
            secret=request.app.state.secret,
            source_target=_configs_dir(request) / config_filename(name),
            runtime_target=runtime_config_path(kernel.data_dir, name),
            content=content,
            overlay=request.app.state.store.get_overlay(),
            should_reload=should_reload,
        )
    except (
        ConfigFileMissing,
        RenderFailed,
        RenderedConfigInvalid,
        KernelRejected,
        HotApplyError,
    ) as exc:
        raise_for_commit_failure(exc)


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
    try:
        await _commit(request, body.name, content=source.read_bytes(), should_reload=None)
    except HTTPException:
        store.remove_config(body.name)
        raise
    log.info("copied config %s -> %s", name, body.name)
    return store.get_config(body.name)


@router.get("/api/panel/configs/{name}")
async def get_config_content(request: Request, name: str) -> dict[str, object]:
    """The Editor read side: the registered entry, the raw Source Config
    content verbatim (the file is the source of truth; the panel never
    rewrites it), and the keys the Overlay currently wins. Those keys are what
    the editor marks as overridden: a value the user edits there never reaches
    the Kernel."""
    store = request.app.state.store
    try:
        entry = store.get_config(name)
    except ConfigNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    path = _configs_dir(request) / config_filename(name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"config file missing: {path.name}")
    try:
        source_document = load_document(path.read_bytes())
    except RenderFailed:
        # An unparseable Source Config has no keys to mark; the save path
        # reports the render failure when the user actually saves.
        source_document = {}
    return {
        **entry,
        "content": path.read_text(),
        "overridden_keys": overridden_keys(source_document, store.get_overlay()),
    }


def _runtime_path(request: Request, name: str) -> Path:
    kernel: KernelManager = request.app.state.kernel
    return runtime_config_path(kernel.data_dir, name)


@router.get("/api/panel/configs/{name}/runtime")
async def get_runtime_config(request: Request, name: str) -> dict[str, object]:
    """The Runtime Config, read-only: the rendered bytes the Kernel runs, plus
    whether they still match a render of the current Source Config and
    Overlay. There is deliberately no write endpoint — the Runtime Config is a
    build artifact, and a second editable copy would be a second truth."""
    store = request.app.state.store
    try:
        store.get_config(name)
    except ConfigNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    runtime = _runtime_path(request, name)
    content = runtime.read_bytes() if runtime.is_file() else None
    if content is None:
        raise HTTPException(
            status_code=404, detail=f"runtime config not rendered yet: {runtime.name}"
        )
    source = _configs_dir(request) / config_filename(name)
    current = False
    if source.is_file():
        try:
            current = render(source.read_bytes(), store.get_overlay()) == content
        except RenderFailed:
            current = False
    return {"content": content.decode("utf-8", errors="replace"), "current": current}


@router.api_route(
    "/api/panel/configs/{name}/runtime",
    methods=["POST", "PUT", "PATCH", "DELETE"],
    status_code=405,
)
async def write_runtime_config(name: str) -> None:
    """Explicit refusal: the Runtime Config is a build artifact and has no
    write endpoint. Without this route the method would fall through to the
    Controller reverse proxy and read as "unknown kernel route" instead."""
    raise HTTPException(
        status_code=405,
        detail=f"runtime config is read-only; edit the Source Config or the Overlay: {name}",
    )


@router.get("/api/panel/configs/{name}/runtime/diff")
async def get_runtime_diff(request: Request, name: str) -> dict[str, object]:
    """Per-key differences between the Runtime Config and its Source Config:
    only the keys the Overlay changed, which is the audit trail for "what did
    the panel add or replace?". The diff is computed structurally, so a
    formatting-only difference is not a row."""
    store = request.app.state.store
    try:
        store.get_config(name)
    except ConfigNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    runtime = _runtime_path(request, name)
    if not runtime.is_file():
        raise HTTPException(
            status_code=404, detail=f"runtime config not rendered yet: {runtime.name}"
        )
    source = _configs_dir(request) / config_filename(name)
    if not source.is_file():
        raise HTTPException(status_code=404, detail=f"config file missing: {source.name}")
    try:
        source_document = load_document(source.read_bytes())
        runtime_document = load_document(runtime.read_bytes())
    except RenderFailed as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"keys": overlay_diff(source_document, runtime_document)}


@router.put("/api/panel/configs/{name}")
async def save_config(request: Request, name: str) -> ConfigEntry:
    """Store the raw YAML body as the Source Config, render it with the
    Overlay, validate the RESULT with the Kernel config-test, and hot-apply the
    Runtime Config when this Config is Active (same process, no restart). A
    render or validation failure leaves both files byte-identical and returns
    the render stage name or the Kernel's output verbatim. On a refused
    hot-apply both pre-save states go back on disk (the Kernel keeps running
    the previous Config) and the Controller's refusal text is returned
    verbatim."""
    store = request.app.state.store
    try:
        entry = store.get_config(name)
    except ConfigNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    kernel: KernelManager = request.app.state.kernel
    content = await request.body()
    await _commit(
        request,
        name,
        content=content,
        should_reload=lambda: entry["active"] and kernel.alive(),
    )
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
    kernel: KernelManager = request.app.state.kernel
    await _commit(request, name, content=UNWRITTEN, should_reload=kernel.alive)
    store.activate_config(name)
    log.info("switched Active Config to %s", name)
    return store.get_config(name)


@router.delete("/api/panel/configs/{name}")
async def delete_config(request: Request, name: str) -> dict[str, str]:
    try:
        entry = request.app.state.store.get_config(name)
    except ConfigNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # The pair rule: a Subscription-derived Config is deleted through its
    # Subscription only, or the Subscription is left orphaned.
    if entry["kind"] == SUBSCRIPTION_DERIVED_KIND:
        raise HTTPException(
            status_code=409,
            detail=f"config is subscription-derived; delete its subscription instead: {name}",
        )
    try:
        request.app.state.store.remove_config(name)
    except ConfigNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConfigIsActive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    (_configs_dir(request) / config_filename(name)).unlink(missing_ok=True)
    _runtime_path(request, name).unlink(missing_ok=True)
    log.info("deleted config %s", name)
    return {"deleted": name}
