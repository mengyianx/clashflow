"""Ticket #9: Config registry — create, list, delete, Active Config at boot.

All assertions stay at the Management Panel HTTP API seam; what the Kernel
actually loads is observed through the pinned Controller and real proxying.
"""

import httpx
from conftest import origin_server

EMPTY_CONFIG_BODY = "{}\n"


async def _mixed_port(client: httpx.AsyncClient, secret: str) -> int:
    resp = await client.get(
        "http://127.0.0.1:9090/configs", headers={"Authorization": f"Bearer {secret}"}
    )
    assert resp.status_code == 200
    return resp.json()["mixed-port"]


async def test_creating_config_with_duplicate_name_is_refused(boot_app) -> None:
    async with boot_app() as panel:
        resp = await panel.client.post("/api/panel/configs", json={"name": "work"})
        assert resp.status_code == 201
        body = resp.json()
        assert body == {
            "name": "work",
            "kind": "hand-written",
            "filename": "work.yaml",
            "active": False,
        }
        # Empty Config semantics: the registered file is the YAML empty
        # mapping, which the pinned Kernel loads as a defaults-only config
        # (a zero-byte file is refused by the Kernel as "is empty").
        assert (panel.data_dir / "configs" / "work.yaml").read_text() == EMPTY_CONFIG_BODY

        dup = await panel.client.post("/api/panel/configs", json={"name": "work"})
        assert dup.status_code == 409
        assert "already registered" in dup.json()["detail"]

        # Names are validated so the server-side filename derivation can
        # never escape /data/configs.
        for bad in ("", " padded", "../escape"):
            resp = await panel.client.post("/api/panel/configs", json={"name": bad})
            assert resp.status_code == 400


async def test_config_list_shows_every_config_and_marks_the_active_one(boot_app) -> None:
    async with boot_app() as panel:
        for name in ("a", "b"):
            resp = await panel.client.post("/api/panel/configs", json={"name": name})
            assert resp.status_code == 201
        resp = await panel.client.put("/api/panel/configs/a/active")
        assert resp.status_code == 200
        assert resp.json() == {
            "name": "a",
            "kind": "hand-written",
            "filename": "a.yaml",
            "active": True,
        }

        configs = (await panel.client.get("/api/panel/configs")).json()
        by_name = {c["name"]: c for c in configs}
        assert set(by_name) == {"default", "a", "b"}
        assert by_name["default"]["kind"] == "hand-written"
        assert by_name["default"]["active"] is False
        assert by_name["a"]["active"] is True
        assert by_name["b"] == {
            "name": "b",
            "kind": "hand-written",
            "filename": "b.yaml",
            "active": False,
        }

        # An unregistered name cannot become Active.
        resp = await panel.client.put("/api/panel/configs/ghost/active")
        assert resp.status_code == 404
        assert "no such config" in resp.json()["detail"]


async def test_setting_active_and_restarting_runs_that_config(boot_app) -> None:
    with origin_server(b"clashflow-alt-config-ok") as origin:
        async with boot_app() as panel:
            secret = panel.app.state.secret
            resp = await panel.client.post("/api/panel/configs", json={"name": "alt"})
            assert resp.status_code == 201
            # Arrange distinct content for the registered file (content
            # editing is ticket #11; the registry hands out the filename).
            (panel.data_dir / "configs" / "alt.yaml").write_text(
                "mixed-port: 7891\nallow-lan: true\n"
            )
            resp = await panel.client.put("/api/panel/configs/alt/active")
            assert resp.status_code == 200

            resp = await panel.client.post("/api/service/restart")
            assert resp.status_code == 200
            body = resp.json()
            assert body["kernel"] == "up"
            assert body["active_config"] == "alt"

            # Observable through status: the Active Config is alt.
            health = (await panel.client.get("/api/service/health")).json()
            assert health["active_config"] == "alt"

            # Observable through the Kernel API: alt's settings are loaded.
            async with httpx.AsyncClient() as kc:
                assert await _mixed_port(kc, secret) == 7891

            # Observable through proxy behavior: traffic flows on alt's port…
            async with httpx.AsyncClient(proxy="http://127.0.0.1:7891") as proxied:
                via_alt = await proxied.get(f"http://127.0.0.1:{origin.port}/")
            assert via_alt.status_code == 200
            assert via_alt.content == b"clashflow-alt-config-ok"
            # …and the default Config's port is gone.
            try:
                async with httpx.AsyncClient(proxy="http://127.0.0.1:7890") as proxied:
                    await proxied.get(f"http://127.0.0.1:{origin.port}/")
                raise AssertionError("default Config port 7890 still proxying")
            except httpx.ConnectError:
                pass


async def test_deleting_the_active_config_is_refused(boot_app) -> None:
    async with boot_app() as panel:
        # Boot left "default" Active.
        resp = await panel.client.delete("/api/panel/configs/default")
        assert resp.status_code == 409
        assert "active" in resp.json()["detail"]

        # Activating another Config releases it for deletion.
        resp = await panel.client.post("/api/panel/configs", json={"name": "spare"})
        assert resp.status_code == 201
        assert (await panel.client.put("/api/panel/configs/spare/active")).status_code == 200
        resp = await panel.client.delete("/api/panel/configs/default")
        assert resp.status_code == 200
        assert not (panel.data_dir / "configs" / "default.yaml").exists()
        names = [c["name"] for c in (await panel.client.get("/api/panel/configs")).json()]
        assert "default" not in names

        resp = await panel.client.delete("/api/panel/configs/ghost")
        assert resp.status_code == 404


async def test_boot_honors_preexisting_active_config_choice(boot_app, tmp_path) -> None:
    data = tmp_path / "data"
    async with boot_app(data_dir=data) as panel:
        resp = await panel.client.post("/api/panel/configs", json={"name": "night"})
        assert resp.status_code == 201
        (data / "configs" / "night.yaml").write_text("mixed-port: 7891\nallow-lan: true\n")
        assert (await panel.client.put("/api/panel/configs/night/active")).status_code == 200

    # Fresh panel process over the same /data: the recorded Active choice is
    # honored when the Kernel starts.
    async with boot_app(data_dir=data) as panel:
        health = (await panel.client.get("/api/service/health")).json()
        assert health["kernel"] == "up"
        assert health["active_config"] == "night"
        async with httpx.AsyncClient() as kc:
            assert await _mixed_port(kc, panel.app.state.secret) == 7891
