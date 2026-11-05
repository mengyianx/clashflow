"""Reverse proxy from the panel's `/api/*` surface to the loopback Controller.

Routing convention (later tickets follow this):
- Panel API routers are included BEFORE `router` in ``main.py``; Starlette
  matches routes in registration order, so an explicitly registered panel route
  always shadows the proxy at that exact path.
- Every other `/api/...` path is forwarded verbatim to the Controller, so
  `/api/version`, `/api/configs`, `/api/proxies`, ... (everything the Runtime
  Dashboard drives) belong to the Controller. New Management Panel routes
  therefore live under `/api/panel/...`, which the Controller does not define;
  the grandfathered exception is `/api/service/health`.
- Three Controller endpoints are never forwarded (ticket #25): restarting or
  upgrading the Kernel and writing the Controller's Config path belong to the
  Management Panel alone. They are refused at this one decision point, before
  any upstream request is made, so a denied endpoint cannot half-apply.
- The Bearer secret is injected server-side on every upstream request and
  WebSocket handshake; browser-supplied credentials (`Authorization` header and
  `token` query parameter) are discarded, so no browser traffic carries the
  secret.
"""

import asyncio
import logging
from urllib.parse import urlencode

import httpx
import websockets
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from starlette.datastructures import QueryParams
from starlette.websockets import WebSocket
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import InvalidHandshake

from clashflow.kernel import CONTROLLER_ADDR

log = logging.getLogger(__name__)

CONTROLLER_URL = f"http://{CONTROLLER_ADDR}"
CONTROLLER_WS_URL = f"ws://{CONTROLLER_ADDR}"

router = APIRouter()

REST_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

# The deny list (ticket #25): the Management Panel is the only path to these
# Controller operations, so the proxy refuses them instead of forwarding them.
# The key is the path the Controller sees (no surrounding slashes), the value
# the methods that are refused on it. `PUT /configs` is the Controller's Config
# write (its forced reload takes the Config path in the body); `PATCH /configs`
# is the mode switch both Runtime Dashboards drive and stays available.
DENIED_ENDPOINTS: dict[str, frozenset[str]] = {
    "restart": frozenset({"POST"}),
    "upgrade": frozenset({"POST"}),
    "configs": frozenset({"PUT"}),
}

# The refusal is panel-authored, not the Kernel's, and names the endpoint it
# refuses: a client can tell "not exposed" from "upstream broken" (502).
DENIED_STATUS = 403
# RFC 6455 §7.4.1: the endpoint is refused by policy. A failed upstream
# handshake is a different close code (1011, see proxy_websocket).
DENIED_WS_CODE = 1008

# Hop-by-hop headers (RFC 9110 §7.6.1) plus framing/identity headers httpx
# re-computes; `authorization` is dropped on the way in (replaced with the
# injected secret) and never relayed back out.
_DROP_REQUEST_HEADERS = {
    "authorization",
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
_DROP_RESPONSE_HEADERS = _DROP_REQUEST_HEADERS | {"content-encoding"}


def _browser_query(query: QueryParams) -> list[tuple[str, str]]:
    """Forward the browser's query minus any `token` (credentials never travel
    browser-side; the upstream handshake carries the injected header instead)."""
    return [(key, value) for key, value in query.multi_items() if key != "token"]


def _controller_path(path: str) -> str:
    """The path the Controller sees: no surrounding slashes."""
    return path.strip("/")


def _denied(method: str | None, path: str) -> bool:
    """The one decision point of the deny list: True when the request is
    refused. `method` is None for the WebSocket route, which carries no method
    of its own and therefore refuses every denied path."""
    refused = DENIED_ENDPOINTS.get(_controller_path(path))
    if refused is None:
        return False
    return method is None or method.upper() in refused


def _refusal(method: str | None, path: str) -> str:
    """The panel-authored refusal, naming the endpoint it refuses."""
    endpoint = f"/api/{_controller_path(path)}"
    if method is None:
        return f"endpoint not exposed by the Management Panel: {endpoint}"
    return f"endpoint not exposed by the Management Panel: {method} {endpoint}"


@router.api_route("/api/{path:path}", methods=REST_METHODS)
async def proxy_rest(request: Request, path: str) -> Response:
    if _denied(request.method, path):
        log.info("refused %s /api/%s at the proxy", request.method, path)
        return JSONResponse({"detail": _refusal(request.method, path)}, status_code=DENIED_STATUS)
    headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() not in _DROP_REQUEST_HEADERS
    }
    headers["Authorization"] = f"Bearer {request.app.state.secret}"
    try:
        upstream = await request.app.state.api_client.request(
            request.method,
            f"{CONTROLLER_URL}/{path}",
            params=_browser_query(request.query_params),
            headers=headers,
            content=await request.body(),
        )
    except httpx.HTTPError:
        log.warning("controller unreachable for %s /api/%s", request.method, path)
        return JSONResponse({"message": "kernel unreachable"}, status_code=502)
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers={
            name: value
            for name, value in upstream.headers.items()
            if name.lower() not in _DROP_RESPONSE_HEADERS
        },
    )


@router.websocket("/api/{path:path}")
async def proxy_websocket(websocket: WebSocket, path: str) -> None:
    if _denied(None, path):
        log.info("refused websocket /api/%s at the proxy", path)
        await websocket.close(code=DENIED_WS_CODE, reason=_refusal(None, path))
        return
    secret = websocket.app.state.secret
    url = CONTROLLER_WS_URL + "/" + path
    query = _browser_query(websocket.query_params)
    if query:
        url += "?" + urlencode(query)
    try:
        controller = await websockets.connect(
            url,
            additional_headers={"Authorization": f"Bearer {secret}"},
            open_timeout=5,
        )
    except (OSError, TimeoutError, InvalidHandshake) as exc:
        log.warning("controller websocket handshake failed for /api/%s: %s", path, exc)
        await websocket.close(code=1011)
        return
    await websocket.accept()
    try:
        await _relay(websocket, controller)
    finally:
        await controller.close()


async def _relay(browser: WebSocket, controller: ClientConnection) -> None:
    """Pump frames both ways; finish when either side stops."""

    async def browser_to_controller() -> None:
        while True:
            message = await browser.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("text") is not None:
                await controller.send(message["text"])
            elif message.get("bytes") is not None:
                await controller.send(message["bytes"])

    async def controller_to_browser() -> None:
        async for message in controller:
            if isinstance(message, str):
                await browser.send_text(message)
            else:
                await browser.send_bytes(message)

    pumps: set[asyncio.Task[None]] = {
        asyncio.create_task(browser_to_controller()),
        asyncio.create_task(controller_to_browser()),
    }
    try:
        await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for pump in pumps:
            pump.cancel()
        await asyncio.gather(*pumps, return_exceptions=True)
    # Surface the Controller connection's stream end to the browser; the
    # browser may have disconnected first, in which case there is nothing
    # left to close.
    try:
        await browser.close(code=1011)
    except RuntimeError:
        pass
