"""Alert rules, computed on read from stored data plus the latest live data."""
from __future__ import annotations

from statistics import median
from typing import Any

from . import util
from .db import Tx

STALE_COLLECT_S = 60 * 60


def _a(level: str, app: str, code: str, title: str, detail: str = "") -> dict[str, Any]:
    return {"level": level, "app": app, "code": code, "title": title, "detail": detail}


def last_states(t: Tx, target: str, n: int) -> list[dict[str, Any]]:
    return t.all("SELECT state, at, status FROM uptime WHERE target = ? ORDER BY at DESC LIMIT ?", (target, n))


def compute(t: Tx, hh_summary: dict[str, Any] | None, hh_error: str | None,
            bb_error: str | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    now = util.now()

    # -- collector health ---------------------------------------------------------
    last = t.one("SELECT at FROM collect_runs ORDER BY bucket DESC LIMIT 1")
    if last is None:
        out.append(_a("info", "bansho", "collector_never", "Collector has not run yet",
                      "Set up cron-job.org or the GitHub workflow to call /api/cron/collect."))
    elif now - last["at"] > STALE_COLLECT_S:
        out.append(_a("warn", "bansho", "collector_stale", "Collector is stale",
                      f"Last run {round((now - last['at']) / 60)} min ago."))

    # -- app down -------------------------------------------------------------------
    for target, label in (("harahachi", "Harahachi"), ("landing", "Landing site")):
        rows = last_states(t, target, 2)
        if rows and rows[0]["state"] in ("down", "misconfigured"):
            level = "crit" if len(rows) > 1 and rows[1]["state"] == "down" else "warn"
            out.append(_a(level, target, "down", f"{label} is down",
                          f"Last check: {rows[0]['state']} (HTTP {rows[0]['status'] or 'no response'})."))
    bb_rows = last_states(t, "busboard", 3)
    if len(bb_rows) == 3 and all(r["state"] == "down" for r in bb_rows):
        out.append(_a("crit", "busboard", "down", "Bus Board is down", "Three failed checks in a row (not sleep)."))

    if hh_error == "unauthorized":
        out.append(_a("crit", "harahachi", "token", "Harahachi rejected the ops token",
                      "HARAHACHI_OPS_TOKEN must equal SEKISHO_OPS_TOKEN on Harahachi."))
    if bb_error == "unauthorized":
        out.append(_a("crit", "busboard", "token", "Bus Board rejected the ops token",
                      "BUSBOARD_OPS_TOKEN must equal MAPS_OPS_TOKEN on Bus Board."))

    # -- Harahachi rates ----------------------------------------------------------------
    today = (hh_summary or {}).get("today") or {}
    req = float(today.get("requests") or 0)
    e5 = float(today.get("errors_5xx") or 0)
    if req >= 50 and e5 / req > 0.05:
        out.append(_a("crit", "harahachi", "5xx_spike", "Harahachi 5xx spike",
                      f"{int(e5)} of {int(req)} requests today failed ({e5 / req:.0%})."))
    calls = float(today.get("ai_calls") or 0)
    aierr = float(today.get("ai_errors") or 0)
    if calls >= 10 and aierr / calls > 0.10:
        out.append(_a("warn", "harahachi", "ai_errors", "AI error rate above 10 %",
                      f"{int(aierr)} of {int(calls)} AI calls failed today ({aierr / calls:.0%})."))
    caps = int(today.get("cap_hits") or 0)
    if caps > 0:
        out.append(_a("warn", "harahachi", "cap_hits", "Daily AI cap reached",
                      f"{caps} request(s) hit the daily cap today."))

    # -- Bus Board -----------------------------------------------------------------------
    restarts = int(t.scalar("SELECT COUNT(*) FROM events WHERE app = 'busboard' AND kind = 'restart' AND at > ?",
                            (now - 2 * 3600,)) or 0)
    if restarts >= 3:
        out.append(_a("crit", "busboard", "restart_storm", "Bus Board restart storm",
                      f"{restarts} restarts (not sleep wakes) in the last 2 hours."))
    rows = t.all("SELECT payload FROM bb_deltas WHERE at > ? ORDER BY at DESC LIMIT 4", (now - 3600,))
    tot = sum(float((util.loads(r["payload"], {}).get("totals") or {}).get("requests") or 0) for r in rows)
    e5b = sum(float((util.loads(r["payload"], {}).get("totals") or {}).get("errors_5xx") or 0) for r in rows)
    if tot >= 50 and e5b / tot > 0.05:
        out.append(_a("crit", "busboard", "5xx_spike", "Bus Board 5xx spike",
                      f"{int(e5b)} of {int(tot)} requests in the last hour failed."))

    # -- ingest anomalies ------------------------------------------------------------------
    day = util.today()
    rows = t.all(
        "SELECT day, COUNT(*) AS n, SUM(is_bot) AS bots FROM site_events WHERE day >= ? GROUP BY day",
        (util.day_of(now - 8 * 86400),),
    )
    by_day = {r["day"]: (int(r["n"]), int(r["bots"] or 0)) for r in rows}
    n_today, bots_today = by_day.get(day, (0, 0))
    history = [v[0] for k, v in by_day.items() if k != day]
    base = median(history) if history else 0
    if n_today >= 200 and n_today > 5 * max(base, 1):
        out.append(_a("warn", "landing", "ingest_spike", "Unusual landing traffic",
                      f"{n_today} events today vs a typical {int(base)}."))
    if n_today >= 50 and bots_today / n_today > 0.5:
        out.append(_a("warn", "landing", "ingest_bots", "Mostly bot traffic on landing",
                      f"{bots_today} of {n_today} events today look automated."))

    order = {"crit": 0, "warn": 1, "info": 2}
    out.sort(key=lambda a: order.get(a["level"], 3))
    return out
