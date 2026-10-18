import httpx


async def test_data_volume_layout_survives_recreation(boot_app, tmp_path) -> None:
    data = tmp_path / "volume"

    async with boot_app(data_dir=data) as first:
        assert (await first.client.get("/api/service/health")).json()["kernel"] == "up"
        first_secret = first.app.state.secret

        # §3 layout: metadata DB, configs/, Kernel cache under the one volume.
        assert (data / "clashflow.db").is_file()
        assert (data / "configs" / "default.yaml").is_file()
        assert (data / "cache.db").is_file()
        default_yaml = (data / "configs" / "default.yaml").read_text()

    # Kernel from the first boot is stopped with the app; Controller is gone.
    async with httpx.AsyncClient() as client:
        try:
            await client.get("http://127.0.0.1:9090/version", timeout=1.0)
        except httpx.HTTPError:
            pass  # refused: expected after shutdown

    # Re-create the app (as a new container would) against the same volume.
    async with boot_app(data_dir=data) as second:
        health = await second.client.get("/api/service/health")
        assert health.status_code == 200
        assert health.json()["kernel"] == "up"
        assert health.json()["active_config"] == "default"

        # The persisted secret still guards the new Kernel; state survived.
        async with httpx.AsyncClient() as client:
            pinned = await client.get(
                "http://127.0.0.1:9090/version",
                headers={"Authorization": f"Bearer {first_secret}"},
            )
            assert pinned.status_code == 200

        assert (data / "configs" / "default.yaml").read_text() == default_yaml
        assert second.app.state.secret == first_secret
