"""Issue #43: the Config summary and the overview status.

Two seams. The summary is a pure function over bytes, so its awkward inputs —
nothing at all, lists that are absent or not lists, an Age-armored source — are
unit tests. What the panel SHOWS is asserted through the Management Panel HTTP
API: the list, the Editor detail, and the health payload the overview reads.
"""

from conftest import PROBE_PROXY_BLOCK, origin_server

from clashflow.summary import AGE_ARMOR_HEADER, summarize

RICH_CONFIG = """\
mixed-port: 7890
allow-lan: true
proxies:
  - {name: n1, type: http, server: 127.0.0.1, port: 1080}
  - {name: n2, type: http, server: 127.0.0.1, port: 1081}
proxy-groups:
  - {name: auto, type: select, proxies: [n1, n2]}
rules:
  - MATCH,auto
"""

ONE_PROXY = PROBE_PROXY_BLOCK.encode() + b"mixed-port: 7890\nallow-lan: true\n"
TWO_PROXIES = (
    b"mixed-port: 7890\nallow-lan: true\n"
    b"proxies:\n"
    b"  - {name: n1, type: http, server: 127.0.0.1, port: 1080}\n"
    b"  - {name: n2, type: http, server: 127.0.0.1, port: 1081}\n"
)
# Parses and carries a proxy, but the Kernel refuses it: the save pipeline
# answers 400 after the summary would have been written by a naive path.
INVALID_TYPE = (
    b"mixed-port: 7890\n"
    b"proxies:\n"
    b"  - {name: broken, type: not-a-real-type, server: 127.0.0.1, port: 1}\n"
)


def test_the_summary_counts_top_level_nodes_groups_and_rules() -> None:
    summary = summarize(RICH_CONFIG.encode(), RICH_CONFIG.encode())
    assert (summary.nodes, summary.groups, summary.rules) == (2, 1, 1)
    assert summary.age_key_missing is False


def test_empty_and_minimal_configs_report_zeros_without_error() -> None:
    # An empty mapping, a defaults-only document, empty lists, a list-valued
    # key that is not a list, and bytes that do not parse at all.
    for body in (
        b"",
        b"{}\n",
        b"mixed-port: 7890\nallow-lan: true\n",
        b"proxies: []\nproxy-groups: {}\nrules: 5\n",
        b"proxies: [\n",
    ):
        summary = summarize(body, body)
        assert summary.as_dict() == {
            "nodes": 0,
            "groups": 0,
            "rules": 0,
            "age_key_missing": False,
        }


def test_an_age_armored_source_reports_the_missing_key() -> None:
    armored = f"{AGE_ARMOR_HEADER}\n...\n-----END AGE ENCRYPTED FILE-----\n".encode()
    summary = summarize(armored, armored)
    assert summary.age_key_missing is True
    # Encrypted bytes are not a YAML document: the counts are unknown, which
    # is exactly what the flag explains.
    assert summary.nodes == 0


async def test_a_successful_save_shows_the_summary_in_the_list_and_the_detail(boot_app) -> None:
    async with boot_app() as panel:
        assert (
            await panel.client.post("/api/panel/configs", json={"name": "rich"})
        ).status_code == 201
        resp = await panel.client.put(
            "/api/panel/configs/rich",
            content=RICH_CONFIG,
            headers={"Content-Type": "application/yaml"},
        )
        assert resp.status_code == 200
        expected = {"nodes": 2, "groups": 1, "rules": 1, "age_key_missing": False}
        assert resp.json()["summary"] == expected

        listed = {c["name"]: c for c in (await panel.client.get("/api/panel/configs")).json()}
        assert listed["rich"]["summary"] == expected
        detail = (await panel.client.get("/api/panel/configs/rich")).json()
        assert detail["summary"] == expected
        # The summary never leaks into the bytes the editor edits.
        assert detail["content"] == RICH_CONFIG


async def test_a_failed_validation_leaves_the_previous_summary_untouched(boot_app) -> None:
    async with boot_app() as panel:
        assert (
            await panel.client.post("/api/panel/configs", json={"name": "rich"})
        ).status_code == 201
        assert (
            await panel.client.put(
                "/api/panel/configs/rich",
                content=RICH_CONFIG,
                headers={"Content-Type": "application/yaml"},
            )
        ).status_code == 200
        before = (await panel.client.get("/api/panel/configs/rich")).json()["summary"]
        assert before == {"nodes": 2, "groups": 1, "rules": 1, "age_key_missing": False}

        # The Kernel refuses the candidate: nothing was committed, so nothing
        # about the stored summary changes.
        resp = await panel.client.put(
            "/api/panel/configs/rich",
            content=INVALID_TYPE,
            headers={"Content-Type": "application/yaml"},
        )
        assert resp.status_code == 400
        after = (await panel.client.get("/api/panel/configs/rich")).json()["summary"]
        assert after == before


async def test_a_minimal_config_that_commits_reports_zeros(boot_app) -> None:
    async with boot_app() as panel:
        # The panel-authored template carries no proxies, so it is made Active
        # without the content check the save endpoint applies — and the
        # summary reports zeros instead of failing.
        assert (
            await panel.client.post("/api/panel/configs", json={"name": "fresh", "template": True})
        ).status_code == 201
        resp = await panel.client.put("/api/panel/configs/fresh/active")
        assert resp.status_code == 200
        assert resp.json()["summary"] == {
            "nodes": 0,
            "groups": 0,
            "rules": 0,
            "age_key_missing": False,
        }


async def test_a_subscription_refresh_updates_the_linked_config_summary(boot_app) -> None:
    async with boot_app() as panel:
        with origin_server(ONE_PROXY) as origin:
            assert (
                await panel.client.post(
                    "/api/panel/subscriptions", json={"name": "prov", "url": origin.url}
                )
            ).status_code == 201
            listed = {c["name"]: c for c in (await panel.client.get("/api/panel/configs")).json()}
            assert listed["prov"]["summary"]["nodes"] == 1

            origin.state["body"] = TWO_PROXIES
            assert (
                await panel.client.post("/api/panel/subscriptions/prov/refresh")
            ).status_code == 200
        listed = {c["name"]: c for c in (await panel.client.get("/api/panel/configs")).json()}
        assert listed["prov"]["summary"] == {
            "nodes": 2,
            "groups": 0,
            "rules": 0,
            "age_key_missing": False,
        }


async def test_the_overview_status_names_the_kernel_state_and_the_active_source_config(
    boot_app,
) -> None:
    async with boot_app() as panel:
        assert (
            await panel.client.post("/api/panel/configs", json={"name": "rich"})
        ).status_code == 201
        assert (
            await panel.client.put(
                "/api/panel/configs/rich",
                content=RICH_CONFIG,
                headers={"Content-Type": "application/yaml"},
            )
        ).status_code == 200
        assert (await panel.client.put("/api/panel/configs/rich/active")).status_code == 200

        health = (await panel.client.get("/api/service/health")).json()
        assert health["kernel"] == "up"
        assert health["active_config"] == "rich"
        # What the overview renders beside the Kernel state: the Active Config
        # entry with its summary, found by `active`, not by a name guess.
        configs = (await panel.client.get("/api/panel/configs")).json()
        active = [config for config in configs if config["active"]]
        assert [config["name"] for config in active] == ["rich"]
        assert active[0]["summary"] == {
            "nodes": 2,
            "groups": 1,
            "rules": 1,
            "age_key_missing": False,
        }
