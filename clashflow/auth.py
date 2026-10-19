"""Panel authentication: password check, server-side sessions, auth middleware.

Mechanism (ticket #17): the panel password comes from the `CLASHFLOW_PASSWORD`
environment variable (REQUIRED — the app refuses to start without it). A
successful `POST /api/panel/auth/login` mints a 256-bit random session token
(`secrets.token_urlsafe(32)`) held SERVER-SIDE in an in-memory registry
(token -> expiry); the browser only ever holds the token in an HttpOnly,
SameSite=Lax cookie. The cookie is therefore not forgeable without guessing
256 bits of entropy, and logout REVOKES the token server-side — a replayed
pre-logout cookie is rejected, not merely a cleared browser jar. Sessions
live in memory: a panel restart ends every session (single-process v1).

Enforcement is a pure-ASGI middleware, not `@app.middleware("http")`: the
HTTP-middleware helper skips non-HTTP scopes entirely, so WebSocket upgrades
through the `/api/*` reverse proxy would bypass it. The middleware guards
both `http` and `websocket` scopes; an unauthenticated upgrade is denied
with WebSocket close code 4401 (per the ASGI spec, close-before-accept
refuses the handshake).

Protected surfaces: everything under `/api` (panel CRUD, service control and
the Controller reverse proxy, REST + WebSocket) and `/ui` (Runtime Dashboard
bundle). Public by design: `GET /healthz` (container liveness — the panel
process answering HTTP, independent of Kernel state), `POST
/api/panel/auth/login`, and the Management Panel's own static shell under `/`
(the login page itself must load before a session exists; it carries no data).
"""

import hashlib
import hmac
import secrets
import time

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from starlette.types import ASGIApp, Receive, Scope, Send

SESSION_COOKIE = "clashflow_session"
SESSION_MAX_AGE = 7 * 24 * 60 * 60  # seconds; cookie and server-side registry agree
WS_UNAUTHORIZED_CODE = 4401

router = APIRouter()


class SessionRegistry:
    """Server-side session store: token -> expiry epoch. Lookup is by exact
    256-bit random token, so a forged cookie can never authenticate."""

    def __init__(self) -> None:
        self._sessions: dict[str, float] = {}

    def create(self) -> str:
        token = secrets.token_urlsafe(32)
        self._sessions[token] = time.time() + SESSION_MAX_AGE
        return token

    def validate(self, token: str) -> bool:
        expiry = self._sessions.get(token)
        if expiry is None:
            return False
        if expiry <= time.time():
            del self._sessions[token]
            return False
        return True

    def revoke(self, token: str) -> None:
        self._sessions.pop(token, None)


def _password_matches(candidate: str, expected: str) -> bool:
    """Constant-time comparison over SHA-256 digests (length-safe)."""
    return hmac.compare_digest(
        hashlib.sha256(candidate.encode()).digest(),
        hashlib.sha256(expected.encode()).digest(),
    )


def _cookie_token(headers: list[tuple[bytes, bytes]]) -> str | None:
    for name, value in headers:
        if name == b"cookie":
            for part in value.decode("latin-1").split(";"):
                key, _, raw = part.strip().partition("=")
                if key == SESSION_COOKIE and raw:
                    return raw
    return None


def _is_public(path: str, method: str) -> bool:
    if path == "/healthz":
        return True
    if path == "/api/panel/auth/login" and method == "POST":
        return True
    # The Management Panel's static shell under `/` carries no data and hosts
    # the login page; every data-bearing surface lives under /api or /ui.
    return not path.startswith(("/api", "/ui"))


class AuthMiddleware:
    """Pure-ASGI guard for both http and websocket scopes (see module docstring)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        method = scope.get("method", "GET")
        if not _is_public(path, method):
            registry: SessionRegistry = scope["app"].state.sessions
            token = _cookie_token(list(scope.get("headers", [])))
            if token is None or not registry.validate(token):
                await self._deny(scope, receive, send)
                return
        await self.app(scope, receive, send)

    async def _deny(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            # Close before accept = handshake refused (ASGI spec).
            await send({"type": "websocket.close", "code": WS_UNAUTHORIZED_CODE})
            return
        accept = dict(scope.get("headers", [])).get(b"accept", b"")
        if scope["path"].startswith("/ui") and scope["method"] == "GET" and b"text/html" in accept:
            response: Response = RedirectResponse("/", status_code=307)
        else:
            response = JSONResponse({"detail": "not authenticated"}, status_code=401)
        await response(scope, receive, send)


class LoginBody(BaseModel):
    password: str


@router.post("/api/panel/auth/login")
async def login(body: LoginBody, request: Request, response: Response) -> dict[str, bool]:
    if not _password_matches(body.password, request.app.state.panel_password):
        raise HTTPException(status_code=401, detail="invalid password")
    token = request.app.state.sessions.create()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return {"authenticated": True}


@router.post("/api/panel/auth/logout")
async def logout(request: Request, response: Response) -> dict[str, bool]:
    """Revoke the session server-side; a replayed pre-logout cookie is dead."""
    token = request.cookies.get(SESSION_COOKIE)
    if token is not None:
        request.app.state.sessions.revoke(token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"authenticated": False}


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Unauthenticated panel liveness for the container HEALTHCHECK: the panel
    process serves HTTP. Kernel health stays behind auth on
    /api/service/health and never gates this."""
    return {"status": "ok"}
