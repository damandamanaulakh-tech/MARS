"""SEQ-08: execution causality, net costs and genuinely frozen selection."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, time, timedelta
import json
import gzip
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from research.profit_protection_2026_11_16 import study as s
from trading import fees
from trading.config import Config
from trading.exits import check_exit
from trading.targets import target_price_for


@pytest.fixture
def protocol():
    return json.loads((s.HERE/'protocol.json').read_text())


def bar(clock='10:00', o=100., h=100.2, low=99.9, c=100., day=date(2026, 8, 10)):
    return s.Bar(datetime.combine(day, time.fromisoformat(clock), s.IST), o, h, low, c, 10000.)


def sample(bars=None):
    bars = bars or [bar(), bar('15:25', o=100.4, h=100.4, low=100.4, c=100.4)]
    return s.Sample('TCS.NS', bars[0].ts.date(), '10:00', tuple(bars), 2., .2, .004,
                    bars[0].ts-timedelta(minutes=5), bars[0].ts.date()-timedelta(days=1))


def arm(protocol, name):
    return next(x for x in protocol['arms'] if x['id']==name)


def order(protocol, bars=None, target=110., bps=5):
    return replace(s.prepare(sample(bars), bps, protocol), current_target=target)


def rows_for_day(day=date(2026, 8, 10)):
    start = datetime.combine(day, time(9,15), s.IST)
    return [[(start+timedelta(minutes=5*i)).isoformat(), 100., 101., 99., 100., 1000.] for i in range(75)]


def test_reproduces_losing_current_trail_and_candidate_waits_for_net_room(protocol):
    bars = [bar(h=100.2, low=99.1), bar('15:25', o=100.4)]
    o = order(protocol, bars)
    baseline = s.simulate(o, arm(protocol,s.BASE),'open_high_low_close',protocol)
    candidate = s.simulate(o, arm(protocol,'net_activation'),'open_high_low_close',protocol)
    assert baseline['exit_reason']=='TRAILING_STOP' and baseline['net_pnl'] < 0
    assert candidate['exit_reason']=='SQUARE_OFF' and candidate['net_pnl'] > 0


def test_sampled_current_rule_agrees_with_actual_check_exit(protocol):
    bars = [bar('10:00'), bar('10:25',c=100.3), bar('10:55',c=99.1), bar('15:25')]
    o=order(protocol,bars)
    cfg=Config(stop_loss_pct=-.015,take_profit_pct=.01,trailing_stop_pct=-.01,score_decay_exit=40,max_hold_days=1)
    pos=SimpleNamespace(entry_price=o.entry,peak_price=o.entry,entry_score=80.,stop_price=o.stop,
                        target_price=o.current_target,entry_date=bars[0].ts,tags=[])
    assert check_exit(pos,100.3,80,cfg,bars[1].ts+timedelta(minutes=5))==(False,'')
    actual=check_exit(pos,99.1,80,cfg,bars[2].ts+timedelta(minutes=5))
    replay=s.simulate(o,arm(protocol,s.BASE),'sampled_30m',protocol)
    assert actual==(True,replay['exit_reason'])
    assert replay['raw_exit']==99.1 and replay['exit_time'].endswith('11:00:00+05:30')


@pytest.mark.parametrize('name',['net_activation','atr_profit_lock','combined'])
def test_profit_lock_still_loses_through_a_price_gap(protocol,name):
    bars=[bar(h=102.,low=100.,c=102.),bar('10:05',o=98.,h=98.,low=98.,c=98.),bar('15:25')]
    o=replace(order(protocol,bars),reachable_target=110.)
    r=s.simulate(o,arm(protocol,name),'open_low_high_close',protocol)
    assert r['exit_reason']=='STOP_LOSS'
    assert r['raw_exit']==98. and r['net_pnl']<0


def test_ambiguous_bar_reports_two_different_exits_without_hindsight(protocol):
    o=order(protocol,[bar(h=103.,low=98.),bar('15:25')],target=101.)
    high=s.simulate(o,arm(protocol,s.BASE),'open_high_low_close',protocol)
    low=s.simulate(o,arm(protocol,s.BASE),'open_low_high_close',protocol)
    assert high['exit_reason']=='TAKE_PROFIT' and high['raw_exit']==101.
    assert low['exit_reason']=='STOP_LOSS' and low['raw_exit']==o.stop
    assert high['observed_mfe_bps'] < 100  # No post-exit high of 103 is attributed.
    assert high['exit_precision']=='within_5m_bar'


def test_delayed_observation_does_not_fill_a_missed_target(protocol):
    bars=[bar(),bar('10:25',h=103.,low=99.9,c=100.2),bar('15:25',o=100.4)]
    o=order(protocol,bars,target=101.)
    continuous=s.simulate(o,arm(protocol,s.BASE),'open_high_low_close',protocol)
    delayed=s.simulate(o,arm(protocol,s.BASE),'sampled_30m',protocol)
    assert continuous['exit_reason']=='TAKE_PROFIT'
    assert delayed['exit_reason']=='SQUARE_OFF' and delayed['raw_exit']==100.4


def test_open_gap_uses_observed_price_not_stop_or_target(protocol):
    o=order(protocol,[bar(),bar('10:05',o=97.,h=97.,low=97.,c=97.),bar('15:25')])
    r=s.simulate(o,arm(protocol,'net_activation'),'open_high_low_close',protocol)
    assert r['raw_exit']==97. and r['raw_exit'] < o.stop
    o=order(protocol,[bar(),bar('10:05',o=104.,h=104.,low=104.,c=104.),bar('15:25')],target=101.)
    assert s.simulate(o,arm(protocol,s.BASE),'open_high_low_close',protocol)['raw_exit']==104.


def test_deadline_fills_open_without_using_final_bar_high(protocol):
    o=order(protocol,[bar(),bar('15:25',o=100.4,h=200.,low=20.,c=150.)])
    r=s.simulate(o,arm(protocol,'net_activation'),'open_high_low_close',protocol)
    assert r['exit_reason']=='SQUARE_OFF' and r['raw_exit']==100.4
    assert r['observed_mfe_bps'] < 50


def test_missing_deadline_cannot_fabricate_an_exit(protocol):
    with pytest.raises(ValueError,match='deadline_bar_unavailable'):
        s.simulate(order(protocol,[bar(h=100.,low=100.)]),arm(protocol,'net_activation'),'sampled_30m',protocol)


@pytest.mark.parametrize('bps',[0,5,15,30])
def test_break_even_includes_buy_and_sell_fees_and_sell_slippage(protocol,bps):
    o=order(protocol,bps=bps)
    net,_,_=s.sale_net(o.qty,o.entry,o.buy_fee,o.break_even,bps,o.sample.day,o.sample.symbol)
    earlier,_,_=s.sale_net(o.qty,o.entry,o.buy_fee,o.break_even-.01,bps,o.sample.day,o.sample.symbol)
    assert net>=0 and earlier<0
    assert o.qty*o.entry+o.buy_fee <= protocol['stake_inr']
    assert o.break_even > o.entry


def test_gross_gain_can_still_be_a_net_loss_and_arithmetic_reconciles(protocol):
    o=order(protocol,[bar(h=100.,low=100.),bar('15:25',o=100.05)])
    r=s.simulate(o,arm(protocol,'net_activation'),'sampled_30m',protocol)
    assert r['gross_pnl']>0 and r['net_pnl']<0
    assert r['net_pnl']==pytest.approx(r['gross_pnl']-r['slippage_inr']-r['buy_fees']-r['sell_fees'])
    assert r['fee_model']==fees.MODEL and r['exchange_schedule']=='2026-03-01'


def test_cost_model_matches_live_order_schedule_not_legacy_formula(protocol,monkeypatch):
    monkeypatch.setattr('trading.costs.trade_costs',lambda *a,**kw:pytest.fail('legacy delivery costs used'))
    o=order(protocol)
    r=s.simulate(o,arm(protocol,'net_activation'),'sampled_30m',protocol)
    fee=fees.charge(o.qty,r['exit_fill'],'SELL','intraday',o.sample.day,o.sample.symbol)
    assert r['sell_fees']==fee['total'] and fee['components']['dp']==0


def test_baseline_target_matches_completed_five_minute_production_formula(protocol):
    bars=[bar(o=100.,h=100.2,low=99.8,c=100.) for _ in range(15)]
    frame=pd.DataFrame([dict(Open=b.open,High=b.high,Low=b.low,Close=b.close) for b in bars])
    obs=replace(sample(),intraday_atr=s.true_range(bars),daily_atr=12.)
    o=s.prepare(obs,5,protocol)
    expected,_=target_price_for('intraday',o.entry,frame,.025)
    assert o.current_target==expected
    assert o.current_target==round(o.entry*1.01,2)  # Daily ATR would hit a different cap.


def test_reachable_target_cannot_be_a_known_net_loss(protocol):
    obs=replace(sample(),reachable_fraction=0.)
    o=s.prepare(obs,30,protocol)
    assert o.reachable_target >= o.break_even + .001*o.entry - .005


def test_slippage_stress_reduces_same_deadline_return(protocol):
    results=[]
    for bps in (5,15,30):
        o=order(protocol,bps=bps)
        results.append(s.simulate(o,arm(protocol,'net_activation'),'sampled_30m',protocol)['net_bps'])
    assert results[0]>results[1]>results[2]


def test_full_completed_session_and_timezone_conversion():
    rows=rows_for_day()
    for r in rows:
        r[0]=datetime.fromisoformat(r[0]).astimezone(s.timezone.utc).isoformat()
    bars,issues=s.validate_session(list(reversed(rows)),date(2026,8,10),datetime(2026,8,11,tzinfo=s.IST))
    assert len(bars)==75 and not issues
    assert bars[0].ts.hour==9 and bars[0].ts.minute==15


@pytest.mark.parametrize('mutation,reason',[
    ('missing','missing_bars'),('duplicate','duplicate_timestamp'),('nan','invalid_ohlcv_or_timestamp'),
    ('infinite','invalid_ohlcv_or_timestamp'),('negative_volume','invalid_ohlcv_or_timestamp'),
    ('incoherent','invalid_ohlcv_or_timestamp'),('naive','timestamp_timezone_unavailable'),
    ('misaligned','misaligned_interval'),('unfinished','unfinished_bar'),('future','future_bar'),
    ('wrong_interval','wrong_interval')])
def test_invalid_bar_invalidates_whole_session(mutation,reason):
    rows=rows_for_day(); asof=datetime(2026,8,11,tzinfo=s.IST); interval=5
    if mutation=='missing': rows.pop(3)
    elif mutation=='duplicate': rows.append(rows[3])
    elif mutation=='nan': rows[3][2]=float('nan')
    elif mutation=='infinite': rows[3][3]=float('inf')
    elif mutation=='negative_volume': rows[3][5]=-1
    elif mutation=='incoherent': rows[3][2]=98
    elif mutation=='naive': rows[3][0]='2026-08-10T09:30:00'
    elif mutation=='misaligned': rows[3][0]='2026-08-10T09:31:00+05:30'
    elif mutation=='unfinished': asof=datetime(2026,8,10,15,27,tzinfo=s.IST)
    elif mutation=='future': asof=datetime(2026,8,10,9,20,tzinfo=s.IST)
    elif mutation=='wrong_interval': interval=30
    bars,issues=s.validate_session(rows,date(2026,8,10),asof,interval)
    assert not bars and issues[reason]>0


def test_unverified_calendar_and_holiday_never_become_trading_data():
    for day, reason in [(date(2027,8,10),'session_calendar_unverified'),(date(2026,8,15),'non_trading_session')]:
        bars,issues=s.validate_session(rows_for_day(day),day,datetime(2027,8,11,tzinfo=s.IST) if day.year==2027 else datetime(2026,8,16,tzinfo=s.IST))
        assert not bars and issues[reason]>0


def fixture_sessions(protocol):
    p=deepcopy(protocol); p['symbols']=['TCS.NS']; p['data_start']='2026-06-15'
    p['splits']['train']=['2026-07-20','2026-07-21']
    sessions={}
    for d in s.days_between(p['data_start'],'2026-07-22'):
        rows=rows_for_day(d)
        sessions[('TCS.NS',d)]=[s.Bar(datetime.fromisoformat(r[0]),*r[1:]) for r in rows]
    return p,sessions


def test_future_prices_do_not_change_entry_features(protocol):
    p,sessions=fixture_sessions(protocol)
    before,_=s.make_samples(sessions,p,'train')
    first=before[0]
    changed=deepcopy(sessions)
    for key,bars in changed.items():
        if key[1]>=first.day:
            changed[key]=[replace(b,high=1000.,low=1.,close=999.) if b.ts>=first.bars[0].ts else b for b in bars]
    after,_=s.make_samples(changed,p,'train')
    assert (before[0].daily_atr,before[0].intraday_atr,before[0].reachable_fraction)==(after[0].daily_atr,after[0].intraday_atr,after[0].reachable_fraction)
    assert first.feature_last_bar+timedelta(minutes=5)<=first.bars[0].ts
    assert first.calibration_last_day < first.day


def test_missing_prior_session_is_unknown_not_shortened_lookback(protocol):
    p,sessions=fixture_sessions(protocol)
    del sessions[('TCS.NS',date(2026,7,17))]
    samples,excluded=s.make_samples(sessions,p,'train')
    assert not samples and len(excluded)==2


def test_empty_all_loss_and_all_win_statistics_are_explicit(protocol):
    assert s.summary([])['mean_net_bps'] is None
    r=s.simulate(order(protocol),arm(protocol,'net_activation'),'sampled_30m',protocol)
    bad=dict(r,net_pnl=-10.,net_bps=-1.)
    assert s.summary([bad])['ratio_status']=='no_wins'
    assert s.summary([bad])['win_pct']==0
    good=dict(r,net_pnl=10.,net_bps=1.)
    assert s.summary([good])['profit_factor'] is None
    assert s.summary([good])['ratio_status']=='no_losses'


def test_selection_uses_worst_training_scenario_and_declared_tie_order(protocol):
    rows=[]
    for a in protocol['arms']:
        for scenario in protocol['execution_scenarios']:
            val=100. if a['id']=='combined' else 2. if a['id']==s.BASE else 1.
            if a['id']=='combined' and scenario=='sampled_30m': val=-1.
            rows.append(dict(arm=a['id'],scenario=scenario,slippage_bps=5,mean_net_bps=val))
    assert s.choose(rows,protocol)[0]==s.BASE
    for r in rows: r['mean_net_bps']=0
    assert s.choose(rows,protocol)[0]==s.BASE
    with pytest.raises(ValueError,match='training_scenario_unavailable'):
        s.choose(rows[:-1],protocol)


def test_pairing_rejects_missing_duplicate_or_unmatched_observations(protocol):
    row=dict(sample='x',day='2026-08-10',net_bps=1.)
    for candidate,base in [([row],[]),([row,row],[row]),([row],[dict(row,sample='y')])]:
        with pytest.raises(ValueError,match='unpaired'):
            s.paired(candidate,base,protocol)


def test_bootstrap_resamples_days_not_correlated_trades(protocol):
    candidate=[]; baseline=[]
    for day,delta in [('2026-08-10',10.),('2026-08-11',-10.)]:
        for i in range(50):
            r=dict(sample=f'{day}/{i}',day=day,net_bps=0.)
            baseline.append(r); candidate.append(dict(r,net_bps=delta))
    result=s.paired(candidate,baseline,protocol)
    assert result['sessions']==2 and result['count']==100
    assert result['mean_gain_bps']==0 and result['ci95_bps']==[-10.,10.]


def test_changed_inputs_cannot_be_called_the_sealed_holdout(monkeypatch,tmp_path):
    monkeypatch.setattr(s,'source_hashes',lambda *a:{'study.py':'new'})
    with pytest.raises(ValueError,match='sealed_inputs_changed'):
        s.verify_seal(tmp_path,{'hashes':{'study.py':'old'}})


def test_no_holdout_before_seal_or_silent_reopening(tmp_path):
    with pytest.raises(FileNotFoundError): s.run_holdout(tmp_path)
    (tmp_path/'holdout.json').write_text('{}')
    with pytest.raises(ValueError,match='already_opened'): s.run_holdout(tmp_path)
    (tmp_path/'selection.json').write_text('{}')
    with pytest.raises(ValueError,match='already_sealed'): s.run_train(tmp_path)


def test_baseline_selection_and_insufficient_coverage_cannot_promote(protocol):
    o=order(protocol)
    rows=[s.simulate(o,a,scenario,protocol) for a in protocol['arms'] for scenario in protocol['execution_scenarios']]
    seal={'selected_arm':s.BASE,'training':s.summarize(rows),'validation':s.summarize(rows)}
    report=s.holdout_report(rows,seal,protocol)
    assert report['statistical_pass'] is False and report['settings_changed'] is False
    assert 'training_selected_baseline' in report['reasons']
    assert 'holdout_coverage_below_predeclared_minimum' in report['reasons']
    assert report['promotion']=='NOT_AUTHORIZED_BY_THIS_STUDY'


def test_one_percent_net_trail_cannot_activate_before_one_percent_target(protocol):
    o=s.prepare(sample(),5,protocol)
    r=s.simulate(o,arm(protocol,'net_activation'),'sampled_30m',protocol)
    assert r['activation_below_target'] is False
    assert r['trail_activation_quote'] > r['target']


def snapshot(tmp_path):
    p={'symbols':['TCS.NS'],'data_start':'2026-08-10','data_end':'2026-08-10'}
    protocol=s.encoded(p); (tmp_path/'protocol.json').write_bytes(protocol)
    body=json.dumps({'data':{'candles':rows_for_day()}})
    req={'symbol':'TCS.NS','start':'2026-08-10','end':'2026-08-10','http_status':200,'body_sha256':s.sha(body.encode())}
    raw=json.dumps([dict(req,body=body)]).encode(); zipped=gzip.compress(raw,mtime=0)
    manifest={'requests':[req],'raw_archive_sha256':s.sha(zipped),'raw_payload_sha256':s.sha(raw),
              'interval_minutes':5,'timestamp_kind':'bar_start','protocol_sha256':s.sha(protocol),
              'finished_at':'2026-08-11T00:00:00+00:00'}
    (tmp_path/'raw_responses.json.gz').write_bytes(zipped);s.write_json(tmp_path/'manifest.json',manifest)
    return p,manifest


@pytest.mark.parametrize('field,reason',[('raw_archive_sha256','raw_archive_hash'),('raw_payload_sha256','raw_payload_hash'),
    ('protocol_sha256','acquisition_protocol_hash'),('interval_minutes','unproved_bar_contract')])
def test_replay_rejects_changed_source_bytes_or_bar_contract(tmp_path,field,reason):
    p,m=snapshot(tmp_path);m[field]='tampered';s.write_json(tmp_path/'manifest.json',m)
    with pytest.raises(ValueError,match=reason):s.load_data(tmp_path,p)


def test_raw_source_manifest_roundtrip(tmp_path):
    p,_=snapshot(tmp_path)
    sessions,coverage=s.load_data(tmp_path,p)
    assert len(sessions[('TCS.NS',date(2026,8,10))])==75
    assert coverage['valid_full_sessions']==1 and not coverage['exclusions']


def test_failed_holdout_keeps_consumed_marker_and_only_identical_resume(monkeypatch,tmp_path,protocol):
    train=[dict(arm=a['id'],scenario=sc,slippage_bps=5,mean_net_bps=0.)
           for a in protocol['arms'] for sc in protocol['execution_scenarios']]
    selected,scores=s.choose(train,protocol)
    data=b'unchanged training artifact'; (tmp_path/'train_validation_trades.json.gz').write_bytes(data)
    seal={'training':train,'selected_arm':selected,'training_scores':scores,'train_validation_trades_sha256':s.sha(data)}
    s.write_json(tmp_path/'selection.json',seal);s.write_json(tmp_path/'protocol.json',protocol)
    monkeypatch.setattr(s,'verify_seal',lambda *a:None)
    monkeypatch.setattr(s,'load_data',lambda *a:(_ for _ in ()).throw(ValueError('simulated_read_failure')))
    with pytest.raises(ValueError,match='simulated_read_failure'):s.run_holdout(tmp_path)
    assert (tmp_path/'holdout_opened.json').exists()
    with pytest.raises(ValueError,match='previously_opened'):s.run_holdout(tmp_path)
    with pytest.raises(ValueError,match='simulated_read_failure'):s.run_holdout(tmp_path,resume=True)
    seal['tamper']='changed';s.write_json(tmp_path/'selection.json',seal)
    with pytest.raises(ValueError,match='previously_opened'):s.run_holdout(tmp_path,resume=True)
