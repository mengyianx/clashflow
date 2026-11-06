"""Issue #38: schema version, migration chain, backup and the version gate.

Seams: a seeded V1 volume plus a real boot (`boot_app`), because the contract
is "this volume boots, that one refuses to" — not "a function was called".
The seed writes the V1 schema literally, the way the previous image left it
(no `user_version` stamp at all).
"""

import shutil
import sqlite3
from pathlib import Path

import pytest

from clashflow.db import SCHEMA_VERSION
from clashflow.migrate import MigrationBackupFailed, SchemaTooNew

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

V1_CONFIG = "mixed-port: 7890\nallow-lan: true\n"


def _version(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def _seed_v1_volume(data: Path) -> None:
    """A volume exactly as the V1 image left it: the V1 tables, an Active
    Config, and no schema version."""
    (data / "configs").mkdir(parents=True)
    (data / "configs" / "default.yaml").write_text(V1_CONFIG)
    conn = sqlite3.connect(data / "clashflow.db")
    try:
        conn.executescript(V1_SCHEMA)
        conn.execute("INSERT INTO configs (name, kind) VALUES ('default', 'hand-written')")
        conn.execute("INSERT INTO active_config (id, name) VALUES (1, 'default')")
        conn.execute("INSERT INTO meta (key, value) VALUES ('secret', 'v1-secret')")
        conn.commit()
    finally:
        conn.close()
    assert _version(data / "clashflow.db") == 0


async def test_a_v1_volume_is_backed_up_then_migrated_and_the_kernel_runs_the_rendered_config(
    boot_app, tmp_path
) -> None:
    data = tmp_path / "volume"
    _seed_v1_volume(data)

    async with boot_app(data_dir=data) as panel:
        health = (await panel.client.get("/api/service/health")).json()
        assert health["kernel"] == "up"
        assert health["active_config"] == "default"

        # The Kernel runs the RENDERED Runtime Config, not the Source Config.
        runtime = data / "runtime-configs" / "default.yaml"
        assert runtime.is_file()
        pid = panel.app.state.kernel.pid
        assert pid is not None
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        assert str(runtime).encode() in cmdline

    # The volume now carries this image's schema version.
    assert _version(data / "clashflow.db") == SCHEMA_VERSION

    # Exactly one backup, named for the version it came from, holding BOTH the
    # metadata database and the configs directory.
    backups = sorted((data / "backup").iterdir())
    assert len(backups) == 1
    backup = backups[0]
    assert backup.name.startswith("v0-")
    assert (backup / "clashflow.db").is_file()
    assert (backup / "configs" / "default.yaml").read_text() == V1_CONFIG
    # The backup's database is the PRE-migration one: still unstamped.
    assert _version(backup / "clashflow.db") == 0

    # The migrated database kept the V1 records.
    conn = sqlite3.connect(data / "clashflow.db")
    try:
        assert conn.execute("SELECT name FROM active_config WHERE id = 1").fetchone() == (
            "default",
        )
        assert conn.execute("SELECT value FROM meta WHERE key = 'secret'").fetchone() == (
            "v1-secret",
        )
        # The column the V1 image did not have was added by the migration.
        columns = {row[1] for row in conn.execute("PRAGMA table_info(subscriptions)")}
        assert "last_error_class" in columns
    finally:
        conn.close()


async def test_a_failed_backup_aborts_the_migration_and_leaves_the_volume_unchanged(
    boot_app, tmp_path, monkeypatch
) -> None:
    data = tmp_path / "volume"
    _seed_v1_volume(data)
    db_before = (data / "clashflow.db").read_bytes()
    configs_before = {path.name: path.read_bytes() for path in (data / "configs").iterdir()}

    def refuse_copy(*args: object, **kwargs: object) -> None:
        raise OSError("backup volume is full")

    monkeypatch.setattr(shutil, "copytree", refuse_copy)

    with pytest.raises(MigrationBackupFailed, match="backup volume is full"):
        async with boot_app(data_dir=data):
            pass

    # Nothing migrated, nothing written, no half-backup left behind.
    assert (data / "clashflow.db").read_bytes() == db_before
    assert _version(data / "clashflow.db") == 0
    assert {path.name: path.read_bytes() for path in (data / "configs").iterdir()} == configs_before
    backup_root = data / "backup"
    assert not backup_root.exists() or not list(backup_root.iterdir())


async def test_a_volume_written_by_a_newer_image_refuses_to_start_naming_the_backup(
    boot_app, tmp_path
) -> None:
    data = tmp_path / "volume"
    _seed_v1_volume(data)
    conn = sqlite3.connect(data / "clashflow.db")
    try:
        conn.execute("PRAGMA user_version = 999")
        conn.commit()
    finally:
        conn.close()
    # The backup the newer image left before migrating this volume.
    backup = data / "backup" / "v1-20260101T000000Z"
    backup.mkdir(parents=True)
    (backup / "clashflow.db").write_bytes(b"newer-image-database")

    with pytest.raises(SchemaTooNew) as excinfo:
        async with boot_app(data_dir=data):
            pass

    message = str(excinfo.value)
    assert "该数据卷已被更新版本的 ClashFlow 使用" in message
    assert "v999" in message
    assert f"v{SCHEMA_VERSION}" in message
    assert str(backup) in message
    # The gate runs before anything is written: the volume is untouched.
    assert _version(data / "clashflow.db") == 999


async def test_a_second_boot_performs_no_migration(boot_app, tmp_path) -> None:
    data = tmp_path / "volume"
    _seed_v1_volume(data)

    async with boot_app(data_dir=data):
        pass
    backups = sorted((data / "backup").iterdir())
    assert len(backups) == 1
    db_after_migration = (data / "clashflow.db").read_bytes()

    async with boot_app(data_dir=data) as panel:
        assert (await panel.client.get("/api/service/health")).json()["kernel"] == "up"

    # Same single backup folder, and the database was not rewritten by a
    # second migration.
    assert sorted((data / "backup").iterdir()) == backups
    assert (data / "clashflow.db").read_bytes() == db_after_migration


async def test_a_fresh_volume_is_created_at_the_current_version_without_a_backup(
    boot_app, tmp_path
) -> None:
    data = tmp_path / "fresh"

    async with boot_app(data_dir=data) as panel:
        assert (await panel.client.get("/api/service/health")).json()["kernel"] == "up"

    assert _version(data / "clashflow.db") == SCHEMA_VERSION
    # Nothing to preserve on a volume that never held user data.
    assert not (data / "backup").exists()
