"""Goal10 causal sizing, complete net accounting and sealed fresh holdout."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, time
import json
from pathlib import Path
import pytest
from trading import fees
from research.sizing_2026_11_16 import study as s
from research.sizing_2026_11_16 import fetch
from trading.tests.test_reentry_research_2026_11_16 import bars


@pytest.fixture
def p():
    return s.protocol_at(s.HERE)


def row(net=-10,day='2026-08-03',**kw):
    return {'day':day,'net_pnl':net,'buy_fees':20.,'sell_fees':25.,'qty':100,
            'entry_fill':100.,'exit_reason':'SQUARE_OFF',**kw}


def small(p):
    p=deepcopy(p);p['performance']['minimum_trades']=1;p['performance']['minimum_sessions']=1
    return p


@pytest.mark.parametrize('bps',[0,5,15,30])
def test_net_stop_budget_includes_both_fees_and_adverse_fill(p,bps):
    day=date(2026,8,3);entry=100*(1+bps/10000);stop=98.5
    q,why=s.risk_quantity(entry,stop,bps,day,'AAA.NS',4000,300000,150000)
    assert q and why=='sized'
    raw=q*(entry-stop*(1-bps/10000))
    expected=raw+fees.charge(q,entry,'BUY','intraday',day,'AAA.NS')['total']+fees.charge(q,stop*(1-bps/10000),'SELL','intraday',day,'AAA.NS')['total']
    assert s.net_stop_loss(q,entry,stop,bps,day,'AAA.NS')==pytest.approx(expected)
    assert expected<=4000 and s.net_stop_loss(q+1,entry,stop,bps,day,'AAA.NS')>4000


def test_minimum_ticket_cannot_round_up_through_net_risk(p):
    assert s.risk_quantity(100,98.5,5,date(2026,8,3),'AAA.NS',2250,300000,150000)==(0,'below_minimum_within_risk')


@pytest.mark.parametrize('stop',[float('nan'),float('inf'),0,-1,100,110])
def test_bad_stop_is_not_zero_risk_even_under_zero_budget(stop):
    with pytest.raises(ValueError,match='invalid_stop'):s.risk_quantity(100,stop,5,date(2026,8,3),'AAA.NS',0,0,0)


@pytest.mark.parametrize('returns,state',[( [],'EMPTY'),([-10,-20],'ALL_LOSS'),([0,0],'ALL_FLAT'),([10,20],'NO_LOSS'),([100,-200],'NO_POSITIVE_EDGE'),([200,-20],'ACTIVE')])
def test_empty_and_one_sided_samples_explicit(p,returns,state):
    r=s.performance([row(x) for x in returns],date(2026,8,4),small(p))
    assert r['status']==state
    if state in ('EMPTY','NO_LOSS'):assert r['fraction'] is None and r['payoff'] is None
    if state=='ALL_LOSS':assert r['fraction']==0 and r['payoff'] is None


def test_insufficient_incomplete_and_unknown_cost_are_not_zero(p):
    assert s.performance([row()],date(2026,8,4),p)['status']=='INSUFFICIENT'
    assert s.performance([row()],date(2026,8,4),small(p),complete=False)['status']=='UNAVAILABLE'
    assert s.performance([row(buy_fees=None)],date(2026,8,4),small(p))['status']=='UNAVAILABLE'
    assert s.performance([row(net_pnl=float('nan'))],date(2026,8,4),small(p))['n']==0


@pytest.mark.parametrize('day',['2026-08-04','2026-08-05'])
def test_same_day_and_future_performance_cannot_enter_size(p,day):
    with pytest.raises(ValueError,match='future_or_same_day'):
        s.performance([row(day=day)],date(2026,8,4),p)


def test_every_net_loss_counts_regardless_of_reason(p):
    values=[row(-20,exit_reason=x) for x in ['SQUARE_OFF','OWNER_SELL','STOP_LOSS','UNKNOWN']]
    r=s.performance(values,date(2026,8,4),small(p))
    assert r['losses']==4 and r['net_pnl']==-80 and r['costs']==180 and len(r['reasons'])==4


def fixture(p):
    p=small(p);p['symbols']=['AAA.NS'];p['data_start']='2026-08-03';p['splits']['train']=['2026-08-05','2026-08-07']
    p['execution_scenarios']=['open_high_low_close'];p['costs']['slippage_bps_per_side']=[5]
    p['performance']['window_sessions']=2;p['loss_control']['window_sessions']=1
    sessions={('AAA.NS',date(2026,8,i)):bars(date(2026,8,i)) for i in range(3,8)}
    return p,sessions


def test_future_prices_do_not_change_current_size_or_performance(p):
    p,sessions=fixture(p);first,_=s.evaluate(sessions,p,'train')
    changed=deepcopy(sessions)
    day=date(2026,8,6)
    changed['AAA.NS',day]=[replace(b,open=80,high=80.1,low=79.9,close=80) if b.ts.time()>=time(10,5) else b for b in changed['AAA.NS',day]]
    second,_=s.evaluate(changed,p,'train')
    for a,b in zip(first,second):
        if a['day']<='2026-08-06':
            assert (a['qty'],a['reason'],a['performance'],a['loss_reading'])==(b['qty'],b['reason'],b['performance'],b['loss_reading'])
        if a['day']<'2026-08-06':assert a==b
    assert any(a['net_pnl']!=b['net_pnl'] for a,b in zip(first,second) if a['day']=='2026-08-06')


def test_missing_session_is_excluded_and_window_unknown(p):
    p,sessions=fixture(p);sessions.pop(('AAA.NS',date(2026,8,5)))
    rows,excluded=s.evaluate(sessions,p,'train')
    assert {e['day'] for e in excluded}=={'2026-08-05','2026-08-06'}
    assert all(r['performance']['status']=='UNAVAILABLE' for r in rows)
    assert all(r['qty']==0 for r in rows if r['arm']=='loss_budget_stop')


def test_net_attribution_and_actual_costs_reconcile(p):
    p,sessions=fixture(p);rows,_=s.evaluate(sessions,p,'train')
    for r in rows:
        assert r['net_pnl']-r['reference_net']==pytest.approx(r['loss_reduction']-r['profit_forgone']+r['flat_reference_difference'],abs=3e-8)
        if r['trade']:
            t=r['trade']
            assert t['net_pnl']==pytest.approx(t['gross_pnl']-t['slippage_inr']-t['buy_fees']-t['sell_fees'],abs=3e-8)
            if r['arm']!='fixed_slab':assert r['net_stop_risk']<=p['net_stop_budget_inr']+1e-8
        assert r['performance']['last_day'] is None or r['performance']['last_day']<r['day']


def test_all_cash_is_diagnostic_never_selectable(p):
    summary=[]
    for a in p['arms']:
        for scenario in p['execution_scenarios']:
            summary.append({'arm':a['id'],'scenario':scenario,'slippage_bps':5,'opportunities':10,'paired_gain':1000 if a['id']=='no_entry' else 0})
    assert s.choose(summary,p)[0]=='fixed_slab'


def test_fresh_panel_and_chronological_split(p):
    assert not set(p['symbols'])&set(json.loads((s.retry.HERE/'protocol.json').read_text())['symbols'])
    assert p['splits']['validation'][1]<p['splits']['holdout'][0]


def test_holdout_requires_seal_before_network(tmp_path,monkeypatch):
    calls=[];monkeypatch.setattr(s,'require_runtime',lambda d:None)
    monkeypatch.setattr(fetch.httpx,'get',lambda *a,**kw:calls.append(a))
    with pytest.raises(FileNotFoundError):fetch.acquire('holdout',tmp_path)
    assert calls==[]


def test_consumed_holdout_cannot_reopen(tmp_path,monkeypatch):
    monkeypatch.setattr(s,'require_runtime',lambda d:None);(tmp_path/'holdout.json').write_text('{}')
    with pytest.raises(ValueError,match='holdout_consumed'):s.open_holdout(tmp_path,resume=True)


def test_seal_rejects_changed_inputs_before_selection(tmp_path,monkeypatch):
    monkeypatch.setattr(s,'hashes',lambda d:{'code':'changed'})
    with pytest.raises(ValueError,match='sealed_inputs_changed'):s.verify_seal(tmp_path,{'hashes':{'code':'old'}})


def test_acquisition_never_replaces_raw_archive(tmp_path,monkeypatch):
    (tmp_path/'training').mkdir();raw=tmp_path/'training/raw_responses.json.gz';raw.write_bytes(b'original')
    with pytest.raises(ValueError,match='snapshot_already_exists'):fetch.acquire('training',tmp_path)
    assert raw.read_bytes()==b'original'


def test_zero_trades_have_unknown_win_rate_and_expectancy(p):
    p,sessions=fixture(p);rows,_=s.evaluate(sessions,p,'train')
    r=s.stats([r for r in rows if r['arm']=='no_entry'])
    assert r['trades']==0 and r['net_per_trade'] is None and r['win_pct'] is None
    assert r['total_net']==0
