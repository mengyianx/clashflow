"""Subscription fetch pipeline (plan §5, flow 3): GET the Subscription URL
with a custom user agent and timeout, then run the fetched bytes through the
shared apply pipeline (`mihomo -t` validation + atomic swap), hot-applying
when the target is the Active Config.

Failure classes (spec issue #6), each raising a distinct exception that the
API layer maps to a status code:
- network error (transport failure or non-2xx response) -> SubscriptionFetchError
- content that is not a complete mihomo YAML Config (unparseable YAML OR
  semantically invalid) -> KernelRejected, carrying the Kernel's own output
  verbatim; both cases go through `mihomo -t`, the single write-path validator
- Controller refusal on the Active-Config hot-apply -> HotApplyError

In every failure case the previous Config file stays byte-identical:
`clashflow.apply.atomic_save` only `os.replace`s after a successful
config-test, and a Controller refusal on the forced hot-apply (HotApplyError)
restores the pre-refresh bytes — the Kernel never left the old Config, so
disk and running state stay consistent.
"""

import logging
from pathlib import Path

import httpx

from clashflow.apply import HotApplyError, KernelRejected, atomic_save, hot_apply
from clashflow.db import MetadataStore
from clashflow.kernel import KernelManager

log = logging.getLogger(__name__)

# Base fetch settings for every Subscription (ticket #12), and the per-
# Subscription overrides added by ticket #13. The user agent identifies
# ClashFlow to providers and lets them distinguish us from browsers;
# `subscriptions.user_agent` overrides it per Subscription, NULL falls back
# to this default.
USER_AGENT = "ClashFlow/0.1"
FETCH_TIMEOUT_SECONDS = 30.0
# Global default update interval (plan §1: 24h), the fallback for
# `subscriptions.update_interval_minutes` — NULL means "use this default";
# #16's scheduler divides minutes into its schedule and applies the
# per-Subscription override where set.
DEFAULT_UPDATE_INTERVAL_MINUTES = 1440


class SubscriptionFetchError(Exception):
    """The Subscription URL could not be fetched (transport error or non-2xx
    response); the message carries the failure reason."""


async def fetch_subscription(
    client: httpx.AsyncClient, url: str, *, user_agent: str | None = None
) -> bytes:
    """GET the Subscription URL; providers commonly redirect to a CDN, so
    redirects are followed. `user_agent` is the Subscription's custom UA;
    when unset the module default goes out. The bytes are returned verbatim
    (no decoding, no re-encoding — the Config file reproduces them exactly)."""
    try:
        resp = await client.get(
            url,
            headers={"User-Agent": user_agent or USER_AGENT},
            timeout=FETCH_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        raise SubscriptionFetchError(f"subscription fetch failed: {exc}") from exc
    if not resp.is_success:
        raise SubscriptionFetchError(
            f"subscription fetch failed: HTTP {resp.status_code} {resp.reason_phrase}"
        )
    return resp.content


async def refresh_subscription(
    kernel: KernelManager,
    client: httpx.AsyncClient,
    secret: str,
    url: str,
    target: Path,
    *,
    apply_when_active: bool,
    user_agent: str | None = None,
) -> None:
    """Fetch and store one Subscription's Config: validate + atomic-swap via
    the shared apply pipeline, then a forced hot-apply when this Config is
    Active and the Kernel is up. If that hot-apply is refused, the pre-refresh
    bytes are restored before re-raising: the Kernel never left the old
    Config, so the failure classes leave the old Config in place on disk too.
    Pipeline exceptions propagate untouched for the API layer to surface
    verbatim."""
    content = await fetch_subscription(client, url, user_agent=user_agent)
    previous = target.read_bytes() if target.is_file() else None
    await atomic_save(kernel.binary, kernel.data_dir, target, content)
    if apply_when_active and kernel.alive():
        try:
            await hot_apply(client, secret, target)
        except HotApplyError:
            if previous is not None:
                target.write_bytes(previous)
            raise
    log.info("refreshed Subscription-derived Config %s (%d bytes)", target.name, len(content))


# The three failure classes of the refresh pipeline (each its own exception),
# shared by the manual-refresh endpoint and the scheduler so both dispatch on
# one tuple instead of re-listing the classes.
REFRESH_FAILURES = (SubscriptionFetchError, KernelRejected, HotApplyError)


def mark_subscription_refresh_failed(store: MetadataStore, name: str, exc: Exception) -> str:
    """One shared dispatch of a refresh failure onto the store: mark the
    Subscription failed with the failure reason — the Kernel's own output for
    KernelRejected, the exception text otherwise — and return that reason for
    the caller to surface (HTTP detail or log line)."""
    reason = exc.output if isinstance(exc, KernelRejected) else str(exc)
    store.mark_subscription_failed(name, reason)
    return reason
