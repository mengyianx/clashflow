import os
import shutil
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

REPO_ROOT = Path(__file__).resolve().parent.parent

type Panel = SimpleNamespace  # app, client, data_dir


def _locate_kernel() -> str:
    env = os.environ.get("MIHOMO_BIN")
    if env:
        return env
    on_path = shutil.which("mihomo")
    if on_path:
        return on_path
    for candidate in sorted(Path.home().glob(".cache/clashflow/kernel-*/mihomo")):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    pytest.fail(
        "mihomo kernel binary not found: set MIHOMO_BIN, put `mihomo` on PATH, "
        "or run scripts/fetch-kernel.sh to download the pinned release",
        pytrace=False,
    )


@pytest.fixture(scope="session")
def mihomo_bin() -> str:
    return _locate_kernel()


BootApp = Callable[..., AbstractAsyncContextManager[Panel]]


@pytest.fixture
def boot_app(mihomo_bin: str, tmp_path_factory: pytest.TempPathFactory) -> BootApp:
    @asynccontextmanager
    async def _boot(
        *,
        data_dir: Path | None = None,
        kernel_bin: str | None = None,
        web_dist: Path | None = None,
        config_body: str | None = None,
    ) -> AsyncIterator[Panel]:
        from clashflow.main import create_app

        data = data_dir or tmp_path_factory.mktemp("clashflow-data")
        if config_body is not None:
            (data / "configs").mkdir(parents=True, exist_ok=True)
            (data / "configs" / "default.yaml").write_text(config_body)
        app: FastAPI = create_app(
            data_dir=data,
            kernel_bin=kernel_bin or mihomo_bin,
            web_dist=web_dist,
        )
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://panel") as client:
                yield SimpleNamespace(app=app, client=client, data_dir=data)

    return _boot


def lan_ip() -> str:
    """A non-loopback local address of this host, used to exercise LAN exposure."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("1.1.1.1", 80))
        return s.getsockname()[0]
