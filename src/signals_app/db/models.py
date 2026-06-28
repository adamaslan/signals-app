"""SQLAlchemy ORM models for signal run persistence."""
from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Float, Index, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SignalRun(Base):
    """One persisted analysis run — mirrors the frontend HistoryEntry Dexie schema."""

    __tablename__ = "signal_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    period: Mapped[str] = mapped_column(String(8), nullable=False)
    resolved_period: Mapped[str] = mapped_column(String(8), nullable=False)
    direction: Mapped[str | None] = mapped_column(String(16), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    no_llm: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ts: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Composite index: all history queries filter by ticker then sort by ts desc
    __table_args__ = (Index("ix_signal_runs_ticker_ts", "ticker", "ts"),)
