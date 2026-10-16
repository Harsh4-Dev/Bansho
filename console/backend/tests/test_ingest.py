"""POST /api/ingest - the public beacon endpoint."""
from __future__ import annotations

import json

from conftest import SITE, Console

from bansho import util
from bansho.db import get_db

UA_PHONE = "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 Chrome/140 Mobile Safari/537.36"


def beacon(console: Console, body, origin: str | None = SITE, ua: str = UA_PHONE, ctype: str = "text/plain;charset=UTF-8"):
    headers = {"content-type": ctype, "user-agent": ua}
    if origin is not None:
        headers["origin"] = origin
    data = body if isinstance(body, (bytes, str)) else json.dumps(body)
    return console.c.post("/api/ingest", content=data, headers=headers)


def rows() -> list[dict]:
    with get_db().tx() as t:
        return t.all("SELECT * FROM site_events ORDER BY id")


def test_accepts_exact_site_origin(console: Console) -> None:
    r = beacon(console, {"type": "pageview", "path": "/harahachi", "ref_host": "www.google.com"})
    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == SITE
    assert "access-control-allow-credentials" not in r.headers
    assert "set-cookie" not in r.headers
    [row] = rows()
    assert row["type"] == "pageview" and row["path"] == "/harahachi" and row["ref_host"] == "www.google.com"
    assert row["device_class"] == "mobile" and row["is_bot"] == 0
    assert row["visitor_hash"] and len(row["visitor_hash"]) == 20


def test_rejects_other_origins(console: Console) -> None:
    ok = {"type": "pageview", "path": "/"}
    for origin in (None, "https://evil.test", SITE + ".evil.test", "http://site.test", SITE + "/", "null"):
        r = beacon(console, ok, origin=origin)
        assert r.status_code == 403, origin
        assert "access-control-allow-origin" not in r.headers
    assert rows() == []


def test_preflight(console: Console) -> None:
    r = console.c.options("/api/ingest", headers={"origin": SITE, "access-control-request-method": "POST"})
    assert r.status_code == 204 and r.headers["access-control-allow-origin"] == SITE
    r = console.c.options("/api/ingest", headers={"origin": "https://evil.test", "access-control-request-method": "POST"})
    assert r.status_code == 403 and "access-control-allow-origin" not in r.headers


def test_size_cap(console: Console) -> None:
    big = json.dumps({"type": "pageview", "path": "/", "ref_host": "a" * 1100})
    assert beacon(console, big).status_code == 413
    assert rows() == []


def test_schema_is_strict(console: Console) -> None:
    for body in (
        {"type": "pageview"},
        {"type": "hack", "path": "/"},
        {"type": "pageview", "path": "no-slash"},
        {"type": "pageview", "path": "/<script>"},
        {"type": "pageview", "path": "/", "email": "x@y.z"},
        {"type": "click", "path": "/"},
        {"type": "click", "path": "/", "target": "UPPER CASE"},
        {"type": "pageview", "path": "/", "ref_host": "has space.com"},
        [1, 2],
        "not json",
    ):
        assert beacon(console, body).status_code == 400, body
    assert beacon(console, {"type": "pageview", "path": "/"}, ctype="application/x-www-form-urlencoded").status_code == 415
    assert rows() == []


def test_click_and_self_referrer(console: Console) -> None:
    assert beacon(console, {"type": "click", "path": "/busboard", "target": "busboard:apk", "ref_host": "site.test"}).status_code == 204
    [row] = rows()
    assert row["target"] == "busboard:apk" and row["ref_host"] == ""


def test_bot_flagged(console: Console) -> None:
    for ua in ("Googlebot/2.1 (+http://www.google.com/bot.html)", "curl/8.5.0", "", "python-httpx/0.28"):
        assert beacon(console, {"type": "pageview", "path": "/"}, ua=ua).status_code == 204
    assert [r["is_bot"] for r in rows()] == [1, 1, 1, 1]
    assert beacon(console, {"type": "pageview", "path": "/"}, ua="Mozilla/5.0 (iPad; CPU OS 18_0) Safari").status_code == 204
    assert rows()[-1]["device_class"] == "tablet" and rows()[-1]["is_bot"] == 0


def test_no_ip_or_ua_stored(console: Console) -> None:
    beacon(console, {"type": "pageview", "path": "/"}, ua=UA_PHONE)
    with get_db().tx() as t:
        dump = json.dumps(t.all("SELECT * FROM site_events"))
    assert "testclient" not in dump and "Pixel" not in dump and "Android" not in dump


def test_rate_limited_per_ip(console: Console) -> None:
    util.now_override = util.now() - util.now() % 600 + 5
    codes = [beacon(console, {"type": "pageview", "path": "/"}).status_code for _ in range(121)]
    assert codes[:120] == [204] * 120 and codes[120] == 429
    assert len(rows()) == 120


def test_disabled_without_site_origin(console: Console, monkeypatch) -> None:
    monkeypatch.delenv("BANSHO_SITE_ORIGIN")
    assert beacon(console, {"type": "pageview", "path": "/"}).status_code == 403
