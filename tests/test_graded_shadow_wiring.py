"""P6 wiring: the shadow score rides beside production and never changes it."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from signals_app import scanner
from signals_app.data.fetcher import OHLCVResult
from signals_app.db import supabase
from signals_app.db.supabase import EngineRun, confluence_shadow_row
from signals_app.scanner import MarketContext, publish_symbol, score_symbol


def _ohlcv(seed: int = 3, n: int = 300) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.012, n)))
    idx = pd.bdate_range(end="2026-09-22", periods=n)
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                         "Close": close, "Volume": 1e6 * (1 + rng.random(n))}, index=idx)


@pytest.fixture(autouse=True)
def _fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    def fetch(self, symbol, period="3mo"):  # noqa: ANN001
        df = _ohlcv()
        return OHLCVResult(symbol.upper(), period, df, from_cache=False, bar_count=len(df))

    monkeypatch.setattr("signals_app.data.fetcher.DataFetcher.fetch", fetch)
    scanner._graded_ranker.cache_clear()


def _score(monkeypatch: pytest.MonkeyPatch, shadow: bool):
    monkeypatch.setattr(scanner, "SHADOW_GRADED", shadow)
    return score_symbol("AAA", "1y", settings=None, market=MarketContext("range", None))


def test_off_by_default_means_no_shadow_work(monkeypatch: pytest.MonkeyPatch) -> None:
    scored = _score(monkeypatch, shadow=False)
    assert scored.graded is None


def test_shadow_does_not_change_the_production_result(monkeypatch: pytest.MonkeyPatch) -> None:
    off = _score(monkeypatch, shadow=False)
    on = _score(monkeypatch, shadow=True)
    assert on.graded is not None and on.graded["ranker_version"] == "graded-1"
    assert on.confluence == off.confluence
    assert [s.signal for s in on.signal_list] == [s.signal for s in off.signal_list]


def test_shadow_payload_carries_the_card_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    graded = _score(monkeypatch, shadow=True).graded
    assert {"score", "action", "events", "states", "proximity", "drivers", "flag_only",
            "risk_context", "location", "evidence_version"} <= set(graded)
    assert graded["regime"] == "range"
    assert graded["unclassified"] == 0


def test_a_failing_shadow_never_breaks_the_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise RuntimeError("evidence file corrupt")

    monkeypatch.setattr(scanner, "_graded_ranker", boom)
    scored = _score(monkeypatch, shadow=True)
    assert scored.graded is None
    assert scored.confluence is not None


class _Writer:
    def __init__(self, with_shadow: bool) -> None:
        self.shadow: list[tuple[str, str, dict]] = []
        self.hits = 0
        if with_shadow:
            self.write_confluence_shadow = lambda t, b, p: self.shadow.append((t, b, p))

    def ensure_symbol(self, ticker: str) -> None: ...

    def write_detector_hits(self, ticker: str, bar_ts: str, signals: list) -> None:
        self.hits += 1


def _publish(monkeypatch: pytest.MonkeyPatch, writer: _Writer) -> None:
    scored = _score(monkeypatch, shadow=True)
    monkeypatch.setattr(scanner, "passes_publication_gate", lambda *a, **k: False)
    publish_symbol(scored, writer, EngineRun(id=1, started_at="x"), settings=None, dry_run=False)


def test_shadow_is_written_for_gated_symbols_too(monkeypatch: pytest.MonkeyPatch) -> None:
    writer = _Writer(with_shadow=True)
    _publish(monkeypatch, writer)
    assert writer.hits == 1
    assert len(writer.shadow) == 1 and writer.shadow[0][0] == "AAA"


def test_a_writer_without_the_method_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    writer = _Writer(with_shadow=False)
    _publish(monkeypatch, writer)
    assert writer.hits == 1


def test_dry_run_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    writer = _Writer(with_shadow=True)
    scored = _score(monkeypatch, shadow=True)
    monkeypatch.setattr(scanner, "passes_publication_gate", lambda *a, **k: False)
    publish_symbol(scored, writer, None, settings=None, dry_run=True)
    assert writer.shadow == [] and writer.hits == 0


def test_shadow_row_shape() -> None:
    payload = {"ranker_version": "graded-1", "score": 0.12, "action": "HOLD", "x": 1}
    row = confluence_shadow_row("AAA", "2026-09-22T00:00:00", payload)
    assert row["ranker_version"] == "graded-1"
    assert (row["score"], row["action"]) == (0.12, "HOLD")
    assert row["payload"] is payload
    assert {"ticker", "bar_ts", "code_version"} <= set(row)


def test_supabase_writer_is_a_no_op_while_the_flag_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supabase, "WRITE_CONFLUENCE_SHADOW", False)
    writer = supabase.SupabaseWriter(url="http://example.invalid", service_role_key="k")
    writer._client.post = lambda *a, **k: pytest.fail("must not post while the flag is off")  # type: ignore[assignment]
    writer.write_confluence_shadow("AAA", "2026-09-22T00:00:00", {"ranker_version": "g", "score": 0, "action": "HOLD"})


def test_supabase_writer_posts_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supabase, "WRITE_CONFLUENCE_SHADOW", True)
    writer = supabase.SupabaseWriter(url="http://example.invalid", service_role_key="k")
    sent: dict = {}

    class _Resp:
        def raise_for_status(self) -> None: ...

    def post(url: str, **kwargs):  # noqa: ANN202
        sent.update(url=url, **kwargs)
        return _Resp()

    writer._client.post = post  # type: ignore[assignment]
    writer.write_confluence_shadow("AAA", "2026-09-22T00:00:00",
                                   {"ranker_version": "graded-1", "score": 0.1, "action": "HOLD"})
    assert sent["url"].startswith("/confluence_shadow?on_conflict=")
    assert sent["json"]["ticker"] == "AAA"


def test_shadow_payload_stores_the_production_call_for_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scored = _score(monkeypatch, shadow=True)
    production = scored.graded["production"]
    assert production == {"score": scored.confluence.score, "action": scored.confluence.action,
                          "bias": scored.confluence.bias}
