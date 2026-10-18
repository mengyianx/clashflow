"""Ticket #8: start/stop/restart actions, crash respawn, exponential backoff.

All assertions stay at the Management Panel HTTP API seam and the Kernel API;
the Active-Config path is proved by what the Kernel actually loads.
"""

import asyncio
import itertools
import os
import signal
import time

import httpx

CONTROLLER = "http://127.0.0.1:9090"


async def _mixed_port(client: httpx.AsyncClient, secret: str) -> int:
    resp = await client.get(f"{CONTROLLER}/configs", headers={"Authorization": f"Bearer {secret}"})
    assert resp.status_code == 200
    return resp.json()["mixed-port"]


async def test_start_stop_restart_actions_report_resulting_state(boot_app) -> None:
    async with boot_app() as panel:
        secret = panel.app.state.secret

        # Boot already left the Kernel running; start is idempotent.
        resp = await panel.client.post("/api/service/start")
        assert resp.status_code == 200
        body = resp.json()
        assert body["kernel"] == "up"
        assert body["state"] == "up"
        assert body["active_config"] == "default"
        pid_after_start = panel.app.state.kernel.pid
        resp = await panel.client.post("/api/service/start")
        assert resp.status_code == 200
        assert resp.json()["state"] == "up"
        assert panel.app.state.kernel.pid == pid_after_start  # idempotent: no respawn

        # Stop reports the resulting down state; the Controller is gone.
        resp = await panel.client.post("/api/service/stop")
        assert resp.status_code == 200
        body = resp.json()
        assert body["kernel"] == "down"
        assert body["state"] == "down"
        async with httpx.AsyncClient() as kc:
            try:
                await kc.get(f"{CONTROLLER}/version", timeout=1.0)
            except httpx.HTTPError:
                pass  # refused: expected once stopped
            else:
                raise AssertionError("Controller still answering after stop")

        # Start again brings the Kernel back up.
        resp = await panel.client.post("/api/service/start")
        assert resp.status_code == 200
        body = resp.json()
        assert body["kernel"] == "up"
        assert body["state"] == "up"
        assert panel.app.state.kernel.pid != pid_after_start

        # Restart spawns with the *current* Active Config path: switch the
        # Active Config to a different port, then restart and observe the
        # Kernel API serving that port's settings.
        alt = panel.data_dir / "configs" / "alt.yaml"
        alt.write_text("mixed-port: 7891\nallow-lan: true\n")
        panel.app.state.store.register_config("alt", "hand-written")
        panel.app.state.store.set_active("alt")
        pid_before_restart = panel.app.state.kernel.pid

        resp = await panel.client.post("/api/service/restart")
        assert resp.status_code == 200
        body = resp.json()
        assert body["kernel"] == "up"
        assert body["state"] == "up"
        assert body["active_config"] == "alt"
        assert panel.app.state.kernel.pid != pid_before_restart
        async with httpx.AsyncClient() as kc:
            assert await _mixed_port(kc, secret) == 7891


async def test_killed_kernel_respawns_with_active_config(boot_app) -> None:
    async with boot_app() as panel:
        secret = panel.app.state.secret

        # The Active Config changes after the Kernel was spawned: the respawn
        # must pick up the *current* one, not the spawn-time path.
        alt = panel.data_dir / "configs" / "alt.yaml"
        alt.write_text("mixed-port: 7891\nallow-lan: true\n")
        panel.app.state.store.register_config("alt", "hand-written")
        panel.app.state.store.set_active("alt")

        old_pid = panel.app.state.kernel.pid
        assert old_pid is not None
        os.kill(old_pid, signal.SIGKILL)

        deadline = time.monotonic() + 10.0
        body: dict = {}
        while time.monotonic() < deadline:
            body = (await panel.client.get("/api/service/health")).json()
            if body["kernel"] == "up":
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError(f"kernel did not respawn: {body}")

        assert body["state"] == "up"
        assert body["active_config"] == "alt"
        assert panel.app.state.kernel.pid != old_pid
        # The respawned Kernel runs the Active Config: its API serves alt's port.
        async with httpx.AsyncClient() as kc:
            assert await _mixed_port(kc, secret) == 7891


async def test_repeated_crashes_back_off_exponentially_and_surface_degraded(
    boot_app, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("CLASHFLOW_RESPAWN_BASE_DELAY", "0.2")
    monkeypatch.setenv("CLASHFLOW_MAX_RESPAWNS", "4")
    crashy = tmp_path / "crashy"
    crashy.write_text("#!/bin/sh\nexit 1\n")
    crashy.chmod(0o755)

    async with boot_app(kernel_bin=str(crashy)) as panel:
        # Every spawn dies at once: watch the backoff through the status seam.
        marks: dict[int, float] = {}
        body: dict = {}
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            body = (await panel.client.get("/api/service/health")).json()
            if body["state"] == "degraded":
                break
            if body["respawns"] not in marks:
                marks[body["respawns"]] = time.monotonic()
            await asyncio.sleep(0.01)
        else:
            raise AssertionError(f"never reached degraded: {body}")

        assert body["state"] == "degraded"
        assert body["kernel"] == "down"
        assert body["respawns"] == 4  # all respawn attempts spent

        # Backoff grows exponentially: the interval between consecutive
        # respawns increases, it is not a fixed-rate retry loop.
        ordered = [t for n, t in sorted(marks.items()) if n >= 1]
        assert len(ordered) >= 3
        gaps = [b - a for a, b in itertools.pairwise(ordered)]
        assert all(b > a * 1.3 for a, b in itertools.pairwise(gaps))

        # Degraded is stable on the status surface: the retry storm stopped
        # instead of looping silently (the next backoff would be 1.6s).
        await asyncio.sleep(2.0)
        body = (await panel.client.get("/api/service/health")).json()
        assert body["state"] == "degraded"
        assert body["respawns"] == 4
