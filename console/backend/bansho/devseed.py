"""SEED/dev mode: fake upstream apps and a seeded database.

Only reachable when ``config.dev_mode()`` is true, which requires
BANSHO_DEV_SEED=1, a plain-http localhost BANSHO_ORIGIN, and NOT running on
Vercel. The data below is invented and shaped exactly like the ops-API
contracts in SPEC.md so every console screen can be exercised locally.
"""
from __future__ import annotations

import json
import random
import time
from datetime import datetime
from typing import Any

import httpx

from . import util
from .db import Database

SLOTS = ("analyze", "body", "chat", "insights", "passthrough")
SEED_MODELS = {"analyze": "qwen/qwen2.5-vl-72b-instruct", "body": "meta/llama-3.2-90b-vision-instruct",
               "chat": "meta/llama-3.3-70b-instruct", "insights": "meta/llama-3.3-70b-instruct",
               "passthrough": "meta/llama-3.3-70b-instruct"}
NAMES = ["asha", "ravi", "meena", "karthik", "priya", "arjun", "divya", "vikram", "lakshmi", "suresh",
         "nila", "harsha", "deepa", "gopal", "kavya", "manoj", "anu", "senthil", "revathi", "bala"]
DISHES = ["Idli sambar", "Masala dosa", "Curd rice", "Chicken biryani", "Pongal", "Dal tadka",
          "Chapati", "Rasam rice", "Upma", "Egg curry", "Banana", "Filter coffee"]
ROUTES = ["GET /v1/board", "GET /v1/board/stream", "GET /v1/stops", "GET /v1/route",
          "POST /v1/contribute", "GET /v1/campus", "GET /v1/health", "GET /v1/basemap"]
STOPS = ["SASTRA", "Thanjavur NB", "Vallam", "Trichy CBS", "Chathiram", "Sengipatti", "Panjappur"]

_BOOT = {"id": "b" + "%011x" % random.getrandbits(44), "started": int(time.time()) - 3 * 3600}
_BLOCKED: set[str] = set()
_AUDIT: list[dict[str, Any]] = []


# --- fake Harahachi --------------------------------------------------------------------

