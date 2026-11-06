"""Ticket #34: every Config write path shares one apply pipeline.

Assertions stay at the Management Panel HTTP API seam (`/api/panel/configs*`,
`/api/panel/subscriptions*`) with the pinned Kernel as a real fixture. The
invariant this ticket introduces: whichever path writes a Config, a Controller
refusal has the same outcome — 502 carrying the Controller's own text, the
Config file byte-identical, and the Kernel still serving what it served
before.
"""

import httpx
from conftest import PROBE_PROXY_BLOCK, origin_server

from clashflow import apply as apply_module
from clashflow.apply import HotApplyError
from clashflow.kernel import CONTROLLER_ADDR

SUB_V1 = PROBE_PROXY_BLOCK.encode() + b"mixed-port: 7892\nallow-lan: true\n"


async def _served_configs(secret: str) -> dict:
    """What the running Kernel actually serves, read from the Controller."""
    async with httpx.AsyncClient() as controller:
        resp = await controller.get(
            f"http://{CONTROLLER_ADDR}/configs",
            headers={"Authorization": f"Bearer {secret}"},
        )
        assert resp.status_code == 200
        return resp.json()


async def test_every_write_path_answers_a_refused_reload_the_same_way(
    boot_app, monkeypatch
) -> None:
    async def refuse(*args, **kwargs):
        raise HotApplyError("forced hot-apply failure")

    real_hot_apply = apply_module.hot_apply

    async with boot_app() as panel:
        target = panel.data_dir / "configs" / "default.yaml"
        before = target.read_bytes()

        # Save: the candidate is swapped in, the reload is refused, so the
        # previous bytes come back — otherwise the next Kernel start would
        # boot bytes the Controller refused.
        monkeypatch.setattr("clashflow.apply.hot_apply", refuse)
        resp = await panel.client.put(
            "/api/panel/configs/default",
            content=PROBE_PROXY_BLOCK + "mixed-port: 7891\nallow-lan: true\n",
        )
        assert (resp.status_code, resp.json()["detail"]) == (502, "forced hot-apply failure")
        assert target.read_bytes() == before

        # Switch: nothing is written, so a refusal leaves the target exactly
        # as it was and the previous Active stays recorded.
        alt = panel.data_dir / "configs" / "alt.yaml"
        assert (
            await panel.client.post("/api/panel/configs", json={"name": "alt"})
        ).status_code == 201
        resp = await panel.client.put(
            "/api/panel/configs/alt",
            content=PROBE_PROXY_BLOCK + "mixed-port: 7893\nallow-lan: true\n",
        )
        assert resp.status_code == 200
        alt_before = alt.read_bytes()
        resp = await panel.client.put("/api/panel/configs/alt/active")
        assert (resp.status_code, resp.json()["detail"]) == (502, "forced hot-apply failure")
        assert alt.read_bytes() == alt_before
        health = (await panel.client.get("/api/service/health")).json()
        assert health["active_config"] == "default"
        assert (await _served_configs(panel.app.state.secret))["mixed-port"] == 7890
        assert (await _served_configs(panel.app.state.secret))["mixed-port"] == 7890

        # Refresh: same restore rule, reached through the Subscription path.
        monkeypatch.setattr("clashflow.apply.hot_apply", real_hot_apply)
        with origin_server(SUB_V1) as origin:
            added = await panel.client.post(
                "/api/panel/subscriptions", json={"name": "prov", "url": origin.url}
            )
            assert added.status_code == 201
            assert (await panel.client.put("/api/panel/configs/prov/active")).status_code == 200
            prov = panel.data_dir / "configs" / "prov.yaml"
            prov_before = prov.read_bytes()

            monkeypatch.setattr("clashflow.apply.hot_apply", refuse)
            resp = await panel.client.post("/api/panel/subscriptions/prov/refresh")
            assert (resp.status_code, resp.json()["detail"]) == (502, "forced hot-apply failure")
            assert prov.read_bytes() == prov_before
            assert (await _served_configs(panel.app.state.secret))["mixed-port"] == 7892


async def test_a_kernel_that_dies_during_validation_stores_the_bytes_without_a_reload(
    boot_app, monkeypatch
) -> None:
    # The liveness check belongs to the moment of the reload, not to the moment
    # the request arrived: validation can take seconds (mihomo -t may download
    # GEO data), and a Kernel that died inside that window must not be asked to
    # reload a Config it can no longer serve. The bytes are stored and the next
    # start picks them up.
    async with boot_app() as panel:
        kernel = panel.app.state.kernel
        target = panel.data_dir / "configs" / "default.yaml"
        real_validate = apply_module.validate_config

        async def validate_then_die(*args, **kwargs):
            await real_validate(*args, **kwargs)
            # The Kernel goes away inside the validation window. `stop()` is the
            # public way to make it not-alive, and it parks the supervisor, so
            # this test observes a dead Kernel instead of racing a respawn.
            kernel.stop()

        async def must_not_reload(*args, **kwargs):
            raise AssertionError("a dead Kernel must not be asked to reload")

        monkeypatch.setattr(apply_module, "validate_config", validate_then_die)
        monkeypatch.setattr("clashflow.apply.hot_apply", must_not_reload)

        resp = await panel.client.put(
            "/api/panel/configs/default",
            content=PROBE_PROXY_BLOCK + "mixed-port: 7891\nallow-lan: true\n",
        )
        assert resp.status_code == 200
        assert (
            target.read_bytes()
            == PROBE_PROXY_BLOCK.encode() + b"mixed-port: 7891\nallow-lan: true\n"
        )
