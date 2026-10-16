"""Passkey-only authentication (py_webauthn 3.0.1).

Endpoints (all JSON, all non-GET need the X-Bansho-CSRF header):

  POST /api/auth/setup/options     {setup_token}                 404 once a passkey exists
  POST /api/auth/setup/verify      {setup_token, ceremony_id, credential, name}
  POST /api/auth/login/options     {}
  POST /api/auth/login/verify      {ceremony_id, credential}
  POST /api/auth/recovery          {code}                        -> recovery-scoped session
  POST /api/auth/reauth/options    {}                            (session) step-up
  POST /api/auth/reauth/verify     {ceremony_id, credential}
  POST /api/auth/logout            {}
  GET  /api/session

Failure responses never say *why* a credential was refused; the audit log does.
"""
from __future__ import annotations

import json
import secrets
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from . import audit, config, guards, sessions, util
from .db import Tx, get_db
from .guards import ApiError

router = APIRouter()

RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # 31 symbols, no 0/o/1/i/l


# --- helpers -------------------------------------------------------------------

async def body_json(request: Request, limit: int = 16_384) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > limit:
        raise ApiError(413, "too_large", "Request body too large")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        raise ApiError(400, "bad_json", "Body must be JSON")
    if not isinstance(data, dict):
        raise ApiError(400, "bad_json", "Body must be a JSON object")
    return data


def rp() -> config.RelyingParty:
    try:
        return config.relying_party()
    except config.ConfigError as exc:
        raise ApiError(503, "misconfigured", str(exc))


def passkey_count(t: Tx) -> int:
    return int(t.scalar("SELECT COUNT(*) FROM credentials") or 0)


def owner_handle(t: Tx, create: bool = False) -> bytes:
    row = t.one("SELECT value FROM meta WHERE key = 'user_handle'")
    if row:
        return base64url_to_bytes(row["value"])
    if not create:
        raise ApiError(500, "no_owner", "Owner not initialised")
    handle = secrets.token_bytes(32)
    t.execute("INSERT INTO meta (key, value) VALUES ('user_handle', ?)", (bytes_to_base64url(handle),))
    return handle


def new_ceremony(t: Tx, purpose: str, challenge: bytes, session_hash: str | None = None) -> str:
    cid = secrets.token_urlsafe(24)
    now = util.now()
    t.execute(
        "INSERT INTO ceremonies (id, purpose, challenge, session_hash, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
        (cid, purpose, bytes_to_base64url(challenge), session_hash, now, now + config.CEREMONY_TTL_S),
    )
    return cid


def take_ceremony(t: Tx, cid: Any, purpose: str, session_hash: str | None = None) -> bytes | None:
    """Single use: the row is deleted whether or not it matches."""
    if not isinstance(cid, str) or not cid or len(cid) > 64:
        return None
    row = t.one("SELECT * FROM ceremonies WHERE id = ?", (cid,))
    if row is None:
        return None
    t.execute("DELETE FROM ceremonies WHERE id = ?", (cid,))
    if row["purpose"] != purpose or row["expires_at"] <= util.now():
        return None
    if session_hash is not None and row["session_hash"] != session_hash:
        return None
    return base64url_to_bytes(row["challenge"])


def _transports(raw: Any) -> list[AuthenticatorTransport]:
    out = []
    for tr in raw if isinstance(raw, list) else []:
        try:
            out.append(AuthenticatorTransport(tr))
        except ValueError:
            continue
    return out


def descriptors(t: Tx) -> list[PublicKeyCredentialDescriptor]:
    rows = t.all("SELECT id, transports FROM credentials")
    return [
        PublicKeyCredentialDescriptor(id=base64url_to_bytes(r["id"]), transports=_transports(util.loads(r["transports"], [])))
        for r in rows
    ]


def registration_options(t: Tx, purpose: str, session_hash: str | None = None) -> dict[str, Any]:
    party = rp()
    opts = generate_registration_options(
        rp_id=party.rp_id,
        rp_name=party.rp_name,
        user_name="owner",
        user_display_name="Bansho owner",
        user_id=owner_handle(t, create=True),
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=descriptors(t),
        timeout=120_000,
    )
    cid = new_ceremony(t, purpose, opts.challenge, session_hash)
    return {"ceremony_id": cid, "options": json.loads(options_to_json(opts))}


