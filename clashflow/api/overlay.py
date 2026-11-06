"""Gateway Overlay surface: one global record under `/api/panel/overlay`.

The Overlay is a single panel-owned set of gateway keys applied over every
Source Config at render time (ADR-0004), not a per-Config setting. Saving it
re-renders the Active Config and hot-applies the result, so a log-level change
reaches the Kernel without a restart; a value for a key outside the M1 surface is refused, and a
render or reload failure leaves both the Runtime Config and the stored Overlay
as they were — the panel never claims a state the Kernel is not running.

The YAML export/import pair is the backup path: exporting yields a
human-readable document, importing it back is a replace (no merge), and the
round trip is lossless because both sides validate to the same canonical
mapping.
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from clashflow.api.failures import raise_for_commit_failure
from clashflow.apply import (
    UNWRITTEN,
    ConfigFileMissing,
    HotApplyError,
    KernelRejected,
    RenderedConfigInvalid,
    commit_rendered_config,
)
from clashflow.db import config_filename, runtime_config_path
from clashflow.kernel import KernelManager
from clashflow.overlay import (
    EDITABLE_KEYS,
    OverlayError,
    export_yaml,
    import_yaml,
    leaf_paths,
    validate,
)
from clashflow.render import RenderFailed

log = logging.getLogger(__name__)

router = APIRouter()


class OverlayPayload(BaseModel):
    # The whole new state (replace semantics): keys absent from `values` are
    # no longer owned, so the Source Config's values pass through again.
    values: dict[str, Any] = {}


def _state(request: Request) -> dict[str, object]:
    return {
        "values": request.app.state.store.get_overlay(),
        "editable": list(EDITABLE_KEYS),
    }


async def _apply(request: Request, values: dict[str, Any]) -> dict[str, object]:
    """Validate, re-render the Active Config, hot-apply, then store.

    The store is written LAST: a refused render or reload must leave the
    panel's Overlay matching what the Kernel is running, and the shared
    pipeline already put the previous Runtime Config bytes back."""
    canonical = validate(values)
    # M1 exposes a narrow surface. The schema can express the TUN block and
    # the DNS mode keys so the renderer is ready for them, but writing them
    # now would apply a data-plane change the M1 hot-apply path cannot make
    # safely (mode changes restart the Kernel — ADR-0010 — and that logic
    # lands with 2.0 M2). Refusing beats silently hot-applying a TUN switch.
    unsupported = [path for path in leaf_paths(canonical) if path not in EDITABLE_KEYS]
    if unsupported:
        raise OverlayError(
            f"{unsupported[0]}: not editable in this release (only {', '.join(EDITABLE_KEYS)})"
        )
    store = request.app.state.store
    active = store.get_active()
    if active is not None:
        kernel: KernelManager = request.app.state.kernel
        try:
            result = await commit_rendered_config(
                kernel_bin=kernel.binary,
                data_dir=kernel.data_dir,
                client=request.app.state.api_client,
                secret=request.app.state.secret,
                source_target=request.app.state.configs_dir / config_filename(active),
                runtime_target=runtime_config_path(kernel.data_dir, active),
                content=UNWRITTEN,
                overlay=canonical,
                should_reload=kernel.alive,
            )
        except (
            ConfigFileMissing,
            RenderFailed,
            RenderedConfigInvalid,
            KernelRejected,
            HotApplyError,
        ) as exc:
            raise_for_commit_failure(exc)
        # Issue #43: the re-rendered Active Config is what the Kernel runs, so
        # its summary is refreshed too (the Overlay does not change counts
        # today, but a future stage may).
        store.set_config_summary(active, result.summary.as_dict())
    store.set_overlay(canonical)
    log.info("saved overlay (%d owned keys)", len(canonical))
    return _state(request)


@router.get("/api/panel/overlay")
async def get_overlay(request: Request) -> dict[str, object]:
    """The stored Overlay plus which keys the M1 panel exposes controls for."""
    return _state(request)


@router.put("/api/panel/overlay")
async def put_overlay(request: Request, body: OverlayPayload) -> dict[str, object]:
    """Replace the Overlay: validate, re-render + hot-apply the Active Config
    (same Kernel process), then store. Refusals answer 400 (bad key/type or a
    render/config-test failure) or 502 (the Controller refused the reload)."""
    try:
        return await _apply(request, body.values)
    except OverlayError as exc:
        raise HTTPException(status_code=400, detail=f"overlay: {exc}") from exc


@router.get("/api/panel/overlay/export")
async def export_overlay(request: Request) -> Response:
    """Download the Overlay as YAML (canonical key order), for review or
    backup; `import` reads exactly this format back."""
    yaml_text = export_yaml(request.app.state.store.get_overlay())
    return Response(
        content=yaml_text,
        media_type="application/yaml",
        headers={"Content-Disposition": 'attachment; filename="overlay.yaml"'},
    )


@router.post("/api/panel/overlay/import")
async def import_overlay(request: Request) -> dict[str, object]:
    """Replace the Overlay from a YAML document (the raw request body). An
    exported Overlay imports back to the same state."""
    text = (await request.body()).decode("utf-8", errors="replace")
    try:
        values = import_yaml(text)
        return await _apply(request, values)
    except OverlayError as exc:
        raise HTTPException(status_code=400, detail=f"overlay: {exc}") from exc
