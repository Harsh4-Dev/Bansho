"""DB-backed fixed-window rate limits and progressive lockout.

Both survive cold starts and are shared by every serverless instance because
they live in the database, not in process memory.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import util
from .db import Tx

# Progressive lockout: the first FREE_FAILURES failures cost nothing; after
# that each failure locks the key for BASE_LOCK_S * 2^(n - FREE_FAILURES - 1),
# capped at MAX_LOCK_S. Failures older than FAILURE_MEMORY_S are forgotten.
FREE_FAILURES = 4
BASE_LOCK_S = 60
MAX_LOCK_S = 3600
FAILURE_MEMORY_S = 24 * 3600


@dataclass
class Verdict:
    allowed: bool
    retry_after: int = 0


def hit(t: Tx, key: str, limit: int, window_s: int) -> Verdict:
    """Count one request against ``key``; deny when over ``limit`` per window."""
    now = util.now()
    window_start = now - (now % window_s)
    row = t.one("SELECT window_start, count FROM rate_limits WHERE key = ?", (key,))
    if row is None or row["window_start"] != window_start:
        t.execute(
            "INSERT INTO rate_limits (key, window_start, count) VALUES (?, ?, 1) "
            "ON CONFLICT (key) DO UPDATE SET window_start = excluded.window_start, count = 1",
            (key, window_start),
        )
        return Verdict(True)
    if row["count"] >= limit:
        return Verdict(False, window_start + window_s - now)
    t.execute("UPDATE rate_limits SET count = count + 1 WHERE key = ?", (key,))
    return Verdict(True)


def locked(t: Tx, key: str) -> Verdict:
    row = t.one("SELECT locked_until FROM lockouts WHERE key = ?", (key,))
    now = util.now()
    if row and row["locked_until"] > now:
        return Verdict(False, row["locked_until"] - now)
    return Verdict(True)


def fail_global(t: Tx, key: str, free: int) -> int:
    """Same as fail() with a larger allowance - for counters shared by all IPs,
    where locking too eagerly would let anyone lock the owner out."""
    return fail(t, key, free=free)


def fail(t: Tx, key: str, free: int = FREE_FAILURES) -> int:
    """Record a failure; returns the lock duration now in force (0 = none)."""
    now = util.now()
    row = t.one("SELECT failures, last_failure_at FROM lockouts WHERE key = ?", (key,))
    failures = 1
    if row and now - row["last_failure_at"] < FAILURE_MEMORY_S:
        failures = row["failures"] + 1
    lock_s = 0
    if failures > free:
        lock_s = min(MAX_LOCK_S, BASE_LOCK_S * (2 ** min(failures - free - 1, 16)))
    t.execute(
        "INSERT INTO lockouts (key, failures, last_failure_at, locked_until) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (key) DO UPDATE SET failures = excluded.failures, "
        "last_failure_at = excluded.last_failure_at, locked_until = excluded.locked_until",
        (key, failures, now, now + lock_s),
    )
    return lock_s


def clear(t: Tx, key: str) -> None:
    t.execute("DELETE FROM lockouts WHERE key = ?", (key,))


def prune(t: Tx) -> None:
    now = util.now()
    t.execute("DELETE FROM rate_limits WHERE window_start < ?", (now - 2 * 86400,))
    t.execute("DELETE FROM lockouts WHERE last_failure_at < ? AND locked_until < ?", (now - FAILURE_MEMORY_S, now))
    t.execute("DELETE FROM ceremonies WHERE expires_at < ?", (now,))
