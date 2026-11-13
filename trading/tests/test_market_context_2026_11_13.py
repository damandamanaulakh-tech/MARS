"""SEQ-06: exact windows, sourced identities and honest unknown context."""
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import pytest

NOW = datetime(2026, 11, 13, 6, 0, tzinfo=timezone.utc)


def daily(values=(100, 101, 103), dates=('2026-11-09', '2026-11-11', '2026-11-12')):
    f = pd.DataFrame({'Close': values}, index=pd.to_datetime(dates))
    f.attrs.update(interval='1d', source='fixture', price_basis='unadjusted')
    return f


def identity():
    return {'status': 'verified', 'sector_status': 'verified', 'sector_index': 'IT',
            'industry': 'Information Technology', 'observed_at': NOW.isoformat()}


def test_exact_window_and_relative_percentage_points():
    from trading.market_context import compare
    c = compare('TCS.NS', daily(), daily((200, 202, 204)), daily((100, 101, 101)),
                identity(), NOW, lookback=2)
    assert c['status'] == 'ready'
    assert c['stock']['return_pct'] == pytest.approx(3)
    assert c['sector']['return_pct'] == pytest.approx(2)
    assert c['stock_vs_market_pp'] == pytest.approx(2)
    assert c['window']['end'] == '2026-11-12T10:00:00+00:00'
    assert c['role'] == 'context_only'


def test_missing_sector_does_not_become_market_or_zero():
    from trading.market_context import compare
    c = compare('UNMAPPED.NS', daily(), None, daily(),
                {'status':'unknown', 'reason':'not_in_verified_sources'}, NOW, lookback=2)
    assert c['sector']['return_pct'] is None
    assert c['stock_vs_sector_pp'] is None
    assert c['stock_vs_market_pp'] == pytest.approx(0)
    assert c['status'] == 'partial'


def test_stale_market_is_not_aligned_by_trimming_stock():
    from trading.market_context import compare
    market = daily(dates=('2026-11-06', '2026-11-09', '2026-11-11'))
    c = compare('TCS.NS', daily(), daily(), market, identity(), NOW, lookback=2)
    assert c['market']['status'] == 'unknown'
    assert c['market']['return_pct'] is None
    assert c['window']['end'].startswith('2026-11-12')


def test_unverified_config_label_cannot_establish_sector_identity():
    from trading.sector_identity import resolve
    got = resolve('TCS.NS', {}, NOW, configured_label='IT')
    assert got['status'] == 'unknown'
    assert got['sector_index'] is None
    assert got['configured_label'] == 'IT'


@pytest.mark.parametrize('mutation,reason', [
    ('future','future_or_unfinished_bar'), ('unfinished','future_or_unfinished_bar'),
    ('holiday','non_trading_session'), ('weekend','non_trading_session'),
    ('bad_timestamp','invalid_row'), ('nan','invalid_close'), ('negative','invalid_close'),
    ('inf','invalid_close'), ('mislabel','unexpected_daily_timestamp'),
    ('conflict','conflicting_duplicates')])
def test_rejected_daily_rows_are_counted_and_never_change_completed_return(mutation, reason):
    from trading.market_context import compare
    f=daily()
    extra=f.tail(1).copy()
    if mutation=='future': extra.index=pd.to_datetime(['2026-11-16'])
    elif mutation=='unfinished': extra.index=pd.to_datetime(['2026-11-13'])
    elif mutation=='holiday': extra.index=pd.to_datetime(['2026-11-10'])
    elif mutation=='weekend': extra.index=pd.to_datetime(['2026-11-08'])
    elif mutation=='bad_timestamp': extra.index=['garbage']
    elif mutation=='mislabel': extra.index=pd.to_datetime(['2026-11-12T10:00:00'])
    else: extra['Close']={'nan':np.nan,'negative':-1,'inf':np.inf,'conflict':900}[mutation]
    f=pd.concat([f,extra]);f.attrs.update(interval='1d',source='fixture')
    c=compare('TCS.NS',f,daily(),daily(),identity(),NOW,lookback=2)
    assert c['stock']['rejected'][reason]==1
    if mutation=='conflict':
        assert c['stock']['return_pct'] is None and c['stock_vs_market_pp'] is None
    else: assert c['stock']['return_pct']==pytest.approx(3)


