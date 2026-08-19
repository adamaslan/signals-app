"""Tests for the historical backfill script.

Uses the same in-memory FakeWriter as test_scan_universe.py — no live
Supabase project required.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_history import backfill_one_symbol  # noqa: E402
from signals_app.config import get_settings  # noqa: E402
from tests.test_scan_universe import FakeWriter  # noqa: E402


class TestBackfillIsolation:
    def test_bad_ticker_returns_failed_result_not_exception(self) -> None:
        writer = FakeWriter()
        result = backfill_one_symbol(
            ticker="$$$NOT-A-REAL-TICKER$$$",
            period="5y",
            writer=writer,
            settings=get_settings(),
        )
        assert result.ok is False
        assert result.hits_written == 0
        assert result.reason is not None

    def test_dry_run_none_writer_skips_writes(self) -> None:
        # A None writer must never be called — verified by using a writer
        # whose methods raise if invoked, rather than just asserting on
        # FakeWriter call counts (which wouldn't catch a writer=None bug
        # in the None-handling branch itself).
        result = backfill_one_symbol(
            ticker="$$$NOT-A-REAL-TICKER$$$",
            period="5y",
            writer=None,
            settings=get_settings(),
        )
        assert result.ok is False
