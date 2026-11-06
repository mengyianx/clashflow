"""Metadata store for ClashFlow's `/data` volume (subscriptions, configs, active record).

The database carries a schema version in `PRAGMA user_version` (ADR-0006):
`clashflow.migrate` reads it, backs the volume up and runs the ordered chain
before this module's store is opened, and refuses a volume stamped by a newer
image. The store itself only creates the baseline schema idempotently.
"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

CONFIG_KINDS = ("hand-written", "subscription-derived")
DEFAULT_CONFIG_KIND = "hand-written"
# The kind every Subscription add registers its Config with (one Subscription
# owns exactly one Subscription-derived Config); named so sibling code never
# hard-codes the literal.
SUBSCRIPTION_DERIVED_KIND = "subscription-derived"

_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
CREATE TABLE IF NOT EXISTS configs (
    name TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('hand-written', 'subscription-derived')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
""",
    """
CREATE TABLE IF NOT EXISTS active_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT NOT NULL REFERENCES configs(name)
)
""",
    """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
""",
    # The single global Overlay (ADR-0004): one row, JSON in `data`, replace
    # semantics. Sparse on purpose — an absent key is not owned and the Source
    # Config's value passes through.
    """
CREATE TABLE IF NOT EXISTS overlay (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    data TEXT NOT NULL
)
""",
    # One row per Subscription (ticket #12). The name is both the Subscription
    # identity and its Subscription-derived Config's name (plan §3); plain
    # columns keep the table ALTER-TABLE-extensible.
    """
CREATE TABLE IF NOT EXISTS subscriptions (
    name TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    last_refresh_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
""",
)

# The schema version this image writes and understands (`PRAGMA user_version`,
# ADR-0006). A volume stamped higher was written by a newer image and refuses
# to start; a lower one is backed up and migrated (clashflow.migrate).
SCHEMA_VERSION = 1