@pytest.mark.parametrize('mutation', ['missing_middle','stale','wrong_interval','no_interval','short','unverified_year'])
def test_missing_or_unproved_windows_stay_unknown(mutation):
    from trading.market_context import compare
    stock=daily();now=NOW
    if mutation=='missing_middle': stock=stock.drop(stock.index[1])
    elif mutation=='stale': stock=stock.iloc[:-1]
    elif mutation=='wrong_interval': stock.attrs['interval']='30m'
    elif mutation=='no_interval': stock.attrs.clear()
    elif mutation=='short': stock=stock.tail(1)
    elif mutation=='unverified_year': now=NOW.replace(year=2027)
    c=compare('TCS.NS',stock,daily(),daily(),identity(),now,lookback=2)
    assert c['stock']['return_pct'] is None and c['stock_vs_sector_pp'] is None


def test_intraday_uses_completed_end_times_and_never_daily_sector_substitute():
    from trading.market_context import compare
    from trading.tests.test_completed_intraday_2026_11_12 import bar_frame
    f=bar_frame(now=NOW)
    c=compare('TCS.NS',f,f,f,identity(),NOW,lookback=21,interval='5m')
    assert c['status']=='ready' and c['window']['end']==NOW.isoformat()
    changed=compare('TCS.NS',f,daily(),f,identity(),NOW,lookback=21,interval='5m')
    assert changed['sector']['reason']=='wrong_or_unproved_interval'
    assert changed['stock_vs_sector_pp'] is None
    assert changed['stock_vs_market_pp']==pytest.approx(0)


def test_intraday_missing_middle_does_not_compare_shortened_horizon():
    from trading.market_context import compare
    from trading.tests.test_completed_intraday_2026_11_12 import bar_frame
    f=bar_frame(now=NOW);broken=f.drop(f.index[-10])
    c=compare('TCS.NS',broken,f,f,identity(),NOW,lookback=21,interval='5m')
    assert c['stock']['return_pct'] is None and c['stock_vs_market_pp'] is None


def test_zero_is_measured_flat_missing_is_not_flat_and_adjusted_basis_is_explicit():
    from trading.market_context import compare
    f=daily((100,100,100));c=compare('TCS.NS',f,f,f,identity(),NOW,lookback=2)
    assert c['stock']['return_pct']==0 and c['agreement_sector'] is None
    f.attrs['price_basis']='auto_adjusted'
    c=compare('TCS.NS',f,daily(),daily(),identity(),NOW,lookback=2)
    assert c['stock']['return_pct']==0 and c['stock_vs_market_pp'] is None
    assert c['relative_basis']=='incompatible_auto_adjusted_series'
    assert c['status']=='partial'


def test_foreign_market_or_unverified_calendar_cannot_claim_same_nse_session():
    from trading.market_context import compare
    c=compare('TCS.NS',daily(),daily(),daily(),identity(),NOW,lookback=2,market_ticker='^GSPC')
    assert c['market']['reason']=='market_session_calendar_unverified'
    assert c['stock_vs_market_pp'] is None


def test_calendar_boundary_never_shortens_requested_horizon_for_any_leg():
    from trading.market_context import compare
    f=daily((100,101),('2026-01-01','2026-01-02'))
    c=compare('TCS.NS',f,f,f,identity(),NOW.replace(month=1,day=3),lookback=21)
    assert all(c[name]['return_pct'] is None for name in ('stock','sector','market'))
    assert c['sector_vs_market_pp'] is None


def source_snapshot(names=('IT',), *, at=None):
    at=at or NOW.isoformat()
    return {'schema':1,'sources':{name:{'rows':{'AAA.NS':{'company':'AAA','industry':'IT','isin':'INE000A01010'}},
            'observed_at':at, 'url':'https://archives.nseindia.com/fixture.csv','sha256':'a'*64} for name in names}}


