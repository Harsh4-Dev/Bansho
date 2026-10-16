"""Vercel Python function entrypoint for the Bansho console API.

vercel.json rewrites /api/* and /robots.txt here; the ASGI app sees the
original request path, so FastAPI routing is unchanged.

Local run (from console/):  python backend/dev_server.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from bansho.app import app, warm_up  # noqa: E402

try:
    # Opens the database and runs idempotent migrations on cold start. A
    # missing DATABASE_URL must not crash the import: requests then fail
    # with a clean 500 instead of a broken function.
    warm_up()
except Exception as exc:  # noqa: BLE001
    print(f"[bansho] warm-up failed: {type(exc).__name__}", file=sys.stderr)

__all__ = ["app"]