def store_credential(t: Tx, credential: Any, challenge: bytes, name: str) -> str:
    party = rp()
    verified = verify_registration_response(
        credential=credential,
        expected_challenge=challenge,
        expected_rp_id=party.rp_id,
        expected_origin=party.origin,
        require_user_verification=True,
    )
    cred_id = bytes_to_base64url(verified.credential_id)
    transports = []
    if isinstance(credential, dict) and isinstance(credential.get("response"), dict):
        transports = [str(x)[:16] for x in credential["response"].get("transports") or [] if isinstance(x, str)][:6]
    clean_name = (name or "Passkey").strip()[:40] or "Passkey"
    t.execute(
        "INSERT INTO credentials (id, public_key, sign_count, name, transports, aaguid, backed_up, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (cred_id, bytes_to_base64url(verified.credential_public_key), verified.sign_count, clean_name,
         util.dumps(transports), str(verified.aaguid), int(verified.credential_backed_up), util.now()),
    )
    return cred_id


def verify_assertion(t: Tx, credential: Any, challenge: bytes) -> tuple[str | None, str]:
    """Returns (credential_id, outcome). outcome == 'ok' on success."""
    if not isinstance(credential, dict):
        return None, "malformed"
    cred_id = credential.get("id")
    if not isinstance(cred_id, str) or len(cred_id) > 1400:
        return None, "malformed"
    row = t.one("SELECT * FROM credentials WHERE id = ?", (cred_id,))
    if row is None:
        return None, "unknown_credential"
    resp = credential.get("response")
    if isinstance(resp, dict) and resp.get("userHandle"):
        try:
            if base64url_to_bytes(resp["userHandle"]) != owner_handle(t):
                return cred_id, "wrong_user_handle"
        except Exception:  # noqa: BLE001
            return cred_id, "malformed"
    party = rp()
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=party.rp_id,
            expected_origin=party.origin,
            credential_public_key=base64url_to_bytes(row["public_key"]),
            credential_current_sign_count=int(row["sign_count"]),
            require_user_verification=True,
        )
    except WebAuthnException as exc:
        msg = str(exc).lower()
        if "sign count" in msg:
            return cred_id, "sign_count_regression"
        if "origin" in msg:
            return cred_id, "wrong_origin"
        if "rp id" in msg:
            return cred_id, "wrong_rp_id"
        if "challenge" in msg:
            return cred_id, "wrong_challenge"
        return cred_id, "invalid_assertion"
    except Exception:  # noqa: BLE001 - malformed input must never 500
        return cred_id, "malformed"
    t.execute(
        "UPDATE credentials SET sign_count = ?, last_used_at = ?, backed_up = ? WHERE id = ?",
        (verified.new_sign_count, util.now(), int(verified.credential_backed_up), cred_id),
    )
    return cred_id, "ok"


def normalize_code(code: Any) -> str:
    if not isinstance(code, str):
        return ""
    return "".join(ch for ch in code.lower() if ch.isalnum())[:64]


def new_recovery_codes(t: Tx) -> list[str]:
    t.execute("DELETE FROM recovery_codes")
    codes = []
    now = util.now()
    for _ in range(config.RECOVERY_CODE_COUNT):
        raw = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(20))
        codes.append("-".join(raw[i:i + 5] for i in range(0, 20, 5)))
        t.execute("INSERT INTO recovery_codes (code_hash, created_at) VALUES (?, ?)", (util.sha256_hex(raw), now))
    return codes


def recovery_remaining(t: Tx) -> int:
    return int(t.scalar("SELECT COUNT(*) FROM recovery_codes WHERE used_at IS NULL") or 0)


def with_cookies(content: dict[str, Any], src: JSONResponse) -> JSONResponse:
    """A fresh JSON response carrying the Set-Cookie headers set on ``src``."""
    out = JSONResponse(content)
    for k, v in src.raw_headers:
        if k == b"set-cookie":
            out.raw_headers.append((k, v))
    return out


def finish(err: ApiError | None, response: JSONResponse) -> JSONResponse:
    if err:
        raise err
    return response


# --- session ---------------------------------------------------------------------

@router.get("/api/session")
def get_session(request: Request) -> JSONResponse:
    with get_db().tx() as t:
        s = sessions.load(t, request)
        if s is None:
            return JSONResponse({"authenticated": False})
        return JSONResponse({"authenticated": True, "session": s.public(), "passkeys": passkey_count(t)})