@pytest.mark.parametrize('case,reason', [('stale','source_stale'),('future','source_observed_in_future'),
    ('no_date','source_time_unavailable'),('isin','instrument_isin_conflict'),('conflict','conflicting_official_identity')])
def test_identity_uncertainty_never_becomes_verified_sector(case,reason):
    from trading.sector_identity import resolve
    d=source_snapshot();kwargs={}
    if case=='stale': d['sources']['IT']['observed_at']='2026-11-01T06:00:00+00:00'
    elif case=='future': d['sources']['IT']['observed_at']='2026-11-14T06:00:00+00:00'
    elif case=='no_date': d['sources']['IT']['observed_at']=None
    elif case=='isin': kwargs['instrument_isin']='INE999A01010'
    else:
        d=source_snapshot(('IT','Auto'));d['sources']['Auto']['rows']['AAA.NS']['industry']='Auto'
    r=resolve('AAA.NS',d,NOW,**kwargs)
    assert r['status']=='unknown' and r['sector_index'] is None and r['reason']==reason


@pytest.mark.parametrize('names,chosen', [(('Bank','Fin Service'),'Bank'),(('Pharma','Healthcare'),'Pharma'),
    (('IT','Auto'),None),(('Total Market',),None)])
def test_membership_priority_is_explicit_and_ambiguous_basket_is_unknown(names,chosen):
    from trading.sector_identity import resolve
    r=resolve('AAA.NS',source_snapshot(names),NOW)
    assert r['status']=='verified' and r['sector_index']==chosen
    assert (r['sector_status']=='verified') == bool(chosen)


def test_real_sources_validate_hashes_and_dummy_rows_are_excluded():
    import json,hashlib
    from pathlib import Path
    from trading import sector_identity as si
    data=json.loads(si.SEED.read_text())
    folder=si.SEED.parents[1]/'docs/repairs/evidence/2026-11-13-sector-sources'
    for name,src in data['sources'].items():
        raw=(folder/si.FILES[name]).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==src['sha256']
        parsed=si.parse(raw,name,datetime.fromisoformat(src['observed_at']))
        assert parsed==src
    total=data['sources']['Total Market']
    assert len(total['excluded'])==5 and 'DUMMYHEG.NS' not in total['rows']
    assert total['rows']['HFCL.NS']['series']=='BE'


@pytest.mark.parametrize('raw',[b'<html>Error</html>', b'Company Name,Industry,Symbol,Series,ISIN Code\n', b'x'*2_000_001])
def test_bad_or_partial_constituent_file_is_not_published(raw):
    from trading.sector_identity import parse
    with pytest.raises(ValueError): parse(raw,'IT',NOW)


def test_independent_source_failure_retains_original_observation_and_retries(monkeypatch):
    from trading import sector_identity as si
    from copy import deepcopy
    previous=source_snapshot();before=deepcopy(previous)
    monkeypatch.setattr(si,'URLS',{'IT':'it','Bank':'bank'})
    calls=[]
    def fail(url): calls.append(url);raise RuntimeError('offline')
    failed=si.refresh(previous,NOW,fail)
    assert previous==before and failed['sources']==previous['sources']
    assert failed['attempts']['Bank']['status']=='unavailable'
    si.refresh(failed,NOW,fail)
    assert calls.count('it')==2 and calls.count('bank')==2


def test_context_failures_do_not_gate_trading(monkeypatch):
    from trading import context_inputs as ci
    from trading.config import Config
    cfg=Config()
    monkeypatch.setattr(ci,'identities',lambda *a,**k: (_ for _ in ()).throw(RuntimeError('store failed')))
    inputs=ci.prepare(['AAA.NS'],cfg,NOW)
    assert inputs['error']=='RuntimeError'
    c=ci.observation(inputs,'AAA.NS',daily(),daily(),cfg,NOW)
    assert c['role']=='context_only' and c['sector']['return_pct'] is None
    assert not any(k in c for k in ('buy_allowed','sell','gate','score_adjustment'))


