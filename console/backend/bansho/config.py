"""Configuration, read from the environment on every call.

Nothing here is cached at import time: the test-suite flips env vars between
tests, and on Vercel the environment is fixed for the life of the instance
anyway, so re-reading costs nothing that matters.

Secrets are validated for length and otherwise treated as opaque. No function
in this module ever returns a secret inside an error message.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

MIN_SETUP_TOKEN = 32
MIN_CRON_SECRET = 32
MIN_OPS_TOKEN = 48

SESSION_ABSOLUTE_S = 8 * 3600
SESSION_IDLE_S = 3600
RECOVERY_SESSION_S = 15 * 60
STEP_UP_WINDOW_S = 5 * 60
CEREMONY_TTL_S = 5 * 60
MAX_PASSKEYS = 5
RECOVERY_CODE_COUNT = 10

SESSION_COOKIE = "__Host-bansho_session"
CSRF_HEADER = "x-bansho-csrf"


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def on_vercel() -> bool:
    return bool(os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"))


def _is_local_host(host: str) -> bool:
    return host in ("localhost", "127.0.0.1")


@dataclass(frozen=True)
class RelyingParty:
    rp_id: str
    origin: str
    rp_name: str = "Bansho"


class ConfigError(Exception):
    """Raised with a message that names the variable, never its value."""


def relying_party() -> RelyingParty:
    """BANSHO_RP_ID / BANSHO_ORIGIN, validated strictly.

    * origin must be exactly ``scheme://host[:port]`` - no path, query,
      fragment, userinfo or trailing slash;
    * scheme must be https, except http on localhost outside Vercel (dev);
    * the RP ID must be exactly the origin's host (no parent-domain RP IDs:
      the console owns its own hostname and nothing else).
    """
    rp_id = env("BANSHO_RP_ID").lower()
    origin = env("BANSHO_ORIGIN")
    if not rp_id or not origin:
        raise ConfigError("BANSHO_RP_ID and BANSHO_ORIGIN must both be set")
    parts = urlsplit(origin)
    if parts.scheme not in ("https", "http"):
        raise ConfigError("BANSHO_ORIGIN must start with https://")
    if parts.path or parts.query or parts.fragment or parts.username or parts.password:
        raise ConfigError("BANSHO_ORIGIN must be just scheme://host (no path, no trailing slash)")
    host = (parts.hostname or "").lower()
    if not host:
        raise ConfigError("BANSHO_ORIGIN has no host")
    if parts.scheme == "http" and not (_is_local_host(host) and not on_vercel()):
        raise ConfigError("BANSHO_ORIGIN must use https:// (http is allowed only for localhost dev)")
    if host != rp_id:
        raise ConfigError("BANSHO_RP_ID must equal the host part of BANSHO_ORIGIN")
    canonical = f"{parts.scheme}://{parts.netloc.lower()}"
    if canonical != origin.lower() or origin != origin.strip():
        raise ConfigError("BANSHO_ORIGIN must be exactly scheme://host")
    return RelyingParty(rp_id=rp_id, origin=canonical)


def setup_token() -> str | None:
    tok = env("BANSHO_SETUP_TOKEN")
    return tok if len(tok) >= MIN_SETUP_TOKEN else None


def cron_secret() -> str | None:
    tok = env("CRON_SECRET")
    return tok if len(tok) >= MIN_CRON_SECRET else None


def site_origin() -> str | None:
    """The landing site's origin - the ONLY origin ingest accepts."""
    o = env("BANSHO_SITE_ORIGIN").rstrip("/")
    if not o:
        return None
    parts = urlsplit(o)
    if parts.scheme not in ("https", "http") or not parts.hostname or parts.path:
        return None
    if parts.scheme == "http" and not (_is_local_host(parts.hostname) and not on_vercel()):
        return None
    return f"{parts.scheme}://{parts.netloc.lower()}"


@dataclass(frozen=True)
class Upstream:
    name: str
    base_url: str | None
    token: str | None

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.token)


def _clean_base(url: str) -> str | None:
    url = url.strip().rstrip("/")
    if not url:
        return None
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return url
    if parts.scheme == "http" and parts.hostname and _is_local_host(parts.hostname) and not on_vercel():
        return url
    return None


def harahachi() -> Upstream:
    tok = env("HARAHACHI_OPS_TOKEN")
    return Upstream("harahachi", _clean_base(env("HARAHACHI_URL")), tok if len(tok) >= MIN_OPS_TOKEN else None)


def busboard() -> Upstream:
    tok = env("BUSBOARD_OPS_TOKEN")
    return Upstream("busboard", _clean_base(env("BUSBOARD_URL")), tok if len(tok) >= MIN_OPS_TOKEN else None)


def all_secret_values() -> list[str]:
    """Every secret this process knows, for redaction and for tests."""
    vals = []
    for name in ("HARAHACHI_OPS_TOKEN", "BUSBOARD_OPS_TOKEN", "CRON_SECRET", "BANSHO_SETUP_TOKEN"):
        v = env(name)
        if len(v) >= 8:
            vals.append(v)
    return vals


def redact(text: str) -> str:
    for v in all_secret_values():
        text = text.replace(v, "[redacted]")
    return text


def timezone_name() -> str:
    return env("BANSHO_TZ", "Asia/Kolkata") or "Asia/Kolkata"


def dev_mode() -> bool:
    """SEED/dev mode: fake upstreams, seeded DB, and a passkey-free dev login.

    It can never be on in production: it needs an explicit opt-in variable,
    refuses to run on Vercel at all (the platform always sets VERCEL=1), and
    requires a plain-http localhost origin, which relying_party() itself
    refuses on Vercel. Each condition alone is enough to keep it off.
    """
    if env("BANSHO_DEV_SEED") != "1":
        return False
    if on_vercel():
        return False
    origin = env("BANSHO_ORIGIN")
    return origin.startswith("http://localhost:") or origin.startswith("http://127.0.0.1:") or origin in (
        "http://localhost",
        "http://127.0.0.1",
    )
