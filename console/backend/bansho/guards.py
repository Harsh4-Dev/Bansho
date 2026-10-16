"""Errors and request guards shared by every route."""
from __future__ import annotations

from starlette.requests import Request

from . import config, ratelimit, sessions, util
from .db import Tx

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str = "", headers: dict[str, str] | None = None):
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message or code.replace("_", " ")
        self.headers = headers or {}


def not_found() -> ApiError:
    return ApiError(404, "not_found", "Not found")


def check_origin(request: Request) -> None:
    """Every state-changing console request must come from the console itself.

    Browsers always send Origin on cross-origin and on same-origin non-GET
    fetches; Sec-Fetch-Site is a second, independent signal. Either one
    pointing elsewhere is a rejection.
    """
    if request.method in SAFE_METHODS:
        return
    try:
        rp = config.relying_party()
    except config.ConfigError:
        raise ApiError(503, "misconfigured", "Console origin is not configured")
    origin = request.headers.get("origin")
    if origin is not None and origin != rp.origin:
        raise ApiError(403, "bad_origin", "Cross-origin request refused")
    sfs = request.headers.get("sec-fetch-site")
    if sfs is not None and sfs not in ("same-origin", "none"):
        raise ApiError(403, "bad_origin", "Cross-site request refused")


def require_anon_csrf(request: Request) -> None:
    """Pre-session endpoints: the custom header must be present.

    A cross-origin page cannot add a custom header without a CORS preflight,
    and the console answers no preflight, so its presence proves same-origin.
    """
    if not request.headers.get(config.CSRF_HEADER):
        raise ApiError(403, "csrf", "Missing CSRF header")


def require_session(t: Tx, request: Request, *, allow_recovery: bool = False) -> sessions.Session:
    # Auto-refresh polling marks itself as background so an unattended open
    # tab does not keep the session alive past the 1 h idle timeout.
    background = request.method in SAFE_METHODS and request.headers.get("x-bansho-background") == "1"
    s = sessions.load(t, request, touch=not background)
    if s is None:
        raise ApiError(401, "unauthenticated", "Sign in with your passkey")
    if request.method not in SAFE_METHODS:
        if not util.const_eq(request.headers.get(config.CSRF_HEADER), s.csrf):
            raise ApiError(403, "csrf", "CSRF token missing or wrong")
    if s.kind != "full" and not allow_recovery:
        raise ApiError(403, "recovery_scope", "A recovery session can only register a new passkey")
    return s


def require_step_up(s: sessions.Session) -> None:
    if not s.step_up_fresh:
        raise ApiError(403, "step_up_required", "Confirm with your passkey to continue")


# --- auth endpoint throttling -------------------------------------------------

AUTH_RATE_LIMIT = 20  # requests per window per hashed IP per scope
AUTH_RATE_WINDOW_S = 300
GLOBAL_FREE_FAILURES = 25
# Only secret-guessing endpoints get an all-IP counter. A passkey assertion
# cannot be guessed, so a global login lock would only let a stranger lock
# the owner out.
GLOBAL_SCOPES = ("setup", "recovery")


def auth_gate(t: Tx, request: Request, scope: str) -> ApiError | None:
    """Rate limit + lockout check for an auth endpoint. Returns an error to
    raise AFTER the transaction commits (so the counters persist)."""
    iph = util.ip_hash(t, request)
    v = ratelimit.hit(t, f"rl:{scope}:{iph}", AUTH_RATE_LIMIT, AUTH_RATE_WINDOW_S)
    if not v.allowed:
        return ApiError(429, "rate_limited", "Too many attempts. Wait and try again.", {"Retry-After": str(v.retry_after)})
    keys = [f"lock:auth:{iph}"]
    if scope in GLOBAL_SCOPES:
        keys.append(f"lock:{scope}:global")
    for key in keys:
        v = ratelimit.locked(t, key)
        if not v.allowed:
            return ApiError(429, "locked", "Too many failed attempts. Locked for now.", {"Retry-After": str(v.retry_after)})
    return None


def auth_failure(t: Tx, request: Request, scope: str) -> int:
    """Count a failure against the hashed IP and the scope-wide counter."""
    iph = util.ip_hash(t, request)
    lock_s = ratelimit.fail(t, f"lock:auth:{iph}")
    if scope in GLOBAL_SCOPES:
        lock_s = max(lock_s, ratelimit.fail_global(t, f"lock:{scope}:global", GLOBAL_FREE_FAILURES))
    return lock_s


def auth_success(t: Tx, request: Request) -> None:
    ratelimit.clear(t, f"lock:auth:{util.ip_hash(t, request)}")