def danger_fixture(monkeypatch):
    from trading import market_context as mc, sector_identity as si, external_panels as ep, data, data_refresh
    dates=mc.daily_grid(NOW,65).tz_convert('Asia/Kolkata').tz_localize(None).normalize()
    def frame(end):
        values=np.full(len(dates),100.);values[-1]=end
        f=pd.DataFrame({'Open':values,'High':values+1,'Low':values-1,'Close':values,'Volume':1000.},index=dates)
        f.attrs.update(interval='1d',source='fixture')
        return f
    own,peer=frame(105),frame(90)
    monkeypatch.setattr(ep,'_sector_map',lambda:{'AAA.NS':'IT','BBB.NS':'IT'})
    snap=source_snapshot();snap['sources']['IT']['rows']['BBB.NS']={'company':'BBB','industry':'IT','isin':'INE000B01010'}
    monkeypatch.setattr(si,'snapshot',lambda:snap)
    monkeypatch.setattr(data,'history_store',lambda:None)
    monkeypatch.setattr(data_refresh,'stored_index_rows',lambda:[{'date':d.date().isoformat(),'nifty_close':100-i} for i,d in enumerate(dates)])
    return own,peer


def test_live_danger_reads_current_aligned_basket_without_bundled_fallback(monkeypatch):
    from trading import danger,external_panels as ep
    own,peer=danger_fixture(monkeypatch)
    monkeypatch.setattr(ep,'panel_set',lambda *a,**k: (_ for _ in ()).throw(AssertionError('old panel used')))
    x=danger.externals_for(['AAA.NS'],{'AAA.NS':own,'BBB.NS':peer},NOW)['AAA.NS']
    assert x.asof==x.index_asof=='2026-11-12'
    assert x.peers==1 and x.peer_break_20d_low==1 and len(x.index_close)==21
    assert danger._EXT_FNS['T12W_0094'](danger._Ctx(own),x,{}, {}) is True
    assert x.context['covered_peers']==x.context['expected_peers']==1


@pytest.mark.parametrize('missing',['peer','identity','index','target'])
def test_live_danger_missing_inputs_abstain_instead_of_false_quiet_or_fire(monkeypatch,missing):
    from trading import danger, sector_identity as si,data_refresh
    own,peer=danger_fixture(monkeypatch)
    if missing=='peer':peer=peer.iloc[:-1]
    if missing=='target':own=own.iloc[:-1]
    if missing=='identity':monkeypatch.setattr(si,'snapshot',lambda:{})
    if missing=='index':monkeypatch.setattr(data_refresh,'stored_index_rows',lambda:[])
    x=danger.externals_for(['AAA.NS'],{'AAA.NS':own,'BBB.NS':peer},NOW)['AAA.NS']
    if missing in ('peer','identity','target'):
        assert x.peer_break_20d_low is None
        assert danger._EXT_FNS['T12W_0094'](danger._Ctx(own),x,{}, {}) is None
    if missing in ('index','target'):
        assert x.index_close is None
        assert danger._EXT_FNS['T12W_0095'](danger._Ctx(own),x,{}, {}) is None


def test_completed_target_is_used_for_danger_support_test(monkeypatch):
    from trading import danger
    own,peer=danger_fixture(monkeypatch)
    today=own.tail(1).copy();today.index=pd.to_datetime(['2026-11-13']);today.loc[:,'Close']=1
    today.loc[:,'Low']=1;today.loc[:,'Open']=1
    f=pd.concat([own,today]);f.attrs=own.attrs.copy()
    x=danger.externals_for(['AAA.NS'],{'AAA.NS':f,'BBB.NS':peer},NOW)['AAA.NS']
    # Current unfinished drop cannot turn yesterday's sector contagion signal off.
    assert x.target_frame.index[-1].date().isoformat()=='2026-11-12'
    assert 'T12W_0094' in danger.evaluate_stock(f,x)
