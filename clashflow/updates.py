"""Runtime updates for the three pinned artifacts — manual, OpenClash-shaped.

The image pins mihomo, metacubexd and zashboard for reproducibility; this module
lets the panel move any of them forward without a new image, the way OpenClash
does it: check the upstream GitHub release, download the asset, verify the
archive, stage it next to the running copy, swap atomically, and keep the
previous one for one-click rollback.

Where things live (all inside the data volume, so a rebuilt container keeps the
versions the user installed):

    /data/runtime/kernel/<version>/mihomo        installed Kernel binaries
    /data/runtime/kernel/current -> <version>    pointer the process manager runs
    /data/runtime/webui/<component>/<version>/   extracted Runtime Dashboard bundles
    /data/runtime/webui/<component>/current      pointer each static mount serves

Every pointer is a symlink replaced with `os.replace` (atomic, no window where
the path is missing). The panel's own metadata store records which version is
current and which one is the rollback target.

Only GitHub Releases "latest" is consulted. `CLASHFLOW_GITHUB_PROXY` prefixes
API and asset URLs for networks where GitHub is unreliable, mirroring
OpenClash's "GitHub address proxy" setting. Assets are checked against the
sha256 digest GitHub publishes when it is present, and every archive is opened
and its entry document validated before it can replace a working install.
"""

import asyncio
import gzip
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from clashflow.db import MetadataStore

log = logging.getLogger(__name__)

KERNEL = "kernel"
METACUBEXD = "metacubexd"
ZASHBOARD = "zashboard"
COMPONENTS = (KERNEL, METACUBEXD, ZASHBOARD)

REPOS = {
    KERNEL: "MetaCubeX/mihomo",
    METACUBEXD: "MetaCubeX/metacubexd",
    ZASHBOARD: "Zephyruso/zashboard",
}

# The asset each component installs. metacubexd and zashboard ship several
# flavour archives; these are the two the image itself pins.
UI_ASSET = {METACUBEXD: "compressed-dist.tgz", ZASHBOARD: "dist-no-fonts.zip"}

KERNEL_READY_TIMEOUT = 15.0
DOWNLOAD_TIMEOUT = 120.0
META_KEY = "updates.{component}"


class KernelControl(Protocol):
    """The slice of `KernelManager` an update needs (tests substitute a stub)."""

    def alive(self) -> bool: ...
    async def probe(self, secret: str) -> bool: ...
    def start(self, config_path: Path, secret: str) -> None: ...
    def stop(self) -> None: ...
    def active_config_path(self) -> Path: ...
    @property
    def binary(self) -> str: ...
    def use_binary(self, path: Path | str) -> None: ...


class UpdateError(Exception):
    """An update step failed; the message is shown verbatim in the panel."""


class UpdateBusy(UpdateError):
    """Another update is already running (one at a time, like OpenClash's lock)."""


@dataclass(frozen=True)
class Release:
    version: str
    assets: dict[str, str]
    digests: dict[str, str]


def kernel_arch() -> str:
    """Asset architecture token used by the mihomo release names."""
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "amd64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    if machine.startswith("armv7") or machine == "arm":
        return "armv7"
    raise UpdateError(f"unsupported architecture for kernel updates: {machine}")


def _meta_key(component: str) -> str:
    return META_KEY.format(component=component)


def _read_state(store: MetadataStore, component: str) -> dict[str, Any]:
    raw = store.get_meta(_meta_key(component))
    if not raw:
        return {}
    try:
        parsed: Any = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _write_state(store: MetadataStore, component: str, state: dict[str, Any]) -> None:
    store.set_meta(_meta_key(component), json.dumps(state))


