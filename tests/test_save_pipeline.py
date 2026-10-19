"""Ticket #10: save pipeline — validate, atomic swap, hot-apply, verbatim errors.

Assertions stay at the Management Panel HTTP API seam (including the /api/*
Controller reverse proxy); the pinned Kernel is a real fixture observed
through its Controller API and through real proxy traffic.
"""

import asyncio
import re
import subprocess

import httpx
from conftest import origin_server

CONTROLLER = "http://127.0.0.1:9090"


def _auth(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


async def _controller_configs(secret: str) -> dict:
    async with httpx.AsyncClient() as kc:
        resp = await kc.get(f"{CONTROLLER}/configs", headers=_auth(secret))
        assert resp.status_code == 200
        return resp.json()


async def test_saving_valid_edit_to_active_config_hot_applies_without_restart(boot_app) -> None:
    async with boot_app() as panel:
        secret = panel.app.state.secret
        pid_before = panel.app.state.kernel.pid

        content = "mixed-port: 7891\nallow-lan: true\n"
        resp = await panel.client.put(
            "/api/panel/configs/default",
            content=content,
            headers={"Content-Type": "application/yaml"},
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "name": "default",
            "kind": "hand-written",
            "filename": "default.yaml",
            "active": True,
        }

        # The save hot-applied instead of restarting: same Kernel process...
        assert panel.app.state.kernel.pid == pid_before
        # ...now serving the new port through the Controller.
        configs = await _controller_configs(secret)
        assert configs["mixed-port"] == 7891

        # Observable through proxy behavior: traffic flows on the new port...
        with origin_server(b"clashflow-save-ok") as origin:
            async with httpx.AsyncClient(proxy="http://127.0.0.1:7891") as proxied:
                via_new = await proxied.get(f"http://127.0.0.1:{origin.port}/")
            assert via_new.status_code == 200
            assert via_new.content == b"clashflow-save-ok"
            # ...and the old port is gone.
            try:
                async with httpx.AsyncClient(proxy="http://127.0.0.1:7890") as proxied:
                    await proxied.get(f"http://127.0.0.1:{origin.port}/")
                raise AssertionError("old port 7890 still proxying")
            except httpx.ConnectError:
                pass

        # The stored file carries the submitted bytes verbatim, no .tmp left.
        assert (panel.data_dir / "configs" / "default.yaml").read_bytes() == content.encode()
        assert list((panel.data_dir / "configs").glob("*.tmp")) == []


async def test_editor_can_load_the_raw_yaml_content(boot_app) -> None:
    async with boot_app() as panel:
        resp = await panel.client.get("/api/panel/configs/default")
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "default"
        assert body["active"] is True
        # The editor receives exactly the bytes on disk (the disk file is the
        # source of truth; the panel must not rewrite them on the way out).
        on_disk = (panel.data_dir / "configs" / "default.yaml").read_bytes()
        assert body["content"] == on_disk.decode()

        resp = await panel.client.get("/api/panel/configs/ghost")
        assert resp.status_code == 404
        assert "no such config" in resp.json()["detail"]


async def test_saving_invalid_yaml_returns_kernel_error_verbatim_and_keeps_file(
    boot_app, mihomo_bin
) -> None:
    async with boot_app() as panel:
        target = panel.data_dir / "configs" / "default.yaml"
        before = target.read_bytes()
        content = "mixed-port: 7890\nproxies: [\n"
        tmp = panel.data_dir / "configs" / "default.yaml.tmp"

        # Independent reproduction at the exact candidate path the panel
        # validates: the Kernel's own output, to be compared verbatim
        # (only the wall-clock log timestamp may differ between runs).
        tmp.write_text(content)
        proc = await asyncio.to_thread(
            subprocess.run,
            [mihomo_bin, "-d", str(panel.data_dir), "-t", "-f", str(tmp)],
            capture_output=True,
            check=False,
        )
        tmp.unlink()
        assert proc.returncode == 1  # exit 1 = invalid (pinned Kernel)

        resp = await panel.client.put("/api/panel/configs/default", content=content)
        assert resp.status_code == 400
        detail = resp.json()["detail"]

        def without_timestamp(text: str) -> str:
            return re.sub(r'time="[^"]*"', "time=<ts>", text)

        expected = (proc.stdout + proc.stderr).decode()
        assert without_timestamp(detail) == without_timestamp(expected)
        # The banner names the validated candidate file, verbatim.
        assert f"configuration file {tmp} test failed" in detail

        # The stored Config stayed byte-identical; no temp residue.
        assert target.read_bytes() == before
        assert list(target.parent.glob("*.tmp")) == []


async def test_failed_save_leaves_no_temp_residue(boot_app) -> None:
    async with boot_app() as panel:
        resp = await panel.client.post("/api/panel/configs", json={"name": "spare"})
        assert resp.status_code == 201
        configs_dir = panel.data_dir / "configs"

        # Invalid content for a non-Active Config: refused by the Kernel,
        # nothing written, no temp file survives the failed save.
        resp = await panel.client.put(
            "/api/panel/configs/spare", content="mixed-port: 7890\nproxies: [\n"
        )
        assert resp.status_code == 400
        assert "test failed" in resp.json()["detail"]
        assert list(configs_dir.glob("*.tmp")) == []
        assert (configs_dir / "spare.yaml").read_text() == "{}\n"

        # An empty body is refused verbatim as well (the Kernel: "is empty").
        resp = await panel.client.put("/api/panel/configs/spare", content="")
        assert resp.status_code == 400
        assert "is empty" in resp.json()["detail"]
        assert list(configs_dir.glob("*.tmp")) == []
        assert (configs_dir / "spare.yaml").read_text() == "{}\n"


async def test_switching_active_config_hot_applies_and_records_without_restart(boot_app) -> None:
    async with boot_app() as panel:
        secret = panel.app.state.secret
        pid_before = panel.app.state.kernel.pid

        resp = await panel.client.post("/api/panel/configs", json={"name": "alt"})
        assert resp.status_code == 201
        resp = await panel.client.put(
            "/api/panel/configs/alt", content="mixed-port: 7891\nallow-lan: true\n"
        )
        assert resp.status_code == 200
        # Saving a non-Active Config stores it without touching the Kernel.
        assert (await _controller_configs(secret))["mixed-port"] == 7890

        resp = await panel.client.put("/api/panel/configs/alt/active")
        assert resp.status_code == 200
        assert resp.json() == {
            "name": "alt",
            "kind": "hand-written",
            "filename": "alt.yaml",
            "active": True,
        }
        # Recorded AND hot-applied in the same process: no restart happened.
        assert panel.app.state.kernel.pid == pid_before
        assert (await _controller_configs(secret))["mixed-port"] == 7891
        health = (await panel.client.get("/api/service/health")).json()
        assert health["active_config"] == "alt"
        assert health["kernel"] == "up"

        # Runtime behavior followed: the new port serves, the old one is gone.
        with origin_server(b"clashflow-switch-ok") as origin:
            async with httpx.AsyncClient(proxy="http://127.0.0.1:7891") as proxied:
                via_alt = await proxied.get(f"http://127.0.0.1:{origin.port}/")
            assert via_alt.content == b"clashflow-switch-ok"
            try:
                async with httpx.AsyncClient(proxy="http://127.0.0.1:7890") as proxied:
                    await proxied.get(f"http://127.0.0.1:{origin.port}/")
                raise AssertionError("default Config port 7890 still proxying")
            except httpx.ConnectError:
                pass


async def test_failed_switch_keeps_previous_active_recorded_and_running(boot_app) -> None:
    async with boot_app() as panel:
        secret = panel.app.state.secret
        pid_before = panel.app.state.kernel.pid

        resp = await panel.client.post("/api/panel/configs", json={"name": "stale"})
        assert resp.status_code == 201
        # The file turns invalid OUTSIDE the save pipeline (e.g. external
        # corruption): the switch must still refuse it through the Kernel.
        (panel.data_dir / "configs" / "stale.yaml").write_text("mixed-port: 7891\nproxies: [\n")

        resp = await panel.client.put("/api/panel/configs/stale/active")
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "level=error" in detail  # the Kernel's own error line, verbatim
        stale = panel.data_dir / "configs" / "stale.yaml"
        assert f"configuration file {stale} test failed" in detail

        # The previous Active stays recorded, running, and serving.
        configs = (await panel.client.get("/api/panel/configs")).json()
        by_name = {c["name"]: c for c in configs}
        assert by_name["default"]["active"] is True
        assert by_name["stale"]["active"] is False
        health = (await panel.client.get("/api/service/health")).json()
        assert health["active_config"] == "default"
        assert health["kernel"] == "up"
        assert panel.app.state.kernel.pid == pid_before
        assert (await _controller_configs(secret))["mixed-port"] == 7890


GROUPED_CONFIG = """\
mixed-port: 7890
allow-lan: true
profile:
  store-selected: true
proxies:
  - name: n1
    type: http
    server: 127.0.0.1
    port: 1080
  - name: n2
    type: http
    server: 127.0.0.1
    port: 1081
proxy-groups:
  - name: shared
    type: select
    proxies: [n1, n2]
  - name: only-a
    type: select
    proxies: [n1, n2]
"""

SWITCHED_CONFIG = """\
mixed-port: 7890
allow-lan: true
profile:
  store-selected: true
proxies:
  - name: n1
    type: http
    server: 127.0.0.1
    port: 1080
  - name: n2
    type: http
    server: 127.0.0.1
    port: 1081
proxy-groups:
  - name: shared
    type: select
    proxies: [n1, n2]
"""


async def test_node_selections_survive_switch_for_shared_groups_and_drop_for_missing(
    boot_app,
) -> None:
    async with boot_app(config_body=GROUPED_CONFIG) as panel:
        # Pick n2 in both proxy groups through the panel seam (the Runtime
        # Dashboard path); the Kernel persists selections in /data/cache.db
        # keyed by proxy-group name (profile.store-selected).
        for group in ("shared", "only-a"):
            resp = await panel.client.put(f"/api/proxies/{group}", json={"name": "n2"})
            assert resp.status_code == 204
        resp = await panel.client.get("/api/proxies/shared")
        assert resp.json()["now"] == "n2"

        # Prepare and switch to a Config carrying only the "shared" group;
        # its YAML default selection is n1 (first in its list).
        resp = await panel.client.post("/api/panel/configs", json={"name": "b"})
        assert resp.status_code == 201
        resp = await panel.client.put("/api/panel/configs/b", content=SWITCHED_CONFIG)
        assert resp.status_code == 200
        resp = await panel.client.put("/api/panel/configs/b/active")
        assert resp.status_code == 200

        # Shared group: the selection followed the switch — restored from the
        # store-selected cache by group name, not from the YAML default.
        resp = await panel.client.get("/api/proxies/shared")
        assert resp.json()["now"] == "n2"
        # Missing group: its selection is silently lost with the old Config.
        resp = await panel.client.get("/api/proxies/only-a")
        assert resp.status_code == 404


async def test_geo_auto_update_enabled_and_geo_files_persist_across_restart(
    boot_app, tmp_path
) -> None:
    data = tmp_path / "volume"
    # GEO databases as they live in the data volume (downloaded into /data by
    # the Kernel's config-test side effect or by its GEO updater). Fresh
    # mtimes keep the startup updater idle instead of re-downloading.
    (data / "configs").mkdir(parents=True)
    geoip = data / "geoip.metadb"
    geosite = data / "GeoSite.dat"
    asn = data / "ASN.mmdb"
    geoip.write_bytes(b"clashflow-geoip-fixture")
    geosite.write_bytes(b"clashflow-geosite-fixture")
    asn.write_bytes(b"clashflow-asn-fixture")

    async with boot_app(data_dir=data) as panel:
        # The default Config template ships GEO auto-update enabled; the
        # Kernel parsed it at startup — Kernel start is also the only moment
        # its updater ticker can register.
        resp = await panel.client.get("/api/configs")
        assert resp.status_code == 200
        body = resp.json()
        assert body["geo-auto-update"] is True
        assert body["geo-update-interval"] == 24

    # The GEO files and the enabled setting survive a panel recreation:
    # everything lives in the one data volume.
    async with boot_app(data_dir=data) as panel:
        resp = await panel.client.get("/api/configs")
        assert resp.json()["geo-auto-update"] is True
        assert geoip.read_bytes() == b"clashflow-geoip-fixture"
        assert geosite.read_bytes() == b"clashflow-geosite-fixture"
        assert asn.read_bytes() == b"clashflow-asn-fixture"
