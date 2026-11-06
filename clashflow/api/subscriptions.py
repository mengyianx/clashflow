"""Subscription surface: CRUD + manual refresh under `/api/panel/subscriptions`
(same panel-routing convention as clashflow.api/configs: Management Panel
paths live under `/api/panel/...`, registered before the Controller reverse
proxy in clashflow/main.py).

Link (issue #41): a Subscription names the Source Config it owns in
`subscriptions.config_name` — an explicit column, no longer "the two names
are equal". Adding a Subscription creates both records and the link in one
step; refresh, delete and the Active check read the link, so a Config is
found by its owner rather than by convention. A Subscription and its Config
are therefore created, refreshed and deleted as a pair — a registered
Subscription always has exactly one Config, unless a legacy record's Config
had already vanished before the v2 migration.

Failure classes map to status codes like the save pipeline: a fetch failure
-> 502, a render-stage or Kernel config-test refusal -> 400 (the render
message begins with the stage name; the Kernel's output is verbatim). Adding
fetches through the same pipeline and rolls both records back on any failure,
so a failed add creates nothing. A failed refresh keeps the previous Source
Config and Runtime Config bytes byte-identical and marks the Subscription with
the reason (`last_error`) and its failure class (`last_error_class`, ticket
#27) for the next scheduled retry (#13)."""

import logging
from pathlib import Path
from typing import cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from clashflow.apply import KernelRejected, RenderedConfigInvalid
from clashflow.db import (
    SUBSCRIPTION_DERIVED_KIND,
    ConfigAlreadyExists,
    ConfigIsActive,
    ConfigNotFound,
    MetadataStore,
    SubscriptionAlreadyExists,
    SubscriptionNotFound,
    config_filename,
    runtime_config_path,
    validate_config_name,
)
from clashflow.kernel import KernelManager
from clashflow.refresh import (
    REFRESH_FAILURES,
    SubscriptionFetchError,
    mark_subscription_refresh_failed,
    refresh_subscription,
)
from clashflow.render import RenderFailed

log = logging.getLogger(__name__)

router = APIRouter()

type SubscriptionEntry = dict[str, str | int | None]


class NewSubscription(BaseModel):
    name: str
    url: str


class SubscriptionSettings(BaseModel):
    # Fetch settings (ticket #13), partial-update semantics on PATCH: a
    # field ABSENT from the request keeps its stored value; an explicit null
    # clears the column back to the documented default fallback.
    user_agent: str | None = None
    update_interval_minutes: int | None = None


def _validate_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"subscription URL must be http(s): {url!r}")
    return url


def _validate_user_agent(user_agent: str) -> str:
    """A custom UA must survive the HTTP round trip: non-empty once
    trimmed, no control characters (CR/LF would break the header line).
    The stored value is the trimmed one."""
    stripped = user_agent.strip()
    if not stripped:
        raise ValueError("user agent must not be empty")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in stripped):
        raise ValueError(f"user agent must not contain control characters: {user_agent!r}")
    return stripped


def _source_config_name(entry: SubscriptionEntry) -> str:
    """The Source Config this Subscription links to (issue #41). A record
    without a link has no source to refresh; the panel answers 404 instead of
    guessing a name."""
    config_name = entry.get("config_name")
    if not isinstance(config_name, str):
        raise HTTPException(
            status_code=404,
            detail=f"subscription has no source config: {entry.get('name')}",
        )
    return config_name


