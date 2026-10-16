"""Tiny database layer: Neon Postgres (psycopg 3) or SQLite fallback.

SQL is written once with ``?`` placeholders and portable types; for Postgres
the placeholders become ``%s`` and ``{pk}`` becomes ``BIGSERIAL PRIMARY KEY``.

* ``DATABASE_URL`` starting with ``postgres`` -> psycopg, one connection per
  ``tx()`` block (Neon's pooled endpoint does the pooling for serverless).
* anything else -> SQLite file at ``BANSHO_SQLITE_PATH`` (default
  ``bansho.db``), or ``:memory:`` for tests; a single shared connection
  guarded by a lock.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Sequence

SCHEMA_VERSION = 1

SCHEMA = [
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    # --- auth -------------------------------------------------------------
    """CREATE TABLE IF NOT EXISTS credentials (
        id TEXT PRIMARY KEY,
        public_key TEXT NOT NULL,
        sign_count BIGINT NOT NULL DEFAULT 0,
        name TEXT NOT NULL,
        transports TEXT NOT NULL DEFAULT '[]',
        aaguid TEXT NOT NULL DEFAULT '',
        backed_up INTEGER NOT NULL DEFAULT 0,
        created_at BIGINT NOT NULL,
        last_used_at BIGINT
    )""",
    """CREATE TABLE IF NOT EXISTS ceremonies (
        id TEXT PRIMARY KEY,
        purpose TEXT NOT NULL,
        challenge TEXT NOT NULL,
        session_hash TEXT,
        created_at BIGINT NOT NULL,
        expires_at BIGINT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS sessions (
        id_hash TEXT PRIMARY KEY,
        public_id TEXT NOT NULL UNIQUE,
        csrf TEXT NOT NULL,
        kind TEXT NOT NULL,
        created_at BIGINT NOT NULL,
        last_seen_at BIGINT NOT NULL,
        expires_at BIGINT NOT NULL,
        reauth_at BIGINT,
        credential_id TEXT,
        ua TEXT NOT NULL DEFAULT '',
        country TEXT NOT NULL DEFAULT '',
        revoked_at BIGINT
    )""",
    """CREATE TABLE IF NOT EXISTS recovery_codes (
        id {pk},
        code_hash TEXT NOT NULL UNIQUE,
        created_at BIGINT NOT NULL,
        used_at BIGINT
    )""",
    """CREATE TABLE IF NOT EXISTS rate_limits (
        key TEXT PRIMARY KEY,
        window_start BIGINT NOT NULL,
        count INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS lockouts (
        key TEXT PRIMARY KEY,
        failures INTEGER NOT NULL,
        last_failure_at BIGINT NOT NULL,
        locked_until BIGINT NOT NULL
    )""",
    "CREATE TABLE IF NOT EXISTS daily_salts (day TEXT PRIMARY KEY, salt TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS audit (
        id {pk},
        at BIGINT NOT NULL,
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        target TEXT NOT NULL DEFAULT '',
        outcome TEXT NOT NULL,
        country TEXT NOT NULL DEFAULT '',
        ua TEXT NOT NULL DEFAULT '',
        ip_hash TEXT NOT NULL DEFAULT '',
        detail TEXT NOT NULL DEFAULT '{}'
    )""",
    "CREATE INDEX IF NOT EXISTS audit_at ON audit (at)",
    # --- collected data ---------------------------------------------------
    """CREATE TABLE IF NOT EXISTS collect_runs (
        bucket BIGINT PRIMARY KEY,
        at BIGINT NOT NULL,
        result TEXT NOT NULL DEFAULT '{}'
    )""",
    """CREATE TABLE IF NOT EXISTS snapshots (
        id {pk},
        app TEXT NOT NULL,
        bucket BIGINT NOT NULL,
        at BIGINT NOT NULL,
        ok INTEGER NOT NULL,
        payload TEXT NOT NULL,
        UNIQUE (app, bucket)
    )""",
    "CREATE INDEX IF NOT EXISTS snapshots_app_at ON snapshots (app, at)",
    """CREATE TABLE IF NOT EXISTS bb_deltas (
        id {pk},
        bucket BIGINT NOT NULL UNIQUE,
        at BIGINT NOT NULL,
        boot_id TEXT NOT NULL,
        interval_s BIGINT NOT NULL,
        restart INTEGER NOT NULL,
        payload TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS bb_deltas_at ON bb_deltas (at)",
    """CREATE TABLE IF NOT EXISTS bb_boots (
        boot_id TEXT PRIMARY KEY,
        started_at BIGINT NOT NULL,
        first_seen_at BIGINT NOT NULL,
        last_seen_at BIGINT NOT NULL,
        last_uptime_s BIGINT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS uptime (
        id {pk},
        target TEXT NOT NULL,
        bucket BIGINT NOT NULL,
        at BIGINT NOT NULL,
        ok INTEGER NOT NULL,
        state TEXT NOT NULL,
        status INTEGER NOT NULL,
        latency_ms INTEGER NOT NULL,
        UNIQUE (target, bucket)
    )""",
    "CREATE INDEX IF NOT EXISTS uptime_target_at ON uptime (target, at)",
    """CREATE TABLE IF NOT EXISTS events (
        id {pk},
        at BIGINT NOT NULL,
        app TEXT NOT NULL,
        kind TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT '{}'
    )""",
    "CREATE INDEX IF NOT EXISTS events_at ON events (at)",
    """CREATE TABLE IF NOT EXISTS site_events (
        id {pk},
        at BIGINT NOT NULL,
        day TEXT NOT NULL,
        type TEXT NOT NULL,
        path TEXT NOT NULL,
        ref_host TEXT NOT NULL DEFAULT '',
        target TEXT NOT NULL DEFAULT '',
        country TEXT NOT NULL DEFAULT '',
        device_class TEXT NOT NULL,
        is_bot INTEGER NOT NULL,
        visitor_hash TEXT NOT NULL DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS site_events_day ON site_events (day)",
]


class Database:
    def __init__(self, url: str | None = None):
        url = url if url is not None else os.environ.get("DATABASE_URL", "").strip()
        self.is_pg = url.startswith("postgres://") or url.startswith("postgresql://")
        self.url = url
        self._lock = threading.RLock()
        self._sqlite: sqlite3.Connection | None = None
        if not self.is_pg and (os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV")) and url != ":memory:":
            # Vercel's filesystem is read-only and per-instance: a SQLite file
            # there would silently lose every session and snapshot.
            raise RuntimeError("DATABASE_URL (Neon Postgres) must be set on Vercel")
        if not self.is_pg:
            path = url if url and not url.startswith("sqlite:") else url[len("sqlite:"):] if url else ""
            path = path or os.environ.get("BANSHO_SQLITE_PATH", "bansho.db")
            self._sqlite = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            self._sqlite.row_factory = sqlite3.Row
            self._sqlite.execute("PRAGMA journal_mode=WAL" if path != ":memory:" else "PRAGMA journal_mode=MEMORY")
            self._sqlite.execute("PRAGMA foreign_keys=ON")

    # -- SQL dialect -------------------------------------------------------
    def sql(self, q: str) -> str:
        if self.is_pg:
            # psycopg uses %-placeholders, so a literal % (e.g. LIKE 'x%') is doubled.
            return q.replace("%", "%%").replace("{pk}", "BIGSERIAL PRIMARY KEY").replace("?", "%s")
        return q.replace("{pk}", "INTEGER PRIMARY KEY AUTOINCREMENT")

    @contextmanager
    def tx(self) -> Iterator["Tx"]:
        if self.is_pg:
            import psycopg
            from psycopg.rows import dict_row

            # prepare_threshold=None: Neon's pooled endpoint is PgBouncer in
            # transaction mode, where server-side prepared statements must not
            # be relied on across transactions.
            conn = psycopg.connect(self.url, row_factory=dict_row, connect_timeout=10,
                                   autocommit=True, prepare_threshold=None)
            try:
                with conn.transaction():
                    yield Tx(self, conn)
            finally:
                conn.close()
        else:
            assert self._sqlite is not None
            with self._lock:
                self._sqlite.execute("BEGIN IMMEDIATE")
                try:
                    yield Tx(self, self._sqlite)
                except BaseException:
                    self._sqlite.execute("ROLLBACK")
                    raise
                else:
                    self._sqlite.execute("COMMIT")

    def migrate(self) -> None:
        with self.tx() as t:
            for stmt in SCHEMA:
                t.execute(stmt)
            t.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?) ON CONFLICT (key) DO NOTHING",
                (str(SCHEMA_VERSION),),
            )

    def is_integrity_error(self, exc: BaseException) -> bool:
        if isinstance(exc, sqlite3.IntegrityError):
            return True
        if self.is_pg:
            import psycopg

            return isinstance(exc, psycopg.errors.UniqueViolation)
        return False


class Tx:
    def __init__(self, db: Database, conn: Any):
        self.db = db
        self.conn = conn

    def execute(self, q: str, params: Sequence[Any] = ()) -> Any:
        return self.conn.execute(self.db.sql(q), tuple(params))

    def one(self, q: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        row = self.execute(q, params).fetchone()
        return dict(row) if row is not None else None

    def all(self, q: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self.execute(q, params).fetchall()]

    def scalar(self, q: str, params: Sequence[Any] = ()) -> Any:
        row = self.execute(q, params).fetchone()
        if row is None:
            return None
        if isinstance(row, dict):
            return next(iter(row.values()))
        return row[0]

    def insert_ignore(self, q: str, params: Sequence[Any] = ()) -> bool:
        """Run an INSERT ... ON CONFLICT DO NOTHING; True if a row was written."""
        cur = self.execute(q, params)
        return (cur.rowcount or 0) > 0


_db: Database | None = None
_db_lock = threading.Lock()


def get_db() -> Database:
    global _db
    with _db_lock:
        if _db is None:
            _db = Database()
            _db.migrate()
        return _db


def set_db(db: Database | None) -> None:
    """Tests swap in a fresh in-memory database."""
    global _db
    with _db_lock:
        _db = db
        if db is not None:
            db.migrate()
