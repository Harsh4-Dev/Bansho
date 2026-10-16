"""Setup token, passkey ceremonies, origin/RP checks, replay, recovery codes."""
from __future__ import annotations

from conftest import ORIGIN, SETUP_TOKEN, Console, make_client
from softauthn import SoftAuthenticator

from bansho.db import get_db


def audit_rows(action: str) -> list[dict]:
    with get_db().tx() as t:
        return t.all("SELECT * FROM audit WHERE action = ? ORDER BY id", (action,))


# --- setup token ---------------------------------------------------------------

def test_setup_wrong_or_missing_token_is_404(console: Console) -> None:
    assert console.post("/api/auth/setup/options", {}).status_code == 404
    assert console.post("/api/auth/setup/options", {"setup_token": "nope"}).status_code == 404
    assert console.post("/api/auth/setup/options", {"setup_token": SETUP_TOKEN[:-1] + "x"}).status_code == 404
    assert audit_rows("setup.options")[-1]["outcome"] == "bad_token"


def test_setup_unset_or_short_token_is_404(console: Console, monkeypatch) -> None:
    monkeypatch.delenv("BANSHO_SETUP_TOKEN")
    assert console.post("/api/auth/setup/options", {"setup_token": ""}).status_code == 404
    monkeypatch.setenv("BANSHO_SETUP_TOKEN", "short-token-31-characters-long!")
    assert console.post("/api/auth/setup/options", {"setup_token": "short-token-31-characters-long!"}).status_code == 404


def test_setup_once_then_404_forever(console: Console) -> None:
    r = console.setup()
    body = r.json()
    assert len(body["recovery_codes"]) == 10
    assert len(set(body["recovery_codes"])) == 10
    assert all(len(c.replace("-", "")) == 20 for c in body["recovery_codes"])
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("__Host-bansho_session=")
    low = cookie.lower()
    assert "secure" in low and "httponly" in low and "samesite=strict" in low and "path=/" in low
    assert "domain=" not in low
    # Codes are stored hashed, never in clear.
    with get_db().tx() as t:
        hashes = [r["code_hash"] for r in t.all("SELECT code_hash FROM recovery_codes")]
    assert len(hashes) == 10
    assert not any(c.replace("-", "") in h for c in body["recovery_codes"] for h in hashes)
    # Setup is gone, even with the correct token, even for a fresh browser.
    fresh = Console(make_client())
    assert fresh.post("/api/auth/setup/options", {"setup_token": SETUP_TOKEN}).status_code == 404
    assert fresh.post("/api/auth/setup/verify", {"setup_token": SETUP_TOKEN}).status_code == 404
    assert console.post("/api/auth/setup/options", {"setup_token": SETUP_TOKEN}).status_code == 404


def test_setup_requires_csrf_header(console: Console) -> None:
    r = console.c.post("/api/auth/setup/options", json={"setup_token": SETUP_TOKEN}, headers={"Origin": ORIGIN})
    assert r.status_code == 403


def test_setup_ceremony_single_use(console: Console) -> None:
    o = console.post("/api/auth/setup/options", {"setup_token": SETUP_TOKEN}).json()
    bad = console.auth.create(o["options"], origin="https://evil.test")
    r = console.post("/api/auth/setup/verify", {"setup_token": SETUP_TOKEN, "ceremony_id": o["ceremony_id"], "credential": bad})
    assert r.status_code == 400
    good = console.auth.create(o["options"])
    r = console.post("/api/auth/setup/verify", {"setup_token": SETUP_TOKEN, "ceremony_id": o["ceremony_id"], "credential": good})
    assert r.status_code == 400  # ceremony was consumed by the failed attempt
    with get_db().tx() as t:
        assert t.scalar("SELECT COUNT(*) FROM credentials") == 0


# --- login ceremony ---------------------------------------------------------------

