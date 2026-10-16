"""Secrets never leak; headers everywhere; dev mode can't turn on in prod."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import BB_TOKEN, CRON, HH_TOKEN, SETUP_TOKEN, SITE, Console, FakeApps, make_client

from bansho import config, util
from bansho.db import get_db

SECRETS = (HH_TOKEN, BB_TOKEN, CRON, SETUP_TOKEN)
CONSOLE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = CONSOLE_ROOT.parent

GETS = [
    "/api/session", "/api/health", "/robots.txt", "/api/overview",
    "/api/harahachi/summary", "/api/harahachi/timeseries?days=30", "/api/harahachi/retention?weeks=8",
    "/api/harahachi/funnel", "/api/harahachi/users?q=a&page=1&sort=meals", "/api/harahachi/users/3",
    "/api/harahachi/requests?status=5xx&limit=50", "/api/harahachi/audit",
    "/api/busboard/live", "/api/busboard/traffic?hours=24", "/api/landing?days=30",
    "/api/audit/console", "/api/audit/harahachi", "/api/audit/busboard",
    "/api/security/passkeys", "/api/security/sessions", "/api/security/recovery",
    "/api/nope", "/api/harahachi/bogus",
]
POSTS = [
    ("/api/harahachi/actions", {"action": "pairing_token"}),
    ("/api/harahachi/actions", {"action": "set_model", "slot": "chat", "model": "meta/llama-3.3-70b-instruct"}),
    ("/api/harahachi/actions", {"action": "revoke_device", "device_id": "d1"}),
    ("/api/harahachi/actions", {"action": "nope"}),
    ("/api/busboard/wake", {}),
    ("/api/busboard/snapshot", {}),
    ("/api/busboard/contributors/abc123/block", {}),
    ("/api/busboard/contributors/abc123/unblock", {}),
]


def everything(owner: Console) -> list[str]:
    owner.step_up()
    out = []
    for path in GETS:
        r = owner.get(path)
        out.append(f"{path} {r.status_code} {dict(r.headers)} {r.text}")
    for path, body in POSTS:
        r = owner.post(path, body)
        out.append(f"{path} {r.status_code} {dict(r.headers)} {r.text}")
    r = owner.c.get("/api/cron/collect", headers={"authorization": f"Bearer {CRON}"})
    out.append(f"cron {r.status_code} {dict(r.headers)} {r.text}")
    r = owner.c.get("/api/cron/collect", headers={"authorization": "Bearer nope"})
    out.append(f"cron-bad {r.status_code} {r.text}")
    r = owner.c.post("/api/ingest", content=json.dumps({"type": "pageview", "path": "/"}),
                     headers={"origin": SITE, "content-type": "text/plain"})
    out.append(f"ingest {r.status_code} {dict(r.headers)} {r.text}")
    return out


def assert_clean(blobs: list[str]) -> None:
    for blob in blobs:
        for s in SECRETS:
            assert s not in blob, blob[:200]
        # Partial leaks too (e.g. a truncated token).
        for s in (HH_TOKEN, BB_TOKEN):
            assert s[:20] not in blob and s[-20:] not in blob


def test_ops_tokens_never_in_any_response(owner: Console, fake_apps: FakeApps, capfd) -> None:
    blobs = everything(owner)
    # Upstreams really were called with the tokens (so the test is not vacuous).
    sent = {r.headers.get("authorization") for r in fake_apps.calls}
    assert f"Bearer {HH_TOKEN}" in sent and f"Bearer {BB_TOKEN}" in sent
    assert_clean(blobs)
    out, err = capfd.readouterr()
    assert_clean([out, err])
    with get_db().tx() as t:
        assert_clean([json.dumps(t.all("SELECT * FROM audit")), json.dumps(t.all("SELECT * FROM snapshots"))])


def test_hostile_upstream_echo_is_redacted(owner: Console, fake_apps: FakeApps, capfd) -> None:
    fake_apps.echo_auth = True  # upstream puts the bearer token in its error body
    blobs = everything(owner)
    assert_clean(blobs)
    fake_apps.echo_auth = False
    fake_apps.hh_down = True  # exception text contains the token
    fake_apps.bb_down = True
    from bansho import upstream

    upstream.clear_cache()
    assert_clean(everything(owner))
    out, err = capfd.readouterr()
    assert_clean([out, err])
    with get_db().tx() as t:
        assert_clean([json.dumps(t.all("SELECT * FROM audit")), json.dumps(t.all("SELECT * FROM snapshots"))])


def test_security_headers_everywhere(owner: Console) -> None:
    for path in ("/api/session", "/api/overview", "/api/nope", "/robots.txt", "/api/cron/collect"):
        r = owner.get(path)
        h = r.headers
        assert "max-age=" in h["strict-transport-security"], path
        assert "default-src 'none'" in h["content-security-policy"]
        assert "frame-ancestors 'none'" in h["content-security-policy"]
        assert h["x-frame-options"] == "DENY"
        assert h["x-content-type-options"] == "nosniff"
        assert h["referrer-policy"] == "no-referrer"
        assert h["x-robots-tag"].startswith("noindex, nofollow")
        assert "camera=()" in h["permissions-policy"]
        assert h["cache-control"] == "no-store"
    r = owner.post("/api/security/sessions/revoke-others")
    assert r.headers["x-robots-tag"].startswith("noindex")
    assert owner.get("/robots.txt").text == "User-agent: *\nDisallow: /\n"


def test_no_openapi_or_docs(console: Console) -> None:
    for p in ("/docs", "/redoc", "/openapi.json"):
        assert console.get(p).status_code == 404


def test_unauthenticated_data_endpoints_401(console: Console) -> None:
    for path in GETS:
        if path in ("/api/session", "/api/health", "/robots.txt", "/api/nope", "/api/harahachi/bogus"):
            continue
        assert console.get(path).status_code == 401, path


def test_dev_mode_cannot_enable_in_production(console: Console, monkeypatch) -> None:
    assert console.post("/api/dev/login").status_code == 404
    monkeypatch.setenv("BANSHO_DEV_SEED", "1")
    assert not config.dev_mode()  # https origin -> not dev
    assert console.post("/api/dev/login").status_code == 404
    monkeypatch.setenv("BANSHO_ORIGIN", "http://localhost:5173")
    monkeypatch.setenv("BANSHO_RP_ID", "localhost")
    monkeypatch.setenv("VERCEL", "1")
    assert not config.dev_mode()
    monkeypatch.delenv("VERCEL")
    monkeypatch.setenv("VERCEL_ENV", "production")
    assert not config.dev_mode()
    monkeypatch.delenv("VERCEL_ENV")
    assert config.dev_mode()  # only now: explicit flag + localhost + not Vercel


def test_rp_config_strict(console: Console, monkeypatch) -> None:
    bad = [
        ("bansho.test", "https://bansho.test/"),
        ("bansho.test", "https://bansho.test/login"),
        ("test", "https://bansho.test"),
        ("bansho.test", "http://bansho.test"),
        ("bansho.test", "https://user@bansho.test"),
        ("other.test", "https://bansho.test"),
    ]
    for rp_id, origin in bad:
        monkeypatch.setenv("BANSHO_RP_ID", rp_id)
        monkeypatch.setenv("BANSHO_ORIGIN", origin)
        with pytest.raises(config.ConfigError):
            config.relying_party()
        r = console.c.post("/api/auth/login/options", json={}, headers={"X-Bansho-CSRF": "1"})
        assert r.status_code == 503
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("BANSHO_RP_ID", "localhost")
    monkeypatch.setenv("BANSHO_ORIGIN", "http://localhost:5173")
    with pytest.raises(config.ConfigError):
        config.relying_party()


def test_short_ops_token_treated_as_unset(console: Console, monkeypatch) -> None:
    monkeypatch.setenv("HARAHACHI_OPS_TOKEN", "x" * 47)
    assert not config.harahachi().configured
    monkeypatch.setenv("HARAHACHI_URL", "http://harahachi.test")  # plain http refused
    monkeypatch.setenv("HARAHACHI_OPS_TOKEN", "x" * 48)
    assert config.harahachi().base_url is None


def test_ip_hash_daily_salt_rotates(db) -> None:
    util.now_override = 1_800_000_000
    with get_db().tx() as t:
        s1 = util.daily_salt(t)
    util.now_override += 86400
    with get_db().tx() as t:
        s2 = util.daily_salt(t)
    util.now_override += 3 * 86400
    with get_db().tx() as t:
        util.daily_salt(t)
        days = [r["day"] for r in t.all("SELECT day FROM daily_salts")]
    assert s1 != s2 and len(days) == 1  # old salts are deleted


# --- static config files ------------------------------------------------------------------

def _headers(vercel: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for block in vercel["headers"]:
        if block["source"] in ("/(.*)",):
            out.update({h["key"].lower(): h["value"] for h in block["headers"]})
    return out


@pytest.mark.parametrize("project", ["console", "site"])
def test_vercel_json_headers(project: str) -> None:
    v = json.loads((REPO_ROOT / project / "vercel.json").read_text(encoding="utf-8"))
    h = _headers(v)
    csp = h["content-security-policy"]
    script_src = next(d for d in csp.split(";") if d.strip().startswith("script-src"))
    assert "'unsafe-inline'" not in script_src and "'unsafe-eval'" not in script_src
    assert "'unsafe-inline'" not in csp
    assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    assert h["x-frame-options"] == "DENY"
    assert "max-age=" in h["strict-transport-security"]
    assert h["x-content-type-options"] == "nosniff"
    assert "permissions-policy" in h
    if project == "console":
        assert h["x-robots-tag"].startswith("noindex, nofollow")
        assert h["referrer-policy"] == "no-referrer"
        assert "connect-src 'self'" in csp
        assert any(c["path"] == "/api/cron/collect" for c in v["crons"])


def test_console_robots_disallow_all() -> None:
    assert (CONSOLE_ROOT / "public" / "robots.txt").read_text(encoding="utf-8").strip() == "User-agent: *\nDisallow: /"


def test_no_inline_scripts_in_html() -> None:
    import re

    for html in [CONSOLE_ROOT / "index.html", *(REPO_ROOT / "site").glob("**/index.html")]:
        if "node_modules" in html.parts or "dist" in html.parts:
            continue
        text = html.read_text(encoding="utf-8")
        for m in re.finditer(r"<script\b([^>]*)>(.*?)</script>", text, re.S):
            assert "src=" in m.group(1) and not m.group(2).strip(), html
        assert not re.search(r"\son[a-z]+=", text), html  # no inline event handlers


def test_production_bundle_has_no_dev_login() -> None:
    dist = CONSOLE_ROOT / "dist"
    if not dist.exists():
        pytest.skip("console not built yet")
    for js in dist.rglob("*.js"):
        assert "/api/dev/login" not in js.read_text(encoding="utf-8"), js
