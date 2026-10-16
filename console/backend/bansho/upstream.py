"""Server-to-server calls to the two apps' ops APIs.

The ops tokens are read from the environment here and placed in exactly one
place: the Authorization header of an outgoing request. They are never
returned, logged, or put into an exception message - every error path below
produces a fixed vocabulary of ``error`` codes instead of the exception text.

GET results are cached in-process for CACHE_TTL_S (a warm serverless instance
reuses them; a cold one simply refetches).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from . import config

CACHE_TTL_S = 45
TIMEOUT_S = 8.0
WAKE_TIMEOUT_S = 50.0

HARAHACHI_OPS = "/api/ops/v1"
BUSBOARD_OPS = "/v1/ops"
HARAHACHI_HEALTH = "/api/v1/health"
BUSBOARD_HEALTH = "/v1/health"


@dataclass
class Result:
    ok: bool
    status: int = 0
    data: Any = None
    # not_configured | unauthorized | timeout | unreachable | http_error | bad_json
    error: str | None = None
    latency_ms: int = 0
    fetched_at: float = field(default_factory=time.time)
    cached: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "error": self.error,
            "latency_ms": self.latency_ms,
            "fetched_at": int(self.fetched_at),
            "cached": self.cached,
        }


_cache: dict[str, Result] = {}
_cache_lock = threading.Lock()

# Tests and dev mode can replace the transport (an httpx.MockTransport or the
# in-process fake apps) - production never sets this.
transport: httpx.BaseTransport | None = None


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _client(timeout: float) -> httpx.Client:
    t = transport
    if t is None and config.dev_mode():
        from . import devseed

        t = devseed.fake_transport()
    return httpx.Client(timeout=timeout, transport=t, follow_redirects=False,
                        headers={"user-agent": "bansho-console/1"})


def _request(method: str, up: config.Upstream, path: str, *, params: dict[str, Any] | None = None,
             json_body: Any = None, auth: bool = True, timeout: float = TIMEOUT_S,
             parse_json: bool = True) -> Result:
    if not up.base_url or (auth and not up.token):
        return Result(False, error="not_configured")
    headers = {"accept": "application/json"}
    if auth:
        headers["authorization"] = f"Bearer {up.token}"
    started = time.perf_counter()
    try:
        with _client(timeout) as c:
            r = c.request(method, up.base_url + path, params=params, json=json_body, headers=headers)
    except httpx.TimeoutException:
        return Result(False, error="timeout", latency_ms=int((time.perf_counter() - started) * 1000))
    except httpx.HTTPError:
        return Result(False, error="unreachable", latency_ms=int((time.perf_counter() - started) * 1000))
    ms = int((time.perf_counter() - started) * 1000)
    if r.status_code == 404 and auth:
        # The ops APIs answer 404 for a missing/short/wrong token by design.
        return Result(False, status=404, error="unauthorized", latency_ms=ms)
    if r.status_code >= 400:
        data = None
        try:
            data = r.json()
        except ValueError:
            pass
        # Only pass through a short app error message, never raw bodies.
        msg = None
        if isinstance(data, dict):
            e = data.get("error")
            msg = e.get("message") if isinstance(e, dict) else e if isinstance(e, str) else data.get("detail")
        return Result(False, status=r.status_code, error="http_error",
                      data={"message": config.redact(str(msg))[:200]} if msg else None, latency_ms=ms)
    if not parse_json:
        return Result(True, status=r.status_code, latency_ms=ms)
    try:
        data = r.json()
    except ValueError:
        return Result(False, status=r.status_code, error="bad_json", latency_ms=ms)
    return Result(True, status=r.status_code, data=data, latency_ms=ms)


def cached_get(up: config.Upstream, path: str, params: dict[str, Any] | None = None, *, fresh: bool = False) -> Result:
    key = f"{up.name}:{path}:{sorted((params or {}).items())}"
    now = time.time()
    if not fresh:
        with _cache_lock:
            hit = _cache.get(key)
            if hit and now - hit.fetched_at < CACHE_TTL_S:
                return Result(hit.ok, hit.status, hit.data, hit.error, hit.latency_ms, hit.fetched_at, True)
    res = _request("GET", up, path, params=params)
    if res.ok or res.error in ("timeout", "unreachable", "unauthorized"):
        with _cache_lock:
            _cache[key] = res
            if len(_cache) > 300:
                for k in sorted(_cache, key=lambda k: _cache[k].fetched_at)[:100]:
                    _cache.pop(k, None)
    return res


def invalidate(prefix: str) -> None:
    with _cache_lock:
        for k in [k for k in _cache if k.startswith(prefix)]:
            _cache.pop(k, None)


# --- Harahachi -------------------------------------------------------------------

def hh_get(path: str, params: dict[str, Any] | None = None, fresh: bool = False) -> Result:
    return cached_get(config.harahachi(), HARAHACHI_OPS + path, params, fresh=fresh)


def hh_action(body: dict[str, Any]) -> Result:
    res = _request("POST", config.harahachi(), HARAHACHI_OPS + "/actions", json_body=body)
    invalidate("harahachi:")
    return res


# --- Bus Board -------------------------------------------------------------------

def bb_stats(fresh: bool = False) -> Result:
    return cached_get(config.busboard(), BUSBOARD_OPS + "/stats", fresh=fresh)


def bb_post(path: str) -> Result:
    res = _request("POST", config.busboard(), BUSBOARD_OPS + path)
    invalidate("busboard:")
    return res


# --- health (no token) -------------------------------------------------------------

def health(target: str, timeout: float = TIMEOUT_S) -> Result:
    if target == "harahachi":
        up = config.harahachi()
        return _request("GET", config.Upstream("h", up.base_url, None), HARAHACHI_HEALTH, auth=False, timeout=timeout, parse_json=False)
    if target == "busboard":
        up = config.busboard()
        return _request("GET", config.Upstream("b", up.base_url, None), BUSBOARD_HEALTH, auth=False, timeout=timeout, parse_json=False)
    if target == "landing":
        origin = config.site_origin()
        return _request("GET", config.Upstream("l", origin, None), "/", auth=False, timeout=timeout, parse_json=False) if origin else Result(False, error="not_configured")
    raise ValueError(target)


def classify(target: str, res: Result) -> str:
    """up | asleep | misconfigured | down | not_configured."""
    if res.ok:
        return "up"
    if res.error == "not_configured":
        return "not_configured"
    if res.error == "unauthorized":
        return "misconfigured"
    if target == "busboard" and (res.error in ("timeout", "unreachable") or res.status in (502, 503, 504)):
        # Render's free tier sleeps after 15 idle minutes; that is normal.
        return "asleep"
    return "down"
