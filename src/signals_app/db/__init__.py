"""SQL database layer for signal run persistence.

Local mode: SQLite via aiosqlite (zero-config, file-based).
Cloud mode: Postgres (Neon or any asyncpg-compatible URL via DATABASE_URL).

Usage:
    from signals_app.db import init_db, record_run, get_ticker_history
"""
from signals_app.db.ops import get_ticker_history, record_run
from signals_app.db.session import init_db

__all__ = ["init_db", "record_run", "get_ticker_history"]
