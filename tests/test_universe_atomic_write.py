"""A failed basket save must not destroy the previous basket."""
import os
import pytest
from signals_app import universes


@pytest.mark.parametrize('existing', [False, True])
def test_failed_csv_write_preserves_destination(tmp_path, monkeypatch, existing):
    monkeypatch.setenv('SIGNALS_UNIVERSE_DIR', str(tmp_path))
    path = tmp_path / 'tech.csv'
    if existing:
        universes.create_universe('tech', ['MSFT'])
    before = path.read_bytes() if existing else None
    real_writer = universes.csv.writer

    class FailingWriter:
        def __init__(self, stream):
            self.writer = real_writer(stream)
        def writerow(self, row):
            self.writer.writerow(row)
            if row == ['AAPL']:
                raise OSError('synthetic disk write failure')

    monkeypatch.setattr(universes.csv, 'writer', FailingWriter)
    with pytest.raises(OSError, match='synthetic disk write failure'):
        universes.create_universe('tech', ['AAPL'], overwrite=True)
    assert (path.read_bytes() if path.exists() else None) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == (['tech.csv'] if existing else [])


def test_replace_failure_preserves_saved_basket_and_cleans_temp(tmp_path, monkeypatch):
    monkeypatch.setenv('SIGNALS_UNIVERSE_DIR', str(tmp_path))
    path = universes.create_universe('tech', ['MSFT']).path
    before = path.read_bytes()
    def fail(*args):
        raise OSError('synthetic replace failure')
    monkeypatch.setattr(os, 'replace', fail)
    with pytest.raises(OSError, match='synthetic replace failure'):
        universes.create_universe('tech', ['AAPL'], overwrite=True)
    assert path.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ['tech.csv']


def test_successful_overwrite_roundtrips_and_cleans_temp(tmp_path, monkeypatch):
    monkeypatch.setenv('SIGNALS_UNIVERSE_DIR', str(tmp_path))
    universes.create_universe('tech', ['MSFT'])
    universes.create_universe('tech', [' aapl ', 'AAPL', 'NVDA'], overwrite=True)
    assert universes.load_universe('tech').tickers == ['AAPL', 'NVDA']
    assert [p.name for p in tmp_path.iterdir()] == ['tech.csv']


def test_concurrent_create_never_clobbers_other_basket(tmp_path, monkeypatch):
    monkeypatch.setenv('SIGNALS_UNIVERSE_DIR', str(tmp_path))
    path = tmp_path / 'tech.csv'
    other = b'ticker\r\nMSFT\r\n'
    real_link = os.link
    def competing_create(source, destination):
        path.write_bytes(other)
        return real_link(source, destination)
    monkeypatch.setattr(os, 'link', competing_create)
    with pytest.raises(universes.UniverseExists):
        universes.create_universe('tech', ['AAPL'])
    assert path.read_bytes() == other
    assert [p.name for p in tmp_path.iterdir()] == ['tech.csv']