# Additive schema for `subscriptions` (ticket #13), applied with
# ALTER TABLE ... ADD COLUMN after the base schema. NULL is the documented
# fallback: NULL user_agent -> the module default UA on fetches,
# NULL update_interval_minutes -> the global default interval (plan §1: 24h,
# DEFAULT_UPDATE_INTERVAL_MINUTES in clashflow.refresh, consumed by #16's
# scheduler). Later tickets APPEND to this tuple — never edit a
# _SCHEMA_STATEMENTS body, databases from earlier tickets migrate in place.
_ADDITIVE_SUBSCRIPTION_COLUMNS = (
    "user_agent TEXT",
    "update_interval_minutes INTEGER",
    # The failure class of the last failed refresh (ticket #27), written by
    # the shared refresh dispatch in clashflow.refresh. NULL means "no class":
    # a Subscription that never failed, one whose record predates the class,
    # or a failure outside the refresh pipeline (a vanished Config
    # registration). The raw error text lives in `last_error` beside it.
    "last_error_class TEXT",
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Apply the baseline schema and the additive columns; migration step 1.

    Idempotent and version-free on purpose: it is what a V1 volume's migration
    runs (clashflow.migrate stamps the version afterwards), and it is also
    what a direct `MetadataStore` open applies so a test or a script can build
    a store without going through the volume migration. Every statement is an
    `IF NOT EXISTS` or a conditional `ALTER TABLE`, so running it twice never
    changes anything."""
    for statement in _SCHEMA_STATEMENTS:
        conn.execute(statement)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(subscriptions)")}
    for column in _ADDITIVE_SUBSCRIPTION_COLUMNS:
        if column.split()[0] not in existing:
            conn.execute(f"ALTER TABLE subscriptions ADD COLUMN {column}")


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


class SubscriptionAlreadyExists(ConfigError):
    def __init__(self, name: str) -> None:
        super().__init__(f"subscription already registered: {name}")
        self.name = name


class SubscriptionNotFound(ConfigError):
    def __init__(self, name: str) -> None:
        super().__init__(f"no such subscription: {name}")
        self.name = name


def config_filename(name: str) -> str:
    """Server-assigned filename for a Config: the registered name plus `.yaml`,
    resolved inside `/data/configs`. Registration validates names so the
    derivation can never escape that directory."""
    return f"{name}.yaml"


# Rendered artifacts live beside the Source Configs, never INSIDE
# `/data/runtime/` — that directory holds Runtime Update payloads (ADR-0003)
# and the two must never be confused (ADR-0004).
RUNTIME_CONFIGS_DIRNAME = "runtime-configs"


def runtime_config_path(data_dir: Path, name: str) -> Path:
    """Where the Kernel runs a Config from: `/data/runtime-configs/<name>.yaml`,
    derived deterministically from the Source Config's name."""
    return data_dir / RUNTIME_CONFIGS_DIRNAME / config_filename(name)


def validate_config_name(name: str) -> str:
    if not name.strip() or name != name.strip():
        raise ValueError(f"config name must not be empty or whitespace-padded: {name!r}")
    if name in (".", "..") or any(ch in name for ch in "/\\\0"):
        raise ValueError(f"config name contains characters unsafe in a filename: {name!r}")
    return name


class MetadataStore:
    def __init__(self, db_path: Path) -> None:
        self._conn = sqlite3.connect(db_path)
        ensure_schema(self._conn)
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

    def create_subscription(self, name: str, url: str) -> None:
        """Register a new Subscription; duplicate names are refused. The name
        doubles as the Subscription-derived Config's name (plan §3)."""
        validate_config_name(name)
        try:
            self._conn.execute("INSERT INTO subscriptions (name, url) VALUES (?, ?)", (name, url))
        except sqlite3.IntegrityError as exc:
            raise SubscriptionAlreadyExists(name) from exc
        self._conn.commit()

    def get_subscription(self, name: str) -> dict[str, str | int | None]:
        row = self._conn.execute(
            _SELECT_SUBSCRIPTION + " WHERE name = ?",
            (name,),
        ).fetchone()
        if row is None:
            raise SubscriptionNotFound(name)
        return _subscription_entry(row)

    def list_subscriptions(self) -> list[dict[str, str | int | None]]:
        rows = self._conn.execute(_SELECT_SUBSCRIPTION + " ORDER BY created_at, name").fetchall()
        return [_subscription_entry(row) for row in rows]

    def set_subscription_fetch_settings(
        self, name: str, user_agent: str | None, update_interval_minutes: int | None
    ) -> None:
        """Persist the per-Subscription fetch settings (ticket #13); None
        clears the column back to its documented default fallback."""
        cursor = self._conn.execute(
            "UPDATE subscriptions SET user_agent = ?, update_interval_minutes = ? WHERE name = ?",
            (user_agent, update_interval_minutes, name),
        )
        if cursor.rowcount == 0:
            raise SubscriptionNotFound(name)
        self._conn.commit()

    def remove_subscription(self, name: str) -> None:
        cursor = self._conn.execute("DELETE FROM subscriptions WHERE name = ?", (name,))
        if cursor.rowcount == 0:
            raise SubscriptionNotFound(name)
        self._conn.commit()

    def mark_subscription_refreshed(self, name: str) -> None:
        """Record a successful refresh: stamp the time and clear the failure
        (its class and its raw text alike)."""
        self._conn.execute(
            "UPDATE subscriptions SET last_refresh_at = ?, last_error = NULL,"
            " last_error_class = NULL WHERE name = ?",
            (_utc_now(), name),
        )
        self._conn.commit()

    def mark_subscription_failed(self, name: str, reason: str, failure_class: str | None) -> None:
        """Record a failed refresh; the reason and its class stay until the
        next success, while `last_refresh_at` keeps the last SUCCESSFUL
        refresh time. `failure_class` is None for a failure outside the
        refresh pipeline: the raw text is kept, no class is claimed."""
        self._conn.execute(
            "UPDATE subscriptions SET last_error = ?, last_error_class = ? WHERE name = ?",
            (reason, failure_class, name),
        )
        self._conn.commit()

    def get_overlay(self) -> dict[str, object]:
        """The global Overlay; absent means the empty Overlay (own nothing)."""
        row = self._conn.execute("SELECT data FROM overlay WHERE id = 1").fetchone()
        return json.loads(row[0]) if row else {}

    def set_overlay(self, values: dict[str, object]) -> None:
        """Replace the Overlay wholesale (ADR-0004: replace semantics only)."""
        self._conn.execute(
            "INSERT OR REPLACE INTO overlay (id, data) VALUES (1, ?)",
            (json.dumps(values, ensure_ascii=False),),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


_SELECT_SUBSCRIPTION = (
    "SELECT name, url, last_refresh_at, last_error, user_agent, update_interval_minutes,"
    " last_error_class FROM subscriptions"
)


def _entry(row: tuple[str, str, int]) -> dict[str, str | bool]:
    name, kind, active = row
    return {"name": name, "kind": kind, "filename": config_filename(name), "active": bool(active)}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _subscription_entry(
    row: tuple[str, str, str | None, str | None, str | None, int | None, str | None],
) -> dict[str, str | int | None]:
    (
        name,
        url,
        last_refresh_at,
        last_error,
        user_agent,
        update_interval_minutes,
        last_error_class,
    ) = row
    # State is derived, never stored: a failure shows until the next success;
    # a never-refreshed Subscription is "pending", not "ok".
    if last_error is not None:
        state = "error"
    elif last_refresh_at is not None:
        state = "ok"
    else:
        state = "pending"
    return {
        "name": name,
        "url": url,
        "config": config_filename(name),
        "state": state,
        "last_refresh_at": last_refresh_at,
        "last_error": last_error,
        # The failure class of the last failed refresh (ticket #27): NULL for
        # a Subscription that never failed and for a record written before
        # the class existed — the panel shows no label for those.
        "last_error_class": last_error_class,
        # Fetch settings (ticket #13): NULL = the documented default
        # fallback (module default UA; global default interval for #16).
        "user_agent": user_agent,
        "update_interval_minutes": update_interval_minutes,
    }
