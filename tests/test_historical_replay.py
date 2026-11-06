import json
import pandas as pd
import pytest
from signals_app.historical_replay import read_bars, replay, run

def bars(n=215):
    import numpy as np
    c=100+np.arange(n)*.08+np.sin(np.arange(n)/5)
    return pd.DataFrame({'Open':c-.1,'High':c+1,'Low':c-1,'Close':c,'Volume':1000000},index=pd.bdate_range('2024-01-01',periods=n))

def test_prefix_and_future_mutation():
    f=bars();start=f.index[205].date().isoformat();end=f.index[209].date().isoformat()
    expected=replay(f.loc[:end],start,end,[1,5,20])
    mutated=f.copy();mutated.loc[mutated.index>pd.Timestamp(end),'Close']*=10
    assert replay(mutated,start,end,[1,5,20])==expected
    last_only=replay(f,f.index[209].date().isoformat(),end,[1])
    assert last_only['decisions'][0]==expected['decisions'][-1]
    assert any(b['pending'] for b in expected['buckets'])
    assert all(b['hit_rate'] is None for b in expected['buckets'])

def test_no_early_warmup():
    f=bars();assert replay(f,'2024-01-01','2024-10-01',[5])['status']=='insufficient_warmup'

def test_missing_ledger_and_duplicate_seed(tmp_path):
    seed=tmp_path/'seed.csv';seed.write_text('ticker\nAAA\nBBB\n');d=tmp_path/'bars';d.mkdir()
    (d/'metadata.json').write_text(json.dumps(dict(source='fixture',adjustment='fixture',retrieved_at='fixture',calendar_policy='fixture')))
    r=run(seed,d,tmp_path/'out','2024-01-01','2024-12-31',[1]);assert len(r['ledger'])==2
    assert all(x['status']=='missing_history' for x in r['ledger'])
    seed.write_text('ticker\nAAA\nAAA\n')
    with pytest.raises(ValueError,match='duplicate'):run(seed,d,tmp_path/'out','2024-01-01','2024-12-31',[1])

def test_bad_input(tmp_path):
    f=bars(4);f.index.name='Date';p=tmp_path/'bad.csv';f.iloc[[0,0,1]].to_csv(p)
    with pytest.raises(ValueError,match='duplicate'):read_bars(p,'2025-01-01')
    f.iloc[::-1].to_csv(p)
    with pytest.raises(ValueError,match='increasing'):read_bars(p,'2025-01-01')

def test_known_labels_flat_and_pending(monkeypatch):
    from signals_app.detection.base import MutableSignal
    class Stub:
        def detect(self, frame):
            return [MutableSignal(signal='fixture',description='fixture',strength=s,category='TEST') for s in ('BULLISH','BEARISH')]
    monkeypatch.setattr('signals_app.detection.orchestrator.get_default_detectors',lambda:[Stub()])
    f=bars(205);f.loc[f.index[201],'Close']=f.Close.iloc[200]+1
    f.loc[f.index[202],'Close']=f.Close.iloc[201]
    start=f.index[200].date().isoformat();end=f.index[202].date().isoformat()
    result=replay(f,start,end,[1,5])
    b={(x['strength'],x['horizon_bars']):x for x in result['buckets']}
    assert b['BULLISH',1]['hits']==1
    assert b['BULLISH',1]['flat']==1
    assert b['BEARISH',1]['misses']==1
    assert b['BEARISH',1]['flat']==1
    assert b['BULLISH',1]['resolved_directional']==2
    assert b['BULLISH',1]['pending']==1
    assert b['BULLISH',5]['pending']==3
    assert b['BULLISH',5]['resolved_directional']==0

def test_actual_pilot_prefix_matches_each_snapshot():
    # Real input is an optional local integration test, never a downloaded CI dependency.
    from pathlib import Path
    p=Path('/downloads/followed-year-validation/bars/AAPL.csv')
    if not p.exists():pytest.skip('local pilot unavailable')
    f=read_bars(p,'2026-11-05');start='2026-10-29';end='2026-11-05'
    full=replay(f,start,end,[1,5])
    for decision in full['decisions']:
        truncated=replay(f.loc[:decision['date']],decision['date'],decision['date'],[1,5])
        assert truncated['decisions'][0]==decision


def test_detector_error_is_not_silent(monkeypatch):
    class Broken:
        def detect(self, frame):raise RuntimeError('fixture error')
    monkeypatch.setattr('signals_app.detection.orchestrator.get_default_detectors',lambda:[Broken()])
    f=bars();start=f.index[205].date().isoformat();end=f.index[209].date().isoformat()
    r=replay(f,start,end,[1]);assert r['status']=='degraded'
    assert len(r['decisions'])==5
    assert all(d['detector_errors']==[{'detector':'Broken','error':'RuntimeError'}] for d in r['decisions'])


def test_fund_marked_unsupported_not_silently_dropped(tmp_path):
    seed=tmp_path/'seed.csv';seed.write_text('ticker,asset_type\nAAA,Fund\nBBB,Equity\n')
    d=tmp_path/'bars';d.mkdir()
    (d/'metadata.json').write_text(json.dumps(dict(source='fixture',adjustment='fixture',retrieved_at='fixture',calendar_policy='fixture')))
    f=bars();f.index.name='Date';f.to_csv(d/'AAA.csv');f.to_csv(d/'BBB.csv')
    start=f.index[205].date().isoformat();end=f.index[209].date().isoformat()
    r=run(seed,d,tmp_path/'out',start,end,[1])
    by={x['ticker']:x for x in r['ledger']}
    assert by['AAA']['status']=='unsupported_asset_type'
    assert by['AAA']['calendar_coverage']=='complete'
    assert 'once-daily NAV' in by['AAA']['signal_suitability']
    assert by['BBB']['status']=='ok'
    assert by['BBB']['signal_suitability']=='ohlcv_detectors_applicable'
    detail=json.loads((tmp_path/'out'/'AAA.json').read_text())
    assert len(detail['decisions'])==5  # research detail retained, only pooling excluded


