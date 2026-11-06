"""Issue #35: the global Overlay, the key-merge stage, and the Runtime Config
the Kernel actually runs.

Every assertion is external — the panel HTTP API, bytes on disk, and the real
Kernel observed through its Controller and `/proc` — because the contract is
"the file the Kernel runs is the rendered one", not "a helper was called".
"""

from pathlib import Path

import httpx
import yaml

from clashflow.render import render

CONTROLLER = "http://127.0.0.1:9090"


def _auth(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


async def _controller_configs(secret: str) -> dict:
    async with httpx.AsyncClient() as kernel_client:
        resp = await kernel_client.get(f"{CONTROLLER}/configs", headers=_auth(secret))
        assert resp.status_code == 200
        return resp.json()


async def _put_overlay(panel, values: dict) -> httpx.Response:
    return await panel.client.put("/api/panel/overlay", json={"values": values})


async def test_overlay_is_one_global_record_and_survives_a_restart(boot_app, tmp_path) -> None:
    data = tmp_path / "data"
    values = {
        "log-level": "debug",
        "mixed-port": 7891,
        "dns": {"nameserver": ["1.1.1.1", "8.8.8.8"], "fallback": ["9.9.9.9"]},
    }
    async with boot_app(data_dir=data) as panel:
        resp = await _put_overlay(panel, values)
        assert resp.status_code == 200, resp.text
        assert resp.json()["values"] == values
        assert "log-level" in resp.json()["editable"]

    # A restart reloads the same record: the Overlay is global and persisted,
    # not a per-Config value held in memory.
    async with boot_app(data_dir=data) as panel:
        body = (await panel.client.get("/api/panel/overlay")).json()
        assert body["values"] == values


async def test_changing_the_log_level_rerenders_and_hot_applies_in_the_same_kernel(
    boot_app,
) -> None:
    async with boot_app() as panel:
        secret = panel.app.state.secret
        pid_before = panel.app.state.kernel.pid
        source = panel.data_dir / "configs" / "default.yaml"
        runtime = panel.data_dir / "runtime-configs" / "default.yaml"
        source_before = source.read_bytes()

        resp = await _put_overlay(panel, {"log-level": "debug"})
        assert resp.status_code == 200, resp.text

        # Same Kernel process — a hot apply, not a restart...
        assert panel.app.state.kernel.pid == pid_before
        # ...the Kernel reports the new level...
        assert (await _controller_configs(secret))["log-level"] == "debug"
        # ...the Runtime Config carries it, and the Source Config is untouched.
        assert yaml.safe_load(runtime.read_bytes())["log-level"] == "debug"
        assert source.read_bytes() == source_before


async def test_runtime_config_holds_the_overlay_values_and_source_stays_byte_identical(
    boot_app,
) -> None:
    async with boot_app() as panel:
        source = panel.data_dir / "configs" / "default.yaml"
        runtime = panel.data_dir / "runtime-configs" / "default.yaml"
        source_before = source.read_bytes()

        values = {
            "mixed-port": 7891,
            "allow-lan": False,
            "bind-address": "127.0.0.1",
            "dns": {"nameserver": ["1.1.1.1"], "fallback": ["8.8.8.8"]},
        }
        resp = await _put_overlay(panel, values)
        assert resp.status_code == 200, resp.text

        rendered = yaml.safe_load(runtime.read_bytes())
        assert rendered["mixed-port"] == 7891
        assert rendered["allow-lan"] is False
        assert rendered["bind-address"] == "127.0.0.1"
        assert rendered["dns"]["nameserver"] == ["1.1.1.1"]
        assert rendered["dns"]["fallback"] == ["8.8.8.8"]
        # Untouched source keys pass through.
        assert rendered["geo-auto-update"] is True

        # The source is user content: byte-identical, still its own YAML.
        assert source.read_bytes() == source_before
        assert (await _controller_configs(panel.app.state.secret))["mixed-port"] == 7891


async def test_diff_lists_only_the_differing_keys_and_editor_marks_overridden(boot_app) -> None:
    async with boot_app() as panel:
        await _put_overlay(panel, {"mixed-port": 7891, "log-level": "debug"})

        diff = (await panel.client.get("/api/panel/configs/default/runtime/diff")).json()
        paths = [row["path"] for row in diff["keys"]]
        # Exactly the keys the Overlay changed: the replaced port and the
        # added level. The source's other keys are not rows.
        assert paths == ["mixed-port", "log-level"]
        by_path = {row["path"]: row for row in diff["keys"]}
        assert by_path["mixed-port"]["source"] == 7890
        assert by_path["mixed-port"]["runtime"] == 7891
        assert by_path["log-level"]["in_source"] is False
        assert by_path["log-level"]["runtime"] == "debug"

        runtime = (await panel.client.get("/api/panel/configs/default/runtime")).json()
        assert runtime["current"] is True
        assert yaml.safe_load(runtime["content"])["log-level"] == "debug"

        editor = (await panel.client.get("/api/panel/configs/default")).json()
        # The editor marks the source key the Overlay wins; a Runtime-only key
        # cannot be marked because the editor has no such line.
        assert editor["overridden_keys"] == ["mixed-port"]
        assert "log-level" not in editor["content"]


async def test_runtime_config_is_read_only(boot_app) -> None:
    async with boot_app() as panel:
        runtime = panel.data_dir / "runtime-configs" / "default.yaml"
        before = runtime.read_bytes()

        # Every write method is refused on the runtime surface: there is no
        # second source of truth to edit.
        url = "/api/panel/configs/default/runtime"
        for method in ("put", "post", "patch"):
            resp = await getattr(panel.client, method)(url, content="mixed-port: 7899\n")
            assert resp.status_code == 405, resp.text
        assert (await panel.client.delete(url)).status_code == 405
        assert runtime.read_bytes() == before


async def test_deleting_the_runtime_config_and_rebooting_rebuilds_it_identically(
    boot_app, tmp_path
) -> None:
    data = tmp_path / "data"
    async with boot_app(data_dir=data) as panel:
        await _put_overlay(panel, {"log-level": "debug", "dns": {"nameserver": ["1.1.1.1"]}})
        runtime = data / "runtime-configs" / "default.yaml"
        built = runtime.read_bytes()
        pid = panel.app.state.kernel.pid
        # The Kernel is launched with `-f` pointing at the Runtime Config, not
        # at the Source Config.
        args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        assert str(runtime).encode() in args

        runtime.unlink()

    async with boot_app(data_dir=data) as panel:
        assert runtime.is_file()
        # Rebuilt from the same Source + Overlay: a byte-identical artifact.
        assert runtime.read_bytes() == built
        assert panel.app.state.kernel.pid != pid


async def test_exporting_the_overlay_and_importing_it_back_restores_the_same_state(
    boot_app,
) -> None:
    async with boot_app() as panel:
        values = {
            "log-level": "warning",
            "mixed-port": 7892,
            "bind-address": "*",
            "dns": {"nameserver": ["https://dns.example/dns-query"]},
        }
        assert (await _put_overlay(panel, values)).status_code == 200

        exported = await panel.client.get("/api/panel/overlay/export")
        assert exported.status_code == 200
        assert exported.headers["content-type"].startswith("application/yaml")
        text = exported.text
        assert yaml.safe_load(text) == values

        # Replace with nothing, then import the export: the same state.
        assert (await _put_overlay(panel, {})).json()["values"] == {}
        imported = await panel.client.post(
            "/api/panel/overlay/import",
            content=text,
            headers={"Content-Type": "application/yaml"},
        )
        assert imported.status_code == 200, imported.text
        assert imported.json()["values"] == values

        # An unknown key is refused instead of silently stopping the override.
        bad = await panel.client.post(
            "/api/panel/overlay/import",
            content="log-leevl: debug\n",
            headers={"Content-Type": "application/yaml"},
        )
        assert bad.status_code == 400
        assert bad.json()["detail"].startswith("overlay: ")
        assert "not an overlay key" in bad.json()["detail"]

        # The schema can express the TUN block (the renderer is ready for
        # 2.0 M2), but M1 has no safe apply path for a data-plane change: the
        # API refuses it rather than hot-applying a mode switch (ADR-0010).
        tun = await panel.client.post(
            "/api/panel/overlay/import",
            content="tun:\n  enable: true\n",
            headers={"Content-Type": "application/yaml"},
        )
        assert tun.status_code == 400
        assert "tun.enable: not editable in this release" in tun.json()["detail"]


async def test_switching_the_active_config_renders_the_target_and_hot_applies_it(
    boot_app,
) -> None:
    async with boot_app() as panel:
        secret = panel.app.state.secret
        pid_before = panel.app.state.kernel.pid
        await _put_overlay(panel, {"log-level": "debug"})
        assert (
            await panel.client.post("/api/panel/configs", json={"name": "second", "template": True})
        ).status_code == 201
        (panel.data_dir / "configs" / "second.yaml").write_text(
            "mixed-port: 7893\nallow-lan: false\n"
        )

        resp = await panel.client.put("/api/panel/configs/second/active")
        assert resp.status_code == 200, resp.text

        runtime = panel.data_dir / "runtime-configs" / "second.yaml"
        rendered = yaml.safe_load(runtime.read_bytes())
        assert rendered["mixed-port"] == 7893
        assert rendered["log-level"] == "debug"
        assert panel.app.state.kernel.pid == pid_before
        configs = await _controller_configs(secret)
        assert configs["mixed-port"] == 7893
        assert configs["log-level"] == "debug"


def test_rendering_is_deterministic_for_the_same_source_and_overlay() -> None:
    source = b"mixed-port: 7890\nrules:\n  - MATCH,DIRECT\n"
    overlay = {"log-level": "debug", "dns": {"nameserver": ["1.1.1.1"]}}
    first = render(source, overlay)
    assert render(source, overlay) == first
    # Source key order is kept and Overlay-only keys follow it.
    lines = first.decode().splitlines()
    assert lines[0] == "mixed-port: 7890"
    assert lines[1] == "rules:"
    assert "log-level: debug" in lines


def test_overlay_schema_accepts_the_full_decided_key_set_and_canonicalizes() -> None:
    # M1 has no controls for the TUN block, DNS mode or the DNS listen keys,
    # but the renderer must be able to express them (ADR-0004); validation is
    # total and the canonical order is the schema's.
    from clashflow.overlay import OverlayError, validate

    values = {
        "dns": {
            "nameserver": ["1.1.1.1"],
            "enhanced-mode": "fake-ip",
            "listen": ":53",
            "enable": True,
        },
        "tun": {"enable": True, "stack": "mixed", "auto-route": True, "dns-hijack": ["any:53"]},
        "log-level": "debug",
    }
    canonical = validate(values)
    assert canonical["log-level"] == "debug"
    assert canonical["tun"]["stack"] == "mixed"
    assert canonical["dns"]["enhanced-mode"] == "fake-ip"
    assert canonical["dns"]["enable"] is True
    # Schema order, not request order.
    assert list(canonical) == ["log-level", "tun", "dns"]
    # An empty nested mapping owns nothing and collapses to absence.
    assert validate({"dns": {}}) == {}

    import pytest

    with pytest.raises(OverlayError, match="not an overlay key"):
        validate({"log-leevl": "debug"})
    with pytest.raises(OverlayError, match="mixed-port"):
        validate({"mixed-port": 70000})
    with pytest.raises(OverlayError, match="allow-lan"):
        validate({"allow-lan": "yes"})
