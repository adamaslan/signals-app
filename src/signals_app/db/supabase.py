"""Supabase writer — persists engine output that the SQLite `signal_runs`
table currently discards (evidence, counter-evidence, confluence score,
detector hits).

Writes only. Reads happen directly from the browser via supabase-js against
the anon key and RLS policies (see docs/backend-state-and-supabase-plan.md
Part 2) — this module exists for scripts/scan_universe.py and the GitHub
Actions workflows, which hold the service-role key and bypass RLS.

A thin REST client over httpx rather than the supabase-py SDK: httpx is
already a dependency, and PostgREST's insert/upsert surface is small enough
that a dedicated SDK isn't worth the extra dependency for this write-only
use case.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import httpx

from signals_app.config import (
    SIGNALS_APP_CODE_VERSION,
    SUPABASE_REQUEST_TIMEOUT_SECONDS,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_URL,
)
from signals_app.detection.base import MutableSignal
from signals_app.scoring.confluence import ConfluenceResult

logger = logging.getLogger(__name__)


class SupabaseConfigError(RuntimeError):
    """Raised when SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set."""


@dataclass(frozen=True)
class EngineRun:
    """One row for `engine_runs` — created at the start of a scan, updated at the end."""

    id: int
    started_at: str


@dataclass(frozen=True)
class SignalRecord:
    """A single publishable signal — see the `signals` table schema in
    supabase/migrations/20260819000000_initial_schema.sql.
    """

    ticker: str
    period: str
    bar_ts: str
    direction: str | None
    confidence: float | None
    confluence_score: float
    bias: str
    bull_count: int
    bear_count: int
    total_signals: int
    data_quality_score: float | None
    data_quality_reasons: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    counter_evidence: list[dict[str, Any]] = field(default_factory=list)
    matrix: dict[str, Any] | None = None
    ai_degraded: bool = False
    no_llm: bool = False
    prompt_version: str | None = None
    llm_model: str | None = None


class SignalWriter(Protocol):
    """Injected by scripts/scan_universe.py — Supabase in prod, an in-memory
    fake in tests, matching the DI pattern used throughout this codebase.
    """

    def ensure_symbol(self, ticker: str) -> None: ...

    def start_run(self, trigger: str, git_sha: str) -> EngineRun: ...

    def finish_run(
        self, run: EngineRun, symbols_total: int, symbols_ok: int, symbols_failed: int,
        llm_provider: str | None, status: str, error: str | None = None,
    ) -> None: ...

    def write_signal(self, run: EngineRun, record: SignalRecord) -> None: ...

    def write_detector_hits(
        self, ticker: str, bar_ts: str, signals: list[MutableSignal],
    ) -> None: ...


class SupabaseWriter:
    """SignalWriter backed by a live Supabase project via PostgREST."""

    def __init__(self, url: str | None = None, service_role_key: str | None = None) -> None:
        self._url = (url or SUPABASE_URL or "").rstrip("/")
        self._key = service_role_key or SUPABASE_SERVICE_ROLE_KEY
        if not self._url or not self._key:
            raise SupabaseConfigError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must both be set"
            )
        self._client = httpx.Client(
            base_url=f"{self._url}/rest/v1",
            headers={
                "apikey": self._key,
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
            timeout=SUPABASE_REQUEST_TIMEOUT_SECONDS,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SupabaseWriter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def ensure_symbol(self, ticker: str) -> None:
        """Upsert a bare `symbols` row so a scanned ticker outside the seeded
        universe still satisfies the FK on `signals`/`detector_hits`.

        Idempotent — safe to call once per scan_one_symbol() invocation.
        Leaves name/asset_type/priority alone on conflict (on_conflict target
        only) so a prior richer row from seed/universe_symbols.csv isn't
        clobbered by a bare upsert.
        """
        resp = self._client.post(
            "/symbols?on_conflict=ticker",
            headers={"Prefer": "resolution=ignore-duplicates,return=minimal"},
            json={"ticker": ticker},
        )
        resp.raise_for_status()

    def start_run(self, trigger: str, git_sha: str) -> EngineRun:
        """Insert an `engine_runs` row with status='running' and return its id."""
        started_at = datetime.utcnow().isoformat() + "Z"
        resp = self._client.post(
            "/engine_runs",
            json={
                "started_at": started_at,
                "trigger": trigger,
                "code_version": SIGNALS_APP_CODE_VERSION,
                "git_sha": git_sha,
                "status": "running",
            },
        )
        resp.raise_for_status()
        row = resp.json()[0]
        run = EngineRun(id=row["id"], started_at=started_at)
        logger.info("supabase: started run id=%s trigger=%s", run.id, trigger)
        return run

    def finish_run(
        self,
        run: EngineRun,
        symbols_total: int,
        symbols_ok: int,
        symbols_failed: int,
        llm_provider: str | None,
        status: str,
        error: str | None = None,
    ) -> None:
        """Update the `engine_runs` row with final counts and status."""
        resp = self._client.patch(
            f"/engine_runs?id=eq.{run.id}",
            json={
                "finished_at": datetime.utcnow().isoformat() + "Z",
                "symbols_total": symbols_total,
                "symbols_ok": symbols_ok,
                "symbols_failed": symbols_failed,
                "llm_provider": llm_provider,
                "status": status,
                "error": error,
            },
        )
        resp.raise_for_status()
        logger.info(
            "supabase: finished run id=%s status=%s ok=%d failed=%d",
            run.id, status, symbols_ok, symbols_failed,
        )

    def write_signal(self, run: EngineRun, record: SignalRecord) -> None:
        """Upsert a `signals` row. Idempotent on (ticker, period, bar_ts, code_version)."""
        resp = self._client.post(
            "/signals?on_conflict=ticker,period,bar_ts,code_version",
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
            json={
                "run_id": run.id,
                "ticker": record.ticker,
                "period": record.period,
                "bar_ts": record.bar_ts,
                "direction": record.direction,
                "confidence": record.confidence,
                "confluence_score": record.confluence_score,
                "bias": record.bias,
                "bull_count": record.bull_count,
                "bear_count": record.bear_count,
                "total_signals": record.total_signals,
                "data_quality_score": record.data_quality_score,
                "data_quality_reasons": record.data_quality_reasons,
                "evidence": record.evidence,
                "counter_evidence": record.counter_evidence,
                "matrix": record.matrix,
                "ai_degraded": record.ai_degraded,
                "no_llm": record.no_llm,
                "prompt_version": record.prompt_version,
                "llm_model": record.llm_model,
                "code_version": SIGNALS_APP_CODE_VERSION,
            },
        )
        resp.raise_for_status()

    def write_detector_hits(
        self, ticker: str, bar_ts: str, signals: list[MutableSignal],
    ) -> None:
        """Batch-upsert raw detector output for calibration mining.

        Idempotent on (ticker, bar_ts, detector, description, code_version).
        No-op on an empty list — avoids a wasted round-trip.
        """
        if not signals:
            return
        rows = [
            {
                "ticker": ticker,
                "bar_ts": bar_ts,
                "detector": s.signal,
                "category": s.category,
                "strength": s.strength,
                "description": s.description,
                "code_version": SIGNALS_APP_CODE_VERSION,
            }
            for s in signals
        ]
        resp = self._client.post(
            "/detector_hits?on_conflict=ticker,bar_ts,detector,description,code_version",
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            json=rows,
        )
        resp.raise_for_status()


def confluence_result_to_signal_record(
    ticker: str,
    period: str,
    bar_ts: str,
    confluence: ConfluenceResult,
    data_quality_score: float | None,
    data_quality_reasons: list[str],
    direction: str | None = None,
    confidence: float | None = None,
    evidence: list[dict[str, Any]] | None = None,
    counter_evidence: list[dict[str, Any]] | None = None,
    matrix: dict[str, Any] | None = None,
    ai_degraded: bool = False,
    no_llm: bool = False,
    prompt_version: str | None = None,
    llm_model: str | None = None,
) -> SignalRecord:
    """Build a SignalRecord from a ConfluenceResult plus optional synthesis output.

    Split out from SupabaseWriter so scan_universe.py's publication-gate logic
    can build the record before deciding whether to call the writer at all.
    """
    return SignalRecord(
        ticker=ticker,
        period=period,
        bar_ts=bar_ts,
        direction=direction,
        confidence=confidence,
        confluence_score=confluence.score,
        bias=confluence.bias,
        bull_count=confluence.bull_count,
        bear_count=confluence.bear_count,
        total_signals=confluence.total_signals,
        data_quality_score=data_quality_score,
        data_quality_reasons=data_quality_reasons,
        evidence=evidence or [],
        counter_evidence=counter_evidence or [],
        matrix=matrix,
        ai_degraded=ai_degraded,
        no_llm=no_llm,
        prompt_version=prompt_version,
        llm_model=llm_model,
    )
