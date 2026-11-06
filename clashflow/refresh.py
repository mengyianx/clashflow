"""Subscription fetch pipeline (plan §5, flow 3): GET the Subscription URL
with a custom user agent and timeout, then run the fetched bytes through the
shared apply pipeline (render, `mihomo -t` on the rendered bytes, atomic
writes of both files), hot-applying when the target is the Active Config.

Failure classes — the four the panel distinguishes (spec issue #6, #27), each
raising a distinct exception that the API layer maps to a status code:
- `network` (transport failure or non-2xx response) -> SubscriptionFetchError
- `render-failed`: the fetched bytes cannot be rendered (unparseable YAML or a
  non-mapping document), refused by the key-merge stage -> RenderFailed, whose
  message begins with the stage name; or the render carries neither `proxies`
  nor `proxy-providers` (an empty Subscription) -> RenderedConfigInvalid, the
  panel's own words
- `kernel-rejected`: the RENDERED Config is not a complete mihomo YAML, so the
  Kernel's config-test refuses it -> KernelRejected, carrying the Kernel's own
  output verbatim
- `hot-apply-refused`: Controller refusal on the Active-Config hot-apply ->
  HotApplyError

A refresh keeps its old promise — the fetched bytes are written to the
Source Config verbatim — and then renders that Source into the Runtime Config
the Kernel runs (ADR-0004), so the Overlay still wins at runtime. The render
and the config-test both run BEFORE anything is written: a render failure
(`render-failed`, naming its stage) or a Kernel refusal (`kernel-rejected`)
leaves the previous Source and Runtime Config bytes byte-identical. A
Controller refusal on the forced hot-apply (`hot-apply-refused`) puts both
pre-refresh states back through the shared pipeline, because the Kernel never
left the old Config.
"""

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

from clashflow.apply import (
    HotApplyError,
    KernelRejected,
    RenderedConfigInvalid,
    commit_rendered_config,
)
from clashflow.db import MetadataStore, runtime_config_path
from clashflow.kernel import KernelManager
from clashflow.render import RenderFailed

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
    overlay: Mapping[str, Any],
    user_agent: str | None = None,
) -> None:
    """Fetch a Subscription and store it: the fetched bytes go to the Source
    Config verbatim, the Source + Overlay are rendered into the Runtime
    Config, and the Runtime is hot-applied when this Config is Active and the
    Kernel is up. A failure before the writes leaves both files
    byte-identical; a refused hot-apply restores both pre-refresh states (the
    shared `commit_rendered_config`).
    Pipeline exceptions propagate untouched for the API layer to surface
    verbatim."""
    content = await fetch_subscription(client, url, user_agent=user_agent)
    await commit_rendered_config(
        kernel_bin=kernel.binary,
        data_dir=kernel.data_dir,
        client=client,
        secret=secret,
        source_target=target,
        runtime_target=runtime_config_path(kernel.data_dir, target.stem),
        content=content,
        overlay=overlay,
        should_reload=lambda: apply_when_active and kernel.alive(),
    )
    log.info("refreshed Subscription-derived Config %s (%d bytes)", target.name, len(content))


# The failure classes of the refresh pipeline (each its own exception),
# shared by the manual-refresh endpoint and the scheduler so both dispatch on
# one tuple instead of re-listing the classes. `RenderFailed` is the
# `render-failed` class (ADR-0004): the Source Config could not be rendered,
# which covers malformed YAML and an unrenderable document shape.
REFRESH_FAILURES = (
    SubscriptionFetchError,
    RenderFailed,
    RenderedConfigInvalid,
    KernelRejected,
    HotApplyError,
)

# The failure class of a failed refresh (ticket #27): the named class travels
# with the raw error text from the shared dispatch below to the panel, so a
# self-hoster can tell a provider outage from a render or Kernel refusal
# without reading English output. Unparseable YAML is its own class
# (`render-failed`): the render pipeline's key-merge stage parses the bytes
# before the Kernel ever sees a document.
FAILURE_CLASS_NETWORK = "network"
FAILURE_CLASS_RENDER_FAILED = "render-failed"
FAILURE_CLASS_KERNEL_REJECTED = "kernel-rejected"
FAILURE_CLASS_HOT_APPLY_REFUSED = "hot-apply-refused"


def failure_class_for(exc: Exception) -> str | None:
    """The failure class of a refresh failure: the named class of the four
    pipeline failures, or None for a failure outside the pipeline (e.g. a
    vanished Config registration) — such a record keeps its raw text and
    claims no class."""
    if isinstance(exc, SubscriptionFetchError):
        return FAILURE_CLASS_NETWORK
    if isinstance(exc, (RenderFailed, RenderedConfigInvalid)):
        # An unrenderable source and a render with nothing to proxy are both
        # "the fetched bytes could not become a usable Config".
        return FAILURE_CLASS_RENDER_FAILED
    if isinstance(exc, KernelRejected):
        return FAILURE_CLASS_KERNEL_REJECTED
    if isinstance(exc, HotApplyError):
        return FAILURE_CLASS_HOT_APPLY_REFUSED
    return None


def mark_subscription_refresh_failed(store: MetadataStore, name: str, exc: Exception) -> str:
    """One shared dispatch of a refresh failure onto the store: mark the
    Subscription failed with the failure reason — the Kernel's own output for
    KernelRejected, the exception text otherwise — and with the failure class
    derived from the exception type. Both writers (the manual-refresh endpoint
    and the scheduler) go through here, so the same failure reports the same
    class either way. Returns the reason for the caller to surface (HTTP
    detail or log line)."""
    reason = exc.output if isinstance(exc, KernelRejected) else str(exc)
    store.mark_subscription_failed(name, reason, failure_class_for(exc))
    return reason
