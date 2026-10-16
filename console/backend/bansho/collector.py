"""GET /api/cron/collect - snapshots, Bus Board deltas, uptime checks.

Idempotent per 5-minute bucket: the bucket is claimed first (a unique row in
``collect_runs``), so two overlapping pings (cron-job.org + GitHub Actions +
Vercel's daily cron) can never double-count.

Bus Board keeps its counters in memory and they restart from zero whenever
Render puts the process to sleep or redeploys it. Each process has a random
``boot_id``; ``bb_delta`` turns two cumulative-since-boot snapshots into the
traffic of the interval between them:

* same boot_id, counters grew  -> delta = current - previous
* new boot_id (restart/wake)   -> the new process's counters ARE the delta
  (everything it counted happened after the previous snapshot), and a
  restart/wake event is recorded
* same boot_id but a counter went DOWN -> treated as a reset (never a
  negative delta), flagged ``counter_reset``
"""
from __future__ import annotations

import concurrent.futures as cf
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import audit, config, upstream, util
from .db import Tx, get_db
from .guards import ApiError

router = APIRouter()

BUCKET_S = 300
SLEEP_GAP_S = 15 * 60  # a boot that follows >15 min of silence is a wake from sleep


def bucket_of(ts: int) -> int:
    return ts - ts % BUCKET_S


def _num(x: Any) -> float:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else 0.0


def _routes(stats: dict[str, Any]) -> dict[str, dict[str, Any]]:
    r = (stats.get("requests") or {}).get("by_route") or {}
    return r if isinstance(r, dict) else {}


def bb_delta(prev: dict[str, Any] | None, cur: dict[str, Any], prev_at: int | None, cur_at: int) -> dict[str, Any]:
    """Pure function: interval traffic between two Bus Board stats payloads."""
    boot = str(cur.get("boot_id") or "")
    new_boot = prev is None or str(prev.get("boot_id") or "") != boot
    reset = False
    if not new_boot and prev is not None:
        if _num(cur.get("uptime_s")) < _num(prev.get("uptime_s")):
            reset = True

    def diff(c: float, p: float) -> float:
        nonlocal reset
        if c < p:
            reset = True
        return c - p

    base = None if new_boot else prev
    routes_out: dict[str, dict[str, Any]] = {}
    cur_routes = _routes(cur)
    prev_routes = _routes(base) if base else {}
    for name, rc in cur_routes.items():
        if not isinstance(rc, dict):
            continue
        rp = prev_routes.get(name) if isinstance(prev_routes.get(name), dict) else {}
        routes_out[name] = {
            "count": diff(_num(rc.get("count")), _num(rp.get("count"))),
            "errors_4xx": diff(_num(rc.get("errors_4xx")), _num(rp.get("errors_4xx"))),
            "errors_5xx": diff(_num(rc.get("errors_5xx")), _num(rp.get("errors_5xx"))),
            # Percentiles are not additive: carry the since-boot values.
            "p50_ms": _num(rc.get("p50_ms")),
            "p95_ms": _num(rc.get("p95_ms")),
        }
    sse_c, sse_p = cur.get("sse") or {}, (base or {}).get("sse") or {}
    pings_c, pings_p = cur.get("pings") or {}, (base or {}).get("pings") or {}
    rej_c = pings_c.get("rejected") or {}
    rej_p = pings_p.get("rejected") or {}
    rejected = {k: diff(_num(v), _num(rej_p.get(k))) for k, v in rej_c.items()} if isinstance(rej_c, dict) else {}
    sse_opened = diff(_num(sse_c.get("total_opened")), _num(sse_p.get("total_opened")))
    received = diff(_num(pings_c.get("received")), _num(pings_p.get("received")))
    accepted = diff(_num(pings_c.get("accepted")), _num(pings_p.get("accepted")))

    if reset:
        # Same boot id but counters fell: never report negative traffic.
        # Re-run against an empty baseline.
        out = bb_delta(None, cur, prev_at, cur_at)
        out["counter_reset"] = True
        out["restart"] = False
        out["baseline"] = False
        out["interval_s"] = max(0, cur_at - prev_at) if prev_at else 0
        return out

    totals = {
        "requests": sum(r["count"] for r in routes_out.values()),
        "errors_4xx": sum(r["errors_4xx"] for r in routes_out.values()),
        "errors_5xx": sum(r["errors_5xx"] for r in routes_out.values()),
    }
    return {
        "boot_id": boot,
        "restart": bool(new_boot and prev is not None),
        "baseline": prev is None,
        "counter_reset": False,
        "interval_s": max(0, cur_at - prev_at) if prev_at else 0,
        "routes": routes_out,
        "totals": totals,
        "sse_opened": sse_opened,
        "sse_open": _num(sse_c.get("open")),
        "pings": {"received": received, "accepted": accepted, "rejected": rejected},
        "vehicles_active": _num((cur.get("vehicles") or {}).get("active")),
    }


