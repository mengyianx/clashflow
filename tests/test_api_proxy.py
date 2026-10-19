"""#14 seam tests: the panel's `/api/*` surface proxies to the Controller with the
secret injected server-side, and panel routes / proxy routes do not shadow each other."""

import json
import socket

import httpx
import pytest
from conftest import TEST_PANEL_PASSWORD, lan_ip
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

CONTROLLER = "http://127.0.0.1:9090"


async def test_rest_calls_through_panel_api_surface_reach_the_controller(boot_app) -> None:
    async with boot_app() as panel:
        version = await panel.client.get("/api/version")
        assert version.status_code == 200
        assert version.json() == {"meta": True, "version": "v1.19.32"}

        proxies = await panel.client.get("/api/proxies")
        assert proxies.status_code == 200
        assert "DIRECT" in proxies.json()["proxies"]


async def test_websocket_streams_pass_through_the_proxy(boot_app) -> None:
    async with boot_app() as panel:
        client = TestClient(panel.app)
        # #17: the WebSocket proxy requires a session like every panel route.
        login = client.post("/api/panel/auth/login", json={"password": TEST_PANEL_PASSWORD})
        assert login.status_code == 200

        # /traffic pushes a frame roughly once per second.
        with client.websocket_connect("/api/traffic") as ws:
            traffic = json.loads(ws.receive_text())
        assert {"up", "down", "upTotal", "downTotal"} <= traffic.keys()

        # A forced reload through the proxy makes the Kernel log; the log
        # stream must deliver the reload's lines end-to-end.
        with client.websocket_connect("/api/logs?level=info") as ws:
            reload_resp = await panel.client.put("/api/configs?force=true", json={})
            assert reload_resp.status_code == 204
            payloads = []
            for _ in range(20):
                entry = json.loads(ws.receive_text())
                payloads.append(entry["payload"])
                if "Initial configuration complete" in entry["payload"]:
                    break
            else:
                pytest.fail(f"no reload log line arrived through the proxy: {payloads!r}")


async def test_browser_side_never_needs_or_sees_the_secret(boot_app) -> None:
    async with boot_app() as panel:
        secret = panel.app.state.secret

        # The Controller itself refuses anonymous and mis-authenticated callers.
        async with httpx.AsyncClient() as direct:
            assert (await direct.get(f"{CONTROLLER}/version")).status_code == 401
            forged_direct = await direct.get(
                f"{CONTROLLER}/version", headers={"Authorization": "Bearer bogus"}
            )
            assert forged_direct.status_code == 401

        # Through the proxy no credential is needed and none is honored: the
        # secret is injected server-side, the browser's own header discarded.
        anonymous = await panel.client.get("/api/version")
        assert anonymous.status_code == 200
        forged = await panel.client.get("/api/version", headers={"Authorization": "Bearer bogus"})
        assert forged.status_code == 200

        # Nothing the browser sees carries the secret.
        assert secret not in anonymous.text
        assert secret not in forged.text
        assert all(secret not in value for value in anonymous.headers.values())

        # And the Controller port stays unreachable from the LAN.
        with (
            pytest.raises(ConnectionRefusedError),
            socket.create_connection((lan_ip(), 9090), timeout=2),
        ):
            pass


async def test_panel_routes_are_not_shadowed_by_the_proxy(boot_app) -> None:
    async with boot_app() as panel:
        resp = await panel.client.get("/api/service/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["kernel"] == "up"
        assert body["active_config"] == "default"


async def test_proxy_owns_api_paths_the_panel_does_not_define(boot_app) -> None:
    """The `/configs` collision, resolved by the routing convention: `/api/configs`
    belongs to the Controller (the Runtime Dashboard drives GET/PATCH /configs);
    Management Panel CRUD must register under `/api/panel/...` instead."""
    async with boot_app() as panel:
        current = await panel.client.get("/api/configs")
        assert current.status_code == 200
        body = current.json()
        assert body["mode"] == "rule"
        assert "mixed-port" in body

        switched = await panel.client.patch("/api/configs", json={"mode": "global"})
        assert switched.status_code == 204
        assert (await panel.client.get("/api/configs")).json()["mode"] == "global"

        restored = await panel.client.patch("/api/configs", json={"mode": "rule"})
        assert restored.status_code == 204


async def test_rest_proxy_reports_kernel_down_as_bad_gateway(boot_app, tmp_path) -> None:
    broken = tmp_path / "not-a-kernel"
    broken.write_text("#!/bin/sh\nexit 1\n")
    broken.chmod(0o755)

    async with boot_app(kernel_bin=str(broken)) as panel:
        resp = await panel.client.get("/api/version")
        assert resp.status_code == 502


async def test_websocket_proxy_reports_kernel_down_as_a_1011_close(boot_app, tmp_path) -> None:
    # The WebSocket twin of the REST 502: an authenticated stream request
    # finds no Controller to complete the upstream handshake against, so the
    # browser sees an internal-error close (1011), not a hang or a bare drop.
    broken = tmp_path / "not-a-kernel"
    broken.write_text("#!/bin/sh\nexit 1\n")
    broken.chmod(0o755)

    async with boot_app(kernel_bin=str(broken)) as panel:
        client = TestClient(panel.app)
        login = client.post("/api/panel/auth/login", json={"password": TEST_PANEL_PASSWORD})
        assert login.status_code == 200
        with (
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect("/api/traffic"),
        ):
            pass
        assert exc_info.value.code == 1011
