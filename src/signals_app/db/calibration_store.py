"""Supabase-backed calibration store — closes the loop between
`detector_hits` (Phase 3/6) and `ConfluenceRanker.rank_signals()`'s
optional `strength_hit_rates` parameter (already implemented, previously
fed by a local JSON file that didn't survive a container restart).

Unlike db/supabase.py (write-only, used by the live scanner), this module
reads: it has to join detector_hits against forward_returns to compute
hit-rates. Kept separate from SupabaseWriter to keep that module's
"writes only, no query logic" contract intact for the scan path.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass

import httpx

from signals_app.config import (
    CALIBRATION_MIN_BUCKET_SIZE,
    SIGNALS_APP_CODE_VERSION,
    SUPABASE_REQUEST_TIMEOUT_SECONDS,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_URL,
)
from signals_app.db.supabase import SupabaseConfigError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DetectorHitRow:
    """One row from `detector_hits`, as needed for calibration scoring."""

    ticker: str
    bar_ts: str
    strength: str
    category: str


@dataclass(frozen=True)
class ForwardReturnRow:
    """One row from `forward_returns`."""

    ticker: str
    bar_ts: str
    horizon_days: int
    pct_return: float


def _is_bullish(strength: str) -> bool:
    return "BULLISH" in strength


def _is_bearish(strength: str) -> bool:
    return "BEARISH" in strength


def _confluence_band(pct_return: float) -> str:
    """Bucket a realized return into a coarse band for the confluence_band
    calibration kind. Mirrors the sign-only scoring backtests/engine.py
    already uses for strength/category — a return's magnitude isn't scored
    here, only its direction, since that's what a directional signal claims.
    """
    return "positive" if pct_return > 0 else "negative" if pct_return < 0 else "flat"


class CalibrationStore:
    """Reads detector_hits/forward_returns and writes calibration rows via
    Supabase's PostgREST API.
    """

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
            },
            timeout=SUPABASE_REQUEST_TIMEOUT_SECONDS,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> CalibrationStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def fetch_unresolved_detector_hits(
        self, horizon_days: int, cutoff_iso: str, limit: int = 5000
    ) -> list[DetectorHitRow]:
        """Fetch (ticker, bar_ts) pairs from detector_hits old enough that a
        horizon_days-ahead forward return should exist, and not already
        present in forward_returns for this horizon.

        PostgREST has no server-side anti-join, so this fetches candidates
        by cutoff date and the caller (compute_forward_returns) filters
        against what's already in forward_returns — acceptable at this
        corpus size (thousands, not millions, of rows).
        """
        resp = self._client.get(
            "/detector_hits",
            params={
                "select": "ticker,bar_ts,strength,category",
                "bar_ts": f"lte.{cutoff_iso}",
                "limit": str(limit),
                "order": "bar_ts.asc",
            },
        )
        resp.raise_for_status()
        return [
            DetectorHitRow(
                ticker=r["ticker"],
                bar_ts=r["bar_ts"],
                strength=r["strength"],
                category=r["category"],
            )
            for r in resp.json()
        ]

    def fetch_forward_returns(
        self, horizon_days: int, limit: int = 20000
    ) -> list[ForwardReturnRow]:
        """Fetch already-computed forward returns for this horizon — used
        both to skip re-computing them and to join against detector_hits.
        """
        resp = self._client.get(
            "/forward_returns",
            params={
                "select": "ticker,bar_ts,horizon_days,pct_return",
                "horizon_days": f"eq.{horizon_days}",
                "limit": str(limit),
            },
        )
        resp.raise_for_status()
        return [
            ForwardReturnRow(
                ticker=r["ticker"],
                bar_ts=r["bar_ts"],
                horizon_days=r["horizon_days"],
                pct_return=r["pct_return"],
            )
            for r in resp.json()
        ]

    def write_forward_returns(self, rows: list[ForwardReturnRow]) -> None:
        """Upsert forward_returns rows. Idempotent on (ticker, bar_ts, horizon_days)."""
        if not rows:
            return
        payload = [
            {
                "ticker": r.ticker,
                "bar_ts": r.bar_ts,
                "horizon_days": r.horizon_days,
                "pct_return": r.pct_return,
            }
            for r in rows
        ]
        resp = self._client.post(
            "/forward_returns?on_conflict=ticker,bar_ts,horizon_days",
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            json=payload,
        )
        resp.raise_for_status()

    def write_calibration_generation(
        self, horizon_days: int, buckets: dict[str, dict[str, tuple[int, int]]]
    ) -> int:
        """Insert a new calibration generation (is_active=false) and return
        how many rows were written. Does NOT activate it — call
        activate_generation() only after every row has been written
        successfully, so a failed run never leaves a partial generation live.

        Args:
            horizon_days: The horizon this generation covers.
            buckets: {bucket_kind: {bucket_key: (hits, total)}}, e.g.
                {"strength": {"STRONG_BULLISH": (42, 60)}, "category": {...}, ...}

        Returns:
            Number of calibration rows inserted.
        """
        rows = []
        for bucket_kind, keyed in buckets.items():
            for bucket_key, (hits, total) in keyed.items():
                if total < CALIBRATION_MIN_BUCKET_SIZE:
                    continue
                rows.append({
                    "code_version": SIGNALS_APP_CODE_VERSION,
                    "horizon_days": horizon_days,
                    "bucket_kind": bucket_kind,
                    "bucket_key": bucket_key,
                    "hits": hits,
                    "total": total,
                    "hit_rate": hits / total,
                    "is_active": False,
                })
        if not rows:
            logger.warning(
                "calibration: no bucket cleared CALIBRATION_MIN_BUCKET_SIZE — nothing written"
            )
            return 0

        resp = self._client.post("/calibration", json=rows)
        resp.raise_for_status()
        logger.info("calibration: wrote %d rows (inactive)", len(rows))
        return len(rows)

    def activate_latest_generation(self, horizon_days: int) -> None:
        """Flip is_active off for all prior generations at this horizon, then
        on for the most recent one. Two sequential PostgREST calls, not a
        real transaction (PostgREST's REST API has no cross-request
        transaction support) — there is a small window where a reader could
        see zero active rows between the two calls. Acceptable here: readers
        treat "no active calibration" identically to "not calibrated yet"
        (ConfluenceRanker's safe default), so the window degrades to
        uncalibrated, never to wrong data.
        """
        # Deactivate everything at this horizon. Filters passed via `params=`
        # (not an f-string URL) so httpx URL-encodes them — a raw f-string
        # here previously broke on computed_at values containing "+"
        # (UTC offset), which decodes to a space in an unencoded query string.
        resp = self._client.patch(
            "/calibration",
            params={"horizon_days": f"eq.{horizon_days}", "is_active": "eq.true"},
            json={"is_active": False},
        )
        resp.raise_for_status()

        # Find the most recent code_version/computed_at generation and activate it.
        resp = self._client.get(
            "/calibration",
            params={
                "select": "computed_at",
                "horizon_days": f"eq.{horizon_days}",
                "order": "computed_at.desc",
                "limit": "1",
            },
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            logger.warning("calibration: no rows to activate for horizon_days=%d", horizon_days)
            return
        latest_computed_at = rows[0]["computed_at"]

        resp = self._client.patch(
            "/calibration",
            params={
                "horizon_days": f"eq.{horizon_days}",
                "computed_at": f"eq.{latest_computed_at}",
            },
            json={"is_active": True},
        )
        resp.raise_for_status()
        logger.info("calibration: activated generation computed_at=%s", latest_computed_at)


def score_hits_against_returns(
    hits: list[DetectorHitRow],
    returns: dict[tuple[str, str], float],
) -> dict[str, dict[str, tuple[int, int]]]:
    """Join detector_hits against a (ticker, bar_ts) -> pct_return map and
    aggregate into hit/total counts per bucket_kind/bucket_key.

    A "hit" is a directional detector strength (BULLISH/BEARISH) agreeing
    with the sign of the realized return — the same scoring rule
    backtests/engine.py already uses for the live backtest path, applied
    here to the stored corpus instead of an in-memory scan.

    Returns:
        {bucket_kind: {bucket_key: (hits, total)}} for "strength",
        "category", and "confluence_band" bucket kinds.
    """
    by_strength: dict[str, list[bool]] = defaultdict(list)
    by_category: dict[str, list[bool]] = defaultdict(list)
    by_band: dict[str, list[bool]] = defaultdict(list)

    for hit in hits:
        pct_return = returns.get((hit.ticker, hit.bar_ts))
        if pct_return is None:
            continue

        if _is_bullish(hit.strength):
            outcome = pct_return > 0
        elif _is_bearish(hit.strength):
            outcome = pct_return < 0
        else:
            continue

        by_strength[hit.strength].append(outcome)
        by_category[hit.category].append(outcome)
        by_band[_confluence_band(pct_return)].append(outcome)

    def to_counts(d: dict[str, list[bool]]) -> dict[str, tuple[int, int]]:
        return {k: (sum(v), len(v)) for k, v in d.items()}

    return {
        "strength": to_counts(by_strength),
        "category": to_counts(by_category),
        "confluence_band": to_counts(by_band),
    }
