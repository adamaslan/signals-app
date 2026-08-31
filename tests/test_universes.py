"""Local universe file I/O + browser JSON interop (design doc §3.5)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from signals_app import universes


@pytest.fixture(autouse=True)
def _tmp_universe_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "universes"
    monkeypatch.setenv("SIGNALS_UNIVERSE_DIR", str(d))
    return d


def test_create_then_load_roundtrips_and_normalizes() -> None:
    u = universes.create_universe("tech", [" aapl ", "MSFT", "aapl", "nvda"])
    assert u.name == "tech"
    assert u.tickers == ["AAPL", "MSFT", "NVDA"]  # upper, stripped, de-duped, order kept
    again = universes.load_universe("tech")
    assert again.tickers == u.tickers
    assert again.path.read_text().splitlines()[0] == "ticker"


def test_create_refuses_to_clobber_without_overwrite() -> None:
    universes.create_universe("x", ["AAPL"])
    with pytest.raises(universes.UniverseExists):
        universes.create_universe("x", ["MSFT"])
    u = universes.create_universe("x", ["MSFT"], overwrite=True)
    assert u.tickers == ["MSFT"]


def test_invalid_name_rejected() -> None:
    for bad in ("a b", "../etc", "x.csv", "", "-lead"):
        with pytest.raises(universes.InvalidUniverseName):
            universes.create_universe(bad, ["AAPL"])


def test_name_is_lowercased_not_rejected() -> None:
    # Mixed case is normalized, not an error.
    u = universes.create_universe("Tech", ["AAPL"])
    assert u.name == "tech"


def test_load_missing_raises_not_found() -> None:
    with pytest.raises(universes.UniverseNotFound):
        universes.load_universe("nope")


def test_list_is_name_sorted_and_empty_when_dir_absent() -> None:
    assert universes.list_universes() == []
    universes.create_universe("zeta", ["A"])
    universes.create_universe("alpha", ["B"])
    assert [u.name for u in universes.list_universes()] == ["alpha", "zeta"]


def test_delete_removes_the_file() -> None:
    universes.create_universe("gone", ["AAPL"])
    universes.delete_universe("gone")
    with pytest.raises(universes.UniverseNotFound):
        universes.load_universe("gone")


def test_export_import_json_roundtrip() -> None:
    u = universes.create_universe("basket", ["AAPL", "MSFT"])
    text = universes.to_export_json(u)
    payload = json.loads(text)
    assert payload["tickers"] == ["AAPL", "MSFT"]
    assert payload["version"] == 1

    name, tickers = universes.from_export_json(text)
    assert name == "basket"
    assert tickers == ["AAPL", "MSFT"]


def test_from_export_json_tolerates_bare_list_and_symbols_key() -> None:
    assert universes.from_export_json('["aapl","msft"]') == ("imported", ["AAPL", "MSFT"])
    assert universes.from_export_json('{"symbols":["nvda"]}') == ("imported", ["NVDA"])


def test_from_export_json_rejects_junk() -> None:
    with pytest.raises(universes.UniverseError):
        universes.from_export_json("{}")
    with pytest.raises(universes.UniverseError):
        universes.from_export_json("not json")
