"""FastAPI application entry point.

Configures logging, lifespan, and registers the routes.
Reads SIGNALS_ENV to configure JSON logging for Cloud Run.
"""
from __future__ import annotations

import logging
import os
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from signals_app.api import v1
from signals_app.api.routes import router
from signals_app.config import LOG_LEVEL, SIGNALS_APP_CODE_VERSION, SIGNALS_ENV
from signals_app.service import SignalsError


def _configure_logging() -> None:
    """Configure logging based on deployment environment.

    Cloud mode: JSON format for Cloud Run log ingestion.
    Local mode: Human-readable format with timestamps.
    """
    level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)

    if SIGNALS_ENV == "cloud":
        fmt = '{"time": "%(asctime)s", "level": "%(levelname)s", "name": "%(name)s", "msg": "%(message)s"}'
    else:
        fmt = "%(asctime)s %(levelname)-8s %(name)s — %(message)s"

    logging.basicConfig(
        level=level,
        format=fmt,
        stream=sys.stdout,
        force=True,
    )


_configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Log startup, validate settings, initialise the database; dispose on shutdown."""
    from signals_app.config import get_settings
    from signals_app.db.session import init_db
    settings = get_settings()
    errors = settings.validate()

    logger.info(
        "signals-app starting env=%s llm_enabled=%s llm_provider=%s",
        settings.env,
        settings.llm_enabled,
        settings.llm_provider,
    )
    if errors:
        for err in errors:
            logger.warning("Config warning: %s", err)

    await init_db()

    yield

    from signals_app.db import session as db_session
    if db_session._engine is not None:
        await db_session._engine.dispose()
        logger.info("db: engine disposed")


app = FastAPI(
    title="Signals App",
    description="Financial signal detection + LLM synthesis",
    version="0.2.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Signals-Code-Version"],
)

_REQUEST_ID_MAX_LEN = 128


@app.middleware("http")
async def _request_id(request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
    """Echo (or mint) X-Request-ID so a portal log line can be joined to ours."""
    supplied = request.headers.get("x-request-id", "")[:_REQUEST_ID_MAX_LEN]
    request_id = supplied or uuid.uuid4().hex
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Signals-Code-Version"] = SIGNALS_APP_CODE_VERSION
    return response


def _error_body(error_type: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"type": error_type, "message": message}}


def _is_v1(request: Request) -> bool:
    return request.url.path.startswith("/v1")


@app.exception_handler(SignalsError)
async def _signals_error(_: Request, exc: SignalsError) -> JSONResponse:
    """Only /v1 lets domain errors escape; legacy routes translate them first."""
    return JSONResponse(
        status_code=v1.status_for(exc), content=_error_body(type(exc).__name__, str(exc))
    )


@app.exception_handler(v1.ApiError)
async def _api_error(_: Request, exc: v1.ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content=_error_body(exc.error_type, exc.message))


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError) -> Response:
    """/v1 gets the uniform {"error": ...} body; legacy keeps FastAPI's default."""
    if not _is_v1(request):
        return await request_validation_exception_handler(request, exc)
    return JSONResponse(status_code=422, content=_error_body("ValidationError", str(exc)))


@app.exception_handler(StarletteHTTPException)
async def _http_exception(request: Request, exc: StarletteHTTPException) -> Response:
    """/v1 gets the uniform {"error": ...} body (e.g. 404 on an unknown /v1 route)."""
    if not _is_v1(request):
        return await http_exception_handler(request, exc)
    return JSONResponse(
        status_code=exc.status_code, content=_error_body("HTTPException", str(exc.detail))
    )


@app.exception_handler(Exception)
async def _unhandled_exception(request: Request, exc: Exception) -> Response:
    """/v1 never leaks a stack trace; legacy routes fall through to the default 500."""
    if not _is_v1(request):
        raise exc
    logger.exception("unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500, content=_error_body("InternalError", "an internal error occurred")
    )


app.include_router(router)
app.include_router(v1.public)
app.include_router(v1.protected)


def cli_entry() -> None:
    """Entry point for uvicorn via pyproject.toml scripts."""
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("signals_app.api.main:app", host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    cli_entry()
