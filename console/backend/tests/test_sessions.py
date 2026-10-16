"""Session lifetime/revocation, CSRF, step-up, passkey management, lockout."""
from __future__ import annotations

from conftest import ORIGIN, Console, make_client
from softauthn import SoftAuthenticator

from bansho import util
from bansho.db import get_db


def advance(seconds: int) -> None:
    util.now_override = (util.now_override or util.now()) + seconds


# --- expiry and revocation --------------------------------------------------------

def test_idle_timeout(owner: Console) -> None:
    advance(59 * 60)
    assert owner.get("/api/overview").status_code == 200  # activity resets idle clock
    advance(59 * 60)
    assert owner.get("/api/overview").status_code == 200
    advance(60 * 60 + 1)
    assert owner.get("/api/overview").status_code == 401
    assert owner.get("/api/session").json() == {"authenticated": False}


def test_absolute_timeout(owner: Console) -> None:
    for _ in range(15):  # 15 x 30 min = 7.5 h, always active
        advance(30 * 60)
        assert owner.get("/api/overview").status_code == 200
    advance(30 * 60)  # 8 h since login
    assert owner.get("/api/overview").status_code == 401


def test_revoke_other_session(owner: Console) -> None:
    other = Console(make_client())
    other.auth = owner.auth
    assert other.login().status_code == 200
    sessions = owner.get("/api/security/sessions").json()["sessions"]
    assert len(sessions) == 2
    target = next(s for s in sessions if not s["current"])
    assert owner.post(f"/api/security/sessions/{target['id']}/revoke").status_code == 200
    assert other.get("/api/overview").status_code == 401
    assert owner.get("/api/overview").status_code == 200
    with get_db().tx() as t:
        assert t.one("SELECT * FROM audit WHERE action = 'session.revoke'")["outcome"] == "ok"


def test_logout_revokes_server_side(owner: Console) -> None:
    cookie = owner.c.cookies.get("__Host-bansho_session")
    assert owner.post("/api/auth/logout").status_code == 200
    replay = make_client()
    replay.cookies.set("__Host-bansho_session", cookie)
    assert replay.get("/api/overview").status_code == 401


def test_session_id_stored_hashed(owner: Console) -> None:
    raw = owner.c.cookies.get("__Host-bansho_session")
    with get_db().tx() as t:
        rows = t.all("SELECT id_hash FROM sessions")
    assert rows and all(raw != r["id_hash"] and raw not in r["id_hash"] for r in rows)


# --- CSRF / origin ------------------------------------------------------------------

def test_csrf_required_on_non_get(owner: Console) -> None:
    path = "/api/security/sessions/revoke-others"
    no_header = {"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"}
    assert owner.c.post(path, json={}, headers=no_header).status_code == 403
    assert owner.c.post(path, json={}, headers={**no_header, "X-Bansho-CSRF": "wrong"}).status_code == 403
    assert owner.c.post(path, json={}, headers={**no_header, "X-Bansho-CSRF": "1"}).status_code == 403
    assert owner.post(path).status_code == 200


def test_cross_origin_refused(owner: Console) -> None:
    path = "/api/security/sessions/revoke-others"
    r = owner.post(path, headers=owner.headers(extra={"Origin": "https://evil.test"}))
    assert r.status_code == 403 and r.json()["error"]["code"] == "bad_origin"
    r = owner.post(path, headers=owner.headers(extra={"Sec-Fetch-Site": "cross-site"}))
    assert r.status_code == 403
    # Anonymous auth endpoints too.
    anon = Console(make_client())
    assert anon.post("/api/auth/login/options", headers=anon.headers(extra={"Origin": "https://evil.test"})).status_code == 403