@router.post("/api/panel/subscriptions", status_code=201)
async def add_subscription(request: Request, body: NewSubscription) -> SubscriptionEntry:
    """Add a Subscription by URL: fetch it, register the pair (Subscription +
    its Subscription-derived Config), and store the fetched bytes verbatim
    through the apply pipeline. ANY failure rolls both records back and
    creates nothing."""
    store: MetadataStore = request.app.state.store
    configs_dir: Path = request.app.state.configs_dir
    kernel: KernelManager = request.app.state.kernel
    try:
        validate_config_name(body.name)
        _validate_url(body.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    target = configs_dir / config_filename(body.name)
    if target.exists():
        # A stale file under a free name: refuse instead of clobbering.
        raise HTTPException(status_code=409, detail=f"config file already exists: {target.name}")
    try:
        # The panel chooses the new Config's name here; the record keeps it in
        # the explicit link (`config_name`), which every later read uses.
        store.create_subscription(body.name, body.url, body.name)
    except SubscriptionAlreadyExists as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        store.create_config(body.name, SUBSCRIPTION_DERIVED_KIND)
    except (ConfigAlreadyExists, ValueError) as exc:
        store.remove_subscription(body.name)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        result = await refresh_subscription(
            kernel,
            request.app.state.api_client,
            request.app.state.secret,
            body.url,
            target,
            apply_when_active=False,
            overlay=store.get_overlay(),
        )
    except SubscriptionFetchError as exc:
        _rollback_pair(store, configs_dir, body.name)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except RenderFailed as exc:
        _rollback_pair(store, configs_dir, body.name)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RenderedConfigInvalid as exc:
        _rollback_pair(store, configs_dir, body.name)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KernelRejected as exc:
        _rollback_pair(store, configs_dir, body.name)
        raise HTTPException(status_code=400, detail=exc.output) from exc
    store.mark_subscription_refreshed(body.name)
    # Issue #43: the summary of the bytes the Kernel accepted.
    store.set_config_summary(body.name, result.summary.as_dict())
    log.info("added subscription %s -> %s", body.name, target.name)
    return store.get_subscription(body.name)


@router.get("/api/panel/subscriptions")
async def list_subscriptions(request: Request) -> list[SubscriptionEntry]:
    """The Subscriptions view: state, last refresh time, error reason and the
    failure class of the last failed refresh (ticket #27; None when there is
    no class to show)."""
    return request.app.state.store.list_subscriptions()


@router.post("/api/panel/subscriptions/{name}/refresh")
async def refresh_subscription_endpoint(request: Request, name: str) -> SubscriptionEntry:
    """Manual refresh: re-run the fetch pipeline for this Subscription's
    Config only (hot-applying it when Active). On failure the previous Config
    stays byte-identical and the Subscription is marked with the reason."""
    store: MetadataStore = request.app.state.store
    kernel: KernelManager = request.app.state.kernel
    try:
        entry = store.get_subscription(name)
    except SubscriptionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    config_name = _source_config_name(entry)
    try:
        active = store.get_config(config_name)["active"]
    except ConfigNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    target = request.app.state.configs_dir / config_filename(config_name)
    url = cast("str", entry["url"])  # the url column is NOT NULL
    user_agent = cast("str | None", entry["user_agent"])
    try:
        result = await refresh_subscription(
            kernel,
            request.app.state.api_client,
            request.app.state.secret,
            url,
            target,
            apply_when_active=bool(active),
            overlay=store.get_overlay(),
            user_agent=user_agent,
        )
    except REFRESH_FAILURES as exc:
        detail = mark_subscription_refresh_failed(store, name, exc)
        # Render and Kernel refusals are content problems and map to 400;
        # network and hot-apply refusals map to 502.
        status = (
            400 if isinstance(exc, (RenderFailed, RenderedConfigInvalid, KernelRejected)) else 502
        )
        raise HTTPException(status_code=status, detail=detail) from exc
    store.mark_subscription_refreshed(name)
    store.set_config_summary(config_name, result.summary.as_dict())
    log.info("refreshed subscription %s", name)
    return store.get_subscription(name)


@router.patch("/api/panel/subscriptions/{name}")
async def update_subscription_settings(
    request: Request, name: str, body: SubscriptionSettings
) -> SubscriptionEntry:
    """Fetch settings (ticket #13): partial update of the per-Subscription
    `user_agent` (sent on every fetch of this Subscription; null clears it
    back to the module default) and `update_interval_minutes` (positive
    minutes; the per-Subscription override of the global default interval
    for #16's scheduler; null clears the override). Fields absent from the
    request keep their stored value."""
    store: MetadataStore = request.app.state.store
    try:
        current = store.get_subscription(name)
    except SubscriptionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        if "user_agent" in body.model_fields_set:
            user_agent = (
                _validate_user_agent(body.user_agent) if body.user_agent is not None else None
            )
        else:
            user_agent = cast("str | None", current["user_agent"])
        if "update_interval_minutes" in body.model_fields_set:
            if body.update_interval_minutes is not None and body.update_interval_minutes < 1:
                raise ValueError(
                    "update interval must be a positive number of minutes: "
                    f"{body.update_interval_minutes}"
                )
            update_interval_minutes = body.update_interval_minutes
        else:
            update_interval_minutes = cast("int | None", current["update_interval_minutes"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    store.set_subscription_fetch_settings(name, user_agent, update_interval_minutes)
    log.info(
        "updated subscription fetch settings %s (ua=%r interval=%r)",
        name,
        user_agent,
        update_interval_minutes,
    )
    return store.get_subscription(name)


@router.delete("/api/panel/subscriptions/{name}")
async def delete_subscription(request: Request, name: str) -> dict[str, str]:
    """Delete the Subscription together with the Source Config it links to
    (issue #41: the link decides which Config, never the two names being
    equal); an Active Config is refused, like Config deletion.

    The Active check runs BEFORE anything is removed, so a refusal leaves the
    pair intact. The Subscription row then goes first, which drops the link
    and lets the shared Config deletion path run without tripping over it."""
    store: MetadataStore = request.app.state.store
    configs_dir: Path = request.app.state.configs_dir
    try:
        entry = store.get_subscription(name)
    except SubscriptionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    config_name = entry.get("config_name")
    if not isinstance(config_name, str):
        # A legacy record whose same-name Config had already vanished: there
        # is nothing to delete but the Subscription itself.
        store.remove_subscription(name)
        log.info("deleted subscription %s (no linked config)", name)
        return {"deleted": name}
    if store.get_active() == config_name:
        raise HTTPException(status_code=409, detail=str(ConfigIsActive(config_name)))
    store.remove_subscription(name)
    try:
        store.remove_config(config_name)
    except ConfigNotFound:
        # The registration is gone but the files may not be; the unlinks
        # below still run so no orphan artifact survives the delete.
        log.warning("subscription %s linked a config that was not registered", name)
    (configs_dir / config_filename(config_name)).unlink(missing_ok=True)
    runtime_config_path(configs_dir.parent, config_name).unlink(missing_ok=True)
    log.info("deleted subscription %s and its config %s", name, config_name)
    return {"deleted": name}


def _rollback_pair(store: MetadataStore, configs_dir: Path, name: str) -> None:
    """Undo a half-created Subscription pair: both registrations and any file
    the pipeline managed to write. The Subscription goes first so its link
    does not block the Config deletion."""
    store.remove_subscription(name)
    try:
        store.remove_config(name)
    except ConfigNotFound:
        pass
    (configs_dir / config_filename(name)).unlink(missing_ok=True)
    runtime_config_path(configs_dir.parent, name).unlink(missing_ok=True)
