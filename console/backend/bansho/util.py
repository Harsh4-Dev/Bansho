"""Small shared helpers: clock, request metadata, daily-salted hashing, UA."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from starlette.requests import Request

from . import config
from .db import Tx

# Tests move the clock by assigning util.now_override.
now_override: float | None = None


def now() -> int:
    return int(now_override if now_override is not None else time.time())


def tz() -> ZoneInfo:
    try:
        return ZoneInfo(config.timezone_name())
    except Exception:  # noqa: BLE001 - bad tz name falls back, never crashes
        return ZoneInfo("UTC")


def day_of(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz()).strftime("%Y-%m-%d")


def today() -> str:
    return day_of(now())


def day_start(ts: int) -> int:
    d = datetime.fromtimestamp(ts, tz()).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(d.timestamp())


def utc_iso(ts: int | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True, default=str)


def loads(s: str | None, default: Any = None) -> Any:
    if not s:
        return default
    try:
        return json.loads(s)
    except ValueError:
        return default


def sha256_hex(s: str | bytes) -> str:
    if isinstance(s, str):
        s = s.encode()
    return hashlib.sha256(s).hexdigest()


def const_eq(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return False
    return hmac.compare_digest(a.encode(), b.encode())


# --- request metadata --------------------------------------------------------

def client_ip(request: Request) -> str:
    """Best-effort client IP. Only ever hashed, never stored or logged.

    On Vercel the edge overwrites x-real-ip / x-forwarded-for, so they are
    trustworthy there; elsewhere (dev, tests) the socket peer is used.
    """
    if config.on_vercel():
        for h in ("x-real-ip", "x-vercel-forwarded-for", "x-forwarded-for"):
            v = request.headers.get(h, "")
            if v:
                return v.split(",")[0].strip()[:64]
    if request.client:
        return request.client.host
    return "unknown"


def country(request: Request) -> str:
    c = request.headers.get("x-vercel-ip-country", "").strip().upper()
    return c if re.fullmatch(r"[A-Z]{2}", c) else ""


def user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "")[:300]


def daily_salt(t: Tx, day: str | None = None) -> str:
    """A random salt per day; older salts are deleted, so hashes from a past
    day can never be recomputed or linked to a new day's hashes."""
    day = day or today()
    row = t.one("SELECT salt FROM daily_salts WHERE day = ?", (day,))
    if row:
        return row["salt"]
    salt = secrets.token_hex(32)
    t.execute("INSERT INTO daily_salts (day, salt) VALUES (?, ?) ON CONFLICT (day) DO NOTHING", (day, salt))
    t.execute("DELETE FROM daily_salts WHERE day < ?", (day_of(now() - 2 * 86400),))
    row = t.one("SELECT salt FROM daily_salts WHERE day = ?", (day,))
    return row["salt"] if row else salt


def ip_hash(t: Tx, request: Request) -> str:
    return hmac.new(daily_salt(t).encode(), client_ip(request).encode(), hashlib.sha256).hexdigest()[:24]


def visitor_hash(t: Tx, request: Request) -> str:
    material = f"{client_ip(request)}|{user_agent(request)}"
    return hmac.new(daily_salt(t).encode(), material.encode(), hashlib.sha256).hexdigest()[:20]


# --- user agent -------------------------------------------------------------

_BOT_RE = re.compile(
    r"bot|crawl|spider|slurp|bingpreview|facebookexternalhit|embedly|quora link preview|"
    r"headless|phantomjs|selenium|puppeteer|playwright|python-requests|python-httpx|aiohttp|"
    r"curl/|wget/|go-http-client|okhttp|java/|libwww|httpclient|scrapy|node-fetch|axios|"
    r"lighthouse|pagespeed|gtmetrix|uptime|monitor|preview",
    re.I,
)


def is_bot(ua: str) -> bool:
    return not ua.strip() or bool(_BOT_RE.search(ua))


def device_class(ua: str) -> str:
    u = ua.lower()
    if "ipad" in u or "tablet" in u or ("android" in u and "mobile" not in u):
        return "tablet"
    if "mobi" in u or "iphone" in u or "android" in u:
        return "mobile"
    return "desktop"


def short_ua(ua: str) -> str:
    """A readable label ('Chrome on Android') for the sessions list."""
    u = ua.lower()
    browser = (
        "Edge" if "edg/" in u else
        "Firefox" if "firefox" in u else
        "Chrome" if "chrome" in u or "crios" in u else
        "Safari" if "safari" in u else
        "Unknown browser"
    )
    os_ = (
        "Android" if "android" in u else
        "iOS" if "iphone" in u or "ipad" in u else
        "Windows" if "windows" in u else
        "macOS" if "mac os" in u or "macintosh" in u else
        "Linux" if "linux" in u else
        "unknown OS"
    )
    return f"{browser} on {os_}"
