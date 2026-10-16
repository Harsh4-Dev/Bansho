"""The console's own audit log. Every login, session event and action."""
from __future__ import annotations

from typing import Any

from starlette.requests import Request

from . import config, util
from .db import Tx


def record(
    t: Tx,
    request: Request | None,
    *,
    actor: str,
    action: str,
    outcome: str,
    target: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    country = ua = iph = ""
    if request is not None:
        country = util.country(request)
        ua = util.user_agent(request)
        iph = util.ip_hash(t, request)
    t.execute(
        "INSERT INTO audit (at, actor, action, target, outcome, country, ua, ip_hash, detail) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            util.now(),
            actor,
            action,
            config.redact(target)[:200],
            outcome,
            country,
            ua,
            iph,
            config.redact(util.dumps(detail or {}))[:2000],
        ),
    )


def recent(t: Tx, limit: int = 200, before_id: int | None = None) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 500))
    if before_id:
        rows = t.all("SELECT * FROM audit WHERE id < ? ORDER BY id DESC LIMIT ?", (before_id, limit))
    else:
        rows = t.all("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "at": util.utc_iso(r["at"]),
                "actor": r["actor"],
                "action": r["action"],
                "target": r["target"],
                "outcome": r["outcome"],
                "country": r["country"],
                "client": util.short_ua(r["ua"]) if r["ua"] else "",
                "ip_hash": r["ip_hash"][:8],
                "detail": util.loads(r["detail"], {}),
            }
        )
    return out
