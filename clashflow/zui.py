"""Runtime Dashboard #2: the zashboard bundle served under `/zui/`.

zashboard is hash-routed (`#/proxies`, `#/setup?...`) and references its assets
relatively (`./assets/...`), so serving it from a subpath needs no server-side
history fallback — the same subpath contract metacubexd satisfies under `/ui/`.

The difference is endpoint discovery. metacubexd reads a deploy config we serve
(`/ui/config.js`); zashboard keeps backend endpoints in browser storage and, on
a page with no hash, probes the page origin (`http://host:8080/`) — not the
`/api` reverse proxy, so the probe fails. The entry document is therefore served
with one injected bootstrap script: on a hash-less load it forwards to
`#/setup` with the panel proxy as the backend (`secondaryPath=/api`) and hides
the two upstream switches v1 rejects by policy — core upgrade (the Kernel
version is pinned to the image) and TUN (not part of v1). The Kernel secret is
never involved: `/api` injects it server-side (#14).
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

log = logging.getLogger(__name__)

router = APIRouter()

# `location.port` is empty for default ports; zashboard wants an explicit port.
BOOTSTRAP = """\
<script>
(function () {
  if (location.hash) return;
  var port = location.port || (location.protocol === "https:" ? "443" : "80");
  var params = new URLSearchParams({
    hostname: location.hostname,
    port: port,
    secondaryPath: "/api",
    disableUpgradeCore: "1",
    disableTunMode: "1",
  });
  location.replace("#/setup?" + params.toString());
})();
</script>
"""


@router.get("/zui")
async def zashboard_entry() -> RedirectResponse:
    """A mount at /zui only matches below the slash; redirect the bare path so
    it never falls through to the Management Panel's static mount."""
    return RedirectResponse(url="/zui/", status_code=307)


@router.get("/zui/")
async def zashboard_index(request: Request) -> HTMLResponse:
    """The bundle's entry document with the endpoint bootstrap injected.

    Served by the panel rather than the static mount so a direct visit (a
    bookmark, no hash) still lands on a connected dashboard.
    """
    dist = request.app.state.zui_dist
    index = dist / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404, detail=f"zashboard bundle missing: {index}")
    html = index.read_text(encoding="utf-8")
    return HTMLResponse(html.replace("</head>", BOOTSTRAP + "</head>", 1))
