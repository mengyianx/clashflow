"""Ticket #12: Subscription import and manual refresh.

Assertions stay at the Management Panel HTTP API seam (`/api/panel/subscriptions*`
and the config list); the pinned Kernel is a real fixture and the Subscription
provider is a local HTTP origin serving YAML, so runs are deterministic and
offline. Each failure class of the fetch pipeline (network error, invalid YAML,
Kernel validation error) has its own seam test.
"""

import contextlib
import http.server
import socket
import socketserver
import threading
from types import SimpleNamespace
from typing import Any

SUB_V1 = b"mixed-port: 7892\nallow-lan: true\n"
SUB_V2 = b"mixed-port: 7893\nallow-lan: true\n"
# Valid YAML, but the Kernel refuses it: an unknown proxy type (the semantic
# failure class, distinct from the unparseable-YAML class below).
SUB_INVALID_TYPE = (
    b"mixed-port: 7890\n"
    b"proxies:\n"
    b"  - name: broken\n"
    b"    type: not-a-real-type\n"
    b"    server: 127.0.0.1\n"
    b"    port: 1\n"
)
# Unparseable YAML (the invalid-YAML failure class).
SUB_INVALID_YAML = b"proxies: [\n"


@contextlib.contextmanager
def subscription_server(body: bytes, status: int = 200):
    """Local origin standing in for a Subscription provider: the served body
    and status can be changed in place, every request's User-Agent is
    recorded, and `stop()` kills the listener to simulate a network error."""

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
        state=state,
        stop=stop,
    )
    try:
        yield origin
    finally:
        stop()


