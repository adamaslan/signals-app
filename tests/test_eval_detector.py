"""Tests for scripts/eval_detector.py: the pure statistics and event gating.

The detector run itself is covered by an end-to-end smoke run; these tests pin
the parts a wrong answer would hide in: non-overlap gating, the kumo-breakdown
rule, the two-way bootstrap, and the pre-registered verdict thresholds.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

import eval_detector as ed  # noqa: E402


def make_cells(n_tickers: int, n_months: int, event_up_rate: float, base_up_rate: float, seed: int = 1):
    """Synthetic cells: every (ticker, month) has 20 baseline bars and 1 event."""
    rng = np.random.default_rng(seed)
    n_cells = n_tickers * n_months
    base_n = np.full(n_cells, 20.0)
    base_up = rng.binomial(20, base_up_rate, n_cells).astype(float)
    ev_n = np.ones(n_cells)
    ev_up = rng.binomial(1, event_up_rate, n_cells).astype(float)
    return ed.Cells(
        ticker_idx=np.repeat(np.arange(n_tickers), n_months),
        month_idx=np.tile(np.arange(n_months), n_tickers),
        base_n=base_n,
        base_up=base_up,
        base_down=base_n - base_up,
        ev_n={"s": ev_n},
        ev_up={"s": ev_up},
        ev_down={"s": ev_n - ev_up},
        n_tickers=n_tickers,
        n_months=n_months,
    )


def test_gate_keeps_events_one_horizon_apart():
    fires = np.zeros(200, dtype=bool)
    fires[[10, 11, 20, 31, 32, 60]] = True
    kept = ed.gate_non_overlapping(fires, 0, 200)
    assert kept.tolist() == [10, 31, 60]
    assert all(b - a >= ed.HORIZON for a, b in zip(kept, kept[1:]))


def test_gate_respects_bounds():
    fires = np.ones(50, dtype=bool)
    assert ed.gate_non_overlapping(fires, 5, 10).tolist() == [5]


def test_kumo_breakdown_fires_only_on_entry_below_the_cloud():
    pos = pd.Series([1.0, 0.0, -1.0, -1.0, 0.0, -1.0, np.nan, -1.0])
    fires = ed.kumo_breakdown_fires(pd.DataFrame({"Ichimoku_CloudPos": pos}))
    # bar 2: 0 -> -1 fires; bar 3 stays below; bar 5: 0 -> -1 fires;
    # bar 7 follows a NaN (cloud not ready), which must not count as a crossing.
    assert fires.tolist() == [False, False, True, False, False, True, False, False]


def test_series_computable_respects_the_window():
    assert ed.series_computable(ed.SERIES["H4"], 0)
    assert not ed.series_computable(ed.SERIES["H4"], 63)
    assert not ed.series_computable(ed.SERIES["H3"], 63)
    assert ed.series_computable(ed.SERIES["fib_hold"], 63)


def test_bootstrap_flags_a_planted_edge():
    cells = make_cells(40, 30, event_up_rate=0.75, base_up_rate=0.5)
    result = ed.two_way_bootstrap(cells, "s", +1, None, draws=300)
    assert result["edge_pp"] > 15
    assert result["p_one_sided"] < ed.SIGNIFICANCE
    assert result["ci_low"] > 0


def test_bootstrap_finds_nothing_when_there_is_no_edge():
    cells = make_cells(40, 30, event_up_rate=0.5, base_up_rate=0.5)
    result = ed.two_way_bootstrap(cells, "s", +1, None, draws=300)
    assert abs(result["edge_pp"]) < 5
    assert result["p_one_sided"] > ed.SIGNIFICANCE
    assert result["ci_low"] < 0 < result["ci_high"]


def test_bearish_side_scores_down_moves():
    cells = make_cells(30, 20, event_up_rate=0.2, base_up_rate=0.5)
    bear = ed.edge_pp(cells, "s", -1, None, np.ones(len(cells.base_n)))
    bull = ed.edge_pp(cells, "s", +1, None, np.ones(len(cells.base_n)))
    assert bear > 0 > bull


def test_reference_series_replaces_the_baseline():
    cells = make_cells(10, 10, event_up_rate=0.6, base_up_rate=0.5)
    cells.ev_n["ref"] = cells.ev_n["s"].copy()
    cells.ev_up["ref"] = cells.ev_up["s"].copy()
    cells.ev_down["ref"] = cells.ev_down["s"].copy()
    ones = np.ones(len(cells.base_n))
    assert ed.edge_pp(cells, "s", +1, "ref", ones) == pytest.approx(0.0)


def test_bootstrap_is_deterministic_for_a_seed():
    cells = make_cells(15, 12, event_up_rate=0.6, base_up_rate=0.5)
    a = ed.two_way_bootstrap(cells, "s", +1, None, draws=100)
    b = ed.two_way_bootstrap(cells, "s", +1, None, draws=100)
    assert a == b


@pytest.mark.parametrize(
    "key, edge, p, n, expected",
    [
        ("H4", 2.5, 0.01, 200, "PROMOTE"),
        ("H4", 2.5, 0.20, 200, "INCONCLUSIVE"),
        ("H4", 0.9, 0.30, 200, "KILL"),
        ("H4", 1.0, 0.30, 200, "INCONCLUSIVE"),  # kill is strictly < 1.0
        ("H1", 0.5, 0.30, 200, "KILL"),  # H1 kill is <= 0.5
        ("H1", 1.0, 0.30, 200, "INCONCLUSIVE"),
        ("H3", 5.0, 0.001, 10, "INCONCLUSIVE (too few events)"),
        ("H3", float("nan"), 0.5, 200, "INCONCLUSIVE (too few events)"),
    ],
)
def test_verdict_uses_the_preregistered_thresholds(key, edge, p, n, expected):
    assert ed.verdict(ed.HYPOTHESES[key], edge, p, n) == expected


def test_above_trend_and_cloud_needs_both_conditions():
    df = pd.DataFrame(
        {
            "Close": [10.0, 10.0, 10.0, 10.0],
            "SMA_200": [9.0, 11.0, 9.0, np.nan],
            "Ichimoku_CloudPos": [1.0, 1.0, 0.0, 1.0],
        }
    )
    assert ed.above_trend_and_cloud(df).tolist() == [True, False, False, False]
