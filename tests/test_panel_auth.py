"""Ticket #17: Panel auth and access-surface lock-down.

The Management Panel requires the `CLASHFLOW_PASSWORD` (plan §2: REQUIRED
env) via a login page and a server-side session cookie that covers every
panel route — the `/api/panel/*` CRUD, the `/api/*` reverse proxy (REST and
WebSocket) and the `/ui/` Runtime Dashboard bundle. Logout revokes the
session server-side. The proxy port 7890 keeps serving LAN clients without
authentication (plan §1), and `GET /healthz` stays an unauthenticated
liveness surface for the container HEALTHCHECK.
"""

import contextlib
import http.server
import json
import socketserver
import threading

import httpx
import pytest
from conftest import TEST_PANEL_PASSWORD, lan_ip
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from clashflow.auth import SESSION_COOKIE

BROWSER_HEADERS = {"Accept": "text/html,application/xhtml+xml"}
SUB_BODY = b"mixed-port: 7892\nallow-lan: true\n"


def anon_client(app) -> httpx.AsyncClient:
    """An unauthenticated panel client (fresh cookie jar) at the ASGI seam."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://panel")


@contextlib.contextmanager
def subscription_server(body: bytes = SUB_BODY):
    """Local origin standing in for a Subscription provider."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/sub"
    finally:
        server.shutdown()
        server.server_close()


# --- Startup: CLASHFLOW_PASSWORD is REQUIRED (plan §2 contract table) ---


async def test_missing_panel_password_fails_startup_with_a_clear_error(
    boot_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLASHFLOW_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="CLASHFLOW_PASSWORD"):
        async with boot_app(password=""):
            pass


# --- AC1: unauthenticated access to any panel surface is rejected ---


async def test_unauthenticated_access_to_every_panel_surface_is_rejected(boot_app) -> None:
    async with boot_app() as panel:
        # A fresh client: no login, no cookie.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=panel.app), base_url="http://panel"
        ) as anon:
            # Management Panel CRUD: read and write alike.
            assert (await anon.get("/api/panel/configs")).status_code == 401
            assert (await anon.get("/api/panel/subscriptions")).status_code == 401
            assert (await anon.get("/api/service/health")).status_code == 401
            refused = await anon.post("/api/panel/configs", json={"name": "evil"})
            assert refused.status_code == 401

            # The /api reverse proxy: REST is rejected before reaching the Kernel.
            assert (await anon.get("/api/version")).status_code == 401
            assert (await anon.get("/api/configs")).status_code == 401

            # The Runtime Dashboard surface: HTML navigation is redirected to
            # the login page (the Management Panel shell), other fetches 401.
            dashboard = await anon.get("/ui/", headers=BROWSER_HEADERS)
            assert dashboard.status_code == 307
            assert dashboard.headers["location"] == "/"
            entry = await anon.get("/ui", headers=BROWSER_HEADERS)
            assert entry.status_code == 307
            assert (await anon.get("/ui/config.js")).status_code == 401

        # The rejected write changed nothing.
        configs = await panel.client.get("/api/panel/configs")
        assert [entry["name"] for entry in configs.json()] == ["default"]


async def test_unauthenticated_websocket_upgrade_through_the_proxy_is_rejected(
    boot_app,
) -> None:
    async with boot_app() as panel:
        client = TestClient(panel.app)  # fresh cookie jar, never logged in
        with (
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect("/api/traffic"),
        ):
            pass
        assert exc_info.value.code == 4401


async def test_healthz_stays_an_unauthenticated_liveness_surface(boot_app) -> None:
    async with boot_app() as panel:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=panel.app), base_url="http://panel"
        ) as anon:
            resp = await anon.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# --- Login: password checking and cookie flags ---


async def test_wrong_password_is_rejected_and_leaves_the_panel_untouched(boot_app) -> None:
    async with (
        boot_app() as panel,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=panel.app), base_url="http://panel"
        ) as anon,
    ):
        wrong = await anon.post("/api/panel/auth/login", json={"password": "not-the-password"})
        assert wrong.status_code == 401
        assert "clashflow_session" not in wrong.headers.get_list("set-cookie")
        assert (await anon.get("/api/service/health")).status_code == 401

        # The documented password still logs in afterwards.
        ok = await anon.post("/api/panel/auth/login", json={"password": TEST_PANEL_PASSWORD})
        assert ok.status_code == 200
        assert (await anon.get("/api/service/health")).status_code == 200


async def test_session_cookie_is_httponly_signed_by_server_side_randomness(boot_app) -> None:
    """The cookie value is a 256-bit `secrets.token_urlsafe` token held
    server-side; the browser sees only the HttpOnly cookie."""
    async with (
        boot_app() as panel,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=panel.app), base_url="http://panel"
        ) as anon,
    ):
        login = await anon.post("/api/panel/auth/login", json={"password": TEST_PANEL_PASSWORD})
        set_cookie = login.headers["set-cookie"]
        assert "clashflow_session=" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "SameSite=lax" in set_cookie
        assert "Path=/" in set_cookie
        token = anon.cookies.get(SESSION_COOKIE)
        assert token is not None and len(token) >= 43

        # A forged cookie value is refused: without the server-side
        # registry entry the request cannot authenticate. (Fresh client —
        # the jar above still holds the real session.)
        forged = "Zm9yZ2VkLXNlc3Npb24tdG9rZW4tZm9yZ2VkLXNlc3Npb24tdG9rZW4"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=panel.app), base_url="http://panel"
        ) as forged_client:
            forged_client.cookies.set("clashflow_session", forged)
            assert (await forged_client.get("/api/service/health")).status_code == 401


