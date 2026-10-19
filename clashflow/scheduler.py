"""Subscription auto-refresh scheduler (plan §5 flow 1: the panel starts the
scheduler once the Kernel is up; ticket #16).

DUE RULE — a Subscription is due for an automatic refresh when its LAST
SUCCESSFUL refresh time (`subscriptions.last_refresh_at`, stamped only by
`store.mark_subscription_refreshed`, #12 semantics) is NULL — never
refreshed, e.g. imported before the panel booted — or older than its
effective update interval: the per-Subscription `update_interval_minutes`
override (#13) where set, else the global default
`DEFAULT_UPDATE_INTERVAL_MINUTES` (24h). Keying on last SUCCESS makes the
retry story fall out of the rule with no separate bookkeeping: a failed
refresh never moves `last_refresh_at`, so the Subscription stays due and is
retried on every following tick, and its `last_error` stays visible in the
Subscriptions view until one attempt succeeds — which clears it through the
same `mark_subscription_refreshed` call.

Each due Subscription refreshes through the exact pipeline manual refresh
uses (`clashflow.refresh.refresh_subscription` with its stored
`user_agent`, hot-applied when its Config is Active) and the outcome is
marked on the store exactly like the manual endpoint's, so scheduled results
surface identically at the seam (`GET /api/panel/subscriptions`).

The loop wakes every `CLASHFLOW_SCHEDULER_TICK_SECONDS` seconds (default
60.0; env-knob precedent: the `CLASHFLOW_RESPAWN_*` supervisor knobs). The
tick is due-detection granularity, not the schedule itself: a Subscription
is refreshed within one tick of becoming due, and the 24h default interval
still governs how often that happens. Tests shorten the tick via the env
var for deterministic, sleep-free coverage.
"""

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import httpx

from clashflow.apply import HotApplyError, KernelRejected
from clashflow.db import MetadataStore, config_filename
from clashflow.kernel import KernelManager
from clashflow.refresh import (
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    SubscriptionFetchError,
    refresh_subscription,
)

log = logging.getLogger(__name__)

DEFAULT_TICK_SECONDS = 60.0

# One Subscription entry as the store reports it; the columns consumed here
# are typed by hand because the store's entry type is a flat dict.
type SubscriptionEntry = dict[str, str | int | None]


class SubscriptionScheduler:
    def __init__(
        self,
        store: MetadataStore,
        kernel: KernelManager,
        client: httpx.AsyncClient,
        secret: str,
        configs_dir: Path,
    ) -> None:
        self._store = store
        self._kernel = kernel
        self._client = client
        self._secret = secret
        self._configs_dir = configs_dir
        self.tick_seconds = float(
            os.environ.get("CLASHFLOW_SCHEDULER_TICK_SECONDS", str(DEFAULT_TICK_SECONDS))
        )
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Start the ticker as a background task (lifespan startup)."""
        self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        """Stop the ticker (lifespan shutdown); stopping twice is harmless."""
        if self._task is not None:
            self._task.cancel()

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.tick_seconds)
            await self.run_once()

    def is_due(self, entry: SubscriptionEntry) -> bool:
        """The due rule (module docstring): NULL last success or last success
        older than the effective interval (override, else global default)."""
        interval = cast("int | None", entry["update_interval_minutes"])
        last = cast("str | None", entry["last_refresh_at"])
        if last is None:
            return True
        age = datetime.now(UTC) - datetime.fromisoformat(last)
        return age >= timedelta(minutes=interval or DEFAULT_UPDATE_INTERVAL_MINUTES)

    async def run_once(self) -> None:
        """One tick: refresh every due Subscription, in list order."""
        for entry in self._store.list_subscriptions():
            if self.is_due(entry):
                await self._refresh(entry)

    async def _refresh(self, entry: SubscriptionEntry) -> None:
        """Refresh one Subscription through the manual-refresh pipeline and
        mark the outcome on the store, mirroring
        `clashflow.api.subscriptions.refresh_subscription_endpoint`."""
        name = cast("str", entry["name"])
        target = self._configs_dir / config_filename(name)
        url = cast("str", entry["url"])  # the url column is NOT NULL
        user_agent = cast("str | None", entry["user_agent"])
        try:
            active = self._store.get_config(name)["active"]
            await refresh_subscription(
                self._kernel,
                self._client,
                self._secret,
                url,
                target,
                apply_when_active=bool(active),
                user_agent=user_agent,
            )
        except SubscriptionFetchError as exc:
            self._store.mark_subscription_failed(name, str(exc))
            log.warning("scheduled refresh of subscription %s failed: %s", name, exc)
            return
        except KernelRejected as exc:
            self._store.mark_subscription_failed(name, exc.output)
            log.warning("scheduled refresh of subscription %s failed: %s", name, exc.output)
            return
        except HotApplyError as exc:
            self._store.mark_subscription_failed(name, str(exc))
            log.warning("scheduled refresh of subscription %s failed: %s", name, exc)
            return
        self._store.mark_subscription_refreshed(name)
        log.info("scheduled refresh updated subscription %s", name)
