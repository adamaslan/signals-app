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
