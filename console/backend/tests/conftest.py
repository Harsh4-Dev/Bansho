from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402

from bansho import upstream, util  # noqa: E402
from bansho.app import create_app  # noqa: E402
from bansho.db import Database, set_db  # noqa: E402
from softauthn import SoftAuthenticator  # noqa: E402

ORIGIN = "https://bansho.test"
RP_ID = "bansho.test"
SITE = "https://site.test"
SETUP_TOKEN = "setup-" + "S" * 40
CRON = "cron-" + "C" * 40
HH_TOKEN = "hh-ops-" + "H" * 60
BB_TOKEN = "bb-ops-" + "B" * 60

ENV = {
    "BANSHO_RP_ID": RP_ID,
    "BANSHO_ORIGIN": ORIGIN,
    "BANSHO_SETUP_TOKEN": SETUP_TOKEN,
    "CRON_SECRET": CRON,
    "HARAHACHI_URL": "https://harahachi.test",
    "HARAHACHI_OPS_TOKEN": HH_TOKEN,
    "BUSBOARD_URL": "https://busboard.test",
    "BUSBOARD_OPS_TOKEN": BB_TOKEN,
    "BANSHO_SITE_ORIGIN": SITE,
    "BANSHO_TZ": "Asia/Kolkata",
}


class FakeApps:
    """In-process stand-ins for Harahachi's and Bus Board's ops APIs."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.bb: dict[str, Any] = bb_stats("boot-a", 600, 100)
        self.bb_down = False
        self.hh_down = False
        self.echo_auth = False  # a hostile upstream that echoes the bearer token
        self.handlers: dict[str, Callable[[httpx.Request], httpx.Response]] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        host, path = request.url.host, request.url.path
        token = request.headers.get("authorization", "")
        if self.echo_auth:
            return httpx.Response(500, json={"error": {"message": f"bad token {token}"}, "token": token})
        if host == "harahachi.test":
            if self.hh_down:
                raise httpx.ConnectError("boom " + token)
            if path == "/api/v1/health":
                return httpx.Response(200, json={"status": "ok"})
            if token != f"Bearer {HH_TOKEN}":
                return httpx.Response(404)
            if path in self.handlers:
                return self.handlers[path](request)
            if path == "/api/ops/v1/summary":
                return httpx.Response(200, json={"generated_at": "x", "version": "1", "totals": {"users": 3},
                                                 "today": {"requests": 100, "errors_5xx": 1, "ai_calls": 20, "ai_errors": 1,
                                                           "cap_hits": 0, "active_users": 2, "meals": 5},
                                                 "ai_by_slot": {"analyze": {"calls": 20, "errors": 1, "tokens_in": 100, "tokens_out": 20}},
                                                 "clients": {"apk": 1, "pwa": 1, "web": 1}, "health": {"db_ok": True}})
            if path.startswith("/api/ops/v1/"):
                if request.method == "POST":
                    return httpx.Response(200, json={"ok": True})
                return httpx.Response(200, json={"rows": [], "users": [], "total": 0, "days": [], "series": {}})
            return httpx.Response(404)
        if host == "busboard.test":
            if self.bb_down:
                raise httpx.ReadTimeout("timeout " + token)
            if path == "/v1/health":
                return httpx.Response(200, json={"status": "ok"})
            if token != f"Bearer {BB_TOKEN}":
                return httpx.Response(404)
            if path == "/v1/ops/stats":
                return httpx.Response(200, json=self.bb)
            if request.method == "POST":
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(404)
        if host == "site.test":
            return httpx.Response(200, text="<!doctype html>")
        return httpx.Response(404)


def bb_stats(boot_id: str, uptime_s: int, n: int, *, received: int = 50, opened: int = 10) -> dict[str, Any]:
    return {
        "version": "t", "boot_id": boot_id, "started_at": util.utc_iso(util.now() - uptime_s), "uptime_s": uptime_s,
        "requests": {"by_route": {
            "GET /v1/board": {"count": n, "errors_4xx": n // 10, "errors_5xx": n // 50, "p50_ms": 5, "p95_ms": 40},
            "GET /v1/board/stream": {"count": n // 2, "errors_4xx": 0, "errors_5xx": 0, "p50_ms": 0, "p95_ms": 0},
        }},
        "sse": {"open": 2, "total_opened": opened},
        "board_queries": {"top": [{"from": "SASTRA", "to": "Thanjavur NB", "count": 9}]},
        "pings": {"received": received, "accepted": received - 10, "rejected": {"coverage": 6, "blocked": 4}},
        "contributors": {"count": 2, "trust_histogram": {"0.0-0.2": 1}, "top": [{"id_hash": "abc123", "score": 0.5, "accepted": 3}]},
        "vehicles": {"active": 1, "by_route": {"r": 1}},
        "feed": {"stops": 31, "unverified_stops": 31, "routes": 3, "version": "v"},
        "runtime": {"goroutines": 10, "heap_mb": 8.5, "go_version": "go1.23"},
    }


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    for k in ("VERCEL", "VERCEL_ENV", "BANSHO_DEV_SEED", "DATABASE_URL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    return ENV


@pytest.fixture
def fake_apps(env: dict[str, str]):
    apps = FakeApps()
    upstream.transport = httpx.MockTransport(apps)
    upstream.clear_cache()
    yield apps
    upstream.transport = None
    upstream.clear_cache()


TABLES = ["meta", "credentials", "ceremonies", "sessions", "recovery_codes", "rate_limits", "lockouts", "daily_salts",
          "audit", "collect_runs", "snapshots", "bb_deltas", "bb_boots", "uptime", "events", "site_events"]


@pytest.fixture
def db(env: dict[str, str]):
    # BANSHO_TEST_DATABASE_URL=postgresql://... runs the whole suite on Postgres.
    pg_url = os.environ.get("BANSHO_TEST_DATABASE_URL", "")
    if pg_url:
        d = Database(pg_url)
        with d.tx() as t:
            for name in TABLES:
                t.execute(f"DROP TABLE IF EXISTS {name} CASCADE")
    else:
        d = Database(":memory:")
    set_db(d)
    util.now_override = None
    yield d
    set_db(None)
    util.now_override = None


class Console:
    """A browser-like client: same-origin headers, cookie jar, CSRF."""

    def __init__(self, client: TestClient):
        self.c = client
        self.csrf: str | None = None
        self.auth = SoftAuthenticator()
        self.recovery_codes: list[str] = []

    def headers(self, csrf: str | None = None, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin", "X-Bansho-CSRF": csrf or self.csrf or "1"}
        h.update(extra or {})
        return h

    def get(self, path: str, **kw: Any) -> httpx.Response:
        return self.c.get(path, **kw)

    def post(self, path: str, json: Any = None, headers: dict[str, str] | None = None, **kw: Any) -> httpx.Response:
        return self.c.post(path, json=json if json is not None else {}, headers=headers or self.headers(), **kw)

    def refresh_csrf(self) -> None:
        r = self.get("/api/session")
        if r.json().get("authenticated"):
            self.csrf = r.json()["session"]["csrf"]

    # -- ceremonies ---------------------------------------------------------
    def setup(self, token: str = SETUP_TOKEN) -> httpx.Response:
        r = self.post("/api/auth/setup/options", {"setup_token": token})
        assert r.status_code == 200, r.text
        o = r.json()
        cred = self.auth.create(o["options"])
        r = self.post("/api/auth/setup/verify", {"setup_token": token, "ceremony_id": o["ceremony_id"], "credential": cred, "name": "Phone"})
        assert r.status_code == 200, r.text
        self.recovery_codes = r.json()["recovery_codes"]
        self.csrf = r.json()["session"]["csrf"]
        return r

    def login(self, authenticator: SoftAuthenticator | None = None, **get_kw: Any) -> httpx.Response:
        a = authenticator or self.auth
        r = self.post("/api/auth/login/options")
        assert r.status_code == 200, r.text
        o = r.json()
        r = self.post("/api/auth/login/verify", {"ceremony_id": o["ceremony_id"], "credential": a.get(o["options"], **get_kw)})
        if r.status_code == 200:
            self.csrf = r.json()["session"]["csrf"]
        return r

    def step_up(self, authenticator: SoftAuthenticator | None = None) -> httpx.Response:
        a = authenticator or self.auth
        r = self.post("/api/auth/reauth/options")
        assert r.status_code == 200, r.text
        o = r.json()
        return self.post("/api/auth/reauth/verify", {"ceremony_id": o["ceremony_id"], "credential": a.get(o["options"])})

    def add_passkey(self, authenticator: SoftAuthenticator) -> httpx.Response:
        r = self.post("/api/security/passkeys/options")
        if r.status_code != 200:
            return r
        o = r.json()
        authenticator.user_handle = None
        return self.post("/api/security/passkeys/verify",
                         {"ceremony_id": o["ceremony_id"], "credential": authenticator.create(o["options"]), "name": "Key 2"})


def make_client() -> TestClient:
    return TestClient(create_app(), base_url=ORIGIN)


@pytest.fixture
def console(db: Database, fake_apps: FakeApps) -> Console:
    return Console(make_client())


@pytest.fixture
def owner(console: Console) -> Console:
    """A console that has completed first-time setup (and is signed in)."""
    console.setup()
    return console
