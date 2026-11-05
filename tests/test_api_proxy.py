"""#14 seam tests: the panel's `/api/*` surface proxies to the Controller with the
secret injected server-side, and panel routes / proxy routes do not shadow each other.

The shipped namespaces: the Management Panel owns `/api/panel/...` and the older
`/api/service/...` group; every other `/api/...` path is Controller-owned and is
forwarded verbatim.

#25 seam tests: the deny list is the one place a Controller path is refused —
the Management Panel is the only path to restarting or upgrading the Kernel and
to writing the Controller's Config path — so the proxy refuses those endpoints
before anything is forwarded, and keeps forwarding everything else."""

import json
import socket
from pathlib import Path

import httpx
import pytest
from conftest import TEST_PANEL_PASSWORD, lan_ip
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

CONTROLLER = "http://127.0.0.1:9090"

# The panel-authored refusal (#25): English, naming the endpoint it refuses.
# The Management Panel maps the prefix to its Chinese title (web/src/lib/messages.ts).
REFUSAL = "endpoint not exposed by the Management Panel"


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

        # A forced reload makes the Kernel log; the log stream must deliver
        # the reload's lines end-to-end. #25: the Controller's Config write is
        # refused at the proxy, so the reload goes through the Management
        # Panel's own save path.
        with client.websocket_connect("/api/logs?level=info") as ws:
            content = (await panel.client.get("/api/panel/configs/default")).json()["content"]
            reload_resp = await panel.client.put(
                "/api/panel/configs/default",
                content=content,
                headers={"Content-Type": "text/plain; charset=utf-8"},
            )
            assert reload_resp.status_code == 200
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


async def test_proxy_refuses_post_restart_and_keeps_the_kernel_on_the_active_config(
    boot_app,
) -> None:
    """A browser-initiated restart re-execs the Kernel with the process's
    original arguments, so it would bring the Kernel back onto the boot
    Config while the panel keeps reporting the new Active Config. The refusal
    keeps the running Config and the reported one the same."""
    async with boot_app() as panel:
        # A second Config with a listener port of its own: the Kernel's
        # arguments still point at the boot Config, so only the running
        # Kernel's own view tells the two apart.
        created = await panel.client.post("/api/panel/configs", json={"name": "second"})
        assert created.status_code == 201
        saved = await panel.client.put(
            "/api/panel/configs/second",
            content="mixed-port: 17891\nallow-lan: true\n",
            headers={"Content-Type": "text/plain; charset=utf-8"},
        )
        assert saved.status_code == 200
        activated = await panel.client.put("/api/panel/configs/second/active")
        assert activated.status_code == 200

        running = await panel.client.get("/api/configs")
        assert running.status_code == 200
        assert running.json()["mixed-port"] == 17891
        version_before = (await panel.client.get("/api/version")).json()

        refused = await panel.client.post("/api/restart")
        assert refused.status_code == 403
        assert refused.json()["detail"] == f"{REFUSAL}: POST /api/restart"

        # Nothing was forwarded: the Kernel still runs the Active Config the
        # panel reports (a forwarded restart would have reloaded the boot
        # Config), its version is unchanged and its listener port is the one
        # the Active Config sets.
        assert (await panel.client.get("/api/configs")).json()["mixed-port"] == 17891
        assert (await panel.client.get("/api/version")).json() == version_before
        health = await panel.client.get("/api/service/health")
        assert health.status_code == 200
        assert health.json()["active_config"] == "second"
        assert health.json()["kernel"] == "up"


