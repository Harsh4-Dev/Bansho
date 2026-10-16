"""POST /api/ingest - the landing site's beacon. The only public write.

* CORS: exactly the site's origin (BANSHO_SITE_ORIGIN); any other or a
  missing Origin is refused. No credentials are ever allowed.
* Body <= 1 KB of JSON with exactly the keys {type, path, ref_host, target?}.
* Rate limited per daily-salted IP hash (and a global ceiling).
* Stored: time, type, path, referrer host, target, country (Vercel header),
  device class and bot flag (from the UA), and a per-day visitor hash that
  cannot be linked across days. No IP, no UA string, no cookies.
"""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, Request
from fastapi.responses import Response

from . import config, ratelimit, util
from .db import get_db

router = APIRouter()

MAX_BODY = 1024
PER_IP_LIMIT = 120
GLOBAL_LIMIT = 6000
WINDOW_S = 600

_PATH_RE = re.compile(r"^/[A-Za-z0-9/_\-.]{0,120}$")
_HOST_RE = re.compile(r"^[a-z0-9.\-]{1,100}$")
_TARGET_RE = re.compile(r"^[a-z0-9_:\-.]{1,64}$")
_ALLOWED_KEYS = {"type", "path", "ref_host", "target"}


def _cors_headers(origin: str) -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": origin,
        "Vary": "Origin",
        "Access-Control-Allow-Methods": "POST",
        "Access-Control-Allow-Headers": "content-type",
        "Access-Control-Max-Age": "600",
    }


def _deny(status: int) -> Response:
    return Response(status_code=status, headers={"Vary": "Origin"})


@router.options("/api/ingest")
def ingest_preflight(request: Request) -> Response:
    site = config.site_origin()
    origin = request.headers.get("origin")
    if not site or origin != site:
        return _deny(403)
    return Response(status_code=204, headers=_cors_headers(site))


@router.post("/api/ingest")
async def ingest(request: Request) -> Response:
    site = config.site_origin()
    origin = request.headers.get("origin")
    if not site or origin != site:
        return _deny(403)
    cors = _cors_headers(site)

    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > MAX_BODY:
                return Response(status_code=413, headers=cors)
        except ValueError:
            return Response(status_code=400, headers=cors)
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > MAX_BODY:
            return Response(status_code=413, headers=cors)

    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype not in ("text/plain", "application/json"):
        return Response(status_code=415, headers=cors)
    try:
        data = json.loads(raw)
    except ValueError:
        return Response(status_code=400, headers=cors)
    if not isinstance(data, dict) or not set(data) <= _ALLOWED_KEYS:
        return Response(status_code=400, headers=cors)

    etype = data.get("type")
    path = data.get("path")
    ref_host = data.get("ref_host") or ""
    target = data.get("target") or ""
    if etype not in ("pageview", "click") or not isinstance(path, str) or not _PATH_RE.match(path):
        return Response(status_code=400, headers=cors)
    if not isinstance(ref_host, str) or (ref_host and not _HOST_RE.match(ref_host.lower())):
        return Response(status_code=400, headers=cors)
    if not isinstance(target, str) or (target and not _TARGET_RE.match(target)):
        return Response(status_code=400, headers=cors)
    if etype == "click" and not target:
        return Response(status_code=400, headers=cors)
    ref_host = ref_host.lower()
    site_host = site.split("://", 1)[1].split(":")[0]
    if ref_host == site_host:
        ref_host = ""

    ua = util.user_agent(request)
    limited = False
    with get_db().tx() as t:
        iph = util.ip_hash(t, request)
        if not ratelimit.hit(t, f"rl:ingest:{iph}", PER_IP_LIMIT, WINDOW_S).allowed:
            limited = True
        elif not ratelimit.hit(t, "rl:ingest:global", GLOBAL_LIMIT, WINDOW_S).allowed:
            limited = True
        else:
            now = util.now()
            t.execute(
                "INSERT INTO site_events (at, day, type, path, ref_host, target, country, device_class, is_bot, visitor_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (now, util.day_of(now), etype, path, ref_host, target if etype == "click" else "",
                 util.country(request), util.device_class(ua), int(util.is_bot(ua)), util.visitor_hash(t, request)),
            )
    if limited:
        return Response(status_code=429, headers={**cors, "Retry-After": "60"})
    return Response(status_code=204, headers=cors)
