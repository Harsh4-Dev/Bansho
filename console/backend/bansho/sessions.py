"""Server-side sessions.

* The browser holds only an opaque 256-bit random id in a ``__Host-`` cookie
  (Secure, HttpOnly, SameSite=Strict, Path=/). The database stores its
  SHA-256, so a database leak does not hand out live sessions.
* 8 h absolute lifetime, 1 h idle timeout (recovery sessions: 15 min, and
  they may only register a passkey).
* A per-session CSRF token must accompany every non-GET request in the
  ``X-Bansho-CSRF`` header.
* Step-up: ``reauth_at`` is set only by an explicit passkey re-authentication
  ceremony; destructive actions need it to be < 5 minutes old.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

from . import config, util
from .db import Tx


@dataclass
class Session:
    id_hash: str
    public_id: str
    csrf: str
    kind: str  # "full" | "recovery"
    created_at: int
    last_seen_at: int
    expires_at: int
    reauth_at: int | None
    credential_id: str | None

    @property
    def idle_expires_at(self) -> int:
        return self.last_seen_at + config.SESSION_IDLE_S

    @property
    def step_up_fresh(self) -> bool:
        return self.reauth_at is not None and util.now() - self.reauth_at < config.STEP_UP_WINDOW_S

    def public(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "csrf": self.csrf,
            "expires_at": util.utc_iso(self.expires_at),
            "idle_expires_at": util.utc_iso(min(self.idle_expires_at, self.expires_at)),
            "step_up_until": util.utc_iso(self.reauth_at + config.STEP_UP_WINDOW_S) if self.step_up_fresh and self.reauth_at else None,
        }


def create(t: Tx, request: Request, response: Response, *, kind: str, credential_id: str | None) -> Session:
    raw = secrets.token_urlsafe(32)
    now = util.now()
    life = config.SESSION_ABSOLUTE_S if kind == "full" else config.RECOVERY_SESSION_S
    s = Session(
        id_hash=util.sha256_hex(raw),
        public_id=secrets.token_hex(8),
        csrf=secrets.token_urlsafe(32),
        kind=kind,
        created_at=now,
        last_seen_at=now,
        expires_at=now + life,
        reauth_at=None,
        credential_id=credential_id,
    )
    t.execute(
        "INSERT INTO sessions (id_hash, public_id, csrf, kind, created_at, last_seen_at, expires_at, "
        "reauth_at, credential_id, ua, country) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (s.id_hash, s.public_id, s.csrf, s.kind, s.created_at, s.last_seen_at, s.expires_at, None,
         credential_id, util.user_agent(request), util.country(request)),
    )
    set_cookie(response, raw, life)
    return s


def set_cookie(response: Response, raw: str, max_age: int) -> None:
    response.set_cookie(
        config.SESSION_COOKIE,
        raw,
        max_age=max_age,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )


def clear_cookie(response: Response) -> None:
    response.delete_cookie(config.SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="strict")


def load(t: Tx, request: Request, *, touch: bool = True) -> Session | None:
    raw = request.cookies.get(config.SESSION_COOKIE)
    if not raw or len(raw) > 128:
        return None
    row = t.one("SELECT * FROM sessions WHERE id_hash = ?", (util.sha256_hex(raw),))
    if row is None or row["revoked_at"] is not None:
        return None
    now = util.now()
    if now >= row["expires_at"] or now >= row["last_seen_at"] + config.SESSION_IDLE_S:
        t.execute("UPDATE sessions SET revoked_at = ? WHERE id_hash = ? AND revoked_at IS NULL", (now, row["id_hash"]))
        return None
    if touch and now - row["last_seen_at"] >= 15:
        t.execute("UPDATE sessions SET last_seen_at = ? WHERE id_hash = ?", (now, row["id_hash"]))
        row["last_seen_at"] = now
    return Session(
        id_hash=row["id_hash"],
        public_id=row["public_id"],
        csrf=row["csrf"],
        kind=row["kind"],
        created_at=row["created_at"],
        last_seen_at=row["last_seen_at"],
        expires_at=row["expires_at"],
        reauth_at=row["reauth_at"],
        credential_id=row["credential_id"],
    )


def revoke(t: Tx, id_hash: str) -> None:
    t.execute("UPDATE sessions SET revoked_at = ? WHERE id_hash = ? AND revoked_at IS NULL", (util.now(), id_hash))


def mark_reauth(t: Tx, s: Session) -> None:
    now = util.now()
    t.execute("UPDATE sessions SET reauth_at = ?, last_seen_at = ? WHERE id_hash = ?", (now, now, s.id_hash))
    s.reauth_at = now


def list_active(t: Tx, current: Session) -> list[dict[str, Any]]:
    now = util.now()
    rows = t.all(
        "SELECT * FROM sessions WHERE revoked_at IS NULL AND expires_at > ? AND last_seen_at > ? ORDER BY created_at DESC",
        (now, now - config.SESSION_IDLE_S),
    )
    return [
        {
            "id": r["public_id"],
            "kind": r["kind"],
            "current": r["id_hash"] == current.id_hash,
            "created_at": util.utc_iso(r["created_at"]),
            "last_seen_at": util.utc_iso(r["last_seen_at"]),
            "expires_at": util.utc_iso(r["expires_at"]),
            "client": util.short_ua(r["ua"]),
            "country": r["country"],
        }
        for r in rows
    ]