def test_no_cors_on_console(owner: Console) -> None:
    r = owner.c.options("/api/overview", headers={"Origin": "https://evil.test", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in r.headers
    r = owner.get("/api/overview", headers={"Origin": "https://evil.test"})
    assert "access-control-allow-origin" not in r.headers


# --- step-up -------------------------------------------------------------------------

def test_step_up_for_destructive_actions(owner: Console) -> None:
    second = SoftAuthenticator()
    # Adding a passkey from a full session needs step-up as well.
    assert owner.add_passkey(second).json()["error"]["code"] == "step_up_required"
    assert owner.step_up().status_code == 200
    assert owner.add_passkey(second).status_code == 200
    pk = owner.get("/api/security/passkeys").json()["passkeys"]
    assert len(pk) == 2
    advance(5 * 60 + 1)  # step-up window over
    r = owner.post(f"/api/security/passkeys/{pk[1]['id']}/delete")
    assert r.status_code == 403 and r.json()["error"]["code"] == "step_up_required"
    for body in ({"action": "revoke_device", "device_id": "d1"}, {"action": "revoke_keeper", "user_id": 3}):
        r = owner.post("/api/harahachi/actions", body)
        assert r.json()["error"]["code"] == "step_up_required", body
    assert owner.post("/api/busboard/contributors/abc123/block").json()["error"]["code"] == "step_up_required"
    # Non-destructive actions do not need it.
    assert owner.post("/api/harahachi/actions", {"action": "unlock_user", "user_id": 3}).status_code == 200
    assert owner.post("/api/busboard/contributors/abc123/unblock").status_code == 200
    # After a fresh re-auth they go through.
    assert owner.step_up().status_code == 200
    assert owner.post(f"/api/security/passkeys/{pk[1]['id']}/delete").status_code == 200
    assert owner.post("/api/harahachi/actions", {"action": "revoke_device", "device_id": "d1"}).status_code == 200
    assert owner.post("/api/busboard/contributors/abc123/block").status_code == 200


def test_step_up_bound_to_session(owner: Console) -> None:
    other = Console(make_client())
    other.auth = owner.auth
    other.login()
    o = other.post("/api/auth/reauth/options").json()
    cred = owner.auth.get(o["options"])
    r = owner.post("/api/auth/reauth/verify", {"ceremony_id": o["ceremony_id"], "credential": cred})
    assert r.status_code == 401  # ceremony belongs to the other session


def test_failed_step_up_does_not_grant(owner: Console) -> None:
    o = owner.post("/api/auth/reauth/options").json()
    bad = owner.auth.get(o["options"], origin="https://evil.test")
    assert owner.post("/api/auth/reauth/verify", {"ceremony_id": o["ceremony_id"], "credential": bad}).status_code == 401
    pk = owner.get("/api/security/passkeys").json()["passkeys"]
    assert owner.post(f"/api/security/passkeys/{pk[0]['id']}/delete").json()["error"]["code"] == "step_up_required"


def test_cannot_remove_last_passkey(owner: Console) -> None:
    owner.step_up()
    pk = owner.get("/api/security/passkeys").json()["passkeys"]
    r = owner.post(f"/api/security/passkeys/{pk[0]['id']}/delete")
    assert r.status_code == 409 and r.json()["error"]["code"] == "last_passkey"


def test_passkey_limit_and_rename(owner: Console) -> None:
    owner.step_up()
    for _ in range(4):
        assert owner.add_passkey(SoftAuthenticator()).status_code == 200
    r = owner.add_passkey(SoftAuthenticator())
    assert r.status_code == 409 and r.json()["error"]["code"] == "passkey_limit"
    pk = owner.get("/api/security/passkeys").json()["passkeys"]
    assert len(pk) == 5
    assert owner.post(f"/api/security/passkeys/{pk[0]['id']}/rename", {"name": "Pixel"}).status_code == 200
    names = {p["id"]: p["name"] for p in owner.get("/api/security/passkeys").json()["passkeys"]}
    assert names[pk[0]["id"]] == "Pixel"


def test_duplicate_passkey_excluded(owner: Console) -> None:
    owner.step_up()
    o = owner.post("/api/security/passkeys/options").json()
    assert owner.auth.id in [c["id"] for c in o["options"]["excludeCredentials"]]


# --- rate limit + lockout -----------------------------------------------------------------

def test_progressive_lockout_on_recovery(owner: Console) -> None:
    attacker = Console(make_client())
    bad = {"code": "zzzzz-zzzzz-zzzzz-zzzzz"}
    for _ in range(4):
        assert attacker.post("/api/auth/recovery", bad).status_code == 401
    assert attacker.post("/api/auth/recovery", bad).status_code == 401  # 5th failure -> locks 60 s
    r = attacker.post("/api/auth/recovery", {"code": owner.recovery_codes[0]})
    assert r.status_code == 429 and r.json()["error"]["code"] == "locked"  # even a GOOD code is refused
    assert int(r.headers["retry-after"]) <= 60
    advance(61)
    assert attacker.post("/api/auth/recovery", bad).status_code == 401  # 6th -> 120 s
    advance(61)
    assert attacker.post("/api/auth/recovery", bad).status_code == 429
    advance(60)
    assert attacker.post("/api/auth/recovery", {"code": owner.recovery_codes[0]}).status_code == 200


def test_login_lockout_and_reset_on_success(owner: Console) -> None:
    browser = Console(make_client())
    browser.auth = owner.auth
    for _ in range(5):
        assert browser.login(origin="https://evil.test").status_code == 401
    r = browser.post("/api/auth/login/options")
    assert r.status_code == 429
    advance(61)
    assert browser.login().status_code == 200
    with get_db().tx() as t:
        assert t.scalar("SELECT COUNT(*) FROM lockouts WHERE key LIKE 'lock:auth:%'") == 0


def test_auth_rate_limit(console: Console) -> None:
    util.now_override = util.now() - util.now() % 300 + 5  # start of a window
    codes = [console.post("/api/auth/login/options").status_code for _ in range(21)]
    assert codes[:20] == [200] * 20 and codes[20] == 429
    advance(301)
    assert console.post("/api/auth/login/options").status_code == 200


def test_global_recovery_lock_spans_ips(owner: Console, monkeypatch) -> None:
    from bansho import guards

    monkeypatch.setattr(guards, "GLOBAL_FREE_FAILURES", 3)
    monkeypatch.setenv("VERCEL", "1")  # trust x-forwarded-for, as on Vercel's edge
    for i in range(4):
        c = Console(make_client())
        c.c.headers["x-forwarded-for"] = f"10.0.0.{i}"
        c.post("/api/auth/recovery", {"code": "bad"})
    with get_db().tx() as t:
        row = t.one("SELECT * FROM lockouts WHERE key = 'lock:recovery:global'")
    assert row and row["locked_until"] > util.now()
    with get_db().tx() as t:
        per_ip = t.all("SELECT * FROM lockouts WHERE key LIKE 'lock:auth:%'")
    assert len(per_ip) == 4 and all(r["locked_until"] <= util.now() for r in per_ip)
    fresh = Console(make_client())
    fresh.c.headers["x-forwarded-for"] = "10.9.9.9"
    r = fresh.post("/api/auth/recovery", {"code": owner.recovery_codes[0]})
    assert r.status_code == 429


def test_background_polling_does_not_extend_idle(owner: Console) -> None:
    for _ in range(2):
        advance(25 * 60)
        assert owner.get("/api/overview", headers={"X-Bansho-Background": "1"}).status_code == 200
    advance(11 * 60)
    # 61 minutes with only background polling: the session went idle.
    assert owner.get("/api/overview").status_code == 401