async def test_proxy_refuses_post_upgrade_and_leaves_the_runtime_update_state_alone(
    boot_app, tmp_path, mihomo_bin
) -> None:
    """The Controller's upgrade replaces the Kernel binary in place and
    restarts it — a Runtime Update is the only way to install a Kernel."""
    # A copy of the Kernel binary: an upgrade that reached the Controller
    # would replace the binary in place, and the shared one must not be
    # touched by a test.
    kernel_copy = tmp_path / "mihomo"
    kernel_copy.write_bytes(Path(mihomo_bin).read_bytes())
    kernel_copy.chmod(0o755)

    async with boot_app(kernel_bin=str(kernel_copy)) as panel:
        updates_before = (await panel.client.get("/api/panel/updates")).json()
        version_before = (await panel.client.get("/api/version")).json()

        refused = await panel.client.post("/api/upgrade")
        assert refused.status_code == 403
        assert refused.json()["detail"] == f"{REFUSAL}: POST /api/upgrade"

        # Nothing was forwarded: the Kernel version and the Runtime Update
        # bookkeeping are the ones from before the refused request.
        assert (await panel.client.get("/api/version")).json() == version_before
        assert (await panel.client.get("/api/panel/updates")).json() == updates_before


async def test_proxy_refuses_put_configs_and_leaves_the_controller_config_alone(
    boot_app, tmp_path
) -> None:
    """The Controller's forced reload takes the Config path in the body: a
    browser-side write skips the validate → atomic swap → hot-apply pipeline
    the Management Panel owns."""
    async with boot_app() as panel:
        # The Config the Controller would load if the write were forwarded.
        other = tmp_path / "other.yaml"
        other.write_text("mixed-port: 17892\nallow-lan: true\n")

        running_before = (await panel.client.get("/api/configs")).json()
        stored_before = (await panel.client.get("/api/panel/configs/default")).json()

        refused = await panel.client.put(
            "/api/configs", params={"force": "true"}, json={"path": str(other)}
        )
        assert refused.status_code == 403
        assert refused.json()["detail"] == f"{REFUSAL}: PUT /api/configs"

        # No Controller write happened: the running Config and the stored
        # Config are the ones from before the refused request.
        assert (await panel.client.get("/api/configs")).json() == running_before
        assert (await panel.client.get("/api/panel/configs/default")).json() == stored_before
        health = await panel.client.get("/api/service/health")
        assert health.json()["active_config"] == "default"


async def test_websocket_route_refuses_the_denied_paths(boot_app) -> None:
    """The WebSocket route applies the same check before it attempts the
    upstream handshake: the close code is the policy refusal (1008), not the
    internal-error close (1011) an attempted handshake ends in."""
    async with boot_app() as panel:
        client = TestClient(panel.app)
        login = client.post("/api/panel/auth/login", json={"password": TEST_PANEL_PASSWORD})
        assert login.status_code == 200

        for path in ("restart", "upgrade", "configs"):
            with (
                pytest.raises(WebSocketDisconnect) as exc_info,
                client.websocket_connect(f"/api/{path}"),
            ):
                pass
            assert exc_info.value.code == 1008
            assert exc_info.value.reason == f"{REFUSAL}: /api/{path}"


async def test_proxy_still_forwards_the_controller_endpoints_the_runtime_dashboards_drive(
    boot_app,
) -> None:
    """The refusal is narrow: mode switching, the version, the proxy list and
    the connection list keep reaching the Controller."""
    async with boot_app() as panel:
        version = await panel.client.get("/api/version")
        assert version.status_code == 200
        assert version.json()["meta"] is True

        proxies = await panel.client.get("/api/proxies")
        assert proxies.status_code == 200
        assert "DIRECT" in proxies.json()["proxies"]

        connections = await panel.client.get("/api/connections")
        assert connections.status_code == 200
        assert "connections" in connections.json()

        current = await panel.client.get("/api/configs")
        assert current.status_code == 200
        assert "mixed-port" in current.json()

        # Mode switching is a PATCH on the Controller's Config path.
        switched = await panel.client.patch("/api/configs", json={"mode": "global"})
        assert switched.status_code == 204
        assert (await panel.client.get("/api/configs")).json()["mode"] == "global"
        restored = await panel.client.patch("/api/configs", json={"mode": "rule"})
        assert restored.status_code == 204
        assert (await panel.client.get("/api/configs")).json()["mode"] == "rule"
