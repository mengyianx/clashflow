"""Issue #36: the triple validation before any write.

A save or a Subscription refresh is refused before anything is written unless
the RENDERED bytes parse as YAML, carry `proxies` or `proxy-providers`, and
pass the Kernel's own `-t` check. The ordering is the point: validation runs
on the rendered bytes, never on the Source Config, because the Source Config
is not what the Kernel reads.

Assertions stay at the external seams: the panel HTTP API with a real Kernel,
bytes on disk, and the apply pipeline's documented stage injection.
"""

from collections.abc import Mapping
from typing import Any

import httpx
import pytest
from conftest import PROBE_PROXY_BLOCK, origin_server

from clashflow.apply import RenderedConfigInvalid, commit_rendered_config, validate_rendered_config
from clashflow.render import RenderContext, key_merge

# A complete Config: it carries a proxy, so the source alone would pass.
VALID_SOURCE = PROBE_PROXY_BLOCK + "mixed-port: 7890\nallow-lan: true\n"
# Valid YAML, but no upstream at all: what a dead Subscription returns.
EMPTY_SOURCE = "mixed-port: 7891\nallow-lan: true\n"


async def test_a_config_with_nothing_to_proxy_is_refused_before_anything_is_written(
    boot_app,
) -> None:
    async with boot_app() as panel:
        source = panel.data_dir / "configs" / "default.yaml"
        runtime = panel.data_dir / "runtime-configs" / "default.yaml"
        source_before = source.read_bytes()
        runtime_before = runtime.read_bytes()

        resp = await panel.client.put("/api/panel/configs/default", content=EMPTY_SOURCE)

        assert resp.status_code == 400
        assert "neither proxies nor proxy-providers" in resp.json()["detail"]
        # Rejected before the writes: both files stay byte-identical, no
        # temp residue in either directory.
        assert source.read_bytes() == source_before
        assert runtime.read_bytes() == runtime_before
        assert list(source.parent.glob("*.tmp")) == []
        assert list(runtime.parent.glob("*.tmp")) == []


async def test_a_kernel_refusal_is_reported_verbatim_and_nothing_is_written(boot_app) -> None:
    async with boot_app() as panel:
        source = panel.data_dir / "configs" / "default.yaml"
        runtime = panel.data_dir / "runtime-configs" / "default.yaml"
        before = source.read_bytes()
        runtime_before = runtime.read_bytes()
        # Parses and carries a proxy, but the Kernel does not know the type.
        content = (
            "mixed-port: 7891\n"
            "proxies:\n"
            "  - name: broken\n"
            "    type: not-a-real-type\n"
            "    server: 127.0.0.1\n"
            "    port: 1\n"
        )

        resp = await panel.client.put("/api/panel/configs/default", content=content)

        assert resp.status_code == 400
        # The Kernel's own output, unmodified.
        assert "unsupport proxy type: not-a-real-type" in resp.json()["detail"]
        assert source.read_bytes() == before
        assert runtime.read_bytes() == runtime_before


async def test_an_empty_subscription_cannot_overwrite_a_good_config(boot_app) -> None:
    async with boot_app() as panel:
        with origin_server(VALID_SOURCE.encode()) as origin:
            added = await panel.client.post(
                "/api/panel/subscriptions", json={"name": "prov", "url": origin.url}
            )
            assert added.status_code == 201, added.text
            source = panel.data_dir / "configs" / "prov.yaml"
            runtime = panel.data_dir / "runtime-configs" / "prov.yaml"
            source_before = source.read_bytes()
            runtime_before = runtime.read_bytes()

            # The provider now answers with a config that has nothing to proxy.
            origin.state["body"] = EMPTY_SOURCE.encode()
            resp = await panel.client.post("/api/panel/subscriptions/prov/refresh")

        assert resp.status_code == 400
        assert "neither proxies nor proxy-providers" in resp.json()["detail"]
        # The working Config survives untouched: same bytes, both files.
        assert source.read_bytes() == source_before
        assert runtime.read_bytes() == runtime_before
        entry = (await panel.client.get("/api/panel/subscriptions")).json()[0]
        assert entry["state"] == "error"
        assert entry["last_error_class"] == "render-failed"


async def test_adding_an_empty_subscription_creates_nothing(boot_app) -> None:
    async with boot_app() as panel:
        with origin_server(EMPTY_SOURCE.encode()) as origin:
            resp = await panel.client.post(
                "/api/panel/subscriptions", json={"name": "dead", "url": origin.url}
            )
        assert resp.status_code == 400
        assert "neither proxies nor proxy-providers" in resp.json()["detail"]
        assert (await panel.client.get("/api/panel/subscriptions")).json() == []
        configs = {c["name"] for c in (await panel.client.get("/api/panel/configs")).json()}
        assert "dead" not in configs
        assert not (panel.data_dir / "configs" / "dead.yaml").exists()


async def test_validation_runs_on_the_rendered_bytes_not_the_source(mihomo_bin, tmp_path) -> None:
    """The source alone passes; a stage that drops the proxy makes the RENDERED
    bytes fail. If validation looked at the source it would accept this."""
    data = tmp_path / "data"
    configs = data / "configs"
    configs.mkdir(parents=True)
    source = configs / "default.yaml"
    source.write_bytes(VALID_SOURCE.encode())
    runtime = data / "runtime-configs" / "default.yaml"

    # The control: the source bytes alone satisfy the content check.
    validate_rendered_config(VALID_SOURCE.encode())

    def drop_the_proxy(rendered: bytes, overlay: Mapping[str, Any], ctx: RenderContext) -> bytes:
        return b"mixed-port: 7890\n"

    async with httpx.AsyncClient() as client:
        with pytest.raises(RenderedConfigInvalid, match="neither proxies nor proxy-providers"):
            await commit_rendered_config(
                kernel_bin=mihomo_bin,
                data_dir=data,
                client=client,
                secret="test-secret",
                source_target=source,
                runtime_target=runtime,
                content=VALID_SOURCE.encode(),
                overlay={},
                should_reload=None,
                stages=(("key-merge", key_merge), ("rule-injection", drop_the_proxy)),
            )
        # Refused before either file was touched.
        assert not runtime.exists()
        assert source.read_bytes() == VALID_SOURCE.encode()

        # The same source through the real stages is accepted and stored.
        await commit_rendered_config(
            kernel_bin=mihomo_bin,
            data_dir=data,
            client=client,
            secret="test-secret",
            source_target=source,
            runtime_target=runtime,
            content=VALID_SOURCE.encode(),
            overlay={},
            should_reload=None,
        )
    assert runtime.is_file()


def test_the_content_check_accepts_proxy_providers_and_rejects_empty_ones() -> None:
    # `proxy-providers` is the second way to have something to proxy; an
    # EMPTY list or mapping is what a dead Subscription returns, so it counts
    # as nothing.
    validate_rendered_config(b"proxy-providers:\n  remote:\n    type: http\n    url: http://x\n")
    for nothing in (b"proxies: []\n", b"proxy-providers: {}\n", b"{}\n"):
        with pytest.raises(RenderedConfigInvalid, match="neither proxies nor proxy-providers"):
            validate_rendered_config(nothing)


def test_bytes_that_are_not_a_yaml_mapping_are_refused() -> None:
    with pytest.raises(RenderedConfigInvalid, match="not valid YAML"):
        validate_rendered_config(b"proxies: [\n")
    with pytest.raises(RenderedConfigInvalid, match="must be a YAML mapping"):
        validate_rendered_config(b"- just\n- a list\n")
