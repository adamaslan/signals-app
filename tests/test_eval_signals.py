"""P5 evaluator: recovers a planted edge, refuses to invent one, and cannot peek.

Pure-function tests on synthetic frames; no network and no real detectors.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

import eval_signals as es  # noqa: E402

from signals_app.detection.base import MutableSignal  # noqa: E402
from signals_app.scoring.evidence import load_evidence, parse_evidence  # noqa: E402

N_BARS = 900
MARK_EVERY = 25


def _frame(seed: int, planted_hit_prob: float | None) -> tuple[pd.DataFrame, np.ndarray]:
    """Random-walk closes; every MARK_EVERY-th bar is marked. When planted, the
    close HORIZON bars after a mark is forced up with probability planted_hit_prob."""
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, N_BARS)))
    marks = np.zeros(N_BARS, dtype=bool)
    marks[es.WARMUP :: MARK_EVERY] = True
    if planted_hit_prob is not None:
        for bar in np.flatnonzero(marks):
            if bar + es.HORIZON < N_BARS:
                up = rng.random() < planted_hit_prob
                close[bar + es.HORIZON] = close[bar] * (1.03 if up else 0.97)
    df = pd.DataFrame(
        {"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1.0},
        index=pd.date_range("2022-01-03", periods=N_BARS, freq="B"),
    )
    df["mark"] = marks
    return df, marks


def _detect(window: pd.DataFrame) -> list[MutableSignal]:
    if not window["mark"].iloc[-1]:
        return []
    return [MutableSignal(signal="PLANTED", description="", strength="BULLISH",
                          category="TREND", kind="X", concept="planted")]


def _results(planted: float | None, tickers: int = 30) -> list[es.FrameResult]:
    return [es.evaluate_frame(f"T{i}", _frame(i, planted)[0], _detect, step=1)
            for i in range(tickers)]


def test_recovers_a_planted_edge() -> None:
    evidence = es.build_evidence(_results(planted=0.85), today="2026-10-06")
    entry = evidence["labels"]["PLANTED"]
    assert entry["fit"]["n"] >= es.MIN_N
    assert entry["fit"]["edge_pp"] > 15
    assert entry["holdout"]["edge_pp"] > 0
    assert entry["E"] == 1.0


def test_no_edge_earns_nothing() -> None:
    evidence = es.build_evidence(_results(planted=None), today="2026-10-06")
    assert evidence["labels"]["PLANTED"]["E"] == 0.0


def test_a_contrarian_planted_edge_is_not_credited_to_a_bullish_label() -> None:
    evidence = es.build_evidence(_results(planted=0.15), today="2026-10-06")
    assert evidence["labels"]["PLANTED"]["E"] == 0.0
    assert evidence["labels"]["PLANTED"]["fit"]["edge_pp"] < 0


def test_edge_that_does_not_hold_out_earns_nothing() -> None:
    fit = {"n": 1000, "z": 4.0, "edge_pp": 5.0}
    assert es.earn_evidence(fit, {"n": 200, "edge_pp": -0.5}) == 0.0
    assert es.earn_evidence(fit, {"n": 0, "edge_pp": 0.0}) == 0.0
    assert es.earn_evidence(fit, {"n": 200, "edge_pp": 1.0}) == 1.0


@pytest.mark.parametrize(
    "fit",
    [{"n": 299, "z": 4.0, "edge_pp": 5.0}, {"n": 500, "z": 1.4, "edge_pp": 5.0},
     {"n": 500, "z": 4.0, "edge_pp": 0.0}],
)
def test_fit_bar_is_n_z_and_positive_edge(fit: dict) -> None:
    assert es.earn_evidence(fit, {"n": 200, "edge_pp": 5.0}) == 0.0


def test_e_scales_with_edge_and_caps_at_one() -> None:
    hold = {"n": 100, "edge_pp": 1.0}
    assert es.earn_evidence({"n": 500, "z": 2.0, "edge_pp": 1.0}, hold) == 0.5
    assert es.earn_evidence({"n": 500, "z": 2.0, "edge_pp": 9.0}, hold) == 1.0


def test_overlapping_events_collapse_to_one_per_horizon() -> None:
    base = dict(ticker="A", label="L", concept="c", kind="X", direction=1, fwd=0.1, regime=None)
    events = [es.Event(bar=b, **base) for b in (100, 105, 120, 121, 122, 150)]
    kept = es.collapse_overlaps(events, lambda e: e.label)
    assert [e.bar for e in kept] == [100, 121, 150]


def test_collapse_is_per_ticker_and_key() -> None:
    mk = lambda t, label, bar: es.Event(t, bar, label, None, "X", 1, 0.0, None)  # noqa: E731
    events = [mk("A", "L", 100), mk("B", "L", 101), mk("A", "M", 102)]
    assert len(es.collapse_overlaps(events, lambda e: e.label)) == 3


def test_fit_events_never_reach_into_the_holdout() -> None:
    result = es.evaluate_frame("A", _frame(1, None)[0], _detect, step=1)
    fit_ev, hold_ev, fit_base, hold_base = es.split_fit_holdout([result])
    cutoff = result.n_bars - es.HOLDOUT_BARS
    assert all(e.bar + es.HORIZON < cutoff for e in fit_ev)
    assert all(b[0] + es.HORIZON < cutoff for b in fit_base)
    assert all(e.bar >= cutoff for e in hold_ev)
    assert hold_ev and fit_ev


def test_detector_only_sees_bars_up_to_t() -> None:
    seen_lengths: list[int] = []

    def spy(window: pd.DataFrame) -> list[MutableSignal]:
        seen_lengths.append(len(window))
        return []

    df, _ = _frame(0, None)
    es.evaluate_frame("A", df, spy, step=7)
    assert seen_lengths and max(seen_lengths) <= len(df) - es.HORIZON


def test_forward_return_uses_the_horizon_ahead_close() -> None:
    df, _ = _frame(2, None)
    result = es.evaluate_frame("A", df, _detect, step=1)
    event = result.events[0]
    expected = df["Close"].iloc[event.bar + es.HORIZON] / df["Close"].iloc[event.bar] - 1
    assert event.fwd == pytest.approx(expected)


def test_direction_of() -> None:
    assert [es.direction_of(s) for s in
            ("EXTREME BULLISH", "STRONG BEARISH", "NEUTRAL", "SIGNIFICANT")] == [1, -1, 0, 0]


def test_bearish_label_is_scored_against_the_down_baseline() -> None:
    base = [(i, -0.01 if i % 2 else 0.01, None) for i in range(1000)]
    events = [es.Event("A", i, "L", None, "X", -1, -0.01, None) for i in range(400)]
    stats = es.label_stats(events, base)
    assert stats["hit_rate"] == 1.0
    assert stats["baseline"] == 0.5
    assert stats["edge_pp"] == 50.0


def test_output_round_trips_through_the_loader(tmp_path: Path) -> None:
    evidence = es.build_evidence(_results(planted=0.85), today="2026-10-06")
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(evidence))
    table = load_evidence(path)
    sig = MutableSignal(signal="PLANTED", description="", strength="BULLISH", category="TREND",
                        concept="planted")
    assert table.e_for(sig) == 1.0
    assert table.default == 0.0
    unknown = MutableSignal(signal="UNSEEN", description="", strength="BULLISH", category="TREND")
    assert table.e_for(unknown) == 0.0


def test_regime_specific_evidence_overrides_the_label_value() -> None:
    table = parse_evidence({"version": "t", "labels": {"X": 0.5}, "regimes": {"X": {"range": 0.0}}})
    sig = MutableSignal(signal="X", description="", strength="BULLISH", category="TREND")
    assert table.e_for(sig) == 0.5
    assert table.e_for(sig, regime="range") == 0.0
    assert table.e_for(sig, regime="trend_up") == 0.5


@pytest.mark.parametrize("bad", [{"labels": {"X": 1.5}}, {"labels": {"X": -0.1}},
                                 {"default_E": 2}, {}, {"labels": {"X": True}}])
def test_malformed_evidence_is_rejected(bad: dict) -> None:
    from signals_app.scoring.evidence import EvidenceError

    with pytest.raises(EvidenceError):
        parse_evidence({"version": "t", **bad} if bad else {})


def test_committed_baseline_loads_and_zeros_the_known_bad_states() -> None:
    table = load_evidence()
    for label, concept in (("PRICE ABOVE KUMO", "ichimoku_cloud_pos"),
                           ("BULLISH KUMO", "ichimoku_cloud_color"),
                           ("KUMO BREAKOUT", "kumo_break")):
        sig = MutableSignal(signal=label, description="", strength="BULLISH", category="ICHIMOKU",
                            concept=concept)
        assert table.e_for(sig) == 0.0
    golden = MutableSignal(signal="GOLDEN CROSS", description="", strength="BULLISH",
                           category="MA_CROSS", concept="ma_cross")
    assert table.e_for(golden) == 1.0


def test_thin_label_falls_through_to_its_concept_instead_of_shadowing_it() -> None:
    """A rare label must not get its own E = 0 entry that hides an earned concept."""
    results = _results(planted=0.85)

    def two_labels(window: pd.DataFrame) -> list[MutableSignal]:
        out = _detect(window)
        if out and (len(window) - 1) % 50 == 10:  # rare variant of the same concept
            out.append(MutableSignal(signal="PLANTED RARE", description="", strength="BULLISH",
                                     category="TREND", kind="X", concept="planted"))
        return out

    results = [es.evaluate_frame(f"T{i}", _frame(i, 0.85)[0], two_labels, step=1) for i in range(30)]
    evidence = es.build_evidence(results, today="2026-10-06")
    assert "PLANTED RARE" not in evidence["labels"]
    assert "PLANTED RARE" in evidence["unmeasured_labels"]
    table = parse_evidence(evidence)
    rare = MutableSignal(signal="PLANTED RARE", description="", strength="BULLISH",
                         category="TREND", concept="planted")
    assert table.e_for(rare) == 1.0
