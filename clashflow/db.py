"""Metadata store for ClashFlow's `/data` volume (subscriptions, configs, active record)."""

import sqlite3
from pathlib import Path

CONFIG_KINDS = ("hand-written", "subscription-derived")
DEFAULT_CONFIG_KIND = "hand-written"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS configs (
    name TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('hand-written', 'subscription-derived')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS active_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT NOT NULL REFERENCES configs(name)
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class ConfigError(Exception):
    """Registry error whose message is safe to show to the user."""


class ConfigAlreadyExists(ConfigError):
    def __init__(self, name: str) -> None:
        super().__init__(f"config name already registered: {name}")
        self.name = name


class ConfigNotFound(ConfigError):
    def __init__(self, name: str) -> None:
        super().__init__(f"no such config: {name}")
        self.name = name


class ConfigIsActive(ConfigError):
    def __init__(self, name: str) -> None:
        super().__init__(f"config is active; activate another config before deleting: {name}")
        self.name = name


def config_filename(name: str) -> str:
    """Server-assigned filename for a Config: the registered name plus `.yaml`,
    resolved inside `/data/configs`. Registration validates names so the
    derivation can never escape that directory."""
    return f"{name}.yaml"


def validate_config_name(name: str) -> str:
    if not name.strip() or name != name.strip():
        raise ValueError(f"config name must not be empty or whitespace-padded: {name!r}")
    if name in (".", "..") or any(ch in name for ch in "/\\\0"):
        raise ValueError(f"config name contains characters unsafe in a filename: {name!r}")
    return name


class MetadataStore:
    def __init__(self, db_path: Path) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def register_config(self, name: str, kind: str) -> None:
        """Boot-time ensure: register the name unless it already exists."""
        if kind not in CONFIG_KINDS:
            raise ValueError(f"unknown config kind: {kind}")
        self._conn.execute("INSERT OR IGNORE INTO configs (name, kind) VALUES (?, ?)", (name, kind))
        self._conn.commit()

    def create_config(self, name: str, kind: str = DEFAULT_CONFIG_KIND) -> None:
        """Register a new Config; duplicate names are refused."""
        validate_config_name(name)
        if kind not in CONFIG_KINDS:
            raise ValueError(f"unknown config kind: {kind}")
        try:
            self._conn.execute("INSERT INTO configs (name, kind) VALUES (?, ?)", (name, kind))
        except sqlite3.IntegrityError as exc:
            raise ConfigAlreadyExists(name) from exc
        self._conn.commit()

    def get_config(self, name: str) -> dict[str, str | bool]:
        row = self._conn.execute(
            "SELECT c.name, c.kind, c.name = a.name AS active"
            " FROM configs c LEFT JOIN active_config a ON a.id = 1 WHERE c.name = ?",
            (name,),
        ).fetchone()
        if row is None:
            raise ConfigNotFound(name)
        return _entry(row)

    def list_configs(self) -> list[dict[str, str | bool]]:
        rows = self._conn.execute(
            "SELECT c.name, c.kind, c.name = a.name AS active"
            " FROM configs c LEFT JOIN active_config a ON a.id = 1"
            " ORDER BY c.created_at, c.name"
        ).fetchall()
        return [_entry(row) for row in rows]

    def activate_config(self, name: str) -> None:
        """Record the Active Config; the name must be registered."""
        self.get_config(name)
        self.set_active(name)

    def remove_config(self, name: str) -> None:
        """Drop a registered Config; the Active Config and unknown names are refused."""
        if self.get_active() == name:
            raise ConfigIsActive(name)
        cursor = self._conn.execute("DELETE FROM configs WHERE name = ?", (name,))
        if cursor.rowcount == 0:
            raise ConfigNotFound(name)
        self._conn.commit()

    def get_active(self) -> str | None:
        row = self._conn.execute("SELECT name FROM active_config WHERE id = 1").fetchone()
        return row[0] if row else None

    def set_active(self, name: str) -> None:
        self._conn.execute("INSERT OR REPLACE INTO active_config (id, name) VALUES (1, ?)", (name,))
        self._conn.commit()

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def _entry(row: tuple[str, str, int]) -> dict[str, str | bool]:
    name, kind, active = row
    return {"name": name, "kind": kind, "filename": config_filename(name), "active": bool(active)}
