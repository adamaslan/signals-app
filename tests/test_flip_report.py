"""scripts/flip_report.py: the diff and the forward-return hit rates it prints."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

import flip_report as fr  # noqa: E402


def _row(action: str, score: float, fwd: float | None) -> dict:
    return {"action": action, "score": score, "fwd": fwd}


def test_hit_rates_count_only_bars_with_a_realised_return(capsys: pytest.CaptureFixture[str]) -> None:
    old = {"a": _row("BUY", 0.4, 0.1), "b": _row("BUY", 0.4, -0.1), "c": _row("SELL", -0.4, -0.2),
           "d": _row("BUY", 0.4, None)}
    new = {"a": _row("BUY", 0.4, 0.1), "b": _row("HOLD", 0.1, -0.1), "c": _row("SELL", -0.4, -0.2),
           "d": _row("BUY", 0.4, None)}
    fr.print_hit_rates(old, new, sorted(old))
    out = capsys.readouterr().out
    assert "forward-return bars: 3" in out
    assert "BUY  old: n=    2  hit-rate=0.500" in out
    assert "BUY  new: n=    1  hit-rate=1.000" in out
    assert "SELL old: n=    1  hit-rate=1.000" in out


def test_diff_reports_flips_and_scores(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import json

    (tmp_path / "old.json").write_text(json.dumps({"rows": {"A@1": _row("BUY", 0.4, 0.1)}}))
    (tmp_path / "new.json").write_text(json.dumps({"rows": {"A@1": _row("HOLD", 0.2, 0.1)}}))
    assert fr.diff(str(tmp_path / "old.json"), str(tmp_path / "new.json")) == 0
    out = capsys.readouterr().out
    assert "action flips: 1" in out and "BUY -> HOLD 1" in out and "A@1: BUY -> HOLD" in out


def test_dump_stores_the_forward_return_of_the_bar_the_signals_describe(tmp_path: Path) -> None:
    import json

    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(0)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, 300)))
    ohlcv = pd.DataFrame({"Open": close, "High": close * 1.005, "Low": close * 0.995, "Close": close,
                          "Volume": 1e6}, index=pd.date_range("2024-01-01", periods=300, freq="B"))
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    ohlcv.to_csv(csv_dir / "AAA.csv")
    out = tmp_path / "dump.json"
    fr.dump(str(out), str(csv_dir), 0)
    rows = json.loads(out.read_text())["rows"]

    end = fr.WARMUP_BARS
    last = end - 1  # the signals at "AAA@210" describe bar 209
    assert rows[f"AAA@{end}"]["fwd"] == pytest.approx(close[last + fr.HORIZON] / close[last] - 1.0)
    final_end = max(int(k.split("@")[1]) for k in rows)
    assert rows[f"AAA@{final_end}"]["fwd"] is None  # no realised return for the newest bars