# --- AC2: after login, all previously built flows work unchanged ---


async def test_after_login_all_panel_flows_work_through_the_session(boot_app) -> None:
    async with boot_app() as panel:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=panel.app), base_url="http://panel"
        ) as client:
            login = await client.post(
                "/api/panel/auth/login", json={"password": TEST_PANEL_PASSWORD}
            )
            assert login.status_code == 200

            # Configs: CRUD + save through the apply pipeline.
            listed = await client.get("/api/panel/configs")
            assert [entry["name"] for entry in listed.json()] == ["default"]
            created = await client.post(
                "/api/panel/configs", json={"name": "custom", "template": True}
            )
            assert created.status_code == 201
            saved = await client.put(
                "/api/panel/configs/custom", content="mixed-port: 7890\nallow-lan: true\n"
            )
            assert saved.status_code == 200
            activated = await client.put("/api/panel/configs/custom/active")
            assert activated.status_code == 200
            restored = await client.put("/api/panel/configs/default/active")
            assert restored.status_code == 200
            removed = await client.delete("/api/panel/configs/custom")
            assert removed.status_code == 200

            # Subscriptions: add, refresh, fetch settings.
            with subscription_server() as url:
                added = await client.post(
                    "/api/panel/subscriptions", json={"name": "sub1", "url": url}
                )
                assert added.status_code == 201
                refreshed = await client.post("/api/panel/subscriptions/sub1/refresh")
                assert refreshed.status_code == 200
                assert refreshed.json()["last_error"] is None
                patched = await client.patch(
                    "/api/panel/subscriptions/sub1",
                    json={"update_interval_minutes": 30},
                )
                assert patched.status_code == 200
                assert patched.json()["update_interval_minutes"] == 30

            # Service Control: status through the panel API.
            health = await client.get("/api/service/health")
            assert health.status_code == 200
            assert health.json()["kernel"] == "up"

            # The Runtime Dashboard's REST probe through the reverse proxy.
            version = await client.get("/api/version")
            assert version.status_code == 200
            assert "version" in version.json()

        # One WebSocket stream through the proxy, on an authenticated session.
        ws_client = TestClient(panel.app)
        ws_client.post("/api/panel/auth/login", json={"password": TEST_PANEL_PASSWORD})
        with ws_client.websocket_connect("/api/traffic") as ws:
            traffic = json.loads(ws.receive_text())
        assert {"up", "down", "upTotal", "downTotal"} <= traffic.keys()


# --- AC3: logout invalidates the session ---


async def test_logout_invalidates_the_session_and_replay_is_rejected(boot_app) -> None:
    async with boot_app() as panel:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=panel.app), base_url="http://panel"
        ) as client:
            await client.post("/api/panel/auth/login", json={"password": TEST_PANEL_PASSWORD})
            assert (await client.get("/api/service/health")).status_code == 200
            token = client.cookies.get(SESSION_COOKIE)
            assert token is not None

            logged_out = await client.post("/api/panel/auth/logout")
            assert logged_out.status_code == 200
            # The session is dead for the logged-out browser...
            assert (await client.get("/api/service/health")).status_code == 401

        # ...and dead for anyone replaying the pre-logout cookie: logout
        # revoked the token server-side, it did not merely clear a cookie.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=panel.app), base_url="http://panel"
        ) as replay:
            replay.cookies.set(SESSION_COOKIE, token)
            stolen = await replay.get("/api/service/health")
            assert stolen.status_code == 401


# --- AC4: the proxy port keeps serving LAN clients without authentication ---


async def test_proxy_port_serves_lan_clients_without_any_authentication(boot_app) -> None:
    class OriginHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = b"clashflow-anon-ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    origin = socketserver.TCPServer(("127.0.0.1", 0), OriginHandler)
    threading.Thread(target=origin.serve_forever, daemon=True).start()
    origin_port = origin.server_address[1]
    try:
        async with boot_app() as panel:
            # The same unauthenticated client is refused by the panel...
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=panel.app), base_url="http://panel"
            ) as anon:
                assert (await anon.get("/api/service/health")).status_code == 401

            # ...yet the proxy port carries its traffic with no credential.
            host = lan_ip()
            async with httpx.AsyncClient(proxy=f"http://{host}:7890") as proxied:
                via_lan = await proxied.get(f"http://127.0.0.1:{origin_port}/")
            assert via_lan.status_code == 200
            assert via_lan.content == b"clashflow-anon-ok"
    finally:
        origin.shutdown()
        origin.server_close()
