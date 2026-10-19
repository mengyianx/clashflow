import contextlib
import http.server
import os
import shutil
import socketserver
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

REPO_ROOT = Path(__file__).resolve().parent.parent

# Ticket #17: the panel password comes from the environment (plan §2, REQUIRED
# env CLASHFLOW_PASSWORD). boot_app sets this value for every boot; tests
# logging in on their own client (e.g. a TestClient for WebSockets) use it too.
TEST_PANEL_PASSWORD = "clashflow-test-panel-password"

type Panel = SimpleNamespace  # app, client, data_dir, password


@contextlib.contextmanager
def origin_server(body: bytes, status: int = 200) -> Iterator[SimpleNamespace]:
    """Local HTTP origin standing in for a Subscription provider (URL under
    `/subscription`) or a plain proxy target (`port`): the served body and
    status can be changed in place via `state`, every request's User-Agent is
    recorded, and `stop()` kills the listener to simulate a network error.
    Shared by every test that needs one (previously duplicated per module)."""

    state: dict[str, Any] = {"body": body, "status": status, "user_agents": []}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            state["user_agents"].append(self.headers.get("User-Agent"))
            self.send_response(state["status"])
            self.send_header("Content-Length", str(len(state["body"])))
            self.end_headers()
            self.wfile.write(state["body"])

        def log_message(self, *args: object) -> None:
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stopped = False

    def stop() -> None:
        nonlocal stopped
        if not stopped:
            stopped = True
            server.shutdown()
            server.server_close()

    origin = SimpleNamespace(
        url=f"http://127.0.0.1:{server.server_address[1]}/subscription",
        port=server.server_address[1],
        state=state,
        stop=stop,
    )
    try:
        yield origin
    finally:
        stop()


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


@pytest.fixture(scope="session")
def metacubexd_dist() -> Path:
    env = os.environ.get("CLASHFLOW_UI_DIST")
    if env and (Path(env) / "index.html").is_file():
        return Path(env)
    repo_bundle = REPO_ROOT / "metacubexd"
    if (repo_bundle / "index.html").is_file():
        return repo_bundle
    for candidate in sorted(Path.home().glob(".cache/clashflow/ui-*/dist")):
        if (candidate / "index.html").is_file():
            return candidate
    pytest.fail(
        "metacubexd bundle not found: run scripts/fetch-ui.sh (or set CLASHFLOW_UI_DIST)",
        pytrace=False,
    )


BootApp = Callable[..., AbstractAsyncContextManager[Panel]]


@pytest.fixture
def boot_app(mihomo_bin: str, tmp_path_factory: pytest.TempPathFactory) -> BootApp:
    @asynccontextmanager
    async def _boot(
        *,
        data_dir: Path | None = None,
        kernel_bin: str | None = None,
        web_dist: Path | None = None,
        ui_dist: Path | None = None,
        config_body: str | None = None,
        password: str | None = None,
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
            ui_dist=ui_dist,
            # #17: every boot carries the panel password; a test may pass "" to
            # exercise the missing-CLASHFLOW_PASSWORD startup failure.
            password=TEST_PANEL_PASSWORD if password is None else password,
        )
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://panel") as client:
                # The shared client authenticates once (session cookie jar); a
                # test that needs an anonymous client builds its own.
                login = await client.post(
                    "/api/panel/auth/login", json={"password": TEST_PANEL_PASSWORD}
                )
                assert login.status_code == 200, login.text
                yield SimpleNamespace(app=app, client=client, data_dir=data)

    return _boot


def lan_ip() -> str:
    """A non-loopback local address of this host, used to exercise LAN exposure."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("1.1.1.1", 80))
        return s.getsockname()[0]
