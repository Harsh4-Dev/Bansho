"""GET /api/cron/collect and the Bus Board delta math."""
from __future__ import annotations

from conftest import CRON, Console, FakeApps, bb_stats

from bansho import util
from bansho.collector import bb_delta
from bansho.db import get_db


def cron(console: Console, auth: str | None = f"Bearer {CRON}"):
    headers = {"authorization": auth} if auth is not None else {}
    return console.c.get("/api/cron/collect", headers=headers)


def at_bucket(n: int) -> None:
    base = 1_800_000_000 - 1_800_000_000 % 300
    util.now_override = base + n * 300 + 7


def test_cron_requires_secret(console: Console, monkeypatch) -> None:
    assert cron(console, None).status_code == 401
    assert cron(console, "Bearer wrong").status_code == 401
    assert cron(console, f"Bearer {CRON}x").status_code == 401
    assert cron(console, CRON).status_code == 401
    assert cron(console, f"bearer {CRON}").status_code == 401
    monkeypatch.setenv("CRON_SECRET", "short")
    assert cron(console, "Bearer short").status_code == 401  # too short = disabled
    with get_db().tx() as t:
        assert t.scalar("SELECT COUNT(*) FROM snapshots") == 0


def test_cron_idempotent_per_bucket(console: Console, fake_apps: FakeApps) -> None:
    at_bucket(0)
    r = cron(console)
    assert r.status_code == 200 and r.json()["status"] == "collected"
    calls = len(fake_apps.calls)
    r = cron(console)
    assert r.json()["status"] == "already_collected"
    util.now_override += 200  # same 5-minute bucket still
    assert cron(console).json()["status"] == "already_collected"
    assert len(fake_apps.calls) == calls  # no upstream traffic for a duplicate
    at_bucket(1)
    assert cron(console).json()["status"] == "collected"
    with get_db().tx() as t:
        assert t.scalar("SELECT COUNT(*) FROM snapshots WHERE app = 'harahachi'") == 2
        assert t.scalar("SELECT COUNT(*) FROM uptime") == 6  # 3 targets x 2 buckets
        assert t.scalar("SELECT COUNT(*) FROM bb_deltas") == 2


def test_delta_same_boot() -> None:
    a = bb_stats("boot-a", 600, 100, received=50, opened=10)
    b = bb_stats("boot-a", 1500, 160, received=80, opened=14)
    d = bb_delta(a, b, 1000, 1900)
    assert not d["restart"] and not d["counter_reset"]
    assert d["routes"]["GET /v1/board"]["count"] == 60
    assert d["routes"]["GET /v1/board/stream"]["count"] == 30
    assert d["totals"]["requests"] == 90
    assert d["sse_opened"] == 4
    assert d["pings"]["received"] == 30 and d["pings"]["accepted"] == 30
    assert d["interval_s"] == 900


def test_delta_across_boot_change() -> None:
    old = bb_stats("boot-a", 50_000, 9_000, received=5000, opened=700)
    new = bb_stats("boot-b", 120, 40, received=12, opened=3)
    d = bb_delta(old, new, 1000, 1900)
    # New process: its since-boot counters ARE the interval's traffic.
    assert d["restart"] and d["boot_id"] == "boot-b"
    assert d["routes"]["GET /v1/board"]["count"] == 40
    assert d["totals"]["requests"] == 60
    assert d["pings"]["received"] == 12 and d["sse_opened"] == 3
    assert all(v >= 0 for r in d["routes"].values() for v in r.values())


def test_delta_counter_reset_same_boot_never_negative() -> None:
    a = bb_stats("boot-a", 600, 100)
    b = bb_stats("boot-a", 300, 20)  # uptime went backwards with same id
    d = bb_delta(a, b, 1000, 1900)
    assert d["counter_reset"] and not d["restart"]
    assert d["routes"]["GET /v1/board"]["count"] == 20
    assert all(v >= 0 for r in d["routes"].values() for v in r.values())


def test_delta_new_route_and_baseline() -> None:
    a = bb_stats("boot-a", 600, 100)
    b = bb_stats("boot-a", 900, 110)
    b["requests"]["by_route"]["POST /v1/contribute"] = {"count": 5, "errors_4xx": 1, "errors_5xx": 0, "p50_ms": 9, "p95_ms": 20}
    d = bb_delta(a, b, 1, 2)
    assert d["routes"]["POST /v1/contribute"]["count"] == 5
    first = bb_delta(None, a, None, 2)
    assert first["baseline"] and not first["restart"]


def test_collect_records_restart_and_wake(console: Console, fake_apps: FakeApps) -> None:
    at_bucket(0)
    fake_apps.bb = bb_stats("boot-a", 600, 100)
    cron(console)
    at_bucket(3)
    fake_apps.bb = bb_stats("boot-a", 1500, 150)
    assert cron(console).json()["busboard"] == "ok"
    # Quick restart: new boot started right after the last sighting.
    at_bucket(6)
    fake_apps.bb = bb_stats("boot-b", 120, 10)
    assert cron(console).json()["busboard"] == "restart"
    # Long silence (asleep), then a wake.
    at_bucket(7)
    fake_apps.bb_down = True
    assert cron(console).json()["busboard"] == "timeout"
    at_bucket(20)
    fake_apps.bb_down = False
    fake_apps.bb = bb_stats("boot-c", 30, 2)
    assert cron(console).json()["busboard"] == "wake"
    with get_db().tx() as t:
        events = [e["kind"] for e in t.all("SELECT kind FROM events ORDER BY id")]
        deltas = [util.loads(r["payload"]) for r in t.all("SELECT payload FROM bb_deltas ORDER BY bucket")]
        states = [r["state"] for r in t.all("SELECT state FROM uptime WHERE target = 'busboard' ORDER BY bucket")]
        boots = t.scalar("SELECT COUNT(*) FROM bb_boots")
    assert events == ["restart", "wake"]
    assert [d["totals"]["requests"] for d in deltas] == [150, 75, 15, 3]
    assert "asleep" in states
    assert boots == 3


def test_collect_survives_everything_down(console: Console, fake_apps: FakeApps) -> None:
    fake_apps.bb_down = True
    fake_apps.hh_down = True
    at_bucket(0)
    r = cron(console)
    assert r.status_code == 200
    assert r.json()["harahachi"] == "unreachable" and r.json()["busboard"] == "timeout"


def test_delta_counter_drop_with_growing_uptime_never_negative() -> None:
    a = bb_stats("boot-a", 600, 100)
    b = bb_stats("boot-a", 900, 100)
    b["requests"]["by_route"]["GET /v1/board"]["count"] = 40  # only this counter fell
    d = bb_delta(a, b, 1000, 1900)
    assert d["counter_reset"]
    assert d["routes"]["GET /v1/board"]["count"] == 40
    assert all(v >= 0 for r in d["routes"].values() for v in r.values())
