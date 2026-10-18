"""Runtime Dashboard (metacubexd) surface: the `/ui/` deploy-time config.

The pinned static bundle (see scripts/fetch-ui.sh) reads its deploy config
from `window.__METACUBEXD_CONFIG__` in a `config.js` it fetches relative to
the page (`/ui/config.js`); in a static deploy that is the only config
channel. The panel serves that one file itself (route registered before the
`/ui` static mount), so the config follows the code instead of being patched
into a downloaded bundle.
"""

from fastapi import APIRouter
from fastapi.responses import RedirectResponse, Response

router = APIRouter()

# v1.273.1 fields (bundle root config.js): `defaultBackendURL` presets the
# Controller endpoint — the bundle probes `<endpoint>/version` on first load,
# so the user never types one — and `githubToken` (GitHub API, unrelated to
# embedding) stays empty. `window.location.host + '/api'` keeps the endpoint
# same-origin: the bundle prefixes a scheme to scheme-less values, so a bare
# '/api' would be misparsed as host `api`, while the host form tracks the
# page's own origin (REST and WebSocket alike). No credential is served: the
# endpoint stays secretless and clashflow.apiproxy injects the Kernel secret
# server-side. Omitting `controlToken` (the upstream all-in-one server's agent
# credential) keeps the dashboard in static mode: its
# `GET /api/control/info` probe fails, hiding profile management.
DEPLOY_CONFIG = """\
// ClashFlow: embedded Runtime Dashboard reaching the Kernel through the
// panel's own /api reverse proxy (secret injected server-side).
window.__METACUBEXD_CONFIG__ = {
  defaultBackendURL: window.location.host + '/api',
  githubToken: '',
};
"""

# Same rationale as upstream's all-in-one server: the deploy config must never
# be served stale from a cache (the PWA service worker also skips precaching
# config.js for the same reason).
_CONFIG_HEADERS = {"Cache-Control": "no-store"}


@router.get("/ui")
async def runtime_dashboard_entry() -> RedirectResponse:
    """A mount at /ui only matches below the slash; redirect the bare path so
    it never falls through to the Management Panel's static mount."""
    return RedirectResponse(url="/ui/", status_code=307)


@router.get("/ui/config.js")
async def runtime_dashboard_config() -> Response:
    return Response(
        content=DEPLOY_CONFIG, media_type="application/javascript", headers=_CONFIG_HEADERS
    )