# --- the run ---------------------------------------------------------------------

def _fetch_all() -> dict[str, upstream.Result]:
    jobs = {
        "hh_summary": lambda: upstream.hh_get("/summary", fresh=True),
        "bb_stats": lambda: upstream.bb_stats(fresh=True),
        "up_harahachi": lambda: upstream.health("harahachi"),
        "up_busboard": lambda: upstream.health("busboard"),
        "up_landing": lambda: upstream.health("landing"),
    }
    with cf.ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        futs = {k: ex.submit(fn) for k, fn in jobs.items()}
        return {k: f.result() for k, f in futs.items()}


def collect(request: Request | None = None) -> dict[str, Any]:
    db = get_db()
    now = util.now()
    bucket = bucket_of(now)
    with db.tx() as t:
        claimed = t.insert_ignore(
            "INSERT INTO collect_runs (bucket, at, result) VALUES (?, ?, '{}') ON CONFLICT (bucket) DO NOTHING",
            (bucket, now),
        )
    if not claimed:
        return {"status": "already_collected", "bucket": bucket}

    res = _fetch_all()
    summary: dict[str, Any] = {"status": "collected", "bucket": bucket}
    with db.tx() as t:
        hh = res["hh_summary"]
        t.execute(
            "INSERT INTO snapshots (app, bucket, at, ok, payload) VALUES ('harahachi', ?, ?, ?, ?) ON CONFLICT (app, bucket) DO NOTHING",
            (bucket, now, int(hh.ok), util.dumps(hh.data if hh.ok else {"error": hh.error, "status": hh.status})),
        )
        summary["harahachi"] = hh.error or "ok"

        bb = res["bb_stats"]
        if bb.ok and isinstance(bb.data, dict):
            summary["busboard"] = record_bb(t, bb.data, bucket, now)
        else:
            t.execute(
                "INSERT INTO snapshots (app, bucket, at, ok, payload) VALUES ('busboard', ?, ?, 0, ?) ON CONFLICT (app, bucket) DO NOTHING",
                (bucket, now, util.dumps({"error": bb.error, "status": bb.status})),
            )
            summary["busboard"] = bb.error

        for target in ("harahachi", "busboard", "landing"):
            r = res[f"up_{target}"]
            state = upstream.classify(target, r)
            if state == "not_configured":
                continue
            t.execute(
                "INSERT INTO uptime (target, bucket, at, ok, state, status, latency_ms) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (target, bucket) DO NOTHING",
                (target, bucket, now, int(r.ok), state, r.status, r.latency_ms),
            )
        prune(t)
        t.execute("UPDATE collect_runs SET result = ? WHERE bucket = ?", (util.dumps(summary), bucket))
        audit.record(t, request, actor="cron", action="collect", outcome="ok", detail=summary)
    return summary


