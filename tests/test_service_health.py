async def test_health_reports_kernel_up_and_active_config(boot_app) -> None:
    async with boot_app() as panel:
        resp = await panel.client.get("/api/service/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["kernel"] == "up"
        assert body["active_config"] == "default"


async def test_health_reports_kernel_down_but_panel_still_serves(boot_app, tmp_path) -> None:
    broken = tmp_path / "not-a-kernel"
    broken.write_text("#!/bin/sh\nexit 1\n")
    broken.chmod(0o755)

    async with boot_app(kernel_bin=str(broken)) as panel:
        resp = await panel.client.get("/api/service/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["kernel"] == "down"
        assert body["active_config"] == "default"