@router.post("/api/auth/logout")
def logout(request: Request) -> JSONResponse:
    resp = JSONResponse({"ok": True})
    with get_db().tx() as t:
        s = guards.require_session(t, request, allow_recovery=True)
        sessions.revoke(t, s.id_hash)
        audit.record(t, request, actor="owner", action="logout", outcome="ok")
    sessions.clear_cookie(resp)
    return resp


# --- first-time setup ------------------------------------------------------------

def _setup_token_ok(supplied: Any) -> bool:
    expected = config.setup_token()
    return expected is not None and isinstance(supplied, str) and util.const_eq(supplied, expected)


@router.post("/api/auth/setup/options")
async def setup_options(request: Request) -> JSONResponse:
    guards.require_anon_csrf(request)
    data = await body_json(request)
    err: ApiError | None = None
    out: dict[str, Any] = {}
    with get_db().tx() as t:
        if passkey_count(t) > 0 or config.setup_token() is None:
            raise guards.not_found()
        err = guards.auth_gate(t, request, "setup")
        if err is None:
            if not _setup_token_ok(data.get("setup_token")):
                guards.auth_failure(t, request, "setup")
                audit.record(t, request, actor="anon", action="setup.options", outcome="bad_token")
                err = guards.not_found()
            else:
                out = registration_options(t, "setup")
    return finish(err, JSONResponse(out))


@router.post("/api/auth/setup/verify")
async def setup_verify(request: Request) -> JSONResponse:
    guards.require_anon_csrf(request)
    data = await body_json(request)
    resp = JSONResponse({})
    err: ApiError | None = None
    with get_db().tx() as t:
        if passkey_count(t) > 0 or config.setup_token() is None:
            raise guards.not_found()
        err = guards.auth_gate(t, request, "setup")
        if err is None and not _setup_token_ok(data.get("setup_token")):
            guards.auth_failure(t, request, "setup")
            audit.record(t, request, actor="anon", action="setup.verify", outcome="bad_token")
            err = guards.not_found()
        if err is None:
            challenge = take_ceremony(t, data.get("ceremony_id"), "setup")
            cred_id = None
            if challenge is not None:
                try:
                    cred_id = store_credential(t, data.get("credential"), challenge, str(data.get("name") or "First passkey"))
                except Exception as exc:  # noqa: BLE001
                    audit.record(t, request, actor="anon", action="setup.verify", outcome="invalid_attestation",
                                 detail={"reason": type(exc).__name__})
            if cred_id is None:
                guards.auth_failure(t, request, "setup")
                if challenge is None:
                    audit.record(t, request, actor="anon", action="setup.verify", outcome="bad_ceremony")
                err = ApiError(400, "registration_failed", "Passkey registration failed. Start again.")
            elif not t.insert_ignore("INSERT INTO meta (key, value) VALUES ('setup_done', ?) ON CONFLICT (key) DO NOTHING", (str(util.now()),)):
                raise ApiError(409, "already_set_up", "Setup already completed")
            else:
                codes = new_recovery_codes(t)
                s = sessions.create(t, request, resp, kind="full", credential_id=cred_id)
                guards.auth_success(t, request)
                audit.record(t, request, actor="owner", action="setup.complete", outcome="ok", target=cred_id[:16])
                resp = with_cookies({"ok": True, "recovery_codes": codes, "session": s.public()}, resp)
    return finish(err, resp)


# --- login -------------------------------------------------------------------------

@router.post("/api/auth/login/options")
async def login_options(request: Request) -> JSONResponse:
    guards.require_anon_csrf(request)
    await body_json(request)
    err: ApiError | None = None
    out: dict[str, Any] = {}
    with get_db().tx() as t:
        err = guards.auth_gate(t, request, "login")
        if err is None:
            party = rp()
            # Empty allowCredentials: the passkey is discoverable, and the
            # endpoint reveals nothing about which credentials exist.
            opts = generate_authentication_options(
                rp_id=party.rp_id,
                user_verification=UserVerificationRequirement.REQUIRED,
                timeout=120_000,
            )
            out = {"ceremony_id": new_ceremony(t, "login", opts.challenge), "options": json.loads(options_to_json(opts))}
    return finish(err, JSONResponse(out))


