"""Run the console API locally.

    python backend/dev_server.py          # SEED/dev mode: fake apps + seeded data
    python backend/dev_server.py --real   # your own env vars, real upstreams

Seed mode sets BANSHO_DEV_SEED=1 with a localhost origin, which is the only
combination config.dev_mode() accepts (and never on Vercel). The Vite dev
server (npm run dev, port 5173) proxies /api here (port 8787).
"""
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def main() -> None:
    real = "--real" in sys.argv
    if os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"):
        raise SystemExit("dev_server.py is for local use only")
    if not real:
        defaults = {
            "BANSHO_DEV_SEED": "1",
            "BANSHO_ORIGIN": "http://localhost:5173",
            "BANSHO_RP_ID": "localhost",
            "BANSHO_SITE_ORIGIN": "http://localhost:5174",
            "HARAHACHI_URL": "https://harahachi.example.invalid",
            "BUSBOARD_URL": "https://busboard.example.invalid",
            "HARAHACHI_OPS_TOKEN": secrets.token_urlsafe(48),
            "BUSBOARD_OPS_TOKEN": secrets.token_urlsafe(48),
            "CRON_SECRET": secrets.token_urlsafe(32),
            "BANSHO_SETUP_TOKEN": secrets.token_urlsafe(32),
            "BANSHO_SQLITE_PATH": str(HERE / "bansho-dev.db"),
        }
        for k, v in defaults.items():
            os.environ.setdefault(k, v)
        print("[bansho] SEED/dev mode: fake Harahachi + Bus Board, seeded history, dev sign-in enabled")
    import uvicorn

    from bansho.app import app, warm_up

    warm_up()
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8787")), log_level="info")


if __name__ == "__main__":
    main()
