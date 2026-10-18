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
config-test.
"""

import logging
from pathlib import Path

import httpx

from clashflow.apply import atomic_save, hot_apply
from clashflow.kernel import KernelManager

log = logging.getLogger(__name__)

# Base fetch settings for every Subscription (ticket #12). The user agent
# identifies ClashFlow to providers and lets them distinguish us from browsers;
# ticket #13 makes both values per-Subscription overrides.
USER_AGENT = "ClashFlow/0.1"
FETCH_TIMEOUT_SECONDS = 30.0


class SubscriptionFetchError(Exception):
    """The Subscription URL could not be fetched (transport error or non-2xx
    response); the message carries the failure reason."""


async def fetch_subscription(client: httpx.AsyncClient, url: str) -> bytes:
    """GET the Subscription URL; providers commonly redirect to a CDN, so
    redirects are followed. The bytes are returned verbatim (no decoding,
    no re-encoding — the Config file reproduces them exactly)."""
    try:
        resp = await client.get(
            url,
            headers={"User-Agent": USER_AGENT},
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
) -> None:
    """Fetch and store one Subscription's Config: validate + atomic-swap via
    the shared apply pipeline, then a forced hot-apply when this Config is
    Active and the Kernel is up. Pipeline exceptions propagate untouched for
    the API layer to surface verbatim."""
    content = await fetch_subscription(client, url)
    await atomic_save(kernel.binary, kernel.data_dir, target, content)
    if apply_when_active and kernel.alive():
        await hot_apply(client, secret, target)
    log.info("refreshed subscription config %s (%d bytes)", target.name, len(content))
