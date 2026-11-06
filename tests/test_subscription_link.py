"""Issue #41: the Subscription <-> Source Config link is explicit data.

Seams: the Management Panel HTTP API for creation, refresh, deletion and the
Active-Config refusal, plus a V1-shaped volume booted through `boot_app` for
the migration backfill. One test deliberately re-points the link at a Config
with a DIFFERENT name: that is the only way to observe that no code path
falls back to "the names are equal".
"""

import sqlite3
from pathlib import Path

from conftest import PROBE_PROXY_BLOCK, origin_server

from clashflow.db import SCHEMA_VERSION

SUB_ONE = PROBE_PROXY_BLOCK.encode() + b"mixed-port: 7896\nallow-lan: true\n"
SUB_TWO = (
    b"mixed-port: 7897\nallow-lan: true\n"
    b"proxies:\n"
    b"  - name: alt\n"
    b"    type: http\n"
    b"    server: 127.0.0.1\n"
    b"    port: 1081\n"
)

V1_SCHEMA = """
CREATE TABLE configs (
    name TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('hand-written', 'subscription-derived')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE active_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT NOT NULL REFERENCES configs(name)
);
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE subscriptions (
    name TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    last_refresh_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _query(panel, sql: str, parameters: tuple[object, ...] = ()) -> list[tuple]:
    conn = sqlite3.connect(panel.data_dir / "clashflow.db")
    try:
        return conn.execute(sql, parameters).fetchall()
    finally:
        conn.close()


def _relink(panel, subscription: str, config_name: str) -> None:
    """Point the link at another Config — the shape a future "choose the
    source" flow produces, and the only way to prove the name is not read."""
    conn = sqlite3.connect(panel.data_dir / "clashflow.db")
    try:
        conn.execute(
            "UPDATE subscriptions SET config_name = ? WHERE name = ?",
            (config_name, subscription),
        )
        conn.commit()
    finally:
        conn.close()


async def test_creating_a_subscription_links_it_to_the_config_it_created(boot_app) -> None:
    async with boot_app() as panel:
        with origin_server(SUB_ONE) as origin:
            resp = await panel.client.post(
                "/api/panel/subscriptions", json={"name": "prov", "url": origin.url}
            )
        assert resp.status_code == 201
        entry = resp.json()
        # The API returns the link explicitly: the registered Config name and
        # the file the server assigned it.
        assert entry["config_name"] == "prov"
        assert entry["config"] == "prov.yaml"

        configs = (await panel.client.get("/api/panel/configs")).json()
        by_name = {config["name"]: config for config in configs}
        assert by_name["prov"]["subscription"] == "prov"
        # A Hand-written Config belongs to no Subscription.
        assert by_name["default"]["subscription"] is None
        assert _query(panel, "SELECT config_name FROM subscriptions WHERE name = 'prov'") == [
            ("prov",)
        ]


async def test_every_read_and_write_follows_the_link_not_the_name(boot_app) -> None:
    async with boot_app() as panel:
        with origin_server(SUB_ONE) as origin:
            assert (
                await panel.client.post(
                    "/api/panel/subscriptions", json={"name": "prov", "url": origin.url}
                )
            ).status_code == 201
            # A second, differently named Config to link the Subscription to.
            assert (
                await panel.client.post("/api/panel/configs", json={"name": "shared"})
            ).status_code == 201
            configs_dir = panel.data_dir / "configs"
            (configs_dir / "shared.yaml").write_bytes(b"mixed-port: 7899\nallow-lan: true\n")
            _relink(panel, "prov", "shared")

            # Refresh writes the LINKED Config and leaves the same-named,
            # now-unowned file alone.
            origin.state["body"] = SUB_TWO
            resp = await panel.client.post("/api/panel/subscriptions/prov/refresh")
            assert resp.status_code == 200
            assert (configs_dir / "shared.yaml").read_bytes() == SUB_TWO
            assert (configs_dir / "prov.yaml").read_bytes() == SUB_ONE

            configs = (await panel.client.get("/api/panel/configs")).json()
            by_name = {config["name"]: config for config in configs}
            assert by_name["shared"]["subscription"] == "prov"
            assert by_name["prov"]["subscription"] is None
            # The refresh updated the linked Config's summary, not the other's.
            assert by_name["shared"]["summary"]["nodes"] == 1

            # The Active check follows the link too: deleting the Subscription
            # is refused while its LINKED Config is the Active one.
            assert (await panel.client.put("/api/panel/configs/shared/active")).status_code == 200
            resp = await panel.client.delete("/api/panel/subscriptions/prov")
            assert resp.status_code == 409
            assert "config is active" in resp.json()["detail"]
            assert (configs_dir / "shared.yaml").read_bytes() == SUB_TWO

            # Once it is not Active, the delete removes the linked Config and
            # its file — and nothing else.
            assert (await panel.client.put("/api/panel/configs/default/active")).status_code == 200
            resp = await panel.client.delete("/api/panel/subscriptions/prov")
            assert resp.status_code == 200
            assert not (configs_dir / "shared.yaml").exists()
            assert (configs_dir / "prov.yaml").read_bytes() == SUB_ONE
            names = {
                config["name"] for config in (await panel.client.get("/api/panel/configs")).json()
            }
            assert {"default", "prov"} == names
            assert (await panel.client.get("/api/panel/subscriptions")).json() == []


