"""Supabase persistence for the learned scorer (plan P7): model artifacts and the
live-vs-backtest IC history used by the drift alert.

Writes only; the read used by the live scan is ``scoring.model.load_scorer_from_supabase``.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from signals_app.config import (
    SUPABASE_REQUEST_TIMEOUT_SECONDS,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_URL,
)
from signals_app.db.supabase import SupabaseConfigError
from signals_app.scoring.model import LogisticScorer

logger = logging.getLogger(__name__)


class ScorerStore:
    """PostgREST client for ``scorer_models`` and ``scorer_ic_history``."""

    def __init__(self, url: str | None = None, service_role_key: str | None = None) -> None:
        self._url = (url or SUPABASE_URL or "").rstrip("/")
        self._key = service_role_key or SUPABASE_SERVICE_ROLE_KEY
        if not self._url or not self._key:
            raise SupabaseConfigError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must both be set")
        self._client = httpx.Client(
            base_url=f"{self._url}/rest/v1",
            headers={
                "apikey": self._key,
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
            },
            timeout=SUPABASE_REQUEST_TIMEOUT_SECONDS,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ScorerStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def publish_model(self, scorer: LogisticScorer) -> None:
        """Insert the artifact inactive, then make it the one active model.

        Two sequential calls, not a transaction: a reader between them sees no
        active model and falls back to the legacy scorer, never to a wrong one.
        """
        resp = self._client.post(
            "/scorer_models",
            json={
                "model_version": scorer.model_version,
                "horizon_days": scorer.horizon_days,
                "artifact": scorer.to_dict(),
                "metrics": scorer.metrics,
                "is_active": False,
            },
        )
        resp.raise_for_status()
        resp = self._client.patch(
            "/scorer_models",
            params={"horizon_days": f"eq.{scorer.horizon_days}", "is_active": "eq.true"},
            json={"is_active": False},
        )
        resp.raise_for_status()
        resp = self._client.patch(
            "/scorer_models",
            params={"model_version": f"eq.{scorer.model_version}"},
            json={"is_active": True},
        )
        resp.raise_for_status()
        logger.info("scorer: activated %s", scorer.model_version)

    def fetch_scored_signals(self, before_iso: str, limit: int = 20000) -> list[dict[str, Any]]:
        """Published signals carrying a p_outperform, old enough to have a realized outcome."""
        resp = self._client.get(
            "/signals",
            params={
                "select": "ticker,bar_ts,p_outperform,rank_pct,model_version",
                "p_outperform": "not.is.null",
                "bar_ts": f"lte.{before_iso}",
                "order": "bar_ts.desc",
                "limit": str(limit),
            },
        )
        resp.raise_for_status()
        return list(resp.json())

    def append_ic(self, row: dict[str, Any]) -> None:
        """Upsert one week's live-vs-backtest IC."""
        resp = self._client.post(
            "/scorer_ic_history?on_conflict=week_start,model_version",
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            json=row,
        )
        resp.raise_for_status()

    def fetch_ic_history(self, model_version: str, limit: int = 12) -> list[dict[str, Any]]:
        """Most recent weeks first."""
        resp = self._client.get(
            "/scorer_ic_history",
            params={
                "select": "week_start,live_ic,backtest_ic",
                "model_version": f"eq.{model_version}",
                "order": "week_start.desc",
                "limit": str(limit),
            },
        )
        resp.raise_for_status()
        return list(resp.json())
