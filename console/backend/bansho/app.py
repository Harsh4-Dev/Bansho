"""The Bansho console API (FastAPI), served as one Vercel Python function."""
from __future__ import annotations

import sys
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import auth, collector, config, data_routes, ingest, security_routes
from .db import get_db
from .guards import ApiError, check_origin

SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cache-Control": "no-store",
}

MAX_REQUEST_BYTES = 64 * 1024


def _error(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": config.redact(message)}}, status_code=status, headers=headers)


def create_app() -> FastAPI:
    app = FastAPI(title="Bansho", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def guard_and_headers(request: Request, call_next: Any) -> Response:
        path = request.url.path
        try:
            cl = request.headers.get("content-length")
            if cl is not None and int(cl) > MAX_REQUEST_BYTES:
                response: Response = _error(413, "too_large", "Request body too large")
            elif path.startswith("/api/") and not (path == "/api/ingest" or path.startswith("/api/cron/")):
                check_origin(request)
                response = await call_next(request)
            else:
                response = await call_next(request)
        except ApiError as exc:
            response = _error(exc.status, exc.code, exc.message, exc.headers)
        except ValueError:
            response = _error(400, "bad_request", "Bad request")
        except Exception as exc:  # noqa: BLE001 - keep headers on 500s too
            print(f"[bansho] unhandled {type(exc).__name__}", file=sys.stderr)
            response = _error(500, "internal", "Internal error")
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if path != "/api/ingest":
            response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        return response

    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _error(exc.status, exc.code, exc.message, exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return _error(404, "not_found", "Not found")
        if exc.status_code == 405:
            return _error(405, "method_not_allowed", "Method not allowed")
        return _error(exc.status_code, "http_error", "Request failed")

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
        return _error(400, "bad_request", "Bad request")

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        # Type name only: exception text could carry an upstream URL or value.
        print(f"[bansho] unhandled {type(exc).__name__}", file=sys.stderr)
        return _error(500, "internal", "Internal error")

    @app.get("/robots.txt")
    def robots() -> PlainTextResponse:
        return PlainTextResponse("User-agent: *\nDisallow: /\n")

    @app.get("/api/health")
    def health() -> JSONResponse:
        # Liveness only: says nothing about configuration or data.
        return JSONResponse({"ok": True})

    app.include_router(auth.router)
    app.include_router(security_routes.router)
    app.include_router(ingest.router)
    app.include_router(collector.router)
    app.include_router(data_routes.router)
    return app


app = create_app()


def warm_up() -> None:
    db = get_db()
    if config.dev_mode():
        from . import devseed

        devseed.seed_if_empty(db)
