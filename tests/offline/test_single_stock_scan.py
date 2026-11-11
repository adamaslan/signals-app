"""Network-free broad scan validation, no provider or DB imports."""
from datetime import date
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest
from signals_app.single_stock_scan import scan_single_stock, read_local_bars


def frame(n=258):
    idx=pd.bdate_range('2025-01-01',periods=n)
    x=np.arange(n,dtype=float)
    close=100+x*.05+np.sin(x/5)*3
    return pd.DataFrame({'Open':close-.1,'High':close+1,'Low':close-1,'Close':close,'Volume':1000+x*3},index=idx)


def run(f=None,**kwargs):
    f=frame() if f is None else f
    args=dict(symbol='TEST',start=f.index[252].date().isoformat(),end=f.index[-1].date().isoformat(),source='synthetic fixture',price_basis='split-adjusted-ohlc',today=date(2026,11,11))
    args.update(kwargs)
    return scan_single_stock(f,**args)


class State:
    def detect(self,f):
        return [SimpleNamespace(signal='STATE',category='trend',strength='NEUTRAL',description='context')]


class Broken:
    def detect(self,f):
        raise RuntimeError('synthetic error')


def test_real19_detectors_execute_without_provider():
    r=run()
    assert r['detector_count']==19 and r['detector_evaluations']==114
    assert r['status']=='completed' and len(r['observations'])>0
    assert all(x['status']=='ok'for x in r['coverage'])


def test_neutral_retained_state_repetition_not_new_events():
    r=run(detectors=[State()])
    assert len(r['observations'])==6 and r['new_state_count']==0
    assert all(x['strength']=='NEUTRAL'for x in r['observations'])
    assert r['nested_windows']['21']['bars_observed']==6


def test_failure_not_silent_zero_and_recovery_not_new_state():
    r=run(detectors=[Broken()])
    assert r['status']=='degraded' and r['failed_evaluations']==6
    class Sometimes:
        def detect(self,f):
            if len(f)==254:raise RuntimeError('missing')
            return State().detect(f)
    r=run(detectors=[Sometimes()]);assert r['new_state_count']==0


def test_prefix_decisions_do_not_depend_on_mutated_future():
    f=frame(260);cut=f.index[256].date().isoformat();changed=f.copy()
    changed.loc[changed.index>cut,['Open','High','Low','Close']]*=10
    a=run(f,end=cut);b=run(changed,end=cut)
    assert a==b


def test_unknown_basis_future_range_short_history_rejected():
    with pytest.raises(ValueError):run(price_basis='unknown')
    with pytest.raises(ValueError):run(end='2099-01-01')
    with pytest.raises(ValueError):run(start=frame().index[200].date().isoformat())


def test_invalid_prices_dates_and_symbol_rejected():
    f=frame();f.iloc[15,f.columns.get_loc('Close')]=0
    with pytest.raises(ValueError):run(f)
    with pytest.raises(ValueError):run(symbol='AAPL,MSFT')
    with pytest.raises(ValueError):run(frame().iloc[::-1])
    with pytest.raises(ValueError):run(detectors=[State(),State()])


def test_strict_csv_and_no_future_price_validation_leak(tmp_path):
    f=frame(260);path=tmp_path/'bars.csv';f.index.name='Date';f.to_csv(path)
    got=read_local_bars(path,f.index[256].date().isoformat());assert len(got)==257
    f.iloc[-1,f.columns.get_loc('Close')]=float('inf');f.to_csv(path)
    got=read_local_bars(path,f.index[256].date().isoformat());assert len(got)==257
    path.write_text('Date,Open,High,Low,Close,Volume\n2026-02-30,1,2,1,2,3\n')
    with pytest.raises(ValueError):read_local_bars(path,'2026-11-10')


def test_nested_windows_report_overlap_not_total():
    r=run(frame(280),detectors=[State()]);assert len(r['observations'])==28
    assert r['nested_windows']['21']['observations']==21
    assert r['nested_windows']['63']['observations']==28
    assert r['nested_windows']['172']['observations']==28


def test_failed_detector_cannot_leave_partial_observations_or_phantom_states():
    class Partial:
        def detect(self,f):
            yield SimpleNamespace(signal='PARTIAL',category='trend',strength='BULLISH',description='uncommitted')
            raise RuntimeError('failed during iteration')
    r=run(detectors=[Partial()])
    assert r['status']=='degraded' and r['failed_evaluations']==6
    assert r['observations']==[] and r['new_state_count']==0
    assert all(x['observations']==0 for x in r['coverage'])


def test_direct_api_rejects_nonmidnight_daily_bars():
    f=frame();f.index=f.index+pd.Timedelta(hours=12)
    with pytest.raises(ValueError,match='completed daily'):
        run(f)
