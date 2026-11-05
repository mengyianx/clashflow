"""A static mount whose root can change at runtime.

The Runtime Dashboards live under `/data/runtime/webui/<name>/current`, a
symlink the update manager repoints after installing a new bundle. A plain
`StaticFiles` captures its directory once, at construction, so it would keep
serving the version it was mounted with. This wrapper resolves the directory
per request and caches one `StaticFiles` app per distinct root — updates take
effect immediately, with no restart.
"""

from collections.abc import Callable
from pathlib import Path

from starlette.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send


class SwitchingStaticFiles:
    def __init__(self, resolver: Callable[[], Path], name: str) -> None:
        self._resolver = resolver
        self._name = name
        self._apps: dict[str, StaticFiles] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        root = str(self._resolver())
        app = self._apps.get(root)
        if app is None:
            app = StaticFiles(directory=root, html=True, check_dir=False)
            self._apps[root] = app
        await app(scope, receive, send)
