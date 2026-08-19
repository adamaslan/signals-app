"""FastAPI application entry point.

Configures logging, lifespan, and registers the routes.
Reads SIGNALS_ENV to configure JSON logging for Cloud Run.
"""
from __future__ import annotations

import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from signals_app.api.routes import router
from signals_app.config import LOG_LEVEL, SIGNALS_ENV


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
    version="0.1.0",
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
)

app.include_router(router)


def cli_entry() -> None:
    """Entry point for uvicorn via pyproject.toml scripts."""
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("signals_app.api.main:app", host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    cli_entry()