def test_fund_excluded_from_pooled_aggregates(monkeypatch, tmp_path):
    from signals_app.detection.base import MutableSignal
    class Stub:
        def detect(self, frame):
            return [MutableSignal(signal='fixture',description='fixture',strength='BULLISH',category='TEST')]
    monkeypatch.setattr('signals_app.detection.orchestrator.get_default_detectors',lambda:[Stub()])
    seed=tmp_path/'seed.csv';seed.write_text('ticker,asset_type\nAAA,Fund\nBBB,Equity\n')
    d=tmp_path/'bars';d.mkdir()
    (d/'metadata.json').write_text(json.dumps(dict(source='fixture',adjustment='fixture',retrieved_at='fixture',calendar_policy='fixture')))
    f=bars();f.index.name='Date';f.to_csv(d/'AAA.csv');f.to_csv(d/'BBB.csv')
    start=f.index[205].date().isoformat();end=f.index[209].date().isoformat()
    r=run(seed,d,tmp_path/'out',start,end,[1])
    pooled=r['pooled_category_strength_horizon']
    total=sum(b['hits']+b['misses']+b['flat']+b['pending'] for b in pooled)
    assert total==5  # BBB's 5 decisions only; the Fund's 5 identical signals contribute nothing


def test_unknown_asset_type_not_signal_eligible(tmp_path):
    seed=tmp_path/'seed.csv';seed.write_text('ticker\nAAA\n')
    d=tmp_path/'bars';d.mkdir()
    (d/'metadata.json').write_text(json.dumps(dict(source='fixture',adjustment='fixture',retrieved_at='fixture',calendar_policy='fixture')))
    f=bars();f.index.name='Date';f.to_csv(d/'AAA.csv')
    start=f.index[205].date().isoformat();end=f.index[209].date().isoformat()
    r=run(seed,d,tmp_path/'out',start,end,[1])
    rec=r['ledger'][0]
    assert rec['status']=='unsupported_asset_type'
    assert 'unverified' in rec['signal_suitability']


def test_mixed_input_basis_flagged(tmp_path):
    seed=tmp_path/'seed.csv';seed.write_text('ticker,asset_type\nAAA,Crypto\nBBB,Equity\n')
    d=tmp_path/'bars';d.mkdir()
    meta=dict(source='yahoo-pilot',adjustment='adjusted',retrieved_at='fixture',calendar_policy='fixture',
              fetch_ledger=[{"ticker":"AAA","source":"alpaca-us-crypto","adjustment":"none"}])
    (d/'metadata.json').write_text(json.dumps(meta))
    f=bars();f.index.name='Date';f.to_csv(d/'AAA.csv');f.to_csv(d/'BBB.csv')
    start=f.index[205].date().isoformat();end=f.index[209].date().isoformat()
    r=run(seed,d,tmp_path/'out',start,end,[1])
    assert r['mixed_input_basis'] is True
    assert {(b['source'],b['adjustment']) for b in r['distinct_input_bases']}=={('yahoo-pilot','adjusted'),('alpaca-us-crypto','none')}
    assert 'descriptive only' in r['basis_policy']
    by={x['ticker']:x for x in r['ledger']}
    assert by['AAA']['input_basis']=={"source":"alpaca-us-crypto","adjustment":"none"}
    assert 'input_basis' not in by['BBB']
    ids={b['id'] for b in r['distinct_input_bases']}
    assert ids=={by['AAA']['basis_id'],by['BBB']['basis_id']} and len(ids)==2
    (d/'metadata.json').write_text(json.dumps(dict(source='yahoo-pilot',adjustment='adjusted',retrieved_at='fixture',calendar_policy='fixture')))
    r2=run(seed,d,tmp_path/'out2',start,end,[1])
    assert r2['mixed_input_basis'] is False
    assert r2['basis_policy']=='single declared price basis'


def test_pooled_stratified_by_basis_never_combined(monkeypatch, tmp_path):
    from signals_app.detection.base import MutableSignal
    class Stub:
        def detect(self, frame):
            return [MutableSignal(signal='fixture',description='fixture',strength='BULLISH',category='TEST')]
    monkeypatch.setattr('signals_app.detection.orchestrator.get_default_detectors',lambda:[Stub()])
    seed=tmp_path/'seed.csv';seed.write_text('ticker,asset_type\nAAA,Crypto\nBBB,Equity\n')
    d=tmp_path/'bars';d.mkdir()
    meta=dict(source='yahoo-pilot',adjustment='adjusted',retrieved_at='fixture',calendar_policy='fixture',
              fetch_ledger=[{"ticker":"AAA","source":"alpaca-us-crypto","adjustment":"none"}])
    (d/'metadata.json').write_text(json.dumps(meta))
    f=bars();f.index.name='Date';f.to_csv(d/'AAA.csv');f.to_csv(d/'BBB.csv')
    start=f.index[205].date().isoformat();end=f.index[209].date().isoformat()
    r=run(seed,d,tmp_path/'out',start,end,[1])
    pooled=r['pooled_category_strength_horizon']
    assert len(pooled)==2  # identical signals, but one bucket per basis, never combined
    for b in pooled:
        assert b['hits']+b['misses']+b['flat']+b['pending']==5
    assert {b['basis'] for b in pooled}=={x['id'] for x in r['distinct_input_bases']}
    single=json.loads((tmp_path/'out'/'BBB.json').read_text())
    assert single['basis_id'] in {b['basis'] for b in pooled}
