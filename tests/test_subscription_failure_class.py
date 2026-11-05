"""Ticket #27: the failure class of a failed Subscription refresh.

A failed refresh carries a class next to its raw error text — `network`,
`kernel-rejected` or `hot-apply-refused` — from the metadata store through
the Subscription API to the panel. Assertions stay at the Management Panel
HTTP API seam (and, for the migration, at the volume's database file); the
pinned Kernel is a real fixture and the Subscription provider is a local HTTP
origin, so runs are deterministic and offline.

There is deliberately no invalid-YAML class: the Kernel is the only
write-path validator, so unparseable content is a `kernel-rejected` failure
like any other config-test refusal.
"""

import asyncio
import re
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from conftest import origin_server

from clashflow.apply import HotApplyError

SUB_V1 = b"mixed-port: 7892\nallow-lan: true\n"
# Unparseable YAML: the fetched bytes reach the Kernel config-test and are
# refused there — a `kernel-rejected` failure, never a class of its own.
SUB_INVALID_YAML = b"proxies: [\n"


async def _add_subscription(panel, name: str, url: str):
    return await panel.client.post("/api/panel/subscriptions", json={"name": name, "url": url})


async def _entry(panel, name: str) -> dict[str, Any]:
    resp = await panel.client.get("/api/panel/subscriptions")
    assert resp.status_code == 200
    return next(entry for entry in resp.json() if entry["name"] == name)


def _arrange_last_refresh(panel, name: str, age: timedelta) -> None:
    """Test arrangement (not a seam action): backdate the LAST SUCCESSFUL
    refresh time in the metadata store so the Subscription's due-ness flips
    without waiting the real interval."""
    when = (datetime.now(UTC) - age).isoformat(timespec="seconds")
    store = panel.app.state.store
    store._conn.execute("UPDATE subscriptions SET last_refresh_at = ? WHERE name = ?", (when, name))
    store._conn.commit()


async def _eventually(predicate, timeout: float = 10.0) -> None:
    """Poll the async `predicate` at tick granularity until it holds."""
    deadline = time.monotonic() + timeout
    while True:
        if await predicate():
            return
        if time.monotonic() > deadline:
            pytest.fail(f"condition not met within {timeout}s")
        await asyncio.sleep(0.02)


async def test_a_fetch_failure_is_recorded_as_the_network_class(boot_app) -> None:
    # The provider's listener goes away: a transport failure is `network`.
    async with boot_app() as panel:
        with origin_server(SUB_V1) as origin:
            assert (await _add_subscription(panel, "prov", origin.url)).status_code == 201
            origin.stop()
            resp = await panel.client.post("/api/panel/subscriptions/prov/refresh")
            assert resp.status_code == 502

        entry = await _entry(panel, "prov")
        assert entry["state"] == "error"
        assert entry["last_error_class"] == "network"
        # The raw text stays beside the class.
        assert "subscription fetch failed" in entry["last_error"]


async def test_a_non_2xx_provider_response_is_recorded_as_the_network_class(boot_app) -> None:
    # A provider that answers with an error status is a transport-level
    # failure too — `network`, not a Kernel refusal.
    async with boot_app() as panel:
        with origin_server(SUB_V1) as origin:
            assert (await _add_subscription(panel, "prov", origin.url)).status_code == 201
            origin.state["status"] = 403
            resp = await panel.client.post("/api/panel/subscriptions/prov/refresh")
            assert resp.status_code == 502

        entry = await _entry(panel, "prov")
        assert entry["last_error_class"] == "network"
        assert "HTTP 403" in entry["last_error"]


async def test_content_the_kernel_refuses_is_recorded_as_the_kernel_rejected_class(
    boot_app,
) -> None:
    # The Kernel's config-test refuses the fetched content: `kernel-rejected`,
    # with the Kernel's own output kept as the raw text.
    async with boot_app() as panel:
        with origin_server(SUB_V1) as origin:
            assert (await _add_subscription(panel, "prov", origin.url)).status_code == 201
            origin.state["body"] = SUB_INVALID_YAML
            resp = await panel.client.post("/api/panel/subscriptions/prov/refresh")
            assert resp.status_code == 400

        entry = await _entry(panel, "prov")
        assert entry["last_error_class"] == "kernel-rejected"
        assert "did not find expected node content" in entry["last_error"]


async def test_a_refused_hot_apply_is_recorded_as_the_hot_apply_refused_class(
    boot_app, monkeypatch
) -> None:
    # The Controller refuses the forced hot-apply of the Active
    # Subscription-derived Config: `hot-apply-refused` — a third class,
    # distinct from the Kernel's config-test refusal.
    async with boot_app() as panel:
        with origin_server(SUB_V1) as origin:
            assert (await _add_subscription(panel, "prov", origin.url)).status_code == 201
            assert (await panel.client.put("/api/panel/configs/prov/active")).status_code == 200

            async def forced_hot_apply_failure(*args, **kwargs):
                raise HotApplyError("forced hot-apply failure")

            # The shared restore implementation (ticket #26) lives in
            # clashflow.apply: the refresh path calls `hot_apply_or_restore`,
            # which calls `hot_apply` there. Patching that one call site
            # refuses the hot-apply for both write paths.
            monkeypatch.setattr("clashflow.apply.hot_apply", forced_hot_apply_failure)
            resp = await panel.client.post("/api/panel/subscriptions/prov/refresh")
            assert resp.status_code == 502

        entry = await _entry(panel, "prov")
        assert entry["last_error_class"] == "hot-apply-refused"
        assert entry["last_error"] == "forced hot-apply failure"


