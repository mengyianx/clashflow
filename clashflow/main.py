"""Application assembly: /data layout, default Config, Kernel spawn, static web shell."""

import asyncio
import json
import logging
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from clashflow.api.configs import router as configs_router
from clashflow.api.overlay import router as overlay_router
from clashflow.api.service import router as service_router
from clashflow.api.subscriptions import router as subscriptions_router
from clashflow.api.updates import router as updates_router
from clashflow.apiproxy import router as apiproxy_router
from clashflow.apply import render_runtime_config
from clashflow.auth import AuthMiddleware, SessionRegistry
from clashflow.auth import router as auth_router
from clashflow.db import RUNTIME_CONFIGS_DIRNAME, MetadataStore, runtime_config_path
from clashflow.kernel import KernelManager
from clashflow.migrate import migrate_volume
from clashflow.render import RenderFailed
from clashflow.scheduler import SubscriptionScheduler
from clashflow.static_mount import SwitchingStaticFiles
from clashflow.ui import router as ui_router
from clashflow.updates import UpdateManager
from clashflow.zui import router as zui_router

log = logging.getLogger(__name__)

DEFAULT_CONFIG_NAME = "default"
# The panel-authored boot template (written once when the file is absent;
# existing files are never rewritten — ADR-0001). GEO auto-update ships
# enabled here: the Kernel's updater ticker registers at process start only.
DEFAULT_CONFIG_BODY = """\
mixed-port: 7890
allow-lan: true
geo-auto-update: true
geo-update-interval: 24
"""


def _ensure_layout(data_dir: Path) -> Path:
    (data_dir / "configs").mkdir(parents=True, exist_ok=True)
    # Rendered artifacts live beside the sources, never under `/data/runtime/`
    # (Runtime Update payloads, ADR-0003).
    (data_dir / RUNTIME_CONFIGS_DIRNAME).mkdir(parents=True, exist_ok=True)
    return data_dir / "configs"


def _ensure_default_config(store: MetadataStore, configs_dir: Path) -> str:
    """Register and activate the boot Config; create its file unless already present."""
    config_file = configs_dir / f"{DEFAULT_CONFIG_NAME}.yaml"
    if not config_file.exists():
        config_file.write_text(DEFAULT_CONFIG_BODY)
    store.register_config(DEFAULT_CONFIG_NAME, "hand-written")
    if store.get_active() is None:
        store.set_active(DEFAULT_CONFIG_NAME)
    return store.get_active() or DEFAULT_CONFIG_NAME


def _resolve_secret(store: MetadataStore) -> str:
    env_secret = os.environ.get("CLASHFLOW_SECRET")
    if env_secret:
        return env_secret
    stored = store.get_meta("secret")
    if stored:
        return stored
    generated = secrets.token_urlsafe(32)
    store.set_meta("secret", generated)
    return generated


async def _wait_ready(manager: KernelManager, secret: str, timeout: float) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if not manager.alive():
            return False
        if await manager.probe(secret):
            return True
        await asyncio.sleep(0.05)
    return False


