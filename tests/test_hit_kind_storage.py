"""P2: detector_hits rows carry kind/concept only when the migration flag is on."""
from __future__ import annotations

from pathlib import Path

import pytest

from signals_app.db import supabase
from signals_app.detection.base import MutableSignal
from signals_app.scoring.kinds import stamp_kinds

_MIGRATIONS = Path(__file__).resolve().parent.parent / "supabase" / "migrations"


def _stamped_signal() -> MutableSignal:
    sig = MutableSignal(
        signal="MACD BULL CROSS", description="d", strength="BULLISH", category="MACD"
    )
    stamp_kinds([sig])
    return sig


def test_row_omits_new_columns_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supabase, "WRITE_HIT_KINDS", False)
    row = supabase.detector_hit_row("AAPL", "2026-10-06T00:00:00Z", _stamped_signal())
    assert not {"kind", "concept", "context"} & set(row)


def test_row_includes_new_columns_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supabase, "WRITE_HIT_KINDS", True)
    row = supabase.detector_hit_row("AAPL", "2026-10-06T00:00:00Z", _stamped_signal())
    assert (row["kind"], row["concept"], row["context"]) == ("X", "macd_cross", None)
    assert row["detector"] == "MACD BULL CROSS"


def test_migration_is_additive_only() -> None:
    path = next(_MIGRATIONS.glob("*detector_hits_kind.sql"))
    code = "\n".join(
        line for line in path.read_text().lower().splitlines() if not line.strip().startswith("--")
    )
    assert "add column if not exists" in code
    for forbidden in ("drop ", "not null", "default", "update ", "delete "):
        assert forbidden not in code