@router.post("/api/auth/login/verify")
async def login_verify(request: Request) -> JSONResponse:
    guards.require_anon_csrf(request)
    data = await body_json(request)
    resp = JSONResponse({})
    err: ApiError | None = None
    with get_db().tx() as t:
        err = guards.auth_gate(t, request, "login")
        if err is None:
            challenge = take_ceremony(t, data.get("ceremony_id"), "login")
            if challenge is None:
                cred_id, outcome = None, "bad_ceremony"
            else:
                cred_id, outcome = verify_assertion(t, data.get("credential"), challenge)
            if outcome != "ok" or cred_id is None:
                guards.auth_failure(t, request, "login")
                audit.record(t, request, actor="anon", action="login", outcome=outcome, target=(cred_id or "")[:16])
                err = ApiError(401, "login_failed", "That passkey was not accepted.")
            else:
                s = sessions.create(t, request, resp, kind="full", credential_id=cred_id)
                guards.auth_success(t, request)
                audit.record(t, request, actor="owner", action="login", outcome="ok", target=cred_id[:16])
                resp = with_cookies({"ok": True, "session": s.public()}, resp)
    return finish(err, resp)


# --- recovery ------------------------------------------------------------------------

@router.post("/api/auth/recovery")
async def recovery_login(request: Request) -> JSONResponse:
    guards.require_anon_csrf(request)
    data = await body_json(request, limit=1024)
    resp = JSONResponse({})
    err: ApiError | None = None
    with get_db().tx() as t:
        err = guards.auth_gate(t, request, "recovery")
        if err is None:
            code = normalize_code(data.get("code"))
            used = False
            if len(code) == 20:
                cur = t.execute(
                    "UPDATE recovery_codes SET used_at = ? WHERE code_hash = ? AND used_at IS NULL",
                    (util.now(), util.sha256_hex(code)),
                )
                used = (cur.rowcount or 0) == 1
            if not used:
                guards.auth_failure(t, request, "recovery")
                audit.record(t, request, actor="anon", action="recovery", outcome="bad_code")
                err = ApiError(401, "recovery_failed", "That recovery code was not accepted.")
            else:
                s = sessions.create(t, request, resp, kind="recovery", credential_id=None)
                guards.auth_success(t, request)
                audit.record(t, request, actor="recovery", action="recovery", outcome="ok",
                             detail={"codes_left": recovery_remaining(t)})
                resp = with_cookies({"ok": True, "session": s.public()}, resp)
    return finish(err, resp)


# --- step-up re-authentication ----------------------------------------------------------

@router.post("/api/auth/reauth/options")
async def reauth_options(request: Request) -> JSONResponse:
    await body_json(request)
    err: ApiError | None = None
    out: dict[str, Any] = {}
    with get_db().tx() as t:
        s = guards.require_session(t, request)
        err = guards.auth_gate(t, request, "reauth")
        if err is None:
            party = rp()
            opts = generate_authentication_options(
                rp_id=party.rp_id,
                allow_credentials=descriptors(t),
                user_verification=UserVerificationRequirement.REQUIRED,
                timeout=120_000,
            )
            out = {"ceremony_id": new_ceremony(t, "reauth", opts.challenge, s.id_hash),
                   "options": json.loads(options_to_json(opts))}
    return finish(err, JSONResponse(out))


@router.post("/api/auth/reauth/verify")
async def reauth_verify(request: Request) -> JSONResponse:
    data = await body_json(request)
    err: ApiError | None = None
    out: dict[str, Any] = {}
    with get_db().tx() as t:
        s = guards.require_session(t, request)
        err = guards.auth_gate(t, request, "reauth")
        if err is None:
            challenge = take_ceremony(t, data.get("ceremony_id"), "reauth", s.id_hash)
            if challenge is None:
                cred_id, outcome = None, "bad_ceremony"
            else:
                cred_id, outcome = verify_assertion(t, data.get("credential"), challenge)
            if outcome != "ok":
                guards.auth_failure(t, request, "reauth")
                audit.record(t, request, actor="owner", action="step_up", outcome=outcome)
                err = ApiError(401, "step_up_failed", "That passkey was not accepted.")
            else:
                sessions.mark_reauth(t, s)
                audit.record(t, request, actor="owner", action="step_up", outcome="ok", target=(cred_id or "")[:16])
                out = {"ok": True, "session": s.public()}
    return finish(err, JSONResponse(out))
