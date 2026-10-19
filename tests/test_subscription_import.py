"""Ticket #12: Subscription import and manual refresh.

Assertions stay at the Management Panel HTTP API seam (`/api/panel/subscriptions*`
and the config list); the pinned Kernel is a real fixture and the Subscription
provider is a local HTTP origin serving YAML, so runs are deterministic and
offline. Each failure class of the fetch pipeline (network error, invalid YAML,
Kernel validation error) has its own seam test.
"""

import contextlib
import socket

from conftest import origin_server

from clashflow.apply import HotApplyError

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
        with origin_server(SUB_V1) as origin:
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


async def test_manual_refresh_replaces_only_the_subscription_derived_config_and_keeps_every_other_config_byte_identical(
    boot_app,
) -> None:
    async with boot_app() as panel:
        with origin_server(SUB_V1) as origin:
            assert (await _add_subscription(panel, "prov", origin.url)).status_code == 201
            # A second Subscription-derived Config and a Hand-written Config
            # to witness byte-identity.
            with origin_server(b"mixed-port: 7895\nallow-lan: true\n") as other:
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


async def test_refreshing_the_active_subscription_derived_config_hot_applies_without_restart(
    boot_app,
) -> None:
    async with boot_app() as panel:
        with origin_server(SUB_V1) as origin:
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


async def test_hot_apply_failure_restores_the_previous_subscription_derived_config_and_marks_error(
    boot_app, monkeypatch
) -> None:
    # Failure class: Controller refusal on the forced hot-apply of the
    # Active Subscription-derived Config. atomic_save has already swapped in
    # the new bytes when the refusal lands, so the pipeline must restore the
    # PREVIOUS bytes (no hot-apply — the Kernel never left the old Config)
    # and keep the failure marking: the old Config stays in place end to end.
    async with boot_app() as panel:
        with origin_server(SUB_V1) as origin:
            assert (await _add_subscription(panel, "prov", origin.url)).status_code == 201
            resp = await panel.client.put("/api/panel/configs/prov/active")
            assert resp.status_code == 200
            before = (panel.data_dir / "configs" / "prov.yaml").read_bytes()

            async def forced_hot_apply_failure(*args, **kwargs):
                raise HotApplyError("forced hot-apply failure")

            monkeypatch.setattr("clashflow.refresh.hot_apply", forced_hot_apply_failure)
            origin.state["body"] = SUB_V2
            resp = await panel.client.post("/api/panel/subscriptions/prov/refresh")
            assert resp.status_code == 502
            assert resp.json()["detail"] == "forced hot-apply failure"

        # The previous Config stays byte-identical despite the validated
        # swap having happened in between...
        assert (panel.data_dir / "configs" / "prov.yaml").read_bytes() == before
        assert list((panel.data_dir / "configs").glob("*.tmp")) == []
        # ...and the Subscriptions view carries the failure state and reason.
        entry = (await panel.client.get("/api/panel/subscriptions")).json()[0]
        assert entry["state"] == "error"
        assert entry["last_error"] == "forced hot-apply failure"


async def test_failed_refresh_keeps_the_previous_config_and_shows_the_failure_reason(
    boot_app,
) -> None:
    # Failure class: network error (the provider's listener goes away).
    async with boot_app() as panel:
        with origin_server(SUB_V1) as origin:
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


async def test_refresh_with_a_non_2xx_response_is_a_fetch_failure_and_keeps_the_previous_config(
    boot_app,
) -> None:
    # Failure class: the provider answers with a non-2xx status (403/404/410
    # are the everyday provider failures) — a fetch failure naming the HTTP
    # status, the previous Config byte-identical, the reason marked.
    async with boot_app() as panel:
        with origin_server(SUB_V1) as origin:
            assert (await _add_subscription(panel, "prov", origin.url)).status_code == 201
            before = (panel.data_dir / "configs" / "prov.yaml").read_bytes()
            refresh_at_before = (await panel.client.get("/api/panel/subscriptions")).json()[0][
                "last_refresh_at"
            ]

            origin.state["status"] = 403
            resp = await panel.client.post("/api/panel/subscriptions/prov/refresh")
            assert resp.status_code == 502
            assert "subscription fetch failed: HTTP 403" in resp.json()["detail"]

        assert (panel.data_dir / "configs" / "prov.yaml").read_bytes() == before
        entry = (await panel.client.get("/api/panel/subscriptions")).json()[0]
        assert entry["state"] == "error"
        assert "subscription fetch failed: HTTP 403" in entry["last_error"]
        assert entry["last_refresh_at"] == refresh_at_before


async def test_refresh_with_invalid_yaml_is_rejected_and_marks_the_subscription(
    boot_app,
) -> None:
    # Failure class: invalid YAML (unparseable; the Kernel names the parse error).
    async with boot_app() as panel:
        with origin_server(SUB_V1) as origin:
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
        with origin_server(SUB_V1) as origin:
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

        with origin_server(SUB_INVALID_YAML) as origin:
            resp = await _add_subscription(panel, "yamlbad", origin.url)
        assert resp.status_code == 400
        assert "test failed" in resp.json()["detail"]

        assert (await panel.client.get("/api/panel/subscriptions")).json() == []
        names = {c["name"] for c in (await panel.client.get("/api/panel/configs")).json()}
        assert names == {"default"}
        assert sorted(p.name for p in (panel.data_dir / "configs").glob("*.yaml")) == [
            "default.yaml"
        ]


async def test_adding_a_subscription_with_a_non_http_url_is_refused_before_any_fetch(
    boot_app,
) -> None:
    # Only http(s) URLs are accepted: a non-http scheme or a bare host is
    # refused as a 400 before any fetch (the dead-port witness would surface
    # a fetch attempt as a transport failure instead), and nothing registers.
    async with boot_app() as panel:
        with dead_subscription_url() as dead:
            for url in (dead.replace("http://", "ftp://"), dead.removeprefix("http://")):
                resp = await _add_subscription(panel, "badurl", url)
                assert resp.status_code == 400
                assert "must be http(s)" in resp.json()["detail"]
        assert (await panel.client.get("/api/panel/subscriptions")).json() == []
        names = {c["name"] for c in (await panel.client.get("/api/panel/configs")).json()}
        assert names == {"default"}


async def test_deleting_a_subscription_removes_the_subscription_and_its_config(
    boot_app,
) -> None:
    async with boot_app() as panel:
        with origin_server(SUB_V1) as origin:
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


async def test_deleting_a_subscription_derived_config_through_the_config_api_is_refused(
    boot_app,
) -> None:
    # The pair rule ("a registered Subscription always has exactly one
    # Config") is enforced on the Config API too: a Subscription-derived
    # Config is deleted through its Subscription only — a direct delete
    # would leave the Subscription orphaned.
    async with boot_app() as panel:
        with origin_server(SUB_V1) as origin:
            assert (await _add_subscription(panel, "prov", origin.url)).status_code == 201

        resp = await panel.client.delete("/api/panel/configs/prov")
        assert resp.status_code == 409
        # The refusal leaves the pair fully intact…
        names = {c["name"] for c in (await panel.client.get("/api/panel/configs")).json()}
        assert names == {"default", "prov"}
        assert (panel.data_dir / "configs" / "prov.yaml").read_bytes() == SUB_V1
        entries = (await panel.client.get("/api/panel/subscriptions")).json()
        assert [e["name"] for e in entries] == ["prov"]
        # …and the Subscription path still deletes it.
        assert (await panel.client.delete("/api/panel/subscriptions/prov")).status_code == 200
        names = {c["name"] for c in (await panel.client.get("/api/panel/configs")).json()}
        assert names == {"default"}
