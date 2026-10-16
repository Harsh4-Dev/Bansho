"""Screen data endpoints, proxying, caching, alerts, dev seed."""
from __future__ import annotations

import json

from conftest import CRON, SITE, Console, FakeApps

from bansho import devseed, upstream, util
from bansho.db import get_db


def test_overview_live_and_alerts(owner: Console, fake_apps: FakeApps) -> None:
    owner.c.get("/api/cron/collect", headers={"authorization": f"Bearer {CRON}"})
    o = owner.get("/api/overview").json()
    hh = o["apps"]["harahachi"]
    assert hh["state"] == "up" and hh["source"] == "live" and hh["kpis"]["users"] == 3
    assert hh["uptime_24h"]["checks"] == 1 and hh["uptime_24h"]["pct_up"] == 100.0
    bb = o["apps"]["busboard"]
    assert bb["state"] == "up" and bb["kpis"]["requests"] == 150 and bb["kpis"]["vehicles_active"] == 1
    assert isinstance(o["alerts"], list)


def test_overview_upstream_down_uses_snapshot(owner: Console, fake_apps: FakeApps) -> None:
    owner.c.get("/api/cron/collect", headers={"authorization": f"Bearer {CRON}"})
    upstream.clear_cache()
    fake_apps.bb_down = True
    fake_apps.hh_down = True
    o = owner.get("/api/overview").json()
    assert o["apps"]["busboard"]["state"] == "asleep"
    assert o["apps"]["harahachi"]["state"] == "down"
    assert o["apps"]["harahachi"]["source"] == "snapshot"
    assert o["apps"]["busboard"]["kpis"]["sse_open"] is None  # never show stale "live" numbers


def test_wrong_token_shown_as_misconfigured(owner: Console, fake_apps: FakeApps, monkeypatch) -> None:
    monkeypatch.setenv("HARAHACHI_OPS_TOKEN", "w" * 60)
    upstream.clear_cache()
    o = owner.get("/api/overview").json()
    assert o["apps"]["harahachi"]["state"] == "misconfigured"
    assert any(a["code"] == "token" for a in o["alerts"])


def test_cache_short_lived(owner: Console, fake_apps: FakeApps) -> None:
    owner.get("/api/harahachi/summary")
    n = len(fake_apps.calls)
    owner.get("/api/harahachi/summary")
    assert len(fake_apps.calls) == n
    upstream._cache[next(iter(upstream._cache))].fetched_at -= upstream.CACHE_TTL_S + 1
    owner.get("/api/harahachi/summary")
    assert len(fake_apps.calls) == n + 1


def test_action_validation(owner: Console, fake_apps: FakeApps) -> None:
    assert owner.post("/api/harahachi/actions", {"action": "drop_tables"}).status_code == 400
    assert owner.post("/api/harahachi/actions", {"action": "set_model", "slot": "evil", "model": "m"}).status_code == 400
    assert owner.post("/api/harahachi/actions", {"action": "unlock_user", "user_id": "../../x"}).status_code == 400
    assert owner.post("/api/harahachi/actions", {"action": "unlock_user"}).status_code == 400
    r = owner.post("/api/harahachi/actions", {"action": "set_model", "slot": "chat", "model": "meta/llama-3.3-70b-instruct", "junk": 1})
    assert r.status_code == 200
    sent = json.loads(fake_apps.calls[-1].content)
    assert sent == {"action": "set_model", "slot": "chat", "model": "meta/llama-3.3-70b-instruct"}
    with get_db().tx() as t:
        assert t.one("SELECT * FROM audit WHERE action = 'harahachi.set_model'")["outcome"] == "ok"


def test_query_validation(owner: Console) -> None:
    assert owner.get("/api/harahachi/users?q=%27%3B--").status_code == 400
    assert owner.get("/api/harahachi/users/..%2F..%2Fadmin").status_code == 404
    assert owner.get("/api/harahachi/requests?status=abc").status_code == 400


def test_wake_and_audit(owner: Console, fake_apps: FakeApps) -> None:
    r = owner.post("/api/busboard/wake")
    assert r.json()["state"] == "up"
    rows = owner.get("/api/audit/console").json()["rows"]
    assert rows[0]["action"] == "busboard.wake"
    assert {"at", "actor", "action", "outcome", "country", "client"} <= set(rows[0])


def test_landing_aggregates(owner: Console) -> None:
    def b(body, ua="Mozilla/5.0 (Windows NT 10.0) Chrome/140"):
        owner.c.post("/api/ingest", content=json.dumps(body), headers={"origin": SITE, "content-type": "text/plain", "user-agent": ua})

    b({"type": "pageview", "path": "/", "ref_host": "t.co"})
    b({"type": "pageview", "path": "/harahachi"})
    b({"type": "click", "path": "/harahachi", "target": "harahachi:apk"})
    b({"type": "pageview", "path": "/"}, ua="Googlebot")
    d = owner.get("/api/landing?days=7").json()
    assert d["today"] == {"pageviews": 2, "visitors": 1, "clicks": 1}
    assert d["totals"]["bots"] == 1
    assert ["harahachi:apk", 1] in d["ctas"]
    assert dict(d["referrers"]) == {"t.co": 1, "(direct)": 1}
    assert len(d["days"]) == 7 and len(d["series"]["pageviews"]) == 7


def test_dev_seed_populates_every_screen(owner: Console, monkeypatch) -> None:
    devseed.seed(get_db(), days=5)
    upstream.transport = devseed.fake_transport()
    monkeypatch.setenv("HARAHACHI_URL", "https://harahachi.example.invalid")
    monkeypatch.setenv("BUSBOARD_URL", "https://busboard.example.invalid")
    upstream.clear_cache()
    for path in ("/api/overview", "/api/harahachi/summary", "/api/harahachi/timeseries?days=30", "/api/harahachi/retention",
                 "/api/harahachi/funnel", "/api/harahachi/users", "/api/harahachi/users/3", "/api/harahachi/requests",
                 "/api/busboard/live", "/api/busboard/traffic?hours=120", "/api/landing", "/api/audit/console"):
        r = owner.get(path)
        assert r.status_code == 200, path
        body = r.json()
        if "upstream" in body and path.startswith("/api/harahachi"):
            assert body["upstream"]["ok"], path
    t = owner.get("/api/busboard/traffic?hours=120").json()
    assert len(t["series"]["requests"]) > 10 and all(v >= 0 for v in t["series"]["requests"])
    live = owner.get("/api/busboard/live").json()
    assert live["timeline"]["boots"]
    util.now_override = None
