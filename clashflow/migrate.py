"""Volume-level schema migration for `/data`: version gate, backup, chain.

ADR-0006's three decisions live here, in the order the boot needs them:

1. **Version gate.** `PRAGMA user_version` says who wrote the database. A
   volume stamped HIGHER than this image understands was written by a newer
   ClashFlow; starting would corrupt it silently, so the boot refuses and the
   message names the backup folder to restore.
2. **Backup before anything.** A volume that needs migrating is first copied
   whole — the metadata database and the `configs/` directory — to
   `/data/backup/v<from>-<UTC timestamp>/`. A backup that cannot be written
   aborts the migration and leaves the volume as it was; a partial backup
   folder is removed so the volume is unchanged in the failure case too.
3. **Ordered chain, in one transaction.** `MIGRATIONS` maps a target version
   to one immutable step. Versions run in order inside a single SQLite
   transaction that also stamps `user_version`, so a failing step rolls the
   database back to its pre-migration state (the backup is still the record of
   it).

Irreversible by design: backups are never cleaned up automatically. Rolling
back means stopping the container, restoring the backup folder over the volume
and running the older image.
"""

import logging
import shutil
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from clashflow.db import SCHEMA_VERSION, ensure_schema

log = logging.getLogger(__name__)

DB_FILENAME = "clashflow.db"
CONFIGS_DIRNAME = "configs"
BACKUP_DIRNAME = "backup"

type Migration = Callable[[sqlite3.Connection], None]

# Version -> the step that takes a database FROM `version - 1` TO `version`.
# Step 1 is the V1 schema itself, which `ensure_schema` can also apply to a
# database that never had a version; later steps append here and are never
# edited once released (a volume may stop at any version).
MIGRATIONS: dict[int, Migration] = {
    1: ensure_schema,
}


class SchemaTooNew(Exception):
    """The volume's schema is newer than this image understands; starting
    would let an older code path write a newer database."""

    def __init__(self, stored: int, supported: int, backup_dir: Path) -> None:
        super().__init__(
            f"data volume was written by a newer ClashFlow: stored schema v{stored}, "
            f"this image supports v{supported}. Restore the backup under {backup_dir} "
            "before running this image"
        )
        self.stored = stored
        self.supported = supported
        self.backup_dir = backup_dir


class MigrationBackupFailed(Exception):
    """The pre-migration backup could not be written; nothing was migrated."""


class MigrationFailed(Exception):
    """A migration step failed and was rolled back; the backup folder from
    this boot is the state to restore."""


def migrate_volume(data_dir: Path) -> Path | None:
    """Bring the volume at `data_dir` to the current schema version.

    Returns the backup folder when a migration ran, and None when there was
    nothing to do (a fresh volume, or one already at the current version).
    Raises `SchemaTooNew` before touching a newer volume and
    `MigrationBackupFailed` before touching an unmigrated one."""
    db_path = data_dir / DB_FILENAME
    if not db_path.is_file():
        # A fresh volume: nothing to preserve and nothing to migrate. The
        # database is created directly at the current version, so the first
        # boot does not leave a meaningless backup behind.
        data_dir.mkdir(parents=True, exist_ok=True)
        _create_current(db_path)
        return None
    stored = read_version(db_path)
    if stored > SCHEMA_VERSION:
        raise SchemaTooNew(stored, SCHEMA_VERSION, _restore_hint(data_dir))
    if stored == SCHEMA_VERSION:
        return None
    backup = backup_volume(data_dir, from_version=stored)
    try:
        _run_chain(db_path, from_version=stored)
    except Exception as exc:
        raise MigrationFailed(
            f"migration from schema v{stored} to v{SCHEMA_VERSION} failed and was "
            f"rolled back: {exc}. The backup is {backup}"
        ) from exc
    log.info("migrated the data volume from schema v%d to v%d", stored, SCHEMA_VERSION)
    return backup


def read_version(db_path: Path) -> int:
    """The stored schema version; read-only, so a gate check never creates or
    modifies the database it is judging."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def backup_volume(data_dir: Path, *, from_version: int) -> Path:
    """Copy the database and the configs directory into a timestamped folder
    inside the volume. A failed copy removes the partial folder and raises, so
    the volume is left exactly as it was."""
    target = _backup_path(data_dir, from_version)
    try:
        target.mkdir(parents=True)
        _copy_database(data_dir / DB_FILENAME, target / DB_FILENAME)
        configs = data_dir / CONFIGS_DIRNAME
        if configs.is_dir():
            shutil.copytree(configs, target / CONFIGS_DIRNAME)
    except (OSError, sqlite3.Error) as exc:
        shutil.rmtree(target, ignore_errors=True)
        raise MigrationBackupFailed(
            f"migration backup failed, nothing was migrated: {exc}"
        ) from exc
    log.info("backed up the data volume to %s before migrating v%d", target, from_version)
    return target


def _create_current(db_path: Path) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN")
        ensure_schema(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    finally:
        conn.close()


def _run_chain(db_path: Path, *, from_version: int) -> None:
    """Run every step above `from_version` in ONE transaction that also stamps
    the version, so a failure leaves the pre-migration database intact."""
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN")
        for version in range(from_version + 1, SCHEMA_VERSION + 1):
            MIGRATIONS[version](conn)
            conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _connect(db_path: Path) -> sqlite3.Connection:
    # Autocommit mode: this module issues its own BEGIN/COMMIT, and Python's
    # implicit transactions would otherwise open a second one.
    return sqlite3.connect(db_path, isolation_level=None)


def _copy_database(source: Path, target: Path) -> None:
    """SQLite's own backup API: a consistent copy even if another process
    still holds the database open."""
    src = sqlite3.connect(source)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _backup_path(data_dir: Path, from_version: int) -> Path:
    root = data_dir / BACKUP_DIRNAME
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    base = f"v{from_version}-{stamp}"
    candidate = root / base
    # Two migrations inside one second must not share a folder: the second
    # gets a numeric suffix instead of overwriting the first record.
    suffix = 2
    while candidate.exists():
        candidate = root / f"{base}-{suffix}"
        suffix += 1
    return candidate


def _restore_hint(data_dir: Path) -> Path:
    """The folder the gate message points at: the newest backup the volume
    holds (the one a newer image left before migrating), else the directory
    those folders live in."""
    root = data_dir / BACKUP_DIRNAME
    if not root.is_dir():
        return root
    folders = [path for path in root.iterdir() if path.is_dir()]
    if not folders:
        return root
    return max(folders, key=lambda path: path.stat().st_mtime)
