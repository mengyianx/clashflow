import httpx
from conftest import lan_ip


async def test_default_config_proxies_lan_clients_through_mixed_port(boot_app) -> None:
    import http.server
    import socketserver
    import threading

    class OriginHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = b"clashflow-proxy-ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    origin = socketserver.TCPServer(("127.0.0.1", 0), OriginHandler)
    threading.Thread(target=origin.serve_forever, daemon=True).start()
    origin_port = origin.server_address[1]
    try:
        async with boot_app() as panel:
            assert (await panel.client.get("/api/service/health")).json()["kernel"] == "up"

            async with httpx.AsyncClient(proxy="http://127.0.0.1:7890") as proxied:
                via_loopback = await proxied.get(f"http://127.0.0.1:{origin_port}/")
            assert via_loopback.status_code == 200
            assert via_loopback.content == b"clashflow-proxy-ok"

            # allow-lan: true — the mixed port must accept LAN clients, not
            # just loopback ones.
            host = lan_ip()
            async with httpx.AsyncClient(proxy=f"http://{host}:7890") as proxied:
                via_lan = await proxied.get(f"http://127.0.0.1:{origin_port}/")
            assert via_lan.status_code == 200
            assert via_lan.content == b"clashflow-proxy-ok"
    finally:
        origin.shutdown()
        origin.server_close()
