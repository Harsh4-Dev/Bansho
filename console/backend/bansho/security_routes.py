"""Security screen: passkeys, sessions, recovery codes (+ the dev-only login)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import audit, auth, config, guards, sessions, util
from .db import get_db
from .guards import ApiError

router = APIRouter()


@router.get("/api/security/passkeys")
def list_passkeys(request: Request) -> JSONResponse:
    with get_db().tx() as t:
        guards.require_session(t, request)
        rows = t.all("SELECT * FROM credentials ORDER BY created_at, id")
        return JSONResponse({
            "max": config.MAX_PASSKEYS,
            "passkeys": [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "created_at": util.utc_iso(r["created_at"]),
                    "last_used_at": util.utc_iso(r["last_used_at"]),
                    "backed_up": bool(r["backed_up"]),
                    "transports": util.loads(r["transports"], []),
                }
                for r in rows
            ],
        })


@router.post("/api/security/passkeys/options")
async def add_passkey_options(request: Request) -> JSONResponse:
    await auth.body_json(request)
    with get_db().tx() as t:
        s = guards.require_session(t, request, allow_recovery=True)
        if s.kind == "full":
            # Adding a credential is how a stolen session would make itself
            # permanent, so it needs a fresh passkey proof like a deletion.
            guards.require_step_up(s)
        if auth.passkey_count(t) >= config.MAX_PASSKEYS:
            raise ApiError(409, "passkey_limit", f"At most {config.MAX_PASSKEYS} passkeys. Remove one first.")
        return JSONResponse(auth.registration_options(t, "add_passkey", s.id_hash))


@router.post("/api/security/passkeys/verify")
async def add_passkey_verify(request: Request) -> JSONResponse:
    data = await auth.body_json(request)
    resp = JSONResponse({})
    err: ApiError | None = None
    with get_db().tx() as t:
        s = guards.require_session(t, request, allow_recovery=True)
        if s.kind == "full":
            guards.require_step_up(s)
        if auth.passkey_count(t) >= config.MAX_PASSKEYS:
            raise ApiError(409, "passkey_limit", f"At most {config.MAX_PASSKEYS} passkeys.")
        challenge = auth.take_ceremony(t, data.get("ceremony_id"), "add_passkey", s.id_hash)
        cred_id = None
        if challenge is not None:
            try:
                cred_id = auth.store_credential(t, data.get("credential"), challenge, str(data.get("name") or "Passkey"))
            except Exception as exc:  # noqa: BLE001
                audit.record(t, request, actor=s.kind == "full" and "owner" or "recovery", action="passkey.add",
                             outcome="invalid_attestation", detail={"reason": type(exc).__name__})
        if cred_id is None:
            err = ApiError(400, "registration_failed", "Passkey registration failed. Start again.")
        else:
            audit.record(t, request, actor="owner" if s.kind == "full" else "recovery", action="passkey.add",
                         outcome="ok", target=cred_id[:16])
            if s.kind == "recovery":
                # The recovery session has done the one thing it may do.
                sessions.revoke(t, s.id_hash)
                resp = JSONResponse({"ok": True, "next": "login"})
                sessions.clear_cookie(resp)
            else:
                resp = JSONResponse({"ok": True})
    if err:
        raise err
    return resp


@router.post("/api/security/passkeys/{cred_id}/rename")
async def rename_passkey(cred_id: str, request: Request) -> JSONResponse:
    data = await auth.body_json(request, limit=1024)
    name = str(data.get("name") or "").strip()[:40]
    if not name:
        raise ApiError(400, "bad_name", "Name required")
    with get_db().tx() as t:
        guards.require_session(t, request)
        cur = t.execute("UPDATE credentials SET name = ? WHERE id = ?", (name, cred_id))
        if (cur.rowcount or 0) != 1:
            raise guards.not_found()
        audit.record(t, request, actor="owner", action="passkey.rename", outcome="ok", target=cred_id[:16])
    return JSONResponse({"ok": True})


@router.post("/api/security/passkeys/{cred_id}/delete")
async def delete_passkey(cred_id: str, request: Request) -> JSONResponse:
    await auth.body_json(request, limit=1024)
    with get_db().tx() as t:
        s = guards.require_session(t, request)
        guards.require_step_up(s)
        if t.one("SELECT id FROM credentials WHERE id = ?", (cred_id,)) is None:
            raise guards.not_found()
        if auth.passkey_count(t) <= 1:
            raise ApiError(409, "last_passkey", "You cannot remove your only passkey.")
        t.execute("DELETE FROM credentials WHERE id = ?", (cred_id,))
        # Sessions opened with that passkey end with it.
        t.execute("UPDATE sessions SET revoked_at = ? WHERE credential_id = ? AND revoked_at IS NULL AND id_hash <> ?",
                  (util.now(), cred_id, s.id_hash))
        audit.record(t, request, actor="owner", action="passkey.remove", outcome="ok", target=cred_id[:16])
    return JSONResponse({"ok": True})


@router.get("/api/security/sessions")
def list_sessions(request: Request) -> JSONResponse:
    with get_db().tx() as t:
        s = guards.require_session(t, request)
        return JSONResponse({"sessions": sessions.list_active(t, s)})


@router.post("/api/security/sessions/{public_id}/revoke")
async def revoke_session(public_id: str, request: Request) -> JSONResponse:
    await auth.body_json(request, limit=1024)
    resp = JSONResponse({"ok": True})
    with get_db().tx() as t:
        s = guards.require_session(t, request)
        row = t.one("SELECT id_hash FROM sessions WHERE public_id = ? AND revoked_at IS NULL", (public_id,))
        if row is None:
            raise guards.not_found()
        sessions.revoke(t, row["id_hash"])
        audit.record(t, request, actor="owner", action="session.revoke", outcome="ok", target=public_id)
        if row["id_hash"] == s.id_hash:
            sessions.clear_cookie(resp)
    return resp


@router.post("/api/security/sessions/revoke-others")
async def revoke_others(request: Request) -> JSONResponse:
    await auth.body_json(request, limit=1024)
    with get_db().tx() as t:
        s = guards.require_session(t, request)
        cur = t.execute("UPDATE sessions SET revoked_at = ? WHERE revoked_at IS NULL AND id_hash <> ?", (util.now(), s.id_hash))
        n = cur.rowcount or 0
        audit.record(t, request, actor="owner", action="session.revoke_others", outcome="ok", detail={"count": n})
    return JSONResponse({"ok": True, "revoked": n})


@router.get("/api/security/recovery")
def recovery_status(request: Request) -> JSONResponse:
    with get_db().tx() as t:
        guards.require_session(t, request)
        return JSONResponse({"remaining": auth.recovery_remaining(t), "total": config.RECOVERY_CODE_COUNT})


@router.post("/api/security/recovery/regenerate")
async def recovery_regenerate(request: Request) -> JSONResponse:
    await auth.body_json(request, limit=1024)
    with get_db().tx() as t:
        s = guards.require_session(t, request)
        guards.require_step_up(s)
        codes = auth.new_recovery_codes(t)
        audit.record(t, request, actor="owner", action="recovery.regenerate", outcome="ok")
    return JSONResponse({"ok": True, "recovery_codes": codes})


# --- dev only --------------------------------------------------------------------------

@router.post("/api/dev/login")
async def dev_login(request: Request) -> JSONResponse:
    """Passkey-free login for local screen checks. 404 unless config.dev_mode()."""
    if not config.dev_mode():
        raise guards.not_found()
    guards.require_anon_csrf(request)
    resp = JSONResponse({})
    with get_db().tx() as t:
        s = sessions.create(t, request, resp, kind="full", credential_id=None)
        sessions.mark_reauth(t, s)
        audit.record(t, request, actor="owner", action="dev.login", outcome="ok")
        out: dict[str, Any] = {"ok": True, "session": s.public()}
    return auth.with_cookies(out, resp)
