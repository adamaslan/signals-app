"""P7 machinery: the ranker switch is off by default, identical when off, and cannot be
turned on without thresholds derived from shadow data."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

from signals_app import scanner
from signals_app.config import SIGNALS_APP_CODE_VERSION
from signals_app.detection.orchestrator import detect_all_signals
from signals_app.indicators.compute import compute_indicators
from signals_app.scoring.confluence import ConfluenceRanker
from signals_app.scoring.graded import GradedConfluenceRanker, decide_action
from signals_app.scoring.production import (
    GradedProductionRanker,
    LegacyRanker,
    RankerConfigError,
    build_production_ranker,
)
from signals_app.scoring.thresholds import (
    DEFAULT_THRESHOLDS,
    GradedThresholds,
    ThresholdsError,
    load_thresholds,
    parse_thresholds,
)

from .test_kinds import _random_walk_ohlcv

_scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

import graded_shadow as gs  # noqa: E402

GOOD = {"version": "t", "buy": 0.3, "sell": -0.25, "min_agreeing": 3, "max_opposing": 1,
        "publish_min": 0.3}


@pytest.fixture
def thresholds_file(tmp_path: Path) -> Path:
    path = tmp_path / "thresholds.json"
    path.write_text(json.dumps(GOOD))
    return path


class TestSwitch:
    def test_default_mode_is_the_legacy_ranker(self) -> None:
        assert isinstance(build_production_ranker(), LegacyRanker)
        assert SIGNALS_APP_CODE_VERSION == "signals-app@1.3.0"

    def test_legacy_adapter_is_byte_identical_to_confluence_ranker(self) -> None:
        full = compute_indicators(_random_walk_ohlcv(6))
        ranker = build_production_ranker()
        for end in range(230, len(full) + 1, 20):
            signals = list(detect_all_signals(full.iloc[:end]))
            rates = {"BULLISH": 0.55}
            assert (ranker.rank_signals(signals, strength_hit_rates=rates, regime="range",
                                        df=full.iloc[:end])
                    == ConfluenceRanker().rank_signals(signals, strength_hit_rates=rates))

    def test_graded_mode_without_thresholds_refuses_to_start(self) -> None:
        with pytest.raises(RankerConfigError, match="SIGNALS_THRESHOLDS_FILE"):
            build_production_ranker("graded", None)

    def test_graded_mode_with_a_missing_file_refuses_to_start(self, tmp_path: Path) -> None:
        with pytest.raises(RankerConfigError, match="not found"):
            build_production_ranker("graded", str(tmp_path / "nope.json"))

    def test_unknown_mode_is_rejected_not_defaulted(self) -> None:
        with pytest.raises(RankerConfigError, match="SIGNALS_RANKER"):
            build_production_ranker("gradedd")

    def test_graded_mode_builds_with_a_valid_file(self, thresholds_file: Path) -> None:
        ranker = build_production_ranker("graded", str(thresholds_file))
        assert isinstance(ranker, GradedProductionRanker)
        assert ranker.publish_min_score == 0.3

    def test_graded_production_ranker_runs_on_real_output(self, thresholds_file: Path) -> None:
        full = compute_indicators(_random_walk_ohlcv(8))
        ranker = build_production_ranker("graded", str(thresholds_file))
        result = ranker.rank_signals(list(detect_all_signals(full)), df=full)
        assert result.action in {"BUY", "SELL", "HOLD"}
        assert result.to_dict()["ranker_version"] == "graded-1"

    def test_scan_universe_fails_before_creating_a_run_when_misconfigured(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        scanner._production_ranker.cache_clear()
        monkeypatch.setattr(scanner, "RANKER_MODE", "graded")
        monkeypatch.setattr(scanner, "THRESHOLDS_FILE", None)

        class Writer:
            def start_run(self, **_: object) -> None:
                pytest.fail("a run row must not be created for a misconfigured ranker")

        with pytest.raises(RankerConfigError):
            scanner.scan_universe(["AAA"], writer=Writer(), dry_run=False)  # type: ignore[arg-type]
        scanner._production_ranker.cache_clear()


class TestGateBar:
    def test_default_bar_is_the_production_constant(self) -> None:
        assert scanner.passes_publication_gate(0.9, 5, 0.35, False)
        assert not scanner.passes_publication_gate(0.9, 5, 0.34, False)

    def test_a_custom_bar_replaces_it_for_both_directions(self) -> None:
        assert scanner.passes_publication_gate(0.9, 5, 0.2, False, min_score=0.2)
        assert scanner.passes_publication_gate(0.9, 5, -0.2, False, min_score=0.2)
        assert not scanner.passes_publication_gate(0.9, 5, 0.19, False, min_score=0.2)
        assert not scanner.passes_publication_gate(0.9, 5, -0.2, False, direction="bullish",
                                                   min_score=0.2)


class TestDecideAction:
    T = GradedThresholds("t", buy=0.3, sell=-0.3, min_agreeing=3, max_opposing=1, publish_min=0.3)

    @pytest.mark.parametrize(
        ("score", "bull", "bear", "live", "expected"),
        [(0.35, 3, 0, {1}, "BUY"), (0.35, 3, 0, set(), "HOLD"), (0.35, 3, 0, {-1}, "HOLD"),
         (0.29, 4, 0, {1}, "HOLD"), (0.35, 2, 0, {1}, "HOLD"), (0.35, 4, 2, {1}, "HOLD"),
         (-0.35, 0, 3, {-1}, "SELL"), (-0.35, 0, 3, {1}, "HOLD"), (-0.29, 0, 4, {-1}, "HOLD")],
    )
    def test_table(self, score: float, bull: int, bear: int, live: set[int], expected: str) -> None:
        assert decide_action(score, bull, bear, live, self.T) == expected

    def test_a_ranker_built_with_thresholds_uses_them(self) -> None:
        strict = GradedThresholds("t", buy=0.99, sell=-0.99, min_agreeing=3, max_opposing=1,
                                  publish_min=0.99)
        full = compute_indicators(_random_walk_ohlcv(1))
        signals = list(detect_all_signals(full, include_experimental=True))
        assert GradedConfluenceRanker(thresholds=strict).rank_signals(signals).action == "HOLD"

    def test_default_shadow_thresholds_are_the_family_ranker_constants(self) -> None:
        assert (DEFAULT_THRESHOLDS.buy, DEFAULT_THRESHOLDS.sell) == (0.20, -0.20)


class TestThresholdFiles:
    @pytest.mark.parametrize("bad", [{"buy": -0.1}, {"sell": 0.1}, {"publish_min": 0},
                                     {"min_agreeing": 0}, {"max_opposing": -1}])
    def test_invalid_values_are_rejected(self, bad: dict) -> None:
        with pytest.raises(ThresholdsError):
            parse_thresholds({**GOOD, **bad})

    def test_missing_key_is_rejected(self) -> None:
        with pytest.raises(ThresholdsError):
            parse_thresholds({k: v for k, v in GOOD.items() if k != "buy"})

    def test_garbage_file_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "t.json"
        path.write_text("not json")
        with pytest.raises(ThresholdsError, match="not valid JSON"):
            load_thresholds(path)


def _shadow_rows(n: int = 3000, seed: int = 0) -> list[dict]:
    """Rows where production BUYs the top ~8% by its own score and the graded score is a
    differently-scaled monotone-ish function of it."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        old_score = float(rng.normal(0, 0.2))
        new_score = float(old_score * 0.5 + rng.normal(0, 0.03))
        old = "BUY" if old_score > 0.3 else "SELL" if old_score < -0.3 else "HOLD"
        rows.append({"ticker": f"T{i}", "bar_ts": "x", "fwd": 0.0, "old_action": old,
                     "old_score": old_score, "new_action": "HOLD", "new_score": new_score,
                     "drivers": [], "bull_families": 3 if new_score > 0 else 0,
                     "bear_families": 3 if new_score < 0 else 0,
                     "live_sides": [1, -1]})
    return rows


