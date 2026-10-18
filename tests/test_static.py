async def test_web_shell_served_from_root(boot_app, tmp_path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>clashflow-web-shell</body></html>")

    async with boot_app(web_dist=dist) as panel:
        resp = await panel.client.get("/")
        assert resp.status_code == 200
        assert "clashflow-web-shell" in resp.text

        # The API keeps working alongside the static shell.
        health = await panel.client.get("/api/service/health")
        assert health.status_code == 200