def test_full_login_ceremony(owner: Console) -> None:
    browser = Console(make_client())
    browser.auth = owner.auth
    assert browser.get("/api/session").json() == {"authenticated": False}
    r = browser.login()
    assert r.status_code == 200, r.text
    s = browser.get("/api/session").json()
    assert s["authenticated"] and s["session"]["kind"] == "full"
    assert browser.get("/api/overview").status_code == 200
    assert audit_rows("login")[-1]["outcome"] == "ok"
    opts = browser.post("/api/auth/login/options").json()["options"]
    assert opts["rpId"] == "bansho.test"
    assert opts.get("allowCredentials", []) == []
    assert opts["userVerification"] == "required"


def test_unknown_passkey_rejected(owner: Console) -> None:
    stranger = SoftAuthenticator()
    stranger.user_handle = owner.auth.user_handle
    browser = Console(make_client())
    assert browser.login(stranger).status_code == 401
    assert audit_rows("login")[-1]["outcome"] == "unknown_credential"


def test_wrong_origin_rejected(owner: Console) -> None:
    browser = Console(make_client())
    browser.auth = owner.auth
    assert browser.login(origin="https://bansho.test.evil.example").status_code == 401
    assert audit_rows("login")[-1]["outcome"] == "wrong_origin"
    assert browser.login(origin="http://bansho.test").status_code == 401


def test_wrong_rp_id_rejected(owner: Console) -> None:
    browser = Console(make_client())
    browser.auth = owner.auth
    assert browser.login(rp_id="evil.test").status_code == 401
    assert audit_rows("login")[-1]["outcome"] == "wrong_rp_id"


def test_registration_with_wrong_rp_id_rejected(console: Console) -> None:
    o = console.post("/api/auth/setup/options", {"setup_token": SETUP_TOKEN}).json()
    cred = console.auth.create(o["options"], rp_id="evil.test")
    r = console.post("/api/auth/setup/verify", {"setup_token": SETUP_TOKEN, "ceremony_id": o["ceremony_id"], "credential": cred})
    assert r.status_code == 400


def test_user_verification_required(owner: Console) -> None:
    browser = Console(make_client())
    browser.auth = owner.auth
    assert browser.login(flags=0x01).status_code == 401  # UP only, no UV


def test_assertion_replay_rejected(owner: Console) -> None:
    browser = Console(make_client())
    o = browser.post("/api/auth/login/options").json()
    cred = owner.auth.get(o["options"])
    assert browser.post("/api/auth/login/verify", {"ceremony_id": o["ceremony_id"], "credential": cred}).status_code == 200
    # Same signed assertion again: ceremony consumed.
    again = Console(make_client())
    assert again.post("/api/auth/login/verify", {"ceremony_id": o["ceremony_id"], "credential": cred}).status_code == 401
    # Same assertion against a NEW ceremony: challenge mismatch.
    o2 = again.post("/api/auth/login/options").json()
    assert again.post("/api/auth/login/verify", {"ceremony_id": o2["ceremony_id"], "credential": cred}).status_code == 401
    assert audit_rows("login")[-1]["outcome"] == "wrong_challenge"


def test_sign_count_regression_rejected(owner: Console) -> None:
    browser = Console(make_client())
    browser.auth = owner.auth
    assert browser.login().status_code == 200          # counter 1
    assert browser.login().status_code == 200          # counter 2
    assert browser.login(counter=2).status_code == 401  # not greater -> cloned?
    assert audit_rows("login")[-1]["outcome"] == "sign_count_regression"
    assert browser.login(counter=1).status_code == 401
    assert browser.login(counter=10).status_code == 200


def test_expired_ceremony_rejected(owner: Console) -> None:
    from bansho import util

    browser = Console(make_client())
    o = browser.post("/api/auth/login/options").json()
    util.now_override = util.now() + 301
    r = browser.post("/api/auth/login/verify", {"ceremony_id": o["ceremony_id"], "credential": owner.auth.get(o["options"])})
    assert r.status_code == 401


