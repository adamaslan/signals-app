"""Reject malformed universe JSON instead of silently inventing symbols."""
import json
import pytest
from signals_app.universes import UniverseError, from_export_json


@pytest.mark.parametrize('payload', [
    {'tickers': 'AAPL'}, {'tickers': {'AAPL': 1}}, {'tickers': 42},
    {'tickers': ['AAPL', None]}, {'tickers': ['AAPL', 3]},
    {'tickers': ['AAPL', {'ticker': 'MSFT'}]},
    {'tickers': ['AAPL', ['MSFT']]}, {'tickers': ['AAPL', True]},
    {'tickers': [], 'symbols': ['MSFT']}, {'tickers': [' ', '']},
    [None, 'AAPL'], [],
])
def test_rejects_malformed_baskets(payload):
    with pytest.raises(UniverseError):
        from_export_json(json.dumps(payload))


@pytest.mark.parametrize('payload,expected', [
    ({'name': 'tech', 'tickers': [' aapl ', 'MSFT', 'aapl']},
     ('tech', ['AAPL', 'MSFT'])),
    ({'symbols': ['brk-b', '^gspc', 'BTC-USD']},
     ('imported', ['BRK-B', '^GSPC', 'BTC-USD'])),
    (['nvda', '', 'NVDA'], ('imported', ['NVDA'])),
])
def test_retains_supported_shapes_and_normalization(payload, expected):
    assert from_export_json(json.dumps(payload)) == expected


def test_cli_rejects_invalid_import_without_overwriting(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from signals_app.cli.main import app
    from signals_app import universes

    monkeypatch.setenv('SIGNALS_UNIVERSE_DIR', str(tmp_path / 'universes'))
    existing = universes.create_universe('tech', ['MSFT'])
    before = existing.path.read_bytes()
    incoming = tmp_path / 'invalid.json'
    incoming.write_text(json.dumps({'name': 'tech', 'tickers': 'AAPL'}))
    result = CliRunner().invoke(app, ['universe', 'import', str(incoming), '--force'])
    assert result.exit_code == 2
    assert 'list of strings' in result.output
    assert existing.path.read_bytes() == before
