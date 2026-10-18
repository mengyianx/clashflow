"""Application assembly: /data layout, default Config, Kernel spawn, static web shell."""

import asyncio
import logging
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from clashflow.api.service import router as service_router
from clashflow.apiproxy import router as apiproxy_router
from clashflow.db import MetadataStore
from clashflow.kernel import KernelManager

log = logging.getLogger(__name__)

DEFAULT_CONFIG_NAME = "default"
DEFAULT_CONFIG_BODY = """\
mixed-port: 7890
allow-lan: true
"""


def _ensure_layout(data_dir: Path) -> Path:
    (data_dir / "configs").mkdir(parents=True, exist_ok=True)
    return data_dir / "configs"


def _ensure_default_config(store: MetadataStore, configs_dir: Path) -> str:
    """Register and activate the boot Config; create its file unless already present."""
    config_file = configs_dir / f"{DEFAULT_CONFIG_NAME}.yaml"
    if not config_file.exists():
        config_file.write_text(DEFAULT_CONFIG_BODY)
    store.register_config(DEFAULT_CONFIG_NAME, "handwritten")
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
    boot_timeout: float = 10.0,
) -> FastAPI:
    resolved_data = data_dir or Path(os.environ.get("CLASHFLOW_DATA_DIR", "/data"))
    resolved_kernel = kernel_bin or os.environ.get("CLASHFLOW_KERNEL", "mihomo")
    if web_dist is None:
        env_dist = os.environ.get("CLASHFLOW_WEB_DIST")
        web_dist = (
            Path(env_dist) if env_dist else Path(__file__).resolve().parent.parent / "web" / "dist"
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configs_dir = _ensure_layout(resolved_data)
        store = MetadataStore(resolved_data / "clashflow.db")
        app.state.store = store
        active = _ensure_default_config(store, configs_dir)
        secret = _resolve_secret(store)
        app.state.secret = secret
        # Shared upstream client for the /api reverse proxy (clashflow.apiproxy).
        api_client = httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(30.0))
        app.state.api_client = api_client

        manager = KernelManager(resolved_kernel, resolved_data)
        app.state.kernel = manager

        def active_config_path() -> Path:
            return configs_dir / f"{store.get_active() or DEFAULT_CONFIG_NAME}.yaml"

        supervisor = manager.supervise(active_config_path, secret)
        try:
            manager.start(configs_dir / f"{active}.yaml", secret)
        except OSError as exc:
            log.error("kernel failed to spawn: %s", exc)
        else:
            ready = await _wait_ready(manager, secret, boot_timeout)
            log.info("kernel %s", "ready" if ready else "failed to become ready")

        try:
            yield
        finally:
            supervisor.cancel()
            await api_client.aclose()
            manager.stop()
            store.close()

    app = FastAPI(title="ClashFlow", lifespan=lifespan)
    app.include_router(service_router)
    # Panel routers stay above the /api reverse proxy: an explicit panel route
    # always shadows the proxy (see clashflow/apiproxy.py routing convention).
    app.include_router(apiproxy_router)
    app.mount("/", StaticFiles(directory=web_dist, html=True, check_dir=False), name="web")
    return app


app = create_app()
