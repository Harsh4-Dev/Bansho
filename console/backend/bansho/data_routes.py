"""Read/write routes behind the console screens. All require a full session."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import alerts, audit, auth, guards, upstream, util
from .db import Tx, get_db
from .guards import ApiError

router = APIRouter()

_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,80}$")
_SAFE_Q = re.compile(r"^[\w .@+\-]{0,64}$", re.UNICODE)
_ROUTE_Q = re.compile(r"^[A-Za-z0-9_/\-.{}:]{0,80}$")


def _int(v: Any, default: int, lo: int, hi: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _session(t: Tx, request: Request):
    return guards.require_session(t, request)


def proxied(res: upstream.Result) -> dict[str, Any]:
    return {"upstream": res.public(), "data": res.data if res.ok else None,
            "message": (res.data or {}).get("message") if (not res.ok and isinstance(res.data, dict)) else None}


# --- uptime helpers ---------------------------------------------------------------

def uptime_stats(t: Tx, target: str, since: int) -> dict[str, Any]:
    rows = t.all("SELECT state, COUNT(*) AS n FROM uptime WHERE target = ? AND at >= ? GROUP BY state", (target, since))
    c = {r["state"]: int(r["n"]) for r in rows}
    total = sum(c.values())
    up = c.get("up", 0)
    asleep = c.get("asleep", 0)
    return {
        "checks": total,
        "up": up,
        "asleep": asleep,
        "down": total - up - asleep,
        "pct_up": round(100 * up / total, 2) if total else None,
        "pct_available": round(100 * (up + asleep) / total, 2) if total else None,
    }


def uptime_strip(t: Tx, target: str, hours: int = 24) -> list[dict[str, Any]]:
    rows = t.all("SELECT at, state, latency_ms FROM uptime WHERE target = ? AND at >= ? ORDER BY at",
                 (target, util.now() - hours * 3600))
    return [{"at": util.utc_iso(r["at"]), "state": r["state"], "latency_ms": r["latency_ms"]} for r in rows]


def last_check(t: Tx, target: str) -> dict[str, Any] | None:
    r = t.one("SELECT at, state, status, latency_ms FROM uptime WHERE target = ? ORDER BY at DESC LIMIT 1", (target,))
    if r is None:
        return None
    return {"at": util.utc_iso(r["at"]), "state": r["state"], "status": r["status"], "latency_ms": r["latency_ms"]}


def last_snapshot(t: Tx, app: str) -> tuple[dict[str, Any] | None, int | None]:
    r = t.one("SELECT payload, at FROM snapshots WHERE app = ? AND ok = 1 ORDER BY at DESC LIMIT 1", (app,))
    return (util.loads(r["payload"]), r["at"]) if r else (None, None)


def bb_today(t: Tx) -> dict[str, Any]:
    rows = t.all("SELECT payload FROM bb_deltas WHERE at >= ?", (util.day_start(util.now()),))
    tot = Counter()
    for r in rows:
        d = util.loads(r["payload"], {})
        tot["requests"] += (d.get("totals") or {}).get("requests") or 0
        tot["errors_5xx"] += (d.get("totals") or {}).get("errors_5xx") or 0
        p = d.get("pings") or {}
        tot["pings_received"] += p.get("received") or 0
        tot["pings_accepted"] += p.get("accepted") or 0
        tot["pings_rejected"] += sum((p.get("rejected") or {}).values())
        tot["sse_opened"] += d.get("sse_opened") or 0
    return {k: int(v) for k, v in tot.items()}


def landing_today(t: Tx) -> dict[str, int]:
    day = util.today()
    r = t.one(
        "SELECT SUM(CASE WHEN type = 'pageview' AND is_bot = 0 THEN 1 ELSE 0 END) AS views, "
        "COUNT(DISTINCT CASE WHEN is_bot = 0 THEN visitor_hash END) AS visitors, "
        "SUM(CASE WHEN type = 'click' AND is_bot = 0 THEN 1 ELSE 0 END) AS clicks, "
        "SUM(is_bot) AS bots FROM site_events WHERE day = ?",
        (day,),
    ) or {}
    return {k: int(r.get(k) or 0) for k in ("views", "visitors", "clicks", "bots")}


# --- overview ---------------------------------------------------------------------

@router.get("/api/overview")
def overview(request: Request) -> JSONResponse:
    with get_db().tx() as t:
        _session(t, request)
    hh = upstream.hh_get("/summary")
    bb = upstream.bb_stats()
    with get_db().tx() as t:
        now = util.now()
        if hh.ok:
            hh_data, hh_src, hh_at = hh.data, "live", int(hh.fetched_at)
        else:
            hh_data, hh_at = last_snapshot(t, "harahachi")
            hh_src = "snapshot" if hh_data else "none"
        if bb.ok:
            bb_data, bb_src, bb_at = bb.data, "live", int(bb.fetched_at)
        else:
            bb_data, bb_at = last_snapshot(t, "busboard")
            bb_src = "snapshot" if bb_data else "none"
        hh_today = (hh_data or {}).get("today") or {}
        hh_tot = (hh_data or {}).get("totals") or {}
        tokens_in = sum(float((v or {}).get("tokens_in") or 0) for v in ((hh_data or {}).get("ai_by_slot") or {}).values())
        tokens_out = sum(float((v or {}).get("tokens_out") or 0) for v in ((hh_data or {}).get("ai_by_slot") or {}).values())
        last_run = t.one("SELECT at, result FROM collect_runs ORDER BY bucket DESC LIMIT 1")
        body = {
            "generated_at": util.utc_iso(now),
            "apps": {
                "harahachi": {
                    "state": "up" if hh.ok else upstream.classify("harahachi", hh),
                    "upstream": hh.public(),
                    "source": hh_src,
                    "as_of": util.utc_iso(hh_at) if hh_at else None,
                    "uptime_24h": uptime_stats(t, "harahachi", now - 86400),
                    "uptime_30d": uptime_stats(t, "harahachi", now - 30 * 86400),
                    "strip": uptime_strip(t, "harahachi"),
                    "kpis": {
                        "users": hh_tot.get("users"),
                        "dau": hh_today.get("active_users"),
                        "new_users": hh_today.get("new_users"),
                        "meals_today": hh_today.get("meals"),
                        "ai_calls": hh_today.get("ai_calls"),
                        "ai_errors": hh_today.get("ai_errors"),
                        "cap_hits": hh_today.get("cap_hits"),
                        "tokens_in_24h": int(tokens_in) if hh_data else None,
                        "tokens_out_24h": int(tokens_out) if hh_data else None,
                        "requests": hh_today.get("requests"),
                        "errors_5xx": hh_today.get("errors_5xx"),
                    },
                },
                "busboard": {
                    "state": "up" if bb.ok else upstream.classify("busboard", bb),
                    "upstream": bb.public(),
                    "source": bb_src,
                    "as_of": util.utc_iso(bb_at) if bb_at else None,
                    "uptime_24h": uptime_stats(t, "busboard", now - 86400),
                    "uptime_30d": uptime_stats(t, "busboard", now - 30 * 86400),
                    "strip": uptime_strip(t, "busboard"),
                    "kpis": {
                        **bb_today(t),
                        "sse_open": ((bb_data or {}).get("sse") or {}).get("open") if bb.ok else None,
                        "vehicles_active": ((bb_data or {}).get("vehicles") or {}).get("active") if bb.ok else None,
                        "uptime_s": (bb_data or {}).get("uptime_s") if bb.ok else None,
                    },
                },
                "landing": {
                    "state": (last_check(t, "landing") or {}).get("state", "unknown"),
                    "uptime_24h": uptime_stats(t, "landing", now - 86400),
                    "uptime_30d": uptime_stats(t, "landing", now - 30 * 86400),
                    "strip": uptime_strip(t, "landing"),
                    "kpis": landing_today(t),
                },
            },
            "alerts": alerts.compute(t, hh_data if hh.ok else None, hh.error, bb.error),
            "collector": {"last_run_at": util.utc_iso(last_run["at"]) if last_run else None,
                          "last_result": util.loads(last_run["result"], {}) if last_run else None},
        }
    return JSONResponse(body)


# --- Harahachi ----------------------------------------------------------------------

@router.get("/api/harahachi/{view}")
def harahachi_view(view: str, request: Request) -> JSONResponse:
    with get_db().tx() as t:
        _session(t, request)
    q = request.query_params
    if view == "summary":
        res = upstream.hh_get("/summary")
    elif view == "timeseries":
        res = upstream.hh_get("/timeseries", {"days": _int(q.get("days"), 30, 1, 365)})
    elif view == "retention":
        res = upstream.hh_get("/retention", {"weeks": _int(q.get("weeks"), 8, 1, 52)})
    elif view == "funnel":
        res = upstream.hh_get("/funnel")
    elif view == "users":
        search = q.get("q", "")
        sort = q.get("sort", "")
        if not _SAFE_Q.match(search) or not re.match(r"^[a-z_\-]{0,32}$", sort):
            raise ApiError(400, "bad_query", "Bad search or sort")
        params: dict[str, Any] = {"page": _int(q.get("page"), 1, 1, 10_000)}
        if search:
            params["q"] = search
        if sort:
            params["sort"] = sort
        res = upstream.hh_get("/users", params)
    elif view == "requests":
        status = q.get("status", "")
        route = q.get("route", "")
        if not re.match(r"^[0-9x]{0,3}$", status) or not _ROUTE_Q.match(route):
            raise ApiError(400, "bad_query", "Bad filter")
        params = {"limit": _int(q.get("limit"), 200, 1, 500)}
        if status:
            params["status"] = status
        if route:
            params["route"] = route
        res = upstream.hh_get("/requests", params)
    elif view == "audit":
        res = upstream.hh_get("/audit", {"limit": _int(q.get("limit"), 200, 1, 500)})
    else:
        raise guards.not_found()
    return JSONResponse(proxied(res))


@router.get("/api/harahachi/users/{user_id}")
def harahachi_user(user_id: str, request: Request) -> JSONResponse:
    with get_db().tx() as t:
        _session(t, request)
    if not _ID_RE.match(user_id):
        raise guards.not_found()
    return JSONResponse(proxied(upstream.hh_get(f"/users/{user_id}")))


HH_ACTIONS: dict[str, dict[str, Any]] = {
    "revoke_device": {"params": {"device_id": "id"}, "step_up": True},
    "grant_keeper": {"params": {"user_id": "id"}, "step_up": False},
    "revoke_keeper": {"params": {"user_id": "id"}, "step_up": True},
    "unlock_user": {"params": {"user_id": "id"}, "step_up": False},
    "set_model": {"params": {"slot": "slot", "model": "model", "fallback": "model?"}, "step_up": False},
    "push_test": {"params": {"user_id": "id"}, "step_up": False},
    "pairing_token": {"params": {}, "step_up": False},
}
_SLOTS = ("analyze", "body", "chat", "insights", "passthrough")
_MODEL_RE = re.compile(r"^[A-Za-z0-9_\-./:]{1,120}$")


def _validate_params(spec: dict[str, str], data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, kind in spec.items():
        optional = kind.endswith("?")
        kind = kind.rstrip("?")
        v = data.get(name)
        if v is None or v == "":
            if optional:
                continue
            raise ApiError(400, "bad_params", f"Missing {name}")
        if kind == "id":
            if isinstance(v, int) and not isinstance(v, bool):
                out[name] = v
                continue
            if not isinstance(v, str) or not _ID_RE.match(v):
                raise ApiError(400, "bad_params", f"Bad {name}")
        elif kind == "slot":
            if v not in _SLOTS:
                raise ApiError(400, "bad_params", "Unknown slot")
        elif kind == "model":
            if not isinstance(v, str) or not _MODEL_RE.match(v):
                raise ApiError(400, "bad_params", f"Bad {name}")
        out[name] = v
    return out


@router.post("/api/harahachi/actions")
async def harahachi_action(request: Request) -> JSONResponse:
    data = await auth.body_json(request, limit=2048)
    action = data.get("action")
    spec = HH_ACTIONS.get(action) if isinstance(action, str) else None
    if spec is None:
        raise ApiError(400, "unknown_action", "Unknown action")
    params = _validate_params(spec["params"], data)
    with get_db().tx() as t:
        s = _session(t, request)
        if spec["step_up"]:
            guards.require_step_up(s)
    res = upstream.hh_action({"action": action, **params})
    with get_db().tx() as t:
        audit.record(t, request, actor="owner", action=f"harahachi.{action}",
                     outcome="ok" if res.ok else (res.error or "error"),
                     target=str(next(iter(params.values()), "")), detail={k: v for k, v in params.items()})
    return JSONResponse(proxied(res), status_code=200 if res.ok else 502)


# --- Bus Board -------------------------------------------------------------------------

def boot_timeline(t: Tx, days: int = 30) -> dict[str, Any]:
    since = util.now() - days * 86400
    boots = t.all("SELECT * FROM bb_boots WHERE last_seen_at >= ? ORDER BY started_at", (since,))
    events = t.all("SELECT * FROM events WHERE app = 'busboard' AND at >= ? ORDER BY at", (since,))
    return {
        "boots": [
            {"boot_id": b["boot_id"][:12], "started_at": util.utc_iso(b["started_at"]),
             "last_seen_at": util.utc_iso(b["last_seen_at"]), "uptime_s": b["last_uptime_s"]}
            for b in boots
        ],
        "events": [{"at": util.utc_iso(e["at"]), "kind": e["kind"], **util.loads(e["detail"], {})} for e in events],
    }


@router.get("/api/busboard/live")
def busboard_live(request: Request) -> JSONResponse:
    with get_db().tx() as t:
        _session(t, request)
    res = upstream.bb_stats()
    with get_db().tx() as t:
        now = util.now()
        snap, snap_at = last_snapshot(t, "busboard")
        body = {
            "state": "up" if res.ok else upstream.classify("busboard", res),
            "upstream": res.public(),
            "stats": res.data if res.ok else snap,
            "source": "live" if res.ok else ("snapshot" if snap else "none"),
            "as_of": util.utc_iso(int(res.fetched_at)) if res.ok else util.utc_iso(snap_at),
            "uptime_24h": uptime_stats(t, "busboard", now - 86400),
            "uptime_30d": uptime_stats(t, "busboard", now - 30 * 86400),
            "strip": uptime_strip(t, "busboard", 48),
            "timeline": boot_timeline(t),
            "today": bb_today(t),
        }
    return JSONResponse(body)


@router.get("/api/busboard/traffic")
def busboard_traffic(request: Request) -> JSONResponse:
    hours = _int(request.query_params.get("hours"), 48, 1, 24 * 30)
    with get_db().tx() as t:
        _session(t, request)
        rows = t.all("SELECT at, restart, payload FROM bb_deltas WHERE at >= ? ORDER BY at", (util.now() - hours * 3600,))
    points = []
    route_tot: Counter[str] = Counter()
    for r in rows:
        d = util.loads(r["payload"], {})
        for name, rc in (d.get("routes") or {}).items():
            route_tot[name] += rc.get("count") or 0
        points.append({"at": util.utc_iso(r["at"]), "restart": bool(r["restart"]), "d": d})
    top = [name for name, _ in route_tot.most_common(8)]
    series = {
        "at": [p["at"] for p in points],
        "restart": [p["restart"] for p in points],
        "requests": [(p["d"].get("totals") or {}).get("requests", 0) for p in points],
        "errors_4xx": [(p["d"].get("totals") or {}).get("errors_4xx", 0) for p in points],
        "errors_5xx": [(p["d"].get("totals") or {}).get("errors_5xx", 0) for p in points],
        "sse_opened": [p["d"].get("sse_opened", 0) for p in points],
        "pings_received": [(p["d"].get("pings") or {}).get("received", 0) for p in points],
        "pings_accepted": [(p["d"].get("pings") or {}).get("accepted", 0) for p in points],
    }
    routes = {
        name: {
            "count": [((p["d"].get("routes") or {}).get(name) or {}).get("count", 0) for p in points],
            "errors_5xx": [((p["d"].get("routes") or {}).get(name) or {}).get("errors_5xx", 0) for p in points],
            "p95_ms": [((p["d"].get("routes") or {}).get(name) or {}).get("p95_ms", 0) for p in points],
            "total": route_tot[name],
        }
        for name in top
    }
    return JSONResponse({"hours": hours, "series": series, "routes": routes})


@router.post("/api/busboard/wake")
async def busboard_wake(request: Request) -> JSONResponse:
    await auth.body_json(request, limit=256)
    with get_db().tx() as t:
        _session(t, request)
    res = upstream.health("busboard", timeout=upstream.WAKE_TIMEOUT_S)
    upstream.invalidate("busboard:")
    state = upstream.classify("busboard", res)
    with get_db().tx() as t:
        audit.record(t, request, actor="owner", action="busboard.wake", outcome=state, detail={"latency_ms": res.latency_ms})
    return JSONResponse({"state": state, "upstream": res.public()})


@router.post("/api/busboard/contributors/{contributor_id}/{op}")
async def busboard_contributor(contributor_id: str, op: str, request: Request) -> JSONResponse:
    await auth.body_json(request, limit=256)
    if op not in ("block", "unblock") or not _ID_RE.match(contributor_id):
        raise guards.not_found()
    with get_db().tx() as t:
        s = _session(t, request)
        if op == "block":
            guards.require_step_up(s)
    res = upstream.bb_post(f"/contributors/{contributor_id}/{op}")
    with get_db().tx() as t:
        audit.record(t, request, actor="owner", action=f"busboard.{op}", outcome="ok" if res.ok else (res.error or "error"),
                     target=contributor_id[:16])
    return JSONResponse(proxied(res), status_code=200 if res.ok else 502)


@router.post("/api/busboard/snapshot")
async def busboard_snapshot(request: Request) -> JSONResponse:
    await auth.body_json(request, limit=256)
    with get_db().tx() as t:
        _session(t, request)
    res = upstream.bb_post("/snapshot")
    with get_db().tx() as t:
        audit.record(t, request, actor="owner", action="busboard.snapshot", outcome="ok" if res.ok else (res.error or "error"))
    return JSONResponse(proxied(res), status_code=200 if res.ok else 502)


# --- Landing ----------------------------------------------------------------------------

@router.get("/api/landing")
def landing(request: Request) -> JSONResponse:
    days = _int(request.query_params.get("days"), 30, 1, 365)
    with get_db().tx() as t:
        _session(t, request)
        now = util.now()
        day_list = [util.day_of(now - i * 86400) for i in range(days - 1, -1, -1)]
        since_day = day_list[0]
        rows = t.all(
            "SELECT day, type, path, ref_host, target, country, device_class, is_bot, visitor_hash "
            "FROM site_events WHERE day >= ?",
            (since_day,),
        )
    views = Counter()
    clicks = Counter()
    visitors: dict[str, set[str]] = defaultdict(set)
    pages, refs, countries, devices, ctas = Counter(), Counter(), Counter(), Counter(), Counter()
    bots = 0
    for r in rows:
        if r["is_bot"]:
            bots += 1
            continue
        if r["type"] == "pageview":
            views[r["day"]] += 1
            pages[r["path"]] += 1
            refs[r["ref_host"] or "(direct)"] += 1
            countries[r["country"] or "??"] += 1
            devices[r["device_class"]] += 1
            if r["visitor_hash"]:
                visitors[r["day"]].add(r["visitor_hash"])
        else:
            clicks[r["day"]] += 1
            ctas[r["target"]] += 1
    total = len(rows)
    return JSONResponse({
        "days": day_list,
        "series": {
            "pageviews": [views[d] for d in day_list],
            "visitors": [len(visitors[d]) for d in day_list],
            "clicks": [clicks[d] for d in day_list],
        },
        "totals": {"pageviews": sum(views.values()), "clicks": sum(clicks.values()), "bots": bots,
                   "bot_share": round(bots / total, 3) if total else 0},
        "today": {"pageviews": views[day_list[-1]], "visitors": len(visitors[day_list[-1]]), "clicks": clicks[day_list[-1]]},
        "top_pages": pages.most_common(15),
        "referrers": refs.most_common(15),
        "countries": countries.most_common(20),
        "devices": devices.most_common(),
        "ctas": ctas.most_common(),
    })


# --- Audit ------------------------------------------------------------------------------

@router.get("/api/audit/{source}")
def audit_view(source: str, request: Request) -> JSONResponse:
    limit = _int(request.query_params.get("limit"), 200, 1, 500)
    with get_db().tx() as t:
        _session(t, request)
        if source == "console":
            before = _int(request.query_params.get("before"), 0, 0, 2**62) or None
            return JSONResponse({"rows": audit.recent(t, limit, before)})
    if source == "harahachi":
        return JSONResponse(proxied(upstream.hh_get("/audit", {"limit": limit})))
    if source == "busboard":
        # Bus Board's contract has no audit read endpoint; try it, report plainly.
        return JSONResponse(proxied(upstream.cached_get(upstream.config.busboard(), upstream.BUSBOARD_OPS + "/audit",
                                                        {"limit": limit})))
    raise guards.not_found()
