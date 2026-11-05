"""#22 seam tests: runtime updates for the three pinned artifacts.

The panel checks GitHub Releases, installs the asset under `/data/runtime`, and
switches the running copy through a symlink; the previous install stays for
rollback. Kernel updates restart the Kernel and undo themselves when the new
binary never answers.

GitHub is replaced by a local HTTP server (releases JSON + assets) so the tests
stay offline and deterministic; the Kernel is either the real binary (API-level
Runtime Dashboard tests) or a stub (restart/rollback semantics).
"""

import asyncio
import gzip
import hashlib
import io
import json
import threading
import zipfile
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Self

import pytest
from conftest import BootApp

from clashflow.updates import Release, UpdateBusy, UpdateError, UpdateManager


def ui_bundle(marker: str) -> bytes:
    """A minimal valid Runtime Dashboard archive: index.html + the script it references."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(
            "dist/index.html",
            f"<!doctype html><html><head><title>{marker}</title>"
            '<script src="./assets/app.js"></script></head><body></body></html>',
        )
        bundle.writestr("dist/assets/app.js", f'console.log("{marker}");')
    return buffer.getvalue()


def kernel_archive(version: str) -> bytes:
    script = (
        "#!/bin/sh\n"
        f'if [ "$1" = "-v" ]; then echo "Mihomo Meta {version} linux amd64"; exit 0; fi\n'
        "while :; do sleep 1; done\n"
    )
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb") as gz:
        gz.write(script.encode())
    return buffer.getvalue()


class FakeGitHub:
    """Serves `/repos/<owner>/<repo>/releases/latest` plus the release assets."""

    def __init__(self) -> None:
        self.releases: dict[str, dict[str, object]] = {}
        self.files: dict[str, bytes] = {}
        self.digests: dict[str, str] = {}
        handler = self._handler()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def publish(
        self, repo: str, version: str, asset: str, payload: bytes, *, redirect: bool = False
    ) -> None:
        path = f"/assets/{repo.replace('/', '-')}-{asset}"
        self.files[path] = payload
        self.digests[path] = hashlib.sha256(payload).hexdigest()
        # GitHub serves release assets through a redirect; the fake reproduces
        # that so a client that does not follow 302 fails the same way.
        download_url = f"{self.url}/redirect{path}" if redirect else f"{self.url}{path}"
        self.releases[repo] = {
            "tag_name": version,
            "assets": [
                {
                    "name": asset,
                    "browser_download_url": download_url,
                    "digest": f"sha256:{self.digests[path]}",
                }
            ],
        }

    def publish_with_bad_digest(self, repo: str, version: str, asset: str, payload: bytes) -> None:
        self.publish(repo, version, asset, payload)
        release = self.releases[repo]
        assets = release["assets"]
        assert isinstance(assets, list)
        assets[0]["digest"] = "sha256:" + "0" * 64

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        github = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path.startswith("/repos/"):
                    repo = "/".join(self.path.split("/")[2:4])
                    release = github.releases.get(repo)
                    if release is None:
                        self.send_error(404)
                        return
                    body = json.dumps(release).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path.startswith("/redirect/"):
                    self.send_response(302)
                    self.send_header("Location", self.path.removeprefix("/redirect"))
                    self.end_headers()
                    return
                payload = github.files.get(self.path)
                if payload is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args: object) -> None:
                pass

        return Handler

    def __enter__(self) -> Self:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake_github(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeGitHub]:
    github = FakeGitHub()
    with github:
        monkeypatch.setenv("CLASHFLOW_GITHUB_API", github.url)
        monkeypatch.setenv("CLASHFLOW_GITHUB_PROXY", "")
        yield github


def state_of(payload: list[dict[str, object]], component: str) -> dict[str, object]:
    for row in payload:
        if row["component"] == component:
            return row
    raise AssertionError(f"{component} missing from {payload}")


async def test_runtime_dashboard_update_switches_the_served_bundle_and_rollback_restores_it(
    boot_app: BootApp, fake_github: FakeGitHub, zashboard_dist: Path
) -> None:
    fake_github.publish(
        "Zephyruso/zashboard", "v9.9.9", "dist-no-fonts.zip", ui_bundle("updated"), redirect=True
    )
    async with boot_app(zui_dist=zashboard_dist) as panel:
        checked = await panel.client.post("/api/panel/updates/check")
        assert checked.status_code == 200
        row = state_of(checked.json(), "zashboard")
        assert row["latest"] == "v9.9.9"
        assert row["source"] == "image"

        updated = await panel.client.post("/api/panel/updates/zashboard/update")
        assert updated.status_code == 200, updated.text
        row = state_of(updated.json(), "zashboard")
        assert row["current"] == "v9.9.9"
        assert row["source"] == "runtime"
        assert str(row["previous"]).startswith("v")

        # The static mount now serves the freshly installed bundle.
        served = await panel.client.get("/zui/")
        assert "updated" in served.text

        rolled = await panel.client.post("/api/panel/updates/zashboard/rollback")
        assert rolled.status_code == 200, rolled.text
        row = state_of(rolled.json(), "zashboard")
        assert row["source"] == "image"
        # Rolling back to the image's bundle drops the pointer entirely rather
        # than leaving a symlink into the image (which a new image invalidates).
        assert not (panel.data_dir / "runtime/webui/zashboard/current").exists()
        assert "updated" not in (await panel.client.get("/zui/")).text


async def test_a_broken_bundle_is_refused_and_the_running_one_survives(
    boot_app: BootApp, fake_github: FakeGitHub, zashboard_dist: Path
) -> None:
    fake_github.publish("Zephyruso/zashboard", "v9.9.9", "dist-no-fonts.zip", b"not a zip")
    async with boot_app(zui_dist=zashboard_dist) as panel:
        before = (await panel.client.get("/zui/")).text
        failed = await panel.client.post("/api/panel/updates/zashboard/update")
        assert failed.status_code == 502
        assert "extract" in failed.json()["detail"]
        assert (await panel.client.get("/zui/")).text == before
        row = state_of((await panel.client.get("/api/panel/updates")).json(), "zashboard")
        assert row["source"] == "image"


async def test_a_digest_mismatch_is_refused(boot_app: BootApp, fake_github: FakeGitHub) -> None:
    fake_github.publish_with_bad_digest(
        "Zephyruso/zashboard", "v9.9.9", "dist-no-fonts.zip", ui_bundle("tampered")
    )
    async with boot_app() as panel:
        failed = await panel.client.post("/api/panel/updates/zashboard/update")
        assert failed.status_code == 502
        assert "sha256" in failed.json()["detail"]


async def test_unknown_component_and_unreachable_api_are_reported(
    boot_app: BootApp, fake_github: FakeGitHub
) -> None:
    async with boot_app() as panel:
        assert (await panel.client.post("/api/panel/updates/nope/update")).status_code == 404
        # No release published for mihomo: the check records the reason.
        checked = await panel.client.post("/api/panel/updates/check")
        assert checked.status_code == 200
        row = state_of(checked.json(), "kernel")
        assert row["error"] and "release check failed" in str(row["error"])


class StubKernel:
    """Kernel-shaped double for restart / rollback semantics."""

    def __init__(self, binary: Path, *, ready: bool = True) -> None:
        self._bin = str(binary)
        self.ready = ready
        self.fail_start = False
        self.running = True
        self.starts: list[str] = []
        self.stops = 0

    def alive(self) -> bool:
        return self.running

    async def probe(self, secret: str) -> bool:
        return self.ready

    def start(self, config_path: Path, secret: str) -> None:
        # `Popen` fails on a missing binary, so the double must too: a dangling
        # or self-referential pointer then shows up as a failure, not a pass.
        if not Path(self._bin).exists():
            raise FileNotFoundError(self._bin)
        if self.fail_start:
            raise OSError(f"cannot execute {self._bin}")
        self.running = True
        self.starts.append(self._bin)

    def stop(self) -> None:
        self.stops += 1
        self.running = False

    def active_config_path(self) -> Path:
        return Path("/tmp/active.yaml")

    @property
    def binary(self) -> str:
        return self._bin

    def use_binary(self, path: Path | str) -> None:
        self._bin = str(path)


def kernel_manager(tmp_path: Path, kernel: StubKernel, api: str) -> UpdateManager:
    from clashflow.db import MetadataStore

    store = MetadataStore(tmp_path / "clashflow.db")
    image_ui = tmp_path / "image-ui"
    image_ui.mkdir(exist_ok=True)
    return UpdateManager(
        store=store,
        kernel=kernel,
        data_dir=tmp_path,
        secret="secret",
        image_kernel=Path(kernel.binary),
        image_ui={"metacubexd": image_ui, "zashboard": image_ui},
        bundle_versions={},
        github_api=api,
    )


async def test_kernel_update_restarts_and_rolls_back_when_the_new_binary_never_answers(
    tmp_path: Path, fake_github: FakeGitHub
) -> None:
    old = tmp_path / "old-mihomo"
    old.write_text("#!/bin/sh\nexit 0\n")
    kernel = StubKernel(old, ready=False)
    manager = kernel_manager(tmp_path, kernel, fake_github.url)
    fake_github.publish(
        "MetaCubeX/mihomo", "v9.9.9", "mihomo-linux-amd64-v9.9.9.gz", kernel_archive("v9.9.9")
    )

    with pytest.raises(UpdateError) as excinfo:
        await manager.update("kernel")
    assert "rolled back" in str(excinfo.value)
    # The previous version was the Image Baseline, so the supervisor falls back
    # to the image binary and no pointer is left behind.
    assert kernel.binary == str(old)
    assert not manager.kernel_pointer().is_symlink()
    await manager.aclose()


async def test_kernel_update_switches_the_pointer_and_keeps_the_previous_binary(
    tmp_path: Path, fake_github: FakeGitHub
) -> None:
    old = tmp_path / "old-mihomo"
    old.write_text("#!/bin/sh\nexit 0\n")
    kernel = StubKernel(old, ready=True)
    manager = kernel_manager(tmp_path, kernel, fake_github.url)
    fake_github.publish(
        "MetaCubeX/mihomo", "v9.9.9", "mihomo-linux-amd64-v9.9.9.gz", kernel_archive("v9.9.9")
    )

    rows = await manager.update("kernel")
    row = state_of(rows, "kernel")
    assert row["current"] == "v9.9.9"
    assert row["source"] == "runtime"
    assert manager.kernel_pointer().is_symlink()
    assert kernel.binary == str(manager.kernel_pointer())
    # Rollback puts the supervisor back on the image binary without downloading.
    rows = await manager.rollback("kernel")
    assert state_of(rows, "kernel")["source"] == "image"
    # Back on the image binary, and no pointer is left behind.
    assert kernel.binary == str(old)
    assert not manager.kernel_pointer().is_symlink()
    await manager.aclose()


async def test_a_second_kernel_update_rolls_back_to_the_version_it_replaced(
    tmp_path: Path, fake_github: FakeGitHub
) -> None:
    old = tmp_path / "old-mihomo"
    old.write_text("#!/bin/sh\nexit 0\n")
    kernel = StubKernel(old)
    manager = kernel_manager(tmp_path, kernel, fake_github.url)

    fake_github.publish(
        "MetaCubeX/mihomo", "v9.9.9", "mihomo-linux-amd64-v9.9.9.gz", kernel_archive("v9.9.9")
    )
    await manager.update("kernel")
    landed = manager.kernel_pointer().resolve()

    kernel.ready = False
    fake_github.publish(
        "MetaCubeX/mihomo", "v9.9.10", "mihomo-linux-amd64-v9.9.10.gz", kernel_archive("v9.9.10")
    )
    with pytest.raises(UpdateError) as excinfo:
        await manager.update("kernel")
    assert "rolled back" in str(excinfo.value)
    # The previous version was itself a runtime install, so it stays on disk and
    # the pointer goes back to it instead of being dropped.
    assert manager.kernel_pointer().is_symlink()
    assert manager.kernel_pointer().resolve() == landed
    assert Path(kernel.binary).resolve() == landed
    assert kernel.alive()
    assert state_of(manager.state(), "kernel")["current"] == "v9.9.9"
    await manager.aclose()


async def test_a_failed_restore_leaves_the_kernel_stopped_and_reports_it(
    tmp_path: Path, fake_github: FakeGitHub
) -> None:
    old = tmp_path / "old-mihomo"
    old.write_text("#!/bin/sh\nexit 0\n")
    kernel = StubKernel(old)
    manager = kernel_manager(tmp_path, kernel, fake_github.url)

    fake_github.publish(
        "MetaCubeX/mihomo", "v9.9.9", "mihomo-linux-amd64-v9.9.9.gz", kernel_archive("v9.9.9")
    )
    await manager.update("kernel")
    landed = manager.kernel_pointer().resolve()

    kernel.fail_start = True
    fake_github.publish(
        "MetaCubeX/mihomo", "v9.9.10", "mihomo-linux-amd64-v9.9.10.gz", kernel_archive("v9.9.10")
    )
    with pytest.raises(UpdateError) as excinfo:
        await manager.update("kernel")
    assert "start it manually" in str(excinfo.value)
    # The Kernel is honestly down and still pointed at the previous version;
    # nothing claims it is running.
    assert not kernel.alive()
    assert manager.kernel_pointer().resolve() == landed
    assert state_of(manager.state(), "kernel")["current"] == "v9.9.9"
    await manager.aclose()


async def test_a_second_runtime_dashboard_update_rolls_back_to_the_first_one(
    boot_app: BootApp, fake_github: FakeGitHub, zashboard_dist: Path
) -> None:
    fake_github.publish(
        "Zephyruso/zashboard", "v9.9.9", "dist-no-fonts.zip", ui_bundle("BUNDLE_A"), redirect=True
    )
    async with boot_app(zui_dist=zashboard_dist) as panel:
        assert (await panel.client.post("/api/panel/updates/zashboard/update")).status_code == 200
        fake_github.publish(
            "Zephyruso/zashboard",
            "v9.9.10",
            "dist-no-fonts.zip",
            ui_bundle("BUNDLE_B"),
            redirect=True,
        )
        assert (await panel.client.post("/api/panel/updates/zashboard/update")).status_code == 200
        assert "BUNDLE_B" in (await panel.client.get("/zui/")).text

        rolled = await panel.client.post("/api/panel/updates/zashboard/rollback")
        assert rolled.status_code == 200, rolled.text
        assert state_of(rolled.json(), "zashboard")["current"] == "v9.9.9"
        served = await panel.client.get("/zui/")
        assert "BUNDLE_A" in served.text
        assert "BUNDLE_B" not in served.text


async def test_rollback_without_a_previous_version_reports_a_failure(
    boot_app: BootApp, fake_github: FakeGitHub
) -> None:
    async with boot_app() as panel:
        failed = await panel.client.post("/api/panel/updates/zashboard/rollback")
        assert failed.status_code == 502
        assert "no previous version" in failed.json()["detail"]


async def test_checking_for_releases_does_not_block_an_update(
    tmp_path: Path, fake_github: FakeGitHub
) -> None:
    old = tmp_path / "old-mihomo"
    old.write_text("#!/bin/sh\nexit 0\n")
    kernel = StubKernel(old)
    manager = kernel_manager(tmp_path, kernel, fake_github.url)
    fake_github.publish(
        "MetaCubeX/mihomo", "v9.9.9", "mihomo-linux-amd64-v9.9.9.gz", kernel_archive("v9.9.9")
    )

    in_check = asyncio.Event()
    proceed = asyncio.Event()
    calls = 0
    original = manager.latest_release

    async def paused(component: str) -> Release:
        nonlocal calls
        calls += 1
        if calls == 1:
            in_check.set()
            await proceed.wait()
        return await original(component)

    manager.latest_release = paused  # type: ignore[method-assign]
    checking = asyncio.create_task(manager.check())
    await in_check.wait()
    rows = await manager.update("kernel")
    assert state_of(rows, "kernel")["current"] == "v9.9.9"
    proceed.set()
    await checking
    await manager.aclose()


async def test_a_check_queued_behind_an_update_keeps_what_the_update_wrote(
    tmp_path: Path, fake_github: FakeGitHub
) -> None:
    """A check that lands mid-update must not answer "an update is already
    running" (it is not an update) and must not overwrite what the update
    wrote: its snapshot is taken after the update's final state write."""
    old = tmp_path / "old-mihomo"
    old.write_text("#!/bin/sh\nexit 0\n")
    kernel = StubKernel(old)
    manager = kernel_manager(tmp_path, kernel, fake_github.url)
    fake_github.publish(
        "MetaCubeX/mihomo", "v9.9.9", "mihomo-linux-amd64-v9.9.9.gz", kernel_archive("v9.9.9")
    )

    in_update = asyncio.Event()
    proceed = asyncio.Event()
    calls = 0
    original = manager.latest_release

    async def paused(component: str) -> Release:
        nonlocal calls
        calls += 1
        if calls == 1:
            in_update.set()
            await proceed.wait()
        return await original(component)

    manager.latest_release = paused  # type: ignore[method-assign]
    running = asyncio.create_task(manager.update("kernel"))
    await in_update.wait()
    checking = asyncio.create_task(manager.check())
    # Let the check reach the component lock and queue behind the update.
    await asyncio.sleep(0)
    proceed.set()

    updated = await running
    assert state_of(updated, "kernel")["current"] == "v9.9.9"
    checked = await checking
    row = state_of(checked, "kernel")
    assert row["current"] == "v9.9.9"
    assert row["latest"] == "v9.9.9"
    assert row["checked_at"] is not None
    assert row["error"] is None
    assert state_of(manager.state(), "kernel")["current"] == "v9.9.9"
    await manager.aclose()


async def test_a_second_update_of_the_same_component_is_refused_while_one_runs(
    tmp_path: Path, fake_github: FakeGitHub
) -> None:
    old = tmp_path / "old-mihomo"
    old.write_text("#!/bin/sh\nexit 0\n")
    kernel = StubKernel(old)
    manager = kernel_manager(tmp_path, kernel, fake_github.url)
    fake_github.publish(
        "MetaCubeX/mihomo", "v9.9.9", "mihomo-linux-amd64-v9.9.9.gz", kernel_archive("v9.9.9")
    )

    in_update = asyncio.Event()
    proceed = asyncio.Event()
    original = manager.latest_release

    async def paused(component: str) -> Release:
        in_update.set()
        await proceed.wait()
        return await original(component)

    manager.latest_release = paused  # type: ignore[method-assign]
    running = asyncio.create_task(manager.update("kernel"))
    await in_update.wait()
    with pytest.raises(UpdateBusy):
        await manager.update("kernel")
    proceed.set()
    rows = await running
    assert state_of(rows, "kernel")["current"] == "v9.9.9"
    await manager.aclose()