@contextlib.contextmanager
def dead_subscription_url():
    """A Subscription URL whose listener is closed: connecting is refused."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    yield f"http://127.0.0.1:{port}/subscription"


async def _add_subscription(panel, name: str, url: str):
    return await panel.client.post("/api/panel/subscriptions", json={"name": name, "url": url})


async def test_adding_a_subscription_creates_exactly_one_subscription_derived_config(
    boot_app,
) -> None:
    async with boot_app() as panel:
        with subscription_server(SUB_V1) as origin:
            resp = await _add_subscription(panel, "prov", origin.url)
            assert resp.status_code == 201
            entry = resp.json()
            assert entry["name"] == "prov"
            assert entry["url"] == origin.url
            assert entry["config"] == "prov.yaml"
            assert entry["state"] == "ok"
            assert entry["last_refresh_at"] is not None
            assert entry["last_error"] is None

            # Exactly ONE Subscription-derived Config, visible in the Config
            # list; the pre-existing Configs are untouched.
            configs = (await panel.client.get("/api/panel/configs")).json()
            derived = [c for c in configs if c["kind"] == "subscription-derived"]
            assert derived == [
                {
                    "name": "prov",
                    "kind": "subscription-derived",
                    "filename": "prov.yaml",
                    "active": False,
                }
            ]
            assert {c["name"] for c in configs} == {"default", "prov"}

            # The fetched content landed verbatim (file and Editor read side).
            assert (panel.data_dir / "configs" / "prov.yaml").read_bytes() == SUB_V1
            body = (await panel.client.get("/api/panel/configs/prov")).json()
            assert body["content"] == SUB_V1.decode()

            # The fetch carried the custom user agent to the provider.
            assert origin.state["user_agents"] == ["ClashFlow/0.1"]

            # Duplicate names are refused without disturbing the pair.
            dup = await _add_subscription(panel, "prov", origin.url)
            assert dup.status_code == 409
            collision = await _add_subscription(panel, "default", origin.url)
            assert collision.status_code == 409


async def test_manual_refresh_replaces_only_the_subscription_config_and_keeps_every_other_config_byte_identical(
    boot_app,
) -> None:
    async with boot_app() as panel:
        with subscription_server(SUB_V1) as origin:
            assert (await _add_subscription(panel, "prov", origin.url)).status_code == 201
            # A second Subscription-derived Config and a Hand-written Config
            # to witness byte-identity.
            with subscription_server(b"mixed-port: 7895\nallow-lan: true\n") as other:
                assert (await _add_subscription(panel, "other", other.url)).status_code == 201
            assert (
                await panel.client.post("/api/panel/configs", json={"name": "spare"})
            ).status_code == 201
            (panel.data_dir / "configs" / "spare.yaml").write_bytes(
                b"mixed-port: 7894\nallow-lan: true\n"
            )

            configs_dir = panel.data_dir / "configs"
            before = {p.name: p.read_bytes() for p in sorted(configs_dir.glob("*.yaml"))}

            origin.state["body"] = SUB_V2
            resp = await panel.client.post("/api/panel/subscriptions/prov/refresh")
            assert resp.status_code == 200
            entry = resp.json()
            assert entry["state"] == "ok"
            assert entry["last_error"] is None

            after = {p.name: p.read_bytes() for p in sorted(configs_dir.glob("*.yaml"))}
            assert set(after) == set(before)
            assert after["prov.yaml"] == SUB_V2
            for name, content in before.items():
                if name != "prov.yaml":
                    assert after[name] == content
            # No temp residue from the swap.
            assert list(configs_dir.glob("*.tmp")) == []


async def test_refreshing_the_active_subscription_config_hot_applies_without_restart(
    boot_app,
) -> None:
    async with boot_app() as panel:
        with subscription_server(SUB_V1) as origin:
            assert (await _add_subscription(panel, "prov", origin.url)).status_code == 201
            resp = await panel.client.put("/api/panel/configs/prov/active")
            assert resp.status_code == 200
            pid_before = panel.app.state.kernel.pid
            secret = panel.app.state.secret

            origin.state["body"] = SUB_V2
            resp = await panel.client.post("/api/panel/subscriptions/prov/refresh")
            assert resp.status_code == 200

        # Same Kernel process, new port loaded through the Controller.
        assert panel.app.state.kernel.pid == pid_before
        configs = await panel.app.state.api_client.get(
            "http://127.0.0.1:9090/configs",
            headers={"Authorization": f"Bearer {secret}"},
        )
        assert configs.status_code == 200
        assert configs.json()["mixed-port"] == 7893


async def test_failed_refresh_keeps_the_previous_config_and_shows_the_failure_reason(
    boot_app,
) -> None:
    # Failure class: network error (the provider's listener goes away).
    async with boot_app() as panel:
        with subscription_server(SUB_V1) as origin:
            assert (await _add_subscription(panel, "prov", origin.url)).status_code == 201
            before = (panel.data_dir / "configs" / "prov.yaml").read_bytes()
            refresh_at_before = (await panel.client.get("/api/panel/subscriptions")).json()[0][
                "last_refresh_at"
            ]

            origin.stop()
            resp = await panel.client.post("/api/panel/subscriptions/prov/refresh")
            assert resp.status_code == 502
            assert "subscription fetch failed" in resp.json()["detail"]

        # The previous Config stays byte-identical...
        assert (panel.data_dir / "configs" / "prov.yaml").read_bytes() == before
        # ...and the Subscriptions view carries the state, the failure reason,
        # and the last SUCCESSFUL refresh time.
        entry = (await panel.client.get("/api/panel/subscriptions")).json()[0]
        assert entry["state"] == "error"
        assert "subscription fetch failed" in entry["last_error"]
        assert entry["last_refresh_at"] == refresh_at_before


async def test_refresh_with_invalid_yaml_is_rejected_and_marks_the_subscription(
    boot_app,
) -> None:
    # Failure class: invalid YAML (unparseable; the Kernel names the parse error).
    async with boot_app() as panel:
        with subscription_server(SUB_V1) as origin:
            assert (await _add_subscription(panel, "prov", origin.url)).status_code == 201
            before = (panel.data_dir / "configs" / "prov.yaml").read_bytes()

            origin.state["body"] = SUB_INVALID_YAML
            resp = await panel.client.post("/api/panel/subscriptions/prov/refresh")
            assert resp.status_code == 400
            detail = resp.json()["detail"]
            assert "did not find expected node content" in detail  # the Kernel's own words

        assert (panel.data_dir / "configs" / "prov.yaml").read_bytes() == before
        assert list((panel.data_dir / "configs").glob("*.tmp")) == []
        entry = (await panel.client.get("/api/panel/subscriptions")).json()[0]
        assert entry["state"] == "error"
        assert "did not find expected node content" in entry["last_error"]


async def test_refresh_with_kernel_validation_error_is_rejected_and_marks_the_subscription(
    boot_app,
) -> None:
    # Failure class: validation error (parses fine, but is not a complete
    # mihomo Config; the Kernel refuses it verbatim).
    async with boot_app() as panel:
        with subscription_server(SUB_V1) as origin:
            assert (await _add_subscription(panel, "prov", origin.url)).status_code == 201
            before = (panel.data_dir / "configs" / "prov.yaml").read_bytes()

            origin.state["body"] = SUB_INVALID_TYPE
            resp = await panel.client.post("/api/panel/subscriptions/prov/refresh")
            assert resp.status_code == 400
            detail = resp.json()["detail"]
            assert "unsupport proxy type: not-a-real-type" in detail

        assert (panel.data_dir / "configs" / "prov.yaml").read_bytes() == before
        entry = (await panel.client.get("/api/panel/subscriptions")).json()[0]
        assert entry["state"] == "error"
        assert "unsupport proxy type: not-a-real-type" in entry["last_error"]


async def test_adding_a_subscription_that_fails_fetch_or_validation_creates_nothing(
    boot_app,
) -> None:
    # A Subscription only exists once its Config exists: a failed add leaves
    # no registration, no file, and no Config-list entry behind.
    async with boot_app() as panel:
        with dead_subscription_url() as url:
            resp = await _add_subscription(panel, "netbad", url)
        assert resp.status_code == 502
        assert "subscription fetch failed" in resp.json()["detail"]

        with subscription_server(SUB_INVALID_YAML) as origin:
            resp = await _add_subscription(panel, "yamlbad", origin.url)
        assert resp.status_code == 400
        assert "test failed" in resp.json()["detail"]

        assert (await panel.client.get("/api/panel/subscriptions")).json() == []
        names = {c["name"] for c in (await panel.client.get("/api/panel/configs")).json()}
        assert names == {"default"}
        assert sorted(p.name for p in (panel.data_dir / "configs").glob("*.yaml")) == [
            "default.yaml"
        ]


async def test_deleting_a_subscription_removes_the_subscription_and_its_config(
    boot_app,
) -> None:
    async with boot_app() as panel:
        with subscription_server(SUB_V1) as origin:
            assert (await _add_subscription(panel, "prov", origin.url)).status_code == 201

        resp = await panel.client.delete("/api/panel/subscriptions/prov")
        assert resp.status_code == 200
        assert resp.json() == {"deleted": "prov"}
        assert (await panel.client.get("/api/panel/subscriptions")).json() == []
        names = {c["name"] for c in (await panel.client.get("/api/panel/configs")).json()}
        assert names == {"default"}
        assert not (panel.data_dir / "configs" / "prov.yaml").exists()

        resp = await panel.client.delete("/api/panel/subscriptions/ghost")
        assert resp.status_code == 404