def _seed_v1_volume(data: Path) -> None:
    """A V1 volume: the pair created by the same-name convention, one Config
    with no Subscription at all, and one Subscription whose Config had already
    vanished."""
    (data / "configs").mkdir(parents=True)
    (data / "configs" / "default.yaml").write_text("mixed-port: 7890\nallow-lan: true\n")
    (data / "configs" / "prov.yaml").write_text(SUB_ONE.decode())
    (data / "configs" / "spare.yaml").write_text("mixed-port: 7891\nallow-lan: true\n")
    conn = sqlite3.connect(data / "clashflow.db")
    try:
        conn.executescript(V1_SCHEMA)
        conn.executemany(
            "INSERT INTO configs (name, kind) VALUES (?, ?)",
            [
                ("default", "hand-written"),
                ("prov", "subscription-derived"),
                ("spare", "hand-written"),
            ],
        )
        conn.execute("INSERT INTO active_config (id, name) VALUES (1, 'default')")
        conn.executemany(
            "INSERT INTO subscriptions (name, url) VALUES (?, ?)",
            [("prov", "http://127.0.0.1:1/subscription"), ("ghost", "http://127.0.0.1:1/ghost")],
        )
        conn.commit()
    finally:
        conn.close()


async def test_migration_backfills_the_link_and_keeps_every_config(boot_app, tmp_path) -> None:
    data = tmp_path / "volume"
    _seed_v1_volume(data)

    async with boot_app(data_dir=data) as panel:
        entries = {
            entry["name"]: entry
            for entry in (await panel.client.get("/api/panel/subscriptions")).json()
        }
        # The same-name pair is linked…
        assert entries["prov"]["config_name"] == "prov"
        assert entries["prov"]["config"] == "prov.yaml"
        # …a Subscription with no Config stays unlinked instead of guessing…
        assert entries["ghost"]["config_name"] is None
        assert entries["ghost"]["config"] is None
        # …and no Config is lost: the owned, the spare and the boot Config all
        # survive with their files.
        configs = {
            config["name"]: config
            for config in (await panel.client.get("/api/panel/configs")).json()
        }
        assert set(configs) == {"default", "prov", "spare"}
        assert configs["prov"]["subscription"] == "prov"
        assert configs["spare"]["subscription"] is None
        assert (data / "configs" / "prov.yaml").read_bytes() == SUB_ONE
        assert (data / "configs" / "spare.yaml").read_bytes() == (
            b"mixed-port: 7891\nallow-lan: true\n"
        )

        # The unlinked Subscription has no source to refresh: 404, no guess.
        resp = await panel.client.post("/api/panel/subscriptions/ghost/refresh")
        assert resp.status_code == 404
        assert "no source config" in resp.json()["detail"]

        # The link is the only relationship: the spare Config (kind
        # hand-written, no Subscription) is deletable, the owned one is not.
        assert (await panel.client.delete("/api/panel/configs/spare")).status_code == 200
        resp = await panel.client.delete("/api/panel/configs/prov")
        assert resp.status_code == 409
        assert "subscription prov" in resp.json()["detail"]

    conn = sqlite3.connect(data / "clashflow.db")
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        columns = {row[1] for row in conn.execute("PRAGMA table_info(subscriptions)")}
        assert "config_name" in columns
    finally:
        conn.close()