class TestDeriveThresholds:
    def test_matches_production_call_counts(self) -> None:
        rows = _shadow_rows()
        result = gs.derive_thresholds(rows)
        assert result["target_calls"]["BUY"] > 100
        for side in ("BUY", "SELL"):
            gap = abs(result["achieved_calls"][side] - result["target_calls"][side])
            assert gap <= max(3, 0.05 * result["target_calls"][side])
        assert result["buy"] > 0 > result["sell"]
        assert result["publish_min"] == result["buy"]

    def test_the_file_it_writes_loads_back(self, tmp_path: Path) -> None:
        path = tmp_path / "t.json"
        path.write_text(json.dumps(gs.derive_thresholds(_shadow_rows())))
        loaded = load_thresholds(path)
        assert loaded.buy > 0 > loaded.sell
        assert loaded.derived_from.startswith("shadow:")

    def test_refuses_too_few_rows(self) -> None:
        with pytest.raises(ValueError, match="shadow rows"):
            gs.derive_thresholds(_shadow_rows(200))

    def test_refuses_when_production_made_almost_no_calls(self) -> None:
        rows = [{**r, "old_action": "HOLD"} for r in _shadow_rows()]
        with pytest.raises(ValueError, match="production BUY"):
            gs.derive_thresholds(rows)

    def test_ignores_rows_stored_before_family_counts_existed(self) -> None:
        rows = _shadow_rows(1200)
        stale = [{**r, "bull_families": None} for r in rows]
        with pytest.raises(ValueError, match="family counts"):
            gs.derive_thresholds(stale)

    def test_deterministic(self) -> None:
        assert gs.derive_thresholds(_shadow_rows()) == gs.derive_thresholds(_shadow_rows())


def test_graded_mode_stamps_a_distinct_code_version() -> None:
    """Rows are upserted on code_version, so graded rows must never merge into the old ranker's."""
    import os
    import subprocess

    src = Path(__file__).resolve().parent.parent / "src"
    code = "from signals_app.config import SIGNALS_APP_CODE_VERSION as v; print(v)"
    env = {**os.environ, "PYTHONPATH": str(src)}
    legacy = subprocess.run([sys.executable, "-c", code], env={**env, "SIGNALS_RANKER": "production"},
                            capture_output=True, text=True, check=True).stdout.strip()
    graded = subprocess.run([sys.executable, "-c", code], env={**env, "SIGNALS_RANKER": "graded"},
                            capture_output=True, text=True, check=True).stdout.strip()
    assert legacy == "signals-app@1.3.0"
    assert graded == "signals-app@1.3.0+graded"