def hh_summary(r: random.Random) -> dict[str, Any]:
    ai = {}
    for s in SLOTS:
        calls = r.randint(5, 120)
        ai[s] = {"calls": calls, "errors": r.randint(0, max(1, calls // 12)), "p50_ms": r.randint(600, 1800),
                 "p95_ms": r.randint(2500, 7000), "tokens_in": calls * r.randint(400, 1500),
                 "tokens_out": calls * r.randint(80, 400), "model": SEED_MODELS.get(s)}
    calls = sum(v["calls"] for v in ai.values())
    errs = sum(v["errors"] for v in ai.values())
    return {
        "generated_at": util.utc_iso(util.now()), "version": "4.2.0-dev",
        "totals": {"users": 42, "devices_active": 51, "devices_revoked": 6, "meals": 3810, "workouts": 412,
                   "weights": 690, "push_subs": 17, "keepers": 2},
        "today": {"new_users": 2, "active_users": 19, "meals": 61, "ai_calls": calls, "ai_errors": errs,
                  "cap_hits": 1, "requests": 2210, "errors_5xx": 4},
        "ai_by_slot": ai,
        "clients": {"apk": 23, "pwa": 21, "web": 7, "unknown": 0},
        # Mirrors Harahachi's real /summary (ops.py): versions in their own field so
        # `clients` keeps plain numbers.
        "client_versions": {"apk": {"0.9.0": 15, "0.8.2": 8}, "pwa": {"0.9.0": 21}, "web": {"0.9.0": 7},
                            "unknown": {}},
        "top_dishes": [{"name": n, "count": c} for n, c in (
            ("Idli + sambar", 64), ("Dal tadka + 2 roti", 51), ("Masala dosa", 38), ("Poha", 33),
            ("Chicken biryani", 27), ("Curd rice", 22), ("Paneer butter masala", 18), ("Banana", 15))],
        "health": {"db_ok": True, "nim_configured": True, "push_configured": True},
    }


def hh_timeseries(days: int) -> dict[str, Any]:
    r = random.Random(7)
    now = util.now()
    day_list = [util.day_of(now - i * 86400) for i in range(days - 1, -1, -1)]
    def walk(base: float, spread: float) -> list[float]:
        return [max(0, round(base * (0.6 + 0.4 * i / max(1, days)) + r.uniform(-spread, spread))) for i in range(days)]
    meals = walk(55, 12)
    split = {k: [round(m * f) for m in meals] for k, f in
             (("meals_photo", .46), ("meals_barcode", .12), ("meals_manual", .14), ("meals_chat", .16), ("meals_quicklog", .12))}
    ai_calls = walk(240, 40)
    dau = walk(17, 4)
    return {"days": day_list, "series": {
        "new_users": walk(1.5, 1.5), "dau": dau,
        # trailing-window distinct users can't be derived from DAU; plausible seeds
        "wau": [round(d * 1.9) for d in dau], "mau": [round(d * 2.6) for d in dau],
        "meals": meals, **split,
        "ai_calls": ai_calls, "ai_errors": [round(c * r.uniform(0.01, 0.07)) for c in ai_calls],
        "ai_tokens_in": [c * 900 for c in ai_calls], "ai_tokens_out": [c * 210 for c in ai_calls],
        "cap_hits": [r.choice([0, 0, 0, 1, 2]) for _ in range(days)], "requests": walk(2000, 300),
        "errors_5xx": [r.choice([0, 0, 1, 2, 5]) for _ in range(days)], "p95_ms": walk(420, 90),
        "pushes_sent": walk(30, 6), "pushes_failed": [r.choice([0, 0, 1]) for _ in range(days)],
    }}


def hh_retention(weeks: int) -> dict[str, Any]:
    r = random.Random(3)
    cohorts = []
    for w in range(weeks, 0, -1):
        start = util.day_of(util.now() - w * 7 * 86400)
        size = r.randint(2, 9)
        pct = [round(max(0.0, 100 * (0.8 ** i) - r.uniform(0, 12)), 1) for i in range(1, w + 1)]
        cohorts.append({"week": start, "size": size, "active_pct": pct})
    return {"weeks": weeks, "cohorts": cohorts}


def hh_users(params: dict[str, str]) -> dict[str, Any]:
    r = random.Random(11)
    users = []
    for i, n in enumerate(NAMES):
        users.append({
            "id": i + 1, "username": n, "created_at": util.utc_iso(util.now() - r.randint(2, 120) * 86400),
            "last_active": util.utc_iso(util.now() - r.randint(0, 9) * 86400), "meals": r.randint(3, 400),
            "streak_current": r.randint(0, 40), "client_kind": r.choice(["apk", "pwa", "web"]),
            "is_admin": i == 11, "locked": i == 7, "ai_calls_7d": r.randint(0, 90),
        })
    q = (params.get("q") or "").lower()
    if q:
        users = [u for u in users if q in u["username"]]
    sort = params.get("sort") or ""
    if sort in ("meals", "streak_current", "ai_calls_7d"):
        users.sort(key=lambda u: -u[sort])
    page = int(params.get("page") or 1)
    return {"users": users[(page - 1) * 25: page * 25], "total": len(users)}


def hh_user(uid: int) -> dict[str, Any] | None:
    if not 1 <= uid <= len(NAMES):
        return None
    r = random.Random(uid)
    now = util.now()
    return {
        "user": {"id": uid, "username": NAMES[uid - 1], "created_at": util.utc_iso(now - 40 * 86400),
                 "is_admin": uid == 12, "locked": uid == 8, "failed_logins": 3 if uid == 8 else 0},
        "profile": {"sex": r.choice(["m", "f"]), "age": r.randint(19, 45), "height_cm": r.randint(150, 185),
                    "weight_kg": round(r.uniform(50, 90), 1), "goal_weight_kg": round(r.uniform(50, 80), 1)},
        "targets": {"kcal": r.choice([1800, 2000, 2200]), "protein_g": 110, "carbs_g": 230, "fat_g": 60},
        "body_fat": {"current_pct": 22, "goal_pct": 17, "source": "silhouette"},
        "meals": [{"at": util.utc_iso(now - i * 20000), "name": r.choice(DISHES), "kcal": r.randint(80, 700),
                   "source": r.choice(["photo", "barcode", "manual", "chat", "quicklog"])} for i in range(30)],
        "weights": [{"day": util.day_of(now - i * 86400 * 3), "kg": round(72 - i * 0.2, 1)} for i in range(10)],
        "workouts": [{"at": util.utc_iso(now - i * 86400 * 2), "kind": "walk", "minutes": 30, "kcal": 140} for i in range(5)],
        "devices": [
            {"id": f"d{uid}a", "client_kind": "apk", "client_version": "1.8.2", "last_seen": util.utc_iso(now - 3600), "revoked": False},
            {"id": f"d{uid}b", "client_kind": "pwa", "client_version": "1.8.0", "last_seen": util.utc_iso(now - 9 * 86400), "revoked": True},
        ],
        "ai_usage": {s: {"calls": r.randint(0, 50), "errors": r.randint(0, 3), "tokens_in": r.randint(0, 40000),
                         "tokens_out": r.randint(0, 9000)} for s in SLOTS},
    }


def hh_requests(limit: int) -> dict[str, Any]:
    r = random.Random(5)
    rows = []
    for i in range(min(limit, 60)):
        st = r.choice([200] * 12 + [401, 404, 429, 500, 502])
        rows.append({"at": util.utc_iso(util.now() - i * 97), "method": r.choice(["GET", "POST"]),
                     "route": r.choice(["/api/v1/meals", "/api/v1/analyze", "/api/v1/chat", "/api/v1/profile"]),
                     "status": st, "ms": r.randint(20, 4000), "client": r.choice(["apk/1.8.2", "pwa/1.8.0", "web/1.8.2"])})
    return {"rows": rows}


def _fake_qr_png() -> str:
    # A 1x1 PNG; dev mode only needs something renderable.
    return ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")


def _hh(request: httpx.Request) -> httpx.Response:
    path = request.url.path.removeprefix("/api/ops/v1")
    p = dict(request.url.params)
    if request.url.path == "/api/v1/health":
        return httpx.Response(200, json={"status": "ok"})
    if path == "/summary":
        return httpx.Response(200, json=hh_summary(random.Random(int(time.time() // 60))))
    if path == "/timeseries":
        return httpx.Response(200, json=hh_timeseries(int(p.get("days", 30))))
    if path == "/retention":
        return httpx.Response(200, json=hh_retention(int(p.get("weeks", 8))))
    if path == "/funnel":
        return httpx.Response(200, json={"paired_devices": 64, "registered_users": 42, "first_meal": 37,
                                         "active_3_days": 26, "active_7_days": 18})
    if path == "/users":
        return httpx.Response(200, json=hh_users(p))
    if path.startswith("/users/"):
        u = hh_user(int(path.split("/")[-1]) if path.split("/")[-1].isdigit() else -1)
        return httpx.Response(200, json=u) if u else httpx.Response(404, json={"error": "no such user"})
    if path == "/requests":
        return httpx.Response(200, json=hh_requests(int(p.get("limit", 200))))
    if path == "/audit":
        return httpx.Response(200, json={"rows": _AUDIT[-200:][::-1]})
    if path == "/actions" and request.method == "POST":
        body = json.loads(request.content or b"{}")
        action = body.get("action")
        _AUDIT.append({"at": util.utc_iso(util.now()), "action": action, "target": str(body.get("user_id") or body.get("device_id") or ""),
                       "params_json": json.dumps(body), "result": "ok"})
        if action == "pairing_token":
            return httpx.Response(200, json={"token": "dev-pairing-token", "url": "http://localhost/pair/dev",
                                             "qr_png_base64": _fake_qr_png(), "expires_in": 600})
        if action not in ("revoke_device", "grant_keeper", "revoke_keeper", "unlock_user", "set_model", "push_test"):
            return httpx.Response(400, json={"error": "unknown action"})
        return httpx.Response(200, json={"ok": True, "action": action})
    return httpx.Response(404, json={"error": "not found"})


# --- fake Bus Board --------------------------------------------------------------------

def bb_stats(boot_id: str, uptime_s: int, scale: float, r: random.Random) -> dict[str, Any]:
    by_route = {}
    for i, name in enumerate(ROUTES):
        c = int(scale * (900 / (i + 1)))
        by_route[name] = {"count": c, "errors_4xx": int(c * 0.02), "errors_5xx": int(c * 0.002),
                          "p50_ms": 4 + i, "p95_ms": 30 + 7 * i}
    received = int(scale * 140)
    return {
        "version": "0.9.1-dev", "boot_id": boot_id, "started_at": util.utc_iso(util.now() - uptime_s),
        "uptime_s": uptime_s,
        "requests": {"by_route": by_route},
        "sse": {"open": r.randint(0, 9), "total_opened": int(scale * 75)},
        "board_queries": {"top": [{"from": a, "to": b, "count": int(scale * (60 - 7 * k))}
                                  for k, (a, b) in enumerate([("SASTRA", "Thanjavur NB"), ("Thanjavur NB", "SASTRA"),
                                                              ("SASTRA", "Trichy CBS"), ("Vallam", "Thanjavur NB"),
                                                              ("Trichy CBS", "SASTRA"), ("Chathiram", "SASTRA")])]},
        "pings": {"received": received, "accepted": int(received * 0.81),
                  "rejected": {"coverage": int(received * 0.1), "route_corridor": int(received * 0.05),
                               "kinematic_profile": int(received * 0.03), "blocked": int(received * 0.01)}},
        "contributors": {"count": 23, "trust_histogram": {"0.0-0.2": 5, "0.2-0.4": 9, "0.4-0.6": 4, "0.6-0.8": 3, "0.8-1.0": 2},
                         "top": [{"id_hash": f"c{k:02d}{'%08x' % r.getrandbits(32)}", "score": round(0.95 - k * 0.09, 2),
                                  "accepted": 40 - 4 * k, "blocked": False} for k in range(8)]},
        "vehicles": {"active": r.randint(0, 4), "by_route": {"TNSTC Trichy-Thanjavur": r.randint(0, 3), "SASTRA campus": r.randint(0, 1)}},
        "feed": {"stops": 31, "unverified_stops": 31, "routes": 3, "version": "seed-2026-09"},
        "runtime": {"goroutines": r.randint(18, 40), "heap_mb": round(r.uniform(9, 24), 1), "go_version": "go1.23.4"},
        "blocked": sorted(_BLOCKED),
    }


def _bb(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/v1/health":
        return httpx.Response(200, json={"status": "ok"})
    uptime = util.now() - _BOOT["started"]
    if path == "/v1/ops/stats":
        return httpx.Response(200, json=bb_stats(_BOOT["id"], uptime, uptime / 3600, random.Random(int(time.time() // 30))))
    if path == "/v1/ops/snapshot" and request.method == "POST":
        return httpx.Response(200, json={"ok": False, "message": "no snapshot store configured (MAPS_STATE unset)"})
    parts = path.split("/")
    if len(parts) == 6 and parts[3] == "contributors" and request.method == "POST":
        (_BLOCKED.add if parts[5] == "block" else _BLOCKED.discard)(parts[4])
        return httpx.Response(200, json={"ok": True, "blocked": parts[5] == "block"})
    return httpx.Response(404, json={"error": "not found"})


def _landing(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text="<!doctype html><title>landing</title>")


def fake_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if "harahachi" in host:
            return _hh(request)
        if "busboard" in host:
            return _bb(request)
        return _landing(request)

    return httpx.MockTransport(handler)


# --- seeded history ---------------------------------------------------------------------

def seed_if_empty(db: Database) -> None:
    with db.tx() as t:
        if t.scalar("SELECT COUNT(*) FROM snapshots"):
            return
    seed(db)


def seed(db: Database, days: int = 30) -> None:
    from .collector import bb_delta, bucket_of

    r = random.Random(42)
    now = util.now()
    start = bucket_of(now - days * 86400)
    step = 15 * 60
    boot_id = "seed" + "%08x" % r.getrandbits(32)
    boot_start = start - 3600
    prev: dict[str, Any] | None = None
    prev_at: int | None = None
    with db.tx() as t:
        for ts in range(start, bucket_of(now) - step, step):
            bucket = bucket_of(ts)
            # Bus Board: sleeps overnight most days (Render free tier), and one
            # restart storm-ish morning for the timeline.
            hour = datetime.fromtimestamp(ts, util.tz()).hour
            asleep = 19 <= hour <= 23 and r.random() < 0.6
            for target, ok in (("harahachi", r.random() > 0.004), ("landing", r.random() > 0.002)):
                t.execute("INSERT INTO uptime (target, bucket, at, ok, state, status, latency_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (target, bucket, ts, int(ok), "up" if ok else "down", 200 if ok else 503, r.randint(60, 420)))
            if asleep:
                t.execute("INSERT INTO uptime (target, bucket, at, ok, state, status, latency_ms) VALUES ('busboard', ?, ?, 0, 'asleep', 0, 8000)",
                          (bucket, ts))
                continue
            if prev is not None and prev_at is not None and ts - prev_at > 30 * 60:
                boot_id = "seed" + "%08x" % r.getrandbits(32)
                boot_start = ts - r.randint(30, 200)
            uptime = ts - boot_start
            stats = bb_stats(boot_id, uptime, uptime / 3600, r)
            d = bb_delta(prev, stats, prev_at, ts)
            t.execute("INSERT INTO uptime (target, bucket, at, ok, state, status, latency_ms) VALUES ('busboard', ?, ?, 1, 'up', 200, ?)",
                      (bucket, ts, r.randint(80, 600)))
            t.execute("INSERT INTO snapshots (app, bucket, at, ok, payload) VALUES ('busboard', ?, ?, 1, ?)", (bucket, ts, util.dumps(stats)))
            t.execute("INSERT INTO bb_deltas (bucket, at, boot_id, interval_s, restart, payload) VALUES (?, ?, ?, ?, ?, ?)",
                      (bucket, ts, boot_id, d["interval_s"], int(d["restart"]), util.dumps(d)))
            t.execute("INSERT INTO bb_boots (boot_id, started_at, first_seen_at, last_seen_at, last_uptime_s) VALUES (?, ?, ?, ?, ?) "
                      "ON CONFLICT (boot_id) DO UPDATE SET last_seen_at = excluded.last_seen_at, last_uptime_s = excluded.last_uptime_s",
                      (boot_id, boot_start, ts, ts, uptime))
            if d["restart"]:
                gap = boot_start - (prev_at or boot_start)
                t.execute("INSERT INTO events (at, app, kind, detail) VALUES (?, 'busboard', ?, ?)",
                          (ts, "wake" if gap >= 900 else "restart", util.dumps({"boot_id": boot_id[:12], "gap_s": gap})))
            prev, prev_at = stats, ts
            t.execute("INSERT INTO snapshots (app, bucket, at, ok, payload) VALUES ('harahachi', ?, ?, 1, ?)",
                      (bucket, ts, util.dumps(hh_summary(r))))
            t.execute("INSERT INTO collect_runs (bucket, at, result) VALUES (?, ?, ?) ON CONFLICT (bucket) DO NOTHING",
                      (bucket, ts, util.dumps({"status": "collected", "seeded": True})))
        # Landing traffic
        pages = ["/", "/", "/", "/harahachi", "/harahachi", "/busboard", "/busboard", "/privacy"]
        refs = ["", "", "", "t.co", "www.google.com", "github.com", "www.reddit.com", "news.ycombinator.com"]
        for dayn in range(days):
            ts0 = util.day_start(now - dayn * 86400)
            for _ in range(r.randint(20, 90)):
                ts = ts0 + r.randint(0, 86000)
                if ts > now:
                    continue
                bot = r.random() < 0.08
                vh = "%016x" % r.getrandbits(40 if not bot else 8)
                path = r.choice(pages)
                t.execute("INSERT INTO site_events (at, day, type, path, ref_host, target, country, device_class, is_bot, visitor_hash) "
                          "VALUES (?, ?, 'pageview', ?, ?, '', ?, ?, ?, ?)",
                          (ts, util.day_of(ts), path, r.choice(refs), r.choice(["IN"] * 8 + ["US", "SG", "DE", ""]),
                           r.choice(["mobile"] * 6 + ["desktop"] * 3 + ["tablet"]), int(bot), vh))
                if not bot and r.random() < 0.18:
                    tgt = r.choice(["harahachi:open", "harahachi:apk", "busboard:open", "busboard:apk"])
                    t.execute("INSERT INTO site_events (at, day, type, path, ref_host, target, country, device_class, is_bot, visitor_hash) "
                              "VALUES (?, ?, 'click', ?, '', ?, 'IN', 'mobile', 0, ?)", (ts + 20, util.day_of(ts), path, tgt, vh))
        seeded = [("login", "invalid_assertion"), ("login", "ok"), ("harahachi.unlock_user", "ok"), ("busboard.wake", "up"),
                  ("step_up", "ok"), ("busboard.block", "ok")]
        for i, (action, outcome) in enumerate(seeded):  # oldest first, like real inserts
            t.execute("INSERT INTO audit (at, actor, action, target, outcome, country, ua, ip_hash, detail) VALUES (?, ?, ?, '', ?, 'IN', ?, ?, '{}')",
                      (now - (len(seeded) - i) * 5400, "anon" if action == "login" and outcome != "ok" else "owner", action, outcome,
                       "Mozilla/5.0 (Linux; Android 15) Chrome/140", "%024x" % r.getrandbits(96)))
