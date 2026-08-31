"""CLI exit-code contract + --json purity (design doc §3.3, §8).

Runs the installed ``signals`` binary as a subprocess so we exercise the real
argument parsing, the real exit codes, and the real stdout/stderr split. The
network is neutralized by pointing every fetch at a symbol that yfinance
rejects (exit 3) or by using flags that never touch the network.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_BOGUS = "ZZ_NOT_A_REAL_TICKER_ZZ"


def _run(*args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "signals_app.cli.main", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


# --- exit codes (§3.3) -----------------------------------------------------


def test_exit_2_on_bad_period() -> None:
    r = _run("analyze", "AAPL", "--period", "not-a-period", "--no-llm")
    assert r.returncode == 2


def test_exit_2_on_unknown_preset() -> None:
    r = _run("scan", "AAPL", "--preset", "no-such-preset", "--dry-run")
    assert r.returncode == 2


def test_exit_3_on_unknown_symbol() -> None:
    r = _run("analyze", _BOGUS, "--no-llm")
    assert r.returncode == 3


def test_exit_3_on_missing_universe() -> None:
    r = _run("universe", "show", "definitely-not-a-universe")
    assert r.returncode == 3


def test_exit_0_on_estimate() -> None:
    # --estimate never calls the network / LLM.
    r = _run("analyze", "AAPL", "MSFT", "NVDA", "--llm", "--estimate")
    assert r.returncode == 0
    assert "3 LLM calls" in r.stdout


def test_exit_0_on_detectors_json() -> None:
    r = _run("detectors", "--json")
    assert r.returncode == 0


# --- --json purity (§3.3) -----------------------------------------------


def test_detectors_json_is_pure_stdout_even_with_verbose() -> None:
    r = _run("detectors", "--json", "--verbose")
    assert r.returncode == 0
    # stdout parses cleanly as JSON …
    payload = json.loads(r.stdout)
    assert isinstance(payload, list) and payload
    # … and the logs really went to stderr
    assert "DEBUG" in r.stderr or "INFO" in r.stderr or r.stderr == ""


def test_universe_show_json_is_pure(tmp_path: Path) -> None:
    d = tmp_path / "u"
    env = {"SIGNALS_UNIVERSE_DIR": str(d)}
    assert _run("universe", "create", "x", "AAPL", "MSFT", env_extra=env).returncode == 0
    r = _run("universe", "show", "x", "--json", env_extra=env)
    assert r.returncode == 0
    assert json.loads(r.stdout) == {"name": "x", "tickers": ["AAPL", "MSFT"]}


def test_help_lists_every_command() -> None:
    r = _run("--help")
    for cmd in (
        "analyze",
        "backtest",
        "history",
        "detectors",
        "health",
        "scan",
        "serve",
        "mcp",
        "universe",
    ):
        assert cmd in r.stdout