def create_app(
    data_dir: Path | None = None,
    kernel_bin: str | None = None,
    web_dist: Path | None = None,
    ui_dist: Path | None = None,
    zui_dist: Path | None = None,
    boot_timeout: float = 10.0,
    password: str | None = None,
) -> FastAPI:
    resolved_data = data_dir or Path(os.environ.get("CLASHFLOW_DATA_DIR", "/data"))
    resolved_kernel = kernel_bin or os.environ.get("CLASHFLOW_KERNEL", "mihomo")
    # Panel password (#17): CLASHFLOW_PASSWORD is a REQUIRED env (plan §2
    # contract table). Resolution is cheap; the fail-fast check lives in the
    # lifespan below so `import clashflow.main` (module-level app) still works
    # and the process refuses to start with a clear error.
    resolved_password = password if password is not None else os.environ.get("CLASHFLOW_PASSWORD")
    if web_dist is None:
        env_dist = os.environ.get("CLASHFLOW_WEB_DIST")
        web_dist = (
            Path(env_dist) if env_dist else Path(__file__).resolve().parent.parent / "web" / "dist"
        )
    if ui_dist is None:
        env_ui = os.environ.get("CLASHFLOW_UI_DIST")
        ui_dist = Path(env_ui) if env_ui else Path(__file__).resolve().parent.parent / "metacubexd"
    if zui_dist is None:
        env_zui = os.environ.get("CLASHFLOW_ZUI_DIST")
        zui_dist = (
            Path(env_zui) if env_zui else Path(__file__).resolve().parent.parent / "zashboard"
        )
    # Runtime updates: the artifacts baked into the image are the baseline, an
    # update installed under /data wins over them (pointers are symlinks).
    image_kernel = Path(str(resolved_kernel))
    image_ui = {"metacubexd": ui_dist, "zashboard": zui_dist}
    runtime_root = resolved_data / "runtime"

    def resolve_ui(name: str) -> Path:
        """A Runtime Dashboard update installs under /data and wins over the image."""
        pointer = runtime_root / "webui" / name / "current"
        if (pointer / "index.html").is_file():
            return pointer
        return image_ui[name]

    def resolve_kernel() -> str:
        """The Kernel binary to spawn: an updated one under /data, else the image's."""
        pointer = runtime_root / "kernel" / "current"
        return str(pointer) if pointer.exists() else str(image_kernel)

    resolved_kernel = resolve_kernel()
    versions_file = Path(
        os.environ.get("CLASHFLOW_VERSIONS_FILE")
        or Path(__file__).resolve().parent.parent / "webui-versions.json"
    )
    try:
        bundle_versions = (
            json.loads(versions_file.read_text(encoding="utf-8")) if versions_file.is_file() else {}
        )
    except OSError, ValueError:
        bundle_versions = {}
    if not isinstance(bundle_versions, dict):
        bundle_versions = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if not resolved_password:
            # REQUIRED env per plan §2; fail fast before touching the volume
            # or spawning the Kernel.
            raise RuntimeError(
                "CLASHFLOW_PASSWORD is not set: the Management Panel password is required "
                "(set the CLASHFLOW_PASSWORD environment variable before starting)"
            )
        app.state.panel_password = resolved_password
        app.state.sessions = SessionRegistry()
        # ADR-0006: the volume is versioned. A newer volume refuses to start
        # (naming the backup folder to restore); an older one is backed up to
        # /data/backup/v<from>-<timestamp>/ and migrated in place BEFORE
        # anything else writes to the volume, so the backup is the volume as
        # the previous image left it and no code below reads an unmigrated
        # database.
        migrate_volume(resolved_data)
        configs_dir = _ensure_layout(resolved_data)
        store = MetadataStore(resolved_data / "clashflow.db")
        app.state.store = store
        app.state.configs_dir = configs_dir
        active = _ensure_default_config(store, configs_dir)
        secret = _resolve_secret(store)
        app.state.secret = secret
        # Shared upstream client for the /api reverse proxy (clashflow.apiproxy).
        api_client = httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(30.0))
        app.state.api_client = api_client
        app.state.resolve_ui = resolve_ui

        manager = KernelManager(resolved_kernel, resolved_data)
        app.state.kernel = manager
        updates = UpdateManager(
            store=store,
            kernel=manager,
            data_dir=resolved_data,
            secret=secret,
            image_kernel=image_kernel,
            image_ui=image_ui,
            bundle_versions={str(k): str(v) for k, v in bundle_versions.items()},
            github_api=os.environ.get("CLASHFLOW_GITHUB_API", "https://api.github.com"),
            github_proxy=os.environ.get("CLASHFLOW_GITHUB_PROXY", ""),
        )
        app.state.updates = updates

        def active_config_path() -> Path:
            # The Kernel only ever runs a Runtime Config (ADR-0004). Rendering
            # on every spawn (boot, restart, respawn) is also what makes the
            # artifact rebuildable: delete it and the next spawn recreates it
            # from the Source Config + Overlay, byte-identically.
            name = store.get_active() or DEFAULT_CONFIG_NAME
            runtime = runtime_config_path(resolved_data, name)
            try:
                render_runtime_config(configs_dir / f"{name}.yaml", runtime, store.get_overlay())
            except (RenderFailed, OSError) as exc:
                # Keep the previous artifact if there is one; the Kernel must
                # never be pointed at the Source Config instead.
                log.error("failed to render config %s into %s: %s", name, runtime.name, exc)
            return runtime

        supervisor = manager.supervise(active_config_path, secret)
        boot_runtime = active_config_path()
        if boot_runtime.is_file():
            try:
                manager.start(boot_runtime, secret)
            except OSError as exc:
                log.error("kernel failed to spawn: %s", exc)
            else:
                ready = await _wait_ready(manager, secret, boot_timeout)
                log.info("kernel %s", "ready" if ready else "failed to become ready")
        else:
            log.error(
                "no Runtime Config for %s; the Kernel is not started until the "
                "Source Config renders (ADR-0004 forbids pointing -f at the source)",
                active,
            )

        # Subscription auto-refresh scheduler (plan §5 flow 1, ticket #16):
        # starts with the panel, stops with it like the supervisor.
        scheduler = SubscriptionScheduler(store, manager, api_client, secret, configs_dir)
        scheduler.start()

        try:
            yield
        finally:
            scheduler.stop()
            supervisor.cancel()
            await updates.aclose()
            await api_client.aclose()
            manager.stop()
            store.close()

    # Ticket #17 AC1 (access-surface lock-down): FastAPI's built-in docs are
    # not part of the Management Panel surface (the UI never links them), so
    # /docs, /redoc and /openapi.json are not served at all.
    app = FastAPI(
        title="ClashFlow", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    # Auth (#17): pure-ASGI guard around every surface (http + WebSocket
    # scopes); registered first, wraps routers and static mounts alike.
    app.add_middleware(AuthMiddleware)
    # Panel routers register before the Controller reverse proxy (#14): the
    # registry owns /api/panel/*, the Kernel keeps /api/configs. The auth
    # router owns /api/panel/auth/* and the public /healthz liveness route.
    app.include_router(auth_router)
    app.include_router(configs_router)
    app.include_router(overlay_router)
    app.include_router(service_router)
    app.include_router(subscriptions_router)
    # Panel routers stay above the /api reverse proxy: an explicit panel route
    # always shadows the proxy (see clashflow/apiproxy.py routing convention).
    app.include_router(ui_router)
    app.include_router(updates_router)
    app.include_router(zui_router)
    app.include_router(apiproxy_router)
    # Runtime Dashboard (metacubexd static bundle); /ui/config.js is served by
    # clashflow.ui.router, which shadows this mount's upstream copy.
    app.mount("/ui", SwitchingStaticFiles(lambda: resolve_ui("metacubexd"), "ui"), name="ui")
    # Second Runtime Dashboard (zashboard); /zui/ itself is served by
    # clashflow.zui.router (bootstrap injection), which shadows this mount.
    app.mount("/zui", SwitchingStaticFiles(lambda: resolve_ui("zashboard"), "zui"), name="zui")
    app.mount("/", StaticFiles(directory=web_dist, html=True, check_dir=False), name="web")
    return app


app = create_app()