def test_the_panel_never_parses_yaml_itself() -> None:
    # The Kernel is the only write-path validator: no module of the panel
    # imports a YAML parser, so a failure class never depends on the panel
    # reading the Config content itself.
    import clashflow

    package = Path(clashflow.__file__).parent
    for path in sorted(package.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"^\s*(?:import|from)\s+yaml\b", source, re.MULTILINE), path


async def test_a_successful_refresh_clears_the_class_and_the_error_text(boot_app) -> None:
    # The recovery path: a later success clears the failure marker AND its
    # class, so the panel shows no stale label.
    async with boot_app() as panel:
        with origin_server(SUB_V1) as origin:
            assert (await _add_subscription(panel, "prov", origin.url)).status_code == 201
            origin.state["body"] = SUB_INVALID_YAML
            resp = await panel.client.post("/api/panel/subscriptions/prov/refresh")
            assert resp.status_code == 400
            assert (await _entry(panel, "prov"))["last_error_class"] == "kernel-rejected"

            # The provider recovers; the next refresh succeeds.
            origin.state["body"] = SUB_V1
            resp = await panel.client.post("/api/panel/subscriptions/prov/refresh")
            assert resp.status_code == 200
            entry = resp.json()
            assert entry["state"] == "ok"
            assert entry["last_error"] is None
            assert entry["last_error_class"] is None


async def test_a_scheduled_refresh_reports_the_same_class_as_a_manual_one(
    boot_app, monkeypatch
) -> None:
    # Both writers dispatch on the same shared failure dispatch, so the same
    # failure reports the same class whichever way the refresh was triggered.
    monkeypatch.setenv("CLASHFLOW_SCHEDULER_TICK_SECONDS", "0.05")
    async with boot_app() as panel:
        with origin_server(SUB_V1) as manual_origin, origin_server(SUB_V1) as sched_origin:
            assert (await _add_subscription(panel, "manual", manual_origin.url)).status_code == 201
            assert (await _add_subscription(panel, "sched", sched_origin.url)).status_code == 201

            # Both providers go away: the same transport failure on both
            # Subscriptions.
            manual_origin.stop()
            sched_origin.stop()

            # The manual refresh of one Subscription...
            resp = await panel.client.post("/api/panel/subscriptions/manual/refresh")
            assert resp.status_code == 502
            manual_entry = await _entry(panel, "manual")
            assert manual_entry["last_error_class"] == "network"

            # ...and the scheduled refresh of the other (a short interval
            # override plus a backdated last success make it due).
            patched = await panel.client.patch(
                "/api/panel/subscriptions/sched", json={"update_interval_minutes": 1}
            )
            assert patched.status_code == 200
            _arrange_last_refresh(panel, "sched", timedelta(minutes=2))

            async def classified() -> bool:
                return (await _entry(panel, "sched"))["last_error_class"] == "network"

            await _eventually(classified)
            sched_entry = await _entry(panel, "sched")
            assert sched_entry["last_error_class"] == manual_entry["last_error_class"]
            assert sched_entry["last_error"] and manual_entry["last_error"]


async def test_booting_on_a_pre_change_database_keeps_the_records_and_adds_the_class_column(
    boot_app, tmp_path
) -> None:
    # A database from before the failure class existed keeps working: the
    # column is added in place, the records survive, and a record without a
    # class reports none (the panel must not invent a label for it).
    data = tmp_path / "volume"
    async with boot_app(data_dir=data) as panel:
        with origin_server(SUB_V1) as origin:
            assert (await _add_subscription(panel, "prov", origin.url)).status_code == 201
            origin.stop()
            resp = await panel.client.post("/api/panel/subscriptions/prov/refresh")
            assert resp.status_code == 502
        assert (await _entry(panel, "prov"))["last_error_class"] == "network"

    # Rewrite the database file into its pre-change shape: the same schema
    # without the failure-class column.
    conn = sqlite3.connect(data / "clashflow.db")
    conn.execute("ALTER TABLE subscriptions DROP COLUMN last_error_class")
    conn.commit()
    conn.close()

    async with boot_app(data_dir=data) as panel:
        entry = await _entry(panel, "prov")
        # The record survived the migration with its raw text...
        assert entry["state"] == "error"
        assert "subscription fetch failed" in entry["last_error"]
        # ...and without a class: nothing to label.
        assert entry["last_error_class"] is None

        # The column exists now, and the next failure writes its class.
        conn = sqlite3.connect(data / "clashflow.db")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(subscriptions)")}
        conn.close()
        assert "last_error_class" in columns

        resp = await panel.client.post("/api/panel/subscriptions/prov/refresh")
        assert resp.status_code == 502
        assert (await _entry(panel, "prov"))["last_error_class"] == "network"
