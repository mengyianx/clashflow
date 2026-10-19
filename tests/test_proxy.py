import httpx
from conftest import lan_ip, origin_server


async def test_default_config_proxies_lan_clients_through_mixed_port(boot_app) -> None:
    with origin_server(b"clashflow-proxy-ok") as origin:
        async with boot_app() as panel:
            assert (await panel.client.get("/api/service/health")).json()["kernel"] == "up"

            async with httpx.AsyncClient(proxy="http://127.0.0.1:7890") as proxied:
                via_loopback = await proxied.get(f"http://127.0.0.1:{origin.port}/")
            assert via_loopback.status_code == 200
            assert via_loopback.content == b"clashflow-proxy-ok"

            # allow-lan: true — the mixed port must accept LAN clients, not
            # just loopback ones.
            host = lan_ip()
            async with httpx.AsyncClient(proxy=f"http://{host}:7890") as proxied:
                via_lan = await proxied.get(f"http://127.0.0.1:{origin.port}/")
            assert via_lan.status_code == 200
            assert via_lan.content == b"clashflow-proxy-ok"
