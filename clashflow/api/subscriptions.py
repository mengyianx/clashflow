"""Subscription surface: CRUD + manual refresh under `/api/panel/subscriptions`
(same panel-routing convention as clashflow.api/configs: Management Panel
paths live under `/api/panel/...`, registered before the Controller reverse
proxy in clashflow/main.py).

Name mapping (plan §3): a Subscription's name IS its Subscription-derived
Config's name; the server assigns `<name>.yaml` under `/data/configs`
(db.config_filename) and registers it with kind=subscription-derived. A
Subscription and its Config are therefore created, refreshed and deleted as
a pair — a registered Subscription always has exactly one Config.

Failure classes map to status codes like the save pipeline: a fetch failure
-> 502, a Kernel config-test rejection -> 400 with the Kernel's output
verbatim. Adding fetches through the same pipeline and rolls both records
back on any failure, so a failed add creates nothing. A failed refresh keeps
the previous Config file byte-identical and marks the Subscription with the
reason (`last_error`) for the next scheduled retry (#13).
"""

import logging
from pathlib import Path
from typing import cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from clashflow.apply import HotApplyError, KernelRejected
from clashflow.db import (
    ConfigAlreadyExists,
    ConfigIsActive,
    ConfigNotFound,
    MetadataStore,
    SubscriptionAlreadyExists,
    SubscriptionNotFound,
    config_filename,
    validate_config_name,
)
from clashflow.kernel import KernelManager
from clashflow.refresh import SubscriptionFetchError, refresh_subscription

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


@router.post("/api/panel/subscriptions", status_code=201)
async def add_subscription(request: Request, body: NewSubscription) -> SubscriptionEntry:
    """Add a Subscription by URL: fetch it, register the pair (Subscription +
    `kind=subscription-derived` Config), and store the fetched bytes verbatim
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
        store.create_subscription(body.name, body.url)
    except SubscriptionAlreadyExists as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        store.create_config(body.name, "subscription-derived")
    except (ConfigAlreadyExists, ValueError) as exc:
        store.remove_subscription(body.name)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        await refresh_subscription(
            kernel,
            request.app.state.api_client,
            request.app.state.secret,
            body.url,
            target,
            apply_when_active=False,
        )
    except SubscriptionFetchError as exc:
        _rollback_pair(store, configs_dir, body.name)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except KernelRejected as exc:
        _rollback_pair(store, configs_dir, body.name)
        raise HTTPException(status_code=400, detail=exc.output) from exc
    store.mark_subscription_refreshed(body.name)
    log.info("added subscription %s -> %s", body.name, target.name)
    return store.get_subscription(body.name)


@router.get("/api/panel/subscriptions")
async def list_subscriptions(request: Request) -> list[SubscriptionEntry]:
    """The Subscriptions view: state, last refresh time and error reason."""
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
        active = store.get_config(name)["active"]
    except (SubscriptionNotFound, ConfigNotFound) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    target = request.app.state.configs_dir / config_filename(name)
    url = cast("str", entry["url"])  # the url column is NOT NULL
    user_agent = cast("str | None", entry["user_agent"])
    try:
        await refresh_subscription(
            kernel,
            request.app.state.api_client,
            request.app.state.secret,
            url,
            target,
            apply_when_active=bool(active),
            user_agent=user_agent,
        )
    except SubscriptionFetchError as exc:
        store.mark_subscription_failed(name, str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except KernelRejected as exc:
        store.mark_subscription_failed(name, exc.output)
        raise HTTPException(status_code=400, detail=exc.output) from exc
    except HotApplyError as exc:
        store.mark_subscription_failed(name, str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    store.mark_subscription_refreshed(name)
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
    """Delete the Subscription together with its Subscription-derived Config
    (the pair); an Active Config is refused, like Config deletion."""
    store: MetadataStore = request.app.state.store
    configs_dir: Path = request.app.state.configs_dir
    try:
        store.get_subscription(name)
    except SubscriptionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        store.remove_config(name)
    except ConfigIsActive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConfigNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    (configs_dir / config_filename(name)).unlink(missing_ok=True)
    store.remove_subscription(name)
    log.info("deleted subscription %s and its config", name)
    return {"deleted": name}


def _rollback_pair(store: MetadataStore, configs_dir: Path, name: str) -> None:
    """Undo a half-created Subscription pair: both registrations and any file
    the pipeline managed to write."""
    store.remove_subscription(name)
    store.remove_config(name)
    (configs_dir / config_filename(name)).unlink(missing_ok=True)