def test_malformed_credential_never_500(owner: Console) -> None:
    browser = Console(make_client())
    for junk in (None, 5, "x", {"id": "AAAA"}, {"id": owner.auth.id, "response": "x"},
                 {"id": owner.auth.id, "rawId": owner.auth.id, "type": "public-key", "response": {"clientDataJSON": "!!"}}):
        with get_db().tx() as t:
            t.execute("DELETE FROM lockouts")  # this test is about parsing, not lockout
        o = browser.post("/api/auth/login/options").json()
        r = browser.post("/api/auth/login/verify", {"ceremony_id": o["ceremony_id"], "credential": junk})
        assert r.status_code == 401, (junk, r.text)


# --- recovery codes --------------------------------------------------------------------

def test_recovery_code_single_use_and_limited_scope(owner: Console) -> None:
    code = owner.recovery_codes[0]
    rec = Console(make_client())
    r = rec.post("/api/auth/recovery", {"code": code.upper().replace("-", " ")})  # forgiving format
    assert r.status_code == 200, r.text
    rec.csrf = r.json()["session"]["csrf"]
    assert r.json()["session"]["kind"] == "recovery"
    # Scope: nothing but passkey registration.
    for path in ("/api/overview", "/api/security/sessions", "/api/security/passkeys", "/api/landing",
                 "/api/harahachi/summary", "/api/audit/console", "/api/security/recovery"):
        assert rec.get(path).status_code == 403, path
    assert rec.post("/api/security/recovery/regenerate").status_code == 403
    assert rec.post("/api/auth/reauth/options").status_code == 403
    assert rec.post("/api/harahachi/actions", {"action": "pairing_token"}).status_code == 403
    # The one allowed thing: register a new passkey -> then the session ends.
    new_key = SoftAuthenticator()
    r = rec.add_passkey(new_key)
    assert r.status_code == 200 and r.json()["next"] == "login"
    assert rec.get("/api/session").json() == {"authenticated": False}
    # The code is spent.
    again = Console(make_client())
    assert again.post("/api/auth/recovery", {"code": code}).status_code == 401
    # The new passkey works for a normal login.
    new_key.user_handle = owner.auth.user_handle
    fresh = Console(make_client())
    assert fresh.login(new_key).status_code == 200
    with get_db().tx() as t:
        assert t.scalar("SELECT COUNT(*) FROM recovery_codes WHERE used_at IS NULL") == 9


def test_recovery_session_expires_fast(owner: Console) -> None:
    from bansho import util

    rec = Console(make_client())
    r = rec.post("/api/auth/recovery", {"code": owner.recovery_codes[1]})
    rec.csrf = r.json()["session"]["csrf"]
    util.now_override = util.now() + 15 * 60 + 1
    assert rec.post("/api/security/passkeys/options").status_code == 401


def test_bad_recovery_code(owner: Console) -> None:
    rec = Console(make_client())
    assert rec.post("/api/auth/recovery", {"code": "aaaaa-bbbbb-ccccc-ddddd"}).status_code == 401
    assert rec.post("/api/auth/recovery", {"code": 12345}).status_code == 401
    assert audit_rows("recovery")[-1]["outcome"] == "bad_code"


def test_regenerate_recovery_needs_step_up_and_invalidates_old(owner: Console) -> None:
    old = owner.recovery_codes
    assert owner.post("/api/security/recovery/regenerate").json()["error"]["code"] == "step_up_required"
    assert owner.step_up().status_code == 200
    r = owner.post("/api/security/recovery/regenerate")
    assert r.status_code == 200
    new = r.json()["recovery_codes"]
    assert len(new) == 10 and not set(new) & set(old)
    assert Console(make_client()).post("/api/auth/recovery", {"code": old[5]}).status_code == 401
    assert Console(make_client()).post("/api/auth/recovery", {"code": new[5]}).status_code == 200
