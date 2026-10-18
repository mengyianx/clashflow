import socket

import httpx
import pytest
from conftest import lan_ip

CONTROLLER = "http://127.0.0.1:9090"
YAML_CONTROLLER_PORT = 19999

# The Config YAML tries to move the Controller to the LAN and set its own
# secret; the CLI pins must win regardless of what the YAML says.
HOSTILE_YAML = (
    "mixed-port: 7890\n"
    "allow-lan: true\n"
    f"external-controller: 0.0.0.0:{YAML_CONTROLLER_PORT}\n"
    "secret: yamlsecret\n"
)


async def test_yaml_controller_block_cannot_move_cli_pins(boot_app, monkeypatch) -> None:
    monkeypatch.setenv("CLASHFLOW_SECRET", "cli-secret")
    async with boot_app(config_body=HOSTILE_YAML), httpx.AsyncClient() as client:
        # The CLI-pinned Controller answers with the CLI-pinned secret.
        pinned = await client.get(
            f"{CONTROLLER}/version", headers={"Authorization": "Bearer cli-secret"}
        )
        assert pinned.status_code == 200

        # The YAML secret is rejected: the YAML cannot set the secret either.
        from_yaml = await client.get(
            f"{CONTROLLER}/version", headers={"Authorization": "Bearer yamlsecret"}
        )
        assert from_yaml.status_code == 401

        # The YAML's own external-controller binding never comes up.
        with pytest.raises(httpx.ConnectError):
            await client.get(f"http://127.0.0.1:{YAML_CONTROLLER_PORT}/version")

        # The pinned Controller listens on loopback only.
        with (
            pytest.raises(ConnectionRefusedError),
            socket.create_connection((lan_ip(), 9090), timeout=2),
        ):
            pass
