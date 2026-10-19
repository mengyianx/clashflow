"""Ticket #16: scheduled Subscription auto-refresh with retry.

Assertions stay at the Management Panel HTTP API seam (the Subscriptions
view + the Config content endpoint) and the local provider fixture's request
record; the pinned Kernel is a real fixture, runs are offline. The scheduler
tick is shortened through the documented CLASHFLOW_SCHEDULER_TICK_SECONDS
knob, and due-ness is arranged by backdating `last_refresh_at` directly in
the metadata store — no test waits a real interval or sleeps blind.
"""

import asyncio
import contextlib
import http.server
import socketserver
import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

SUB_V1 = b"mixed-port: 7892\nallow-lan: true\n"
SUB_V2 = b"mixed-port: 7893\nallow-lan: true\n"
SUB_PLAIN_V2 = b"mixed-port: 7894\nallow-lan: true\n"
# Unparseable YAML: the fetched bytes reach the Kernel config-test and are
# rejected, so the scheduled refresh fails as the validation-error class and
# the previous Config must survive byte-identical.
SUB_INVALID_YAML = b"proxies: [\n"


@contextlib.contextmanager
def subscription_server(body: bytes):
    """Local origin standing in for a Subscription provider: the served body
    can be changed in place, and every request's User-Agent is recorded."""

    state: dict[str, Any] = {"body": body, "user_agents": []}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            state["user_agents"].append(self.headers.get("User-Agent"))
            self.send_response(200)
            self.send_header("Content-Length", str(len(state["body"])))
            self.end_headers()
            self.wfile.write(state["body"])

        def log_message(self, *args: object) -> None:
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield SimpleNamespace(url=f"http://127.0.0.1:{server.server_address[1]}/sub", state=state)
    finally:
        server.shutdown()
        server.server_close()


async def _add_subscription(panel, name: str, url: str) -> httpx.Response:
    return await panel.client.post("/api/panel/subscriptions", json={"name": name, "url": url})


async def _subscription_state(panel, name: str) -> dict[str, Any]:
    resp = await panel.client.get("/api/panel/subscriptions")
    assert resp.status_code == 200
    return next(entry for entry in resp.json() if entry["name"] == name)


async def _config_content(panel, name: str) -> str:
    resp = await panel.client.get(f"/api/panel/configs/{name}")
    assert resp.status_code == 200
    return resp.json()["content"]


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
            pytest.fail(f"scheduler condition not met within {timeout}s")
        await asyncio.sleep(0.02)


async def test_a_short_interval_subscription_refreshes_on_a_scheduler_tick_without_user_action(
    boot_app, monkeypatch
) -> None:
    # AC1: with a short per-Subscription interval override, a tick refreshes
    # the Subscription-derived Config with no user action — the seam shows
    # the new content and a new last_refresh_at, and the provider received
    # the Subscription's custom user agent on the scheduled fetch.
    monkeypatch.setenv("CLASHFLOW_SCHEDULER_TICK_SECONDS", "0.05")
    async with boot_app() as panel:
        with subscription_server(SUB_V1) as origin:
            assert (await _add_subscription(panel, "prov", origin.url)).status_code == 201
            patched = await panel.client.patch(
                "/api/panel/subscriptions/prov",
                json={"update_interval_minutes": 1, "user_agent": "ClashFlow-Test/9.9"},
            )
            assert patched.status_code == 200
            assert origin.state["user_agents"] == ["ClashFlow/0.1"]
            # Due under the 1-minute override; the provider's source updates;
            # from here on NO user action touches the Subscription.
            origin.state["body"] = SUB_V2
            _arrange_last_refresh(panel, "prov", timedelta(minutes=2))
            last_before = (await _subscription_state(panel, "prov"))["last_refresh_at"]

            async def refreshed() -> bool:
                entry = await _subscription_state(panel, "prov")
                return (
                    entry["last_refresh_at"] != last_before
                    and await _config_content(panel, "prov") == SUB_V2.decode()
                )

            await _eventually(refreshed)
            entry = await _subscription_state(panel, "prov")
            assert entry["state"] == "ok"
            assert entry["last_error"] is None
            # The scheduled fetch carried the per-Subscription user agent.
            assert origin.state["user_agents"] == ["ClashFlow/0.1", "ClashFlow-Test/9.9"]


