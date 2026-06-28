"""Database operations for signal run persistence."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from signals_app.db.models import SignalRun
from signals_app.db.session import get_session

logger = logging.getLogger(__name__)


@dataclass
class RunRecord:
    """A single persisted signal run returned from the DB.

    Matches the shape of the frontend HistoryEntry Dexie schema so the
    /history endpoint can be consumed directly by the web client.
    """

    id: int
    ticker: str
    period: str
    resolved_period: str
    direction: str | None
    confidence: float | None
    ai_degraded: bool
    no_llm: bool
    prompt_version: str | None
    ts: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ticker": self.ticker,
            "period": self.period,
            "resolvedPeriod": self.resolved_period,
            "signal": self.direction,
            "confidence": self.confidence,
            "aiDegraded": self.ai_degraded,
            "noLlm": self.no_llm,
            "promptVersion": self.prompt_version,
            "ts": self.ts,
        }


async def record_run(
    ticker: str,
    period: str,
    resolved_period: str,
    direction: str | None,
    confidence: float | None,
    ai_degraded: bool,
    no_llm: bool,
    prompt_version: str | None = None,
) -> int:
    """Persist a signal run and return its auto-incremented id.

    Args:
        ticker: Stock ticker symbol.
        period: UI period string (e.g. "3mo").
        resolved_period: Backend period after fallback mapping.
        direction: Signal direction value, or None if the run failed.
        confidence: Confidence 0–1, or None.
        ai_degraded: True when the AI fell back to rule-based.
        no_llm: True when LLM synthesis was intentionally skipped.
        prompt_version: Prompt version tag from the signal.

    Returns:
        Auto-incremented row id.
    """
    async with get_session() as session:
        run = SignalRun(
            ticker=ticker.upper(),
            period=period,
            resolved_period=resolved_period,
            direction=direction,
            confidence=confidence,
            ai_degraded=ai_degraded,
            no_llm=no_llm,
            prompt_version=prompt_version,
            ts=int(time.time() * 1000),
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        logger.info("db: recorded run id=%s ticker=%s direction=%s", run.id, ticker, direction)
        return run.id  # type: ignore[return-value]


async def get_ticker_history(
    ticker: str,
    limit: int = 50,
    offset: int = 0,
) -> list[RunRecord]:
    """Return recent runs for a ticker, newest first.

    Args:
        ticker: Stock ticker symbol.
        limit: Maximum rows to return.
        offset: Pagination offset.

    Returns:
        List of RunRecord in descending timestamp order.
    """
    async with get_session() as session:
        stmt = (
            select(SignalRun)
            .where(SignalRun.ticker == ticker.upper())
            .order_by(SignalRun.ts.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()

    return [
        RunRecord(
            id=r.id,
            ticker=r.ticker,
            period=r.period,
            resolved_period=r.resolved_period,
            direction=r.direction,
            confidence=r.confidence,
            ai_degraded=r.ai_degraded,
            no_llm=r.no_llm,
            prompt_version=r.prompt_version,
            ts=r.ts,
        )
        for r in rows
    ]
