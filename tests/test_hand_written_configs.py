"""Ticket #11: Hand-written Configs — template creation and copy-to-edit.

Assertions stay at the Management Panel HTTP API seam; the pinned Kernel is
a real fixture observed through its Controller API and real proxy traffic.
"""

import httpx
from conftest import origin_server

CONTROLLER = "http://127.0.0.1:9090"

# The blank template skeleton, per the implementation plan (§5): a mixed
# proxy port, allow-lan, and the GEO auto-update keys (US21).
TEMPLATE_SKELETON = (
    "mixed-port: 7890\nallow-lan: true\ngeo-auto-update: true\ngeo-update-interval: 24\n"
)


def _auth(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


async def _controller_configs(secret: str) -> dict:
    async with httpx.AsyncClient() as kc:
        resp = await kc.get(f"{CONTROLLER}/configs", headers=_auth(secret))
        assert resp.status_code == 200
        return resp.json()


async def test_template_creation_yields_a_valid_config_that_passes_validation_and_can_be_made_active(
    boot_app,
) -> None:
    async with boot_app() as panel:
        secret = panel.app.state.secret

        resp = await panel.client.post(
            "/api/panel/configs", json={"name": "fresh", "template": True}
        )
        assert resp.status_code == 201
        assert resp.json() == {
            "name": "fresh",
            "kind": "hand-written",
            "filename": "fresh.yaml",
            "active": False,
        }
        # The file carries the plan's blank skeleton, not the empty mapping:
        # the template Config is runnable the moment it becomes Active.
        assert (
            panel.data_dir / "configs" / "fresh.yaml"
        ).read_bytes() == TEMPLATE_SKELETON.encode()

        # The apply pipeline validates the file and makes it Active in the
        # same process (a 400 here would mean the template failed mihomo -t).
        pid_before = panel.app.state.kernel.pid
        resp = await panel.client.put("/api/panel/configs/fresh/active")
        assert resp.status_code == 200
        assert resp.json() == {
            "name": "fresh",
            "kind": "hand-written",
            "filename": "fresh.yaml",
            "active": True,
        }
        assert panel.app.state.kernel.pid == pid_before

        # Observable through status: the template Config is the Active one.
        health = (await panel.client.get("/api/service/health")).json()
        assert health["active_config"] == "fresh"
        assert health["kernel"] == "up"

        # Observable through proxy behavior: the template's mixed port
        # serves traffic (an empty `{}` Config would leave no listener).
        assert (await _controller_configs(secret))["mixed-port"] == 7890
        with origin_server(b"clashflow-template-ok") as origin:
            async with httpx.AsyncClient(proxy="http://127.0.0.1:7890") as proxied:
                via_fresh = await proxied.get(f"http://127.0.0.1:{origin.port}/")
            assert via_fresh.status_code == 200
            assert via_fresh.content == b"clashflow-template-ok"


SOURCE_BODY = """\
mixed-port: 7890
allow-lan: true
proxies:
  - name: n1
    type: http
    server: 127.0.0.1
    port: 1080
"""


async def test_copy_produces_an_independent_config_and_edits_to_one_never_affect_the_other(
    boot_app,
) -> None:
    async with boot_app() as panel:
        # Arrange a distinctive valid source Config through the seam.
        resp = await panel.client.put(
            "/api/panel/configs/default",
            content=SOURCE_BODY,
            headers={"Content-Type": "application/yaml"},
        )
        assert resp.status_code == 200

        resp = await panel.client.post("/api/panel/configs/default/copy", json={"name": "cloned"})
        assert resp.status_code == 201
        assert resp.json() == {
            "name": "cloned",
            "kind": "hand-written",
            "filename": "cloned.yaml",
            "active": False,
        }

        configs_dir = panel.data_dir / "configs"
        # Same content, separate name, separate file.
        assert (configs_dir / "cloned.yaml").read_bytes() == SOURCE_BODY.encode()
        assert (configs_dir / "cloned.yaml").read_bytes() == (
            configs_dir / "default.yaml"
        ).read_bytes()

        # The editor seam hands the copy back verbatim.
        resp = await panel.client.get("/api/panel/configs/cloned")
        assert resp.status_code == 200
        assert resp.json()["content"] == SOURCE_BODY

        # Editing the copy never touches the source.
        edited = "mixed-port: 7891\nallow-lan: true\n"
        resp = await panel.client.put("/api/panel/configs/cloned", content=edited)
        assert resp.status_code == 200
        assert (configs_dir / "cloned.yaml").read_bytes() == edited.encode()
        assert (configs_dir / "default.yaml").read_bytes() == SOURCE_BODY.encode()

        # A copy onto an existing name is refused, never a clobber.
        resp = await panel.client.post("/api/panel/configs/default/copy", json={"name": "cloned"})
        assert resp.status_code == 409


SUBSCRIPTION_BODY = """\
mixed-port: 7892
allow-lan: true
proxies:
  - name: sub1
    type: http
    server: 127.0.0.1
    port: 1082
"""


async def test_copy_flow_works_on_subscription_derived_configs(boot_app) -> None:
    async with boot_app() as panel:
        # Arrange a Subscription-derived Config at the registry level (#12
        # builds the actual fetch flow later; the copy path is under test).
        store = panel.app.state.store
        store.create_config("sub1", "subscription-derived")
        (panel.data_dir / "configs" / "sub1.yaml").write_text(SUBSCRIPTION_BODY)

        resp = await panel.client.post("/api/panel/configs/sub1/copy", json={"name": "sub1-hand"})
        assert resp.status_code == 201
        assert resp.json() == {
            "name": "sub1-hand",
            "kind": "hand-written",
            "filename": "sub1-hand.yaml",
            "active": False,
        }

        configs_dir = panel.data_dir / "configs"
        # Same bytes under the new name; the source keeps its own kind and file.
        assert (configs_dir / "sub1-hand.yaml").read_bytes() == (
            configs_dir / "sub1.yaml"
        ).read_bytes()
        configs = {c["name"]: c for c in (await panel.client.get("/api/panel/configs")).json()}
        assert configs["sub1"]["kind"] == "subscription-derived"
        assert configs["sub1-hand"]["kind"] == "hand-written"

        # The hand-written copy is fully usable: it passes the pipeline's
        # validation and becomes the Active Config.
        resp = await panel.client.put("/api/panel/configs/sub1-hand/active")
        assert resp.status_code == 200
        health = (await panel.client.get("/api/service/health")).json()
        assert health["active_config"] == "sub1-hand"
        assert (await _controller_configs(panel.app.state.secret))["mixed-port"] == 7892
