"""Dip-timing study, tunable config, and chain runner — all offline."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from signals_app.chains import ChainError, ChainRunner, ChainStep
from signals_app.clients.holdem import HoldemUnavailable
from signals_app.config import TUNABLE_DEFAULTS, TUNABLE_EFFECTIVE
from signals_app.service import BatchFailure, BatchResult
from signals_app.studies.dip import DipStudyParams, run_dip_study


def _frame(closes: list[float]) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=len(closes))
    return pd.DataFrame({"Close": closes}, index=idx)


def _sawtooth(cycles: int = 10) -> list[float]:
    """Flat at 100, drop 10% over 3 bars, bottom 2 bars later, recover, repeat."""
    one = [100.0] * 10 + [97.0, 94.0, 91.0, 90.0, 89.0, 92.0, 96.0, 100.0]
    return one * cycles + [100.0] * 30


class TestDipStudy:
    def test_counts_one_episode_per_selloff(self) -> None:
        params = DipStudyParams(windows=(10,), dip_pct=5)
        result = run_dip_study("SAW", _frame(_sawtooth()), params)
        w = result.windows[0]
        assert w.n_dips == 10
        assert not w.low_sample
        assert w.recovery_rate == 1.0

    def test_trigger_to_trough_and_depth(self) -> None:
        w = run_dip_study(
            "SAW", _frame(_sawtooth()), DipStudyParams(windows=(10,), dip_pct=5)
        ).windows[0]
        # Trigger on 94 (first close ≤ 95), trough at 89 three bars later.
        assert w.median_trigger_to_trough_bars == 3
        assert w.median_peak_to_trigger_bars == 2
        assert w.median_depth_pct == pytest.approx(-11.0)

    def test_best_entry_delay_is_the_trough_day(self) -> None:
        w = run_dip_study(
            "SAW",
            _frame(_sawtooth()),
            DipStudyParams(windows=(10,), dip_pct=5, max_entry_delay=5, horizon_days=4),
        ).windows[0]
        assert w.best_entry_delay == 3
        assert w.edge_vs_baseline_pct is not None and w.edge_vs_baseline_pct > 0

    def test_current_dip_detected(self) -> None:
        closes = _sawtooth(3)[:-30] + [100.0] * 15 + [96.0, 93.0, 92.0]
        params = DipStudyParams(windows=(10,), dip_pct=5)
        w = run_dip_study("SAW", _frame(closes), params).windows[0]
        assert w.current is not None and w.current.in_dip
        assert w.current.bars_since_trigger == 1

    def test_too_short_history_raises(self) -> None:
        with pytest.raises(ValueError, match="need at least"):
            run_dip_study("X", _frame([100.0] * 20), DipStudyParams(windows=(50,)))

    def test_overrides_reject_unknown_and_bad_values(self) -> None:
        with pytest.raises(ValueError, match="unknown dip params"):
            DipStudyParams.from_overrides({"dip_percent": 5})
        with pytest.raises(ValueError, match="dip_pct"):
            DipStudyParams.from_overrides({"dip_pct": 0})
        assert DipStudyParams.from_overrides({"windows": [20, 5, 5]}).windows == (5, 20)


class TestTunableConfig:
    def test_thresholds_are_registered(self) -> None:
        for name in ("RSI_OVERSOLD", "CONFLUENCE_BUY_THRESHOLD", "PUBLISH_MIN_SIGNALS"):
            assert name in TUNABLE_DEFAULTS
            assert name in TUNABLE_EFFECTIVE

    def test_env_override_and_malformed_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from signals_app import config

        monkeypatch.setenv("SIGNALS_TEST_KNOB", "25")
        assert config._tunable("TEST_KNOB", 30.0) == 25.0
        monkeypatch.setenv("SIGNALS_TEST_KNOB", "abc")
        assert config._tunable("TEST_KNOB", 30.0) == 30.0
        config.TUNABLE_DEFAULTS.pop("TEST_KNOB")
        config.TUNABLE_EFFECTIVE.pop("TEST_KNOB")


class _FakeState:
    def __init__(self, score: float, bias: str) -> None:
        self.confluence_score, self.bias = score, bias
        self.action, self.close, self.rsi = "BUY", 100.0, 45.0


class _FakeOut:
    def __init__(self, ticker: str, score: float, bias: str = "bullish") -> None:
        from signals_app.schemas.signal_output import SignalDirection

        self.ticker = ticker
        self.state = _FakeState(score, bias)
        self.signal = type("S", (), {"direction": SignalDirection.buy, "confidence": 0.6})()


class _FakeHoldem:
    def __init__(self, verdicts: dict[str, str]) -> None:
        self._verdicts = verdicts
        self.seen_params: list[dict | None] = []

    async def verdict(self, symbol: str, *, period: str, params: dict | None) -> dict:
        self.seen_params.append(params)
        if symbol not in self._verdicts:
            raise HoldemUnavailable("down")
        return {"verdict": self._verdicts[symbol], "confidence": 70, "params_used": params}


def _runner(holdem: _FakeHoldem | None = None) -> ChainRunner:
    scores = {"AAA": 0.5, "BBB": 0.05, "CCC": 0.4}

    async def analyze_many(symbols, period, *, no_llm):  # noqa: ANN001, ANN202
        ok = [_FakeOut(s, scores[s]) for s in symbols if s in scores]
        failed = [BatchFailure(s, "SymbolNotFound", "nope") for s in symbols if s not in scores]
        return BatchResult(ok=ok, failed=failed)

    def fetch_daily(symbol: str, period: str) -> pd.DataFrame:
        closes = _sawtooth()
        if symbol == "CCC":  # currently dipping
            closes = closes[:-30] + [100.0] * 15 + [96.0, 93.0, 92.0]
        return _frame(closes)

    return ChainRunner(fetch_daily=fetch_daily, analyze_many=analyze_many, holdem=holdem)


class TestChains:
    async def test_full_chain_filters_and_audits(self) -> None:
        holdem = _FakeHoldem({"AAA": "HOLD EM", "CCC": "HOLD EM"})
        result = await _runner(holdem).run(
            [
                ChainStep("symbols", {"symbols": ["aaa", "bbb", "ccc", "zzz"]}),
                ChainStep("signals", {"min_confluence": 0.1}),
                ChainStep("dip_study", {"windows": [10], "dip_pct": 5}),
                ChainStep(
                    "holdem",
                    {"keep": ["HOLD EM"], "verdict_params": {"hold_threshold": 58}},
                ),
                ChainStep("rank", {"by": "signal.confluence_score", "top": 1}),
            ]
        )
        assert result.symbols == ["AAA"]
        signals_log = result.steps[1]
        assert set(signals_log.dropped) == {"BBB", "ZZZ"}
        assert result.steps[-1].dropped == {"CCC": "outside top 1 by signal.confluence_score"}
        assert holdem.seen_params == [{"hold_threshold": 58}] * 2
        assert result.rows["AAA"]["dip"]["10"]["n_dips"] == 10

    async def test_in_dip_window_keeps_only_dipping(self) -> None:
        result = await _runner().run(
            [
                ChainStep("symbols", {"symbols": ["AAA", "CCC"]}),
                ChainStep("dip_study", {"windows": [10], "dip_pct": 5, "in_dip_window": 10}),
            ]
        )
        assert result.symbols == ["CCC"]

    async def test_holdem_unconfigured_is_loud_not_silent(self) -> None:
        result = await _runner(None).run(
            [ChainStep("symbols", {"symbols": ["AAA"]}), ChainStep("holdem", {})]
        )
        assert result.steps[1].skipped
        assert "HOLDEM_API_URL" in result.steps[1].warnings[0]

    @pytest.mark.parametrize(
        "steps, match",
        [
            ([ChainStep("signals")], "first step"),
            ([ChainStep("symbols", {"symbols": ["A"]}), ChainStep("nope")], "unknown op"),
            ([ChainStep("symbols", {"symbols": ["A"], "x": 1})], "unknown params"),
            (
                [ChainStep("symbols", {"symbols": ["A"]}),
                 ChainStep("dip_study", {"windows": [5], "in_dip_window": 10})],
                "in_dip_window",
            ),
        ],
    )
    async def test_bad_chains_rejected(self, steps: list[ChainStep], match: str) -> None:
        with pytest.raises(ChainError, match=match):
            await _runner().run(steps)


def test_api_routes_registered() -> None:
    from fastapi.testclient import TestClient

    from signals_app.api.main import app

    client = TestClient(app)
    body = client.get("/v1/params").json()
    assert any(v["name"] == "RSI_OVERSOLD" for v in body["process"]["values"])
    assert body["per_request"]["dip_study"]["windows"] == [5, 10, 20, 50]
    assert "dip_study" in client.get("/v1/chains/ops").json()
    bad = client.post("/v1/chains/run", json={"steps": [{"op": "rank"}]})
    assert bad.status_code == 400
    np.testing.assert_equal(bad.json()["error"]["type"], "InvalidChain")
