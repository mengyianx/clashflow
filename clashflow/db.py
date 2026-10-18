"""Metadata store for ClashFlow's `/data` volume (subscriptions, configs, active record)."""

import sqlite3
from pathlib import Path

CONFIG_KINDS = ("handwritten", "subscription")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS configs (
    name TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('handwritten', 'subscription')),
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


class MetadataStore:
    def __init__(self, db_path: Path) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def register_config(self, name: str, kind: str) -> None:
        if kind not in CONFIG_KINDS:
            raise ValueError(f"unknown config kind: {kind}")
        self._conn.execute("INSERT OR IGNORE INTO configs (name, kind) VALUES (?, ?)", (name, kind))
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