def record_bb(t: Tx, stats: dict[str, Any], bucket: int, now: int) -> str:
    prev_row = t.one("SELECT payload, at FROM snapshots WHERE app = 'busboard' AND ok = 1 ORDER BY at DESC LIMIT 1")
    prev = util.loads(prev_row["payload"]) if prev_row else None
    prev_at = prev_row["at"] if prev_row else None
    d = bb_delta(prev, stats, prev_at, now)
    t.execute(
        "INSERT INTO snapshots (app, bucket, at, ok, payload) VALUES ('busboard', ?, ?, 1, ?) ON CONFLICT (app, bucket) DO NOTHING",
        (bucket, now, util.dumps(stats)),
    )
    t.execute(
        "INSERT INTO bb_deltas (bucket, at, boot_id, interval_s, restart, payload) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (bucket) DO NOTHING",
        (bucket, now, d["boot_id"], d["interval_s"], int(d["restart"]), util.dumps(d)),
    )
    boot_id = d["boot_id"]
    started_at = _started_at(stats, now)
    existing = t.one("SELECT boot_id FROM bb_boots WHERE boot_id = ?", (boot_id,))
    if existing is None:
        t.execute(
            "INSERT INTO bb_boots (boot_id, started_at, first_seen_at, last_seen_at, last_uptime_s) VALUES (?, ?, ?, ?, ?)",
            (boot_id, started_at, now, now, int(_num(stats.get("uptime_s")))),
        )
    else:
        t.execute("UPDATE bb_boots SET last_seen_at = ?, last_uptime_s = ? WHERE boot_id = ?",
                  (now, int(_num(stats.get("uptime_s"))), boot_id))
    if d["restart"] and prev is not None:
        prev_boot = t.one("SELECT last_seen_at FROM bb_boots WHERE boot_id = ?", (str(prev.get("boot_id") or ""),))
        last_seen = prev_boot["last_seen_at"] if prev_boot else (prev_at or now)
        gap = max(0, started_at - last_seen)
        kind = "wake" if gap >= SLEEP_GAP_S else "restart"
        t.execute(
            "INSERT INTO events (at, app, kind, detail) VALUES (?, 'busboard', ?, ?)",
            (now, kind, util.dumps({"boot_id": boot_id[:12], "prev_boot_id": str(prev.get("boot_id") or "")[:12],
                                     "started_at": util.utc_iso(started_at), "gap_s": gap,
                                     "version": stats.get("version")})),
        )
        return kind
    return "counter_reset" if d["counter_reset"] else "ok"


def _started_at(stats: dict[str, Any], now: int) -> int:
    s = stats.get("started_at")
    if isinstance(s, (int, float)) and not isinstance(s, bool):
        return int(s / 1000) if s > 1e12 else int(s)
    if isinstance(s, str):
        from datetime import datetime

        try:
            return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
        except ValueError:
            pass
    return now - int(_num(stats.get("uptime_s")))


def prune(t: Tx) -> None:
    now = util.now()
    t.execute("DELETE FROM snapshots WHERE at < ?", (now - 120 * 86400,))
    t.execute("DELETE FROM bb_deltas WHERE at < ?", (now - 120 * 86400,))
    t.execute("DELETE FROM uptime WHERE at < ?", (now - 120 * 86400,))
    t.execute("DELETE FROM collect_runs WHERE at < ?", (now - 30 * 86400,))
    t.execute("DELETE FROM site_events WHERE at < ?", (now - 400 * 86400,))
    t.execute("DELETE FROM sessions WHERE expires_at < ?", (now - 30 * 86400,))
    from . import ratelimit

    ratelimit.prune(t)


@router.get("/api/cron/collect")
def cron_collect(request: Request) -> JSONResponse:
    secret = config.cron_secret()
    supplied = request.headers.get("authorization", "")
    expected = f"Bearer {secret}" if secret else None
    if not util.const_eq(supplied, expected):
        raise ApiError(401, "unauthorized", "Unauthorized")
    return JSONResponse(collect(request))
