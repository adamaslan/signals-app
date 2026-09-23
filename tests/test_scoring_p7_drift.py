"""P7: drift alert, live-IC computation, model persistence payloads."""
from __future__ import annotations

import json

import httpx
import numpy as np
import pandas as pd

from signals_app.db.scorer_store import ScorerStore
from signals_app.db.supabase import EngineRun, SignalRecord, SupabaseWriter
from signals_app.scoring.drift import drift_alert, weekly_live_ic
from signals_app.scoring.model import LogisticScorer


def _hist(*live: float | None, backtest: float = 0.04) -> list[dict]:
    return [{"live_ic": v, "backtest_ic": backtest} for v in live]


def test_alert_needs_four_consecutive_weeks_below_half():
    assert drift_alert(_hist(0.01, 0.0, -0.01, 0.019))
    assert not drift_alert(_hist(0.01, 0.0, -0.01))  # only 3 weeks of history
    assert not drift_alert(_hist(0.01, 0.0, 0.03, 0.01))  # one healthy week breaks the run
    assert not drift_alert(_hist(0.01, None, 0.0, 0.0))  # a week without data is not evidence
    assert not drift_alert(_hist(0.01, 0.0, 0.0, 0.0, backtest=0.0))  # nothing to be half of


def test_alert_uses_most_recent_weeks_first():
    history = _hist(0.0, 0.0, 0.0, 0.0, 0.05, 0.05)
    assert drift_alert(history)
    assert not drift_alert(list(reversed(history)))


def test_weekly_live_ic_is_positive_for_a_predictive_score_and_skips_thin_days():
    rng = np.random.default_rng(0)
    rows = []
    for day in pd.bdate_range("2026-06-01", periods=15):
        for s in range(20):
            p = rng.uniform(0.3, 0.7)
            rows.append({"bar_ts": day.isoformat(), "p_outperform": p,
                         "realized_excess": (p - 0.5) * 0.1 + rng.normal(0, 0.02)})
    rows.append({"bar_ts": "2026-07-30T00:00:00", "p_outperform": 0.6, "realized_excess": 0.01})  # 1 name: too thin
    weekly = weekly_live_ic(pd.DataFrame(rows))
    assert len(weekly) == 3
    assert (weekly["live_ic"] > 0.3).all()
    assert weekly_live_ic(pd.DataFrame({"bar_ts": [], "p_outperform": [], "realized_excess": []})).empty


def _writer_capturing(sent: list[dict]) -> SupabaseWriter:
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=[{}])

    writer = SupabaseWriter(url="https://example.invalid", service_role_key="k")
    writer._client = httpx.Client(base_url="https://example.invalid/rest/v1", transport=httpx.MockTransport(handler))
    return writer


def _record(**extra) -> SignalRecord:
    return SignalRecord(
        ticker="AAPL", period="3mo", bar_ts="2026-09-22T00:00:00", direction="BUY", confidence=0.6,
        confluence_score=0.4, bias="bullish", bull_count=3, bear_count=1, total_signals=6,
        data_quality_score=0.9, **extra,
    )


def test_legacy_write_omits_model_columns_so_unmigrated_databases_keep_working():
    sent: list[dict] = []
    _writer_capturing(sent).write_signal(EngineRun(id=1, started_at="x"), _record())
    assert not {"rank_pct", "p_outperform", "expected_excess", "model_version"} & set(sent[0])


def test_model_write_includes_rank_and_probability():
    sent: list[dict] = []
    _writer_capturing(sent).write_signal(
        EngineRun(id=1, started_at="x"),
        _record(rank_pct=97.5, p_outperform=0.61, expected_excess=0.004, model_version="m1"),
    )
    assert sent[0]["rank_pct"] == 97.5 and sent[0]["p_outperform"] == 0.61 and sent[0]["model_version"] == "m1"


def test_publish_model_activates_exactly_one_after_inserting_inactive():
    calls: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url.path), json.loads(request.content or b"{}")))
        return httpx.Response(200, json=[])

    store = ScorerStore(url="https://example.invalid", service_role_key="k")
    store._client = httpx.Client(base_url="https://example.invalid/rest/v1", transport=httpx.MockTransport(handler))
    scorer = LogisticScorer(("a",), (0.0,), (1.0,), (0.5,), 0.0, 20, "m-test")
    store.publish_model(scorer)
    assert [c[0] for c in calls] == ["POST", "PATCH", "PATCH"]
    assert calls[0][2]["is_active"] is False and calls[0][2]["artifact"]["model_version"] == "m-test"
    assert calls[1][2] == {"is_active": False}  # deactivate previous first...
    assert calls[2][2] == {"is_active": True}  # ...then activate the new one