class UpdateManager:
    """One update at a time; state lives in the metadata store and on disk."""

    def __init__(
        self,
        *,
        store: MetadataStore,
        kernel: KernelControl,
        data_dir: Path,
        secret: str,
        image_kernel: Path,
        image_ui: dict[str, Path],
        bundle_versions: dict[str, str],
        github_api: str = "https://api.github.com",
        github_proxy: str = "",
    ) -> None:
        self._store = store
        self._kernel = kernel
        self._secret = secret
        self._image_kernel = image_kernel
        self._image_ui = image_ui
        self._bundle_versions = bundle_versions
        self._api = github_api.rstrip("/")
        self._proxy = github_proxy
        self._root = data_dir / "runtime"
        self._locks = {component: asyncio.Lock() for component in COMPONENTS}
        # Checks queue behind each other only: a check in flight must not read as
        # "an update is already running" for someone pressing update.
        self._check_lock = asyncio.Lock()
        # Secrets/URLs a test may inject; unset means a fresh client per call.
        self._client: httpx.AsyncClient | None = None

    # ---------- paths ----------

    def kernel_pointer(self) -> Path:
        return self._root / "kernel" / "current"

    def ui_pointer(self, component: str) -> Path:
        return self._root / "webui" / component / "current"

    def kernel_binary(self) -> Path:
        """The binary the supervisor should run: the updated one, else the image's."""
        pointer = self.kernel_pointer()
        if pointer.exists():
            return pointer
        return self._image_kernel

    def ui_dist(self, component: str) -> Path:
        pointer = self.ui_pointer(component)
        if (pointer / "index.html").is_file():
            return pointer
        return self._image_ui[component]

    # ---------- state ----------

    def _current_kernel_version(self) -> tuple[str, str]:
        state = _read_state(self._store, KERNEL)
        current = state.get("current") or {}
        if current.get("version"):
            return str(current["version"]), str(current.get("source", "runtime"))
        try:
            output = subprocess.run(
                [str(self._image_kernel), "-v"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            ).stdout
        except OSError, subprocess.SubprocessError:
            output = ""
        match = re.search(r"v\d+\.\d+\.\d+", output)
        return (match.group(0) if match else "unknown"), "image"

    def _current_ui_version(self, component: str) -> tuple[str, str]:
        state = _read_state(self._store, component)
        current = state.get("current") or {}
        if current.get("version"):
            return str(current["version"]), str(current.get("source", "runtime"))
        return self._bundle_versions.get(component, "unknown"), "image"

    def state(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for component in COMPONENTS:
            state = _read_state(self._store, component)
            previous = state.get("previous") or {}
            if component == KERNEL:
                version, source = self._current_kernel_version()
            else:
                version, source = self._current_ui_version(component)
            rows.append(
                {
                    "component": component,
                    "current": version,
                    "source": source,
                    "previous": previous.get("version"),
                    "latest": state.get("latest"),
                    "checked_at": state.get("checked_at"),
                    "error": state.get("check_error"),
                }
            )
        return rows

    # ---------- checking ----------

    async def _client_for(self) -> httpx.AsyncClient:
        if self._client is None:
            # GitHub release assets answer 302 to objects.githubusercontent.com.
            self._client = httpx.AsyncClient(
                trust_env=False, timeout=DOWNLOAD_TIMEOUT, follow_redirects=True
            )
        return self._client

    def _url(self, url: str) -> str:
        return f"{self._proxy}{url}" if self._proxy else url

    async def latest_release(self, component: str) -> Release:
        client = await self._client_for()
        api = self._url(f"{self._api}/repos/{REPOS[component]}/releases/latest")
        try:
            response = await client.get(
                api, headers={"Accept": "application/vnd.github+json", "User-Agent": "ClashFlow"}
            )
        except httpx.HTTPError as exc:
            raise UpdateError(f"release check failed: {exc}") from exc
        if response.status_code == 404:
            raise UpdateError("release check failed: repository has no published release")
        if not response.is_success:
            raise UpdateError(f"release check failed: HTTP {response.status_code}")
        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise UpdateError("release check failed: response is not JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("tag_name"), str):
            raise UpdateError("release check failed: release has no tag")
        assets: dict[str, str] = {}
        digests: dict[str, str] = {}
        raw_assets = payload.get("assets")
        if isinstance(raw_assets, list):
            for asset in raw_assets:
                if not isinstance(asset, dict):
                    continue
                name = asset.get("name")
                url = asset.get("browser_download_url")
                if isinstance(name, str) and isinstance(url, str):
                    assets[name] = url
                    digest = asset.get("digest")
                    if isinstance(digest, str) and digest.startswith("sha256:"):
                        digests[name] = digest.removeprefix("sha256:")
        return Release(version=payload["tag_name"], assets=assets, digests=digests)

    async def check(self) -> list[dict[str, Any]]:
        async with self._check_lock:
            for component in COMPONENTS:
                latest: str | None = None
                error: str | None = None
                try:
                    release = await self.latest_release(component)
                except UpdateError as exc:
                    error = str(exc)
                else:
                    latest = release.version
                # Read-modify-write under the component's lock (no awaits inside)
                # so a snapshot cannot overwrite what a just-finished update wrote.
                async with self._locks[component]:
                    state = _read_state(self._store, component)
                    if latest is not None:
                        state["latest"] = latest
                    state["check_error"] = error
                    state["checked_at"] = _now()
                    _write_state(self._store, component, state)
        return self.state()

    # ---------- installing ----------

    async def update(self, component: str) -> list[dict[str, Any]]:
        if component not in COMPONENTS:
            raise UpdateError(f"unknown component: {component}")
        lock = self._locks[component]
        if lock.locked():
            raise UpdateBusy("an update is already running")
        async with lock:
            state = _read_state(self._store, component)
            release = await self.latest_release(component)
            if release.version == (state.get("current") or {}).get("version"):
                state["latest"] = release.version
                state["checked_at"] = _now()
                state["check_error"] = None
                _write_state(self._store, component, state)
                return self.state()
            if component == KERNEL:
                await self._install_kernel(release, state)
            else:
                await self._install_ui(component, release, state)
        return self.state()

    async def rollback(self, component: str) -> list[dict[str, Any]]:
        if component not in COMPONENTS:
            raise UpdateError(f"unknown component: {component}")
        lock = self._locks[component]
        if lock.locked():
            raise UpdateBusy("an update is already running")
        async with lock:
            state = _read_state(self._store, component)
            previous = state.get("previous") or {}
            if not previous.get("version") or not previous.get("path"):
                raise UpdateError("no previous version to roll back to")
            target = Path(str(previous["path"]))
            if not target.exists():
                raise UpdateError("the previous version is no longer on disk")
            current = state.get("current") or {}
            if component == KERNEL:
                await self._switch_kernel(target, previous["version"], state)
            else:
                self._switch_ui(component, target, previous["version"], state)
            state["current"], state["previous"] = previous, current
            _write_state(self._store, component, state)
        return self.state()

    async def _install_kernel(self, release: Release, state: dict[str, Any]) -> None:
        asset = f"mihomo-linux-{kernel_arch()}-{release.version}.gz"
        url = release.assets.get(asset)
        if not url:
            raise UpdateError(f"release {release.version} has no asset {asset}")
        target_dir = self._root / "kernel" / release.version
        target_dir.mkdir(parents=True, exist_ok=True)
        binary = target_dir / "mihomo"
        with tempfile.TemporaryDirectory(dir=self._root) as tmp:
            archive = Path(tmp) / "mihomo.gz"
            await self._download(url, archive, release.digests.get(asset))
            try:
                with gzip.open(archive, "rb") as source, binary.open("wb") as sink:
                    shutil.copyfileobj(source, sink)
            except OSError as exc:
                raise UpdateError(f"kernel archive is not a gzip stream: {exc}") from exc
            binary.chmod(0o755)
            try:
                probe = await asyncio.to_thread(
                    subprocess.run,
                    [str(binary), "-v"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                binary.unlink(missing_ok=True)
                raise UpdateError(f"downloaded kernel does not run: {exc}") from exc
            if probe.returncode != 0 or release.version not in (probe.stdout + probe.stderr):
                binary.unlink(missing_ok=True)
                raise UpdateError(
                    "downloaded kernel reported an unexpected version: "
                    f"{(probe.stdout + probe.stderr).strip()[:200]}"
                )
        previous_path = self.kernel_binary().resolve()
        previous_version, previous_source = self._current_kernel_version()
        previous = {
            "version": (state.get("current") or {}).get("version") or previous_version,
            "path": str(previous_path),
            "source": (state.get("current") or {}).get("source", previous_source),
        }
        await self._switch_kernel(binary, release.version, state, previous)

    async def _switch_kernel(
        self,
        binary: Path,
        version: str,
        state: dict[str, Any],
        previous: dict[str, Any] | None = None,
    ) -> None:
        """Point the supervisor at `binary`; restart, and undo everything if the
        new Kernel never answers."""
        was_running = self._kernel.alive()
        # Capture the concrete target now: once the pointer is swapped it names
        # the new version, so the old one is unrecoverable from the pointer.
        old_target = Path(self._kernel.binary).resolve()
        if Path(binary).resolve() == self._image_kernel.resolve():
            # Back to the image's binary: no pointer is better than a pointer
            # into the image, which would go stale when the image is replaced.
            self.kernel_pointer().unlink(missing_ok=True)
            self._kernel.use_binary(self._image_kernel)
        else:
            self._link(self.kernel_pointer(), binary)
            self._kernel.use_binary(self.kernel_pointer())
        if was_running:
            self._kernel.stop()
            try:
                self._kernel.start(self._kernel.active_config_path(), self._secret)
                if not await self._wait_kernel():
                    raise UpdateError("the new kernel did not become ready")
            except Exception as exc:
                self._kernel.stop()
                self._restore_kernel(old_target)
                raise UpdateError(f"kernel update rolled back: {exc}") from exc
        current = {"version": version, "path": str(binary), "source": "runtime"}
        if previous is not None:
            state["previous"] = previous
        else:
            state["previous"] = state.get("current")
        state["current"] = current
        state["latest"] = version
        state["checked_at"] = _now()
        state["check_error"] = None
        _write_state(self._store, KERNEL, state)

    def _restore_kernel(self, target: Path) -> None:
        """Run the Kernel from `target` again after a switch that failed.

        A previous runtime version is still on disk, so the pointer goes back to
        it; the Image Baseline gets no pointer (it would go stale when the image
        is replaced). If the old Kernel cannot be spawned either it is left
        stopped and reported — never left looking alive.
        """
        pointer = self.kernel_pointer()
        if target.is_file() and target != self._image_kernel.resolve():
            self._link(pointer, target)
            self._kernel.use_binary(pointer)
        else:
            pointer.unlink(missing_ok=True)
            self._kernel.use_binary(self._image_kernel)
        try:
            self._kernel.start(self._kernel.active_config_path(), self._secret)
        except Exception as exc:
            self._kernel.stop()
            raise UpdateError(
                f"kernel stopped after a failed update: {exc}; start it manually"
            ) from exc

    async def _install_ui(self, component: str, release: Release, state: dict[str, Any]) -> None:
        asset = UI_ASSET[component]
        url = release.assets.get(asset)
        if not url:
            raise UpdateError(f"release {release.version} has no asset {asset}")
        target = self._root / "webui" / component / release.version
        staging = self._root / "webui" / component / f".{release.version}.new"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        with tempfile.TemporaryDirectory(dir=self._root) as tmp:
            archive = Path(tmp) / asset
            await self._download(url, archive, release.digests.get(asset))
            _extract(archive, staging)
        validate_bundle(staging)
        shutil.rmtree(target, ignore_errors=True)
        os.replace(staging, target)
        previous_path = self.ui_dist(component).resolve()
        previous_version, previous_source = self._current_ui_version(component)
        previous = {
            "version": (state.get("current") or {}).get("version") or previous_version,
            "path": str(previous_path),
            "source": (state.get("current") or {}).get("source", previous_source),
        }
        self._switch_ui(component, target, release.version, state, previous)

    def _switch_ui(
        self,
        component: str,
        target: Path,
        version: str,
        state: dict[str, Any],
        previous: dict[str, Any] | None = None,
    ) -> None:
        if target.resolve() == self._image_ui[component].resolve():
            self.ui_pointer(component).unlink(missing_ok=True)
        else:
            self._link(self.ui_pointer(component), target)
        if previous is not None:
            state["previous"] = previous
        else:
            state["previous"] = state.get("current")
        state["current"] = {"version": version, "path": str(target), "source": "runtime"}
        state["latest"] = version
        state["checked_at"] = _now()
        state["check_error"] = None
        _write_state(self._store, component, state)

    # ---------- plumbing ----------

    def _link(self, pointer: Path, target: Path) -> None:
        pointer.parent.mkdir(parents=True, exist_ok=True)
        tmp = pointer.with_name(pointer.name + ".tmp")
        tmp.unlink(missing_ok=True)
        os.symlink(os.path.relpath(target, pointer.parent), tmp)
        os.replace(tmp, pointer)

    async def _download(self, url: str, dest: Path, sha256: str | None) -> None:
        client = await self._client_for()
        digest = hashlib.sha256()
        try:
            async with client.stream("GET", self._url(url)) as response:
                if not response.is_success:
                    raise UpdateError(f"download failed: HTTP {response.status_code}")
                with dest.open("wb") as sink:
                    async for chunk in response.aiter_bytes():
                        digest.update(chunk)
                        sink.write(chunk)
        except httpx.HTTPError as exc:
            raise UpdateError(f"download failed: {exc}") from exc
        if sha256 and digest.hexdigest() != sha256:
            dest.unlink(missing_ok=True)
            raise UpdateError("download failed: sha256 mismatch")

    async def _wait_kernel(self) -> bool:
        deadline = asyncio.get_running_loop().time() + KERNEL_READY_TIMEOUT
        while asyncio.get_running_loop().time() < deadline:
            if not self._kernel.alive():
                return False
            if await self._kernel.probe(self._secret):
                return True
            await asyncio.sleep(0.1)
        return False

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


_ASSET_REF = re.compile(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


def validate_bundle(root: Path) -> None:
    """OpenClash's check, tightened: the entry document must exist, every local
    script/style it references must exist, and it must reference a script — a
    half-downloaded or wrong-format archive can never replace a working one."""
    index = root / "index.html"
    if not index.is_file():
        raise UpdateError("bundle has no index.html")
    scripts = 0
    for ref in _ASSET_REF.findall(index.read_text(encoding="utf-8", errors="replace")):
        ref = ref.split("#", 1)[0].split("?", 1)[0].lstrip("./")
        if not ref or ref.startswith(("http://", "https://", "//", "/", "data:", "mailto:")):
            continue
        if not ref.endswith((".js", ".css")):
            continue
        if not (root / ref).is_file():
            raise UpdateError(f"bundle is missing asset: {ref}")
        if ref.endswith(".js"):
            scripts += 1
    if scripts == 0:
        raise UpdateError("bundle references no script")


def _extract(archive: Path, dest: Path) -> None:
    try:
        if archive.name.endswith(".tgz") or archive.name.endswith(".tar.gz"):
            with tarfile.open(archive) as tar:
                tar.extractall(dest, filter="data")
        elif archive.name.endswith(".zip"):
            with zipfile.ZipFile(archive) as bundle:
                for member in bundle.infolist():
                    name = member.filename
                    if name.startswith("/") or ".." in Path(name).parts:
                        raise UpdateError(f"archive contains an unsafe path: {name}")
                bundle.extractall(dest)
        else:
            raise UpdateError(f"unsupported archive: {archive.name}")
    except (tarfile.TarError, zipfile.BadZipFile, OSError) as exc:
        raise UpdateError(f"archive could not be extracted: {exc}") from exc
    # zashboard ships its bundle under a top-level `dist/` directory.
    nested = dest / "dist"
    if not (dest / "index.html").is_file() and (nested / "index.html").is_file():
        for item in nested.iterdir():
            os.replace(item, dest / item.name)
        shutil.rmtree(nested, ignore_errors=True)
