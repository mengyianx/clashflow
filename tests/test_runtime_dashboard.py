"""#15 seam tests: the Runtime Dashboard (metacubexd) is embedded under `/ui/`,
preconfigured to reach the Kernel through the panel's own `/api` reverse proxy,
with no credential served to the browser and profile management hidden in
static mode."""

import re
from pathlib import Path

from conftest import BootApp


async def test_ui_loads_the_runtime_dashboard_preset_to_the_panel_proxy_without_a_secret(
    boot_app: BootApp, metacubexd_dist: Path
) -> None:
    async with boot_app(ui_dist=metacubexd_dist) as panel:
        page = await panel.client.get("/ui/")
        assert page.status_code == 200
        # The pinned bundle's entry document (hash-routed Nuxt build; assets are
        # referenced relatively, so serving under a /ui/ subpath needs no
        # server-side history fallback).
        assert "window.__METACUBEXD_CONFIG__" in page.text
        assert "./_nuxt/" in page.text

        # The bare path redirects into the dashboard instead of falling through
        # to the Management Panel's static mount.
        entry = await panel.client.get("/ui")
        assert entry.status_code == 307
        assert entry.headers["location"] == "/ui/"

        config = await panel.client.get("/ui/config.js")
        assert config.status_code == 200
        # Deploy-time config presets the Controller endpoint, so the user never
        # types one (the bundle probes `<endpoint>/version` on first load).
        assert "defaultBackendURL" in config.text
        assert "'/api'" in config.text
        # No credential is served: the Kernel secret stays server-side (#14's
        # proxy injects it) and the upstream agent token field is absent.
        assert panel.app.state.secret not in config.text
        assert re.search(r"githubToken:\s*''", config.text)
        assert "controlToken" not in config.text


async def test_runtime_dashboard_endpoints_resolve_through_the_panel_proxy(
    boot_app: BootApp, metacubexd_dist: Path
) -> None:
    """Wiring only: the base preset in the *served* deploy config resolves
    through the panel proxy. Full REST route and WebSocket coverage of the
    proxy itself is #14's tests/test_api_proxy.py and is not duplicated."""
    async with boot_app(ui_dist=metacubexd_dist) as panel:
        config = await panel.client.get("/ui/config.js")
        match = re.search(r"window\.location\.host \+ '([^']+)'", config.text)
        assert match is not None
        base = match.group(1)
        assert base == "/api"

        # The dashboard's connect probe (v1.273.1: GET <endpoint>/version).
        version = await panel.client.get(f"{base}/version")
        assert version.status_code == 200
        assert "version" in version.json()


async def test_profile_management_stays_hidden_in_static_mode(
    boot_app: BootApp, metacubexd_dist: Path
) -> None:
    """The upstream metacubexd dashboard gates its Profile management UI (an
    upstream metacubexd feature; and Control Center) on
    `hasFeature('profiles') = hasAgent && features.includes('profiles')`, where
    `hasAgent` comes from `GET <page origin>/api/control/info` — any failure
    (v1.273.1 useControlInfo: catch -> hasAgent=false, features=[]) hides the
    entries. In this static embed the served deploy config carries no
    `controlToken` (the upstream all-in-one server's agent credential) and the
    probe crosses the panel proxy to the Kernel Controller, which has no
    `/control/info` route — so the probe fails and the UI stays hidden."""
    async with boot_app(ui_dist=metacubexd_dist) as panel:
        config = await panel.client.get("/ui/config.js")
        assert "controlToken" not in config.text

        probe = await panel.client.get("/api/control/info")
        assert probe.status_code == 404
        assert "hasAgent" not in probe.text


async def test_deploy_endpoint_is_same_origin_not_a_build_time_absolute_url(
    boot_app: BootApp, metacubexd_dist: Path
) -> None:
    """Endpoint decision: `window.location.host + '/api'` — the closest working
    form of "relative" (the bundle's URL transform prefixes a scheme, so a bare
    '/api' becomes `http:///api` and is misparsed as host `api`). The endpoint
    follows the page origin at runtime; REST and WebSocket therefore stay
    same-origin and secret injection stays server-side. Documented fallback
    (plan M4 / §8.1): if same-origin WebSocket fails in practice, the build
    rewrites the served `defaultBackendURL` to an absolute
    `http://<panel-host>:8080/api`."""
    async with boot_app(ui_dist=metacubexd_dist) as panel:
        config = await panel.client.get("/ui/config.js")
        assert "window.location.host + '/api'" in config.text
        # No scheme is baked in: the endpoint was not injected at build time.
        assert "http://" not in config.text
        assert "https://" not in config.text