async def test_a_failing_scheduled_refresh_keeps_the_old_config_and_error_marker_until_a_later_tick_succeeds(
    boot_app, monkeypatch
) -> None:
    # AC2: a scheduled refresh cycle the Kernel rejects leaves the old Config
    # byte-identical and marks the Subscription (state=error + reason). A
    # failure never moves the last-success time, so the next tick retries;
    # once the provider recovers, the success clears the marker.
    monkeypatch.setenv("CLASHFLOW_SCHEDULER_TICK_SECONDS", "0.05")
    async with boot_app() as panel:
        with subscription_server(SUB_V1) as origin:
            assert (await _add_subscription(panel, "prov", origin.url)).status_code == 201
            patched = await panel.client.patch(
                "/api/panel/subscriptions/prov", json={"update_interval_minutes": 1}
            )
            assert patched.status_code == 200
            # The provider starts serving broken content, then the tick fires.
            origin.state["body"] = SUB_INVALID_YAML
            _arrange_last_refresh(panel, "prov", timedelta(minutes=2))

            async def marked_error() -> bool:
                return (await _subscription_state(panel, "prov"))["state"] == "error"

            await _eventually(marked_error)
            entry = await _subscription_state(panel, "prov")
            assert entry["state"] == "error"
            assert "did not find expected node content" in entry["last_error"]
            # The failing cycle keeps the old Config byte-identical.
            assert await _config_content(panel, "prov") == SUB_V1.decode()

            # The provider recovers; the retrying tick succeeds and clears
            # the marker and the stale content in one go.
            origin.state["body"] = SUB_V2

            async def recovered() -> bool:
                entry = await _subscription_state(panel, "prov")
                return (
                    entry["state"] == "ok"
                    and entry["last_error"] is None
                    and await _config_content(panel, "prov") == SUB_V2.decode()
                )

            await _eventually(recovered)


async def test_a_subscription_without_an_interval_override_follows_the_global_default_interval(
    boot_app, monkeypatch
) -> None:
    # AC3: no override -> the global default (1440 minutes) is the effective
    # interval: a Subscription whose last success is an hour old is NOT due,
    # while an overridden sibling refreshed on the very same ticks is.
    monkeypatch.setenv("CLASHFLOW_SCHEDULER_TICK_SECONDS", "0.05")
    async with boot_app() as panel:
        with (
            subscription_server(SUB_V1) as plain_origin,
            subscription_server(SUB_V1) as over_origin,
        ):
            assert (await _add_subscription(panel, "plain", plain_origin.url)).status_code == 201
            assert (await _add_subscription(panel, "over", over_origin.url)).status_code == 201
            patched = await panel.client.patch(
                "/api/panel/subscriptions/over", json={"update_interval_minutes": 1}
            )
            assert patched.status_code == 200
            plain_origin.state["body"] = SUB_PLAIN_V2
            over_origin.state["body"] = SUB_V2
            # "plain" is an hour old: due if the default were misapplied as
            # the override's minute (or always-due), NOT due under the
            # documented 1440-minute global default.
            _arrange_last_refresh(panel, "plain", timedelta(minutes=60))
            _arrange_last_refresh(panel, "over", timedelta(minutes=2))

            async def sibling_refreshed() -> bool:
                return await _config_content(panel, "over") == SUB_V2.decode()

            await _eventually(sibling_refreshed)
            # Several more ticks pass before asserting the un-overridden
            # Subscription stayed untouched (a bounded settle, not a wait).
            await asyncio.sleep(0.3)
            assert await _config_content(panel, "plain") == SUB_V1.decode()
            # Only the import-time fetch ever reached the provider.
            assert plain_origin.state["user_agents"] == ["ClashFlow/0.1"]
            assert (await _subscription_state(panel, "plain"))["state"] == "ok"
