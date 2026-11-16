"""SEQ09: changed evidence, matched counterfactuals and fresh sealed data."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, time, timedelta
import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from research.reentry_2026_11_16 import study as s
from research.reentry_2026_11_16 import fetch
from trading import fees
from trading.config import Config
from trading.exits import check_exit
from trading.targets import target_price_for


@pytest.fixture
def p():
    return s.protocol_at(s.HERE)


DAY=date(2026,3,9)


def at(clock,day=DAY):
    return datetime.combine(day,time.fromisoformat(clock),s.IST)


def bars(day=DAY,close=100.):
    opened=datetime.combine(day,time(9,15),s.IST)
    return [s.exits.Bar(opened+timedelta(minutes=5*i),close,close+.1,close-.1,close,1000.) for i in range(75)]


def setbar(rows,clock,close,volume=1000.):
    return [replace(b,open=close,high=close+.1,low=close-.1,close=close,volume=volume)
            if b.ts==at(clock) else b for b in rows]


def reclaimed():
    rows=bars(close=100.)
    for clock,px in [('10:00',99.),('10:05',101.),('10:10',102.),('10:15',103.),('10:20',104.)]:
        rows=setbar(rows,clock,px)
    return rows


def seed_sample(p,rows=None):
    return s.sample_at('TCS.NS',DAY,at('10:00'),rows or bars(),bars(date(2026,3,6)),p)


def test_original_scope_has_no_overlap_and_chronological_splits(p):
    assert p['data_end'] < json.loads((s.exits.HERE/'protocol.json').read_text())['data_start']
    assert p['splits']['train'][1]<p['splits']['validation'][0]<p['splits']['holdout'][0]


@pytest.mark.parametrize('change,reason',[
    ({'data_end':'2026-06-15'},'overlapping'),
    ({'interval_minutes':1},'unsupported_interval'),
    ({'feature_lag_minutes':0},'unsupported_interval'),
])
def test_invalid_protocol_refuses_claim_of_new_data(tmp_path,p,change,reason):
    p.update(change); s.write_json(tmp_path/'protocol.json',p)
    with pytest.raises(ValueError,match=reason):s.protocol_at(tmp_path)


def test_cannot_relabel_goal08_dates_to_hide_overlap(tmp_path,p):
    p['freshness']['goal08_data_start']='2027-01-01';s.write_json(tmp_path/'protocol.json',p)
    with pytest.raises(ValueError,match='misrepresented'):s.protocol_at(tmp_path)


@pytest.mark.parametrize('precision,expected',[
    ('within_5m_bar','10:05'),('bar_open_proxy','10:00'),('completed_close_proxy','10:00')])
def test_intrabar_loss_is_known_only_at_bar_end(precision,expected):
    assert s.known_exit({'exit_time':at('10:00').isoformat(),'exit_precision':precision})==at(expected)


@pytest.mark.parametrize('trade,reason',[
    ({'exit_time':'2026-03-09T10:00:00','exit_precision':'within_5m_bar'},'timezone'),
    ({'exit_time':at('10:00').isoformat(),'exit_precision':'exact_tick_guessed'},'precision')])
def test_unknown_exit_time_is_not_assumed(trade,reason):
    with pytest.raises(ValueError,match=reason):s.known_exit(trade)


def test_retry_never_uses_same_exit_slot_or_deadline(p):
    assert s.retry_slots(DAY,at('10:30'),p)[0]==at('11:00')
    assert s.retry_slots(DAY,at('14:30'),p)==[]
    assert s.retry_slots(DAY,at('10:29'),p)[-1]==at('14:30')


def test_same_bar_rebound_does_not_qualify(p):
    d=s.changed_setup(reclaimed(),at('10:30'),at('10:25'),100.5,p)
    assert d['state']=='unavailable' and not d['breakout_reclaim'] and not d['vwap_reclaim']


def test_completed_post_loss_reclaim_records_witnesses(p):
    d=s.changed_setup(reclaimed(),at('10:30'),at('10:00'),100.5,p)
    assert d['breakout_reclaim'] and d['vwap_reclaim']
    assert d['breakout_reset_bar']==at('10:00').isoformat()
    assert d['breakout_cross_bar']==at('10:05').isoformat()
    assert d['last_completed_bar']==at('10:20').isoformat()
    assert not d['score_reused']
    assert s.rule_passes('combined_reclaim',d,at('10:30'),at('10:00'))


def test_price_already_above_level_is_not_a_new_setup(p):
    d=s.changed_setup(bars(close=103.),at('10:30'),at('10:00'),100.5,p)
    assert not d['breakout_reclaim'] and d['breakout_cross_bar'] is None


def test_old_pre_loss_cross_cannot_be_reused(p):
    rows=bars(close=103.)
    rows=setbar(rows,'09:30',99.)
    d=s.changed_setup(rows,at('10:30'),at('10:00'),100.5,p)
    assert not d['breakout_reclaim']


def test_reclaim_that_failed_again_is_not_latched(p):
    rows=setbar(reclaimed(),'10:20',99.)
    d=s.changed_setup(rows,at('10:30'),at('10:00'),100.5,p)
    assert not d['breakout_reclaim'] and not d['vwap_reclaim']
    assert d['breakout_cross_bar'] is None


def test_future_or_too_recent_bars_cannot_change_decision(p):
    rows=reclaimed()
    original=s.changed_setup(rows,at('10:30'),at('10:00'),100.5,p)
    altered=[replace(b,open=9999.,high=9999.,low=9999.,close=9999.,volume=1e12)
             if b.ts>=at('10:25') else b for b in rows]
    assert s.changed_setup(altered,at('10:30'),at('10:00'),100.5,p)==original


def test_missing_completed_bar_remains_unavailable(p):
    rows=[b for b in reclaimed() if b.ts!=at('09:35')]
    d=s.changed_setup(rows,at('10:30'),at('10:00'),100.5,p)
    assert d['state']=='unavailable' and d['reason']=='incomplete_completed_session_prefix'
    assert not d['breakout_reclaim'] and not d['vwap_reclaim']


@pytest.mark.parametrize('kind',['duplicate','unsorted','negative_volume','nan','incoherent'])
def test_bad_completed_history_cannot_manufacture_setup(p,kind):
    rows=reclaimed()
    if kind=='duplicate':rows.insert(0,rows[0])
    elif kind=='unsorted':rows[0],rows[1]=rows[1],rows[0]
    elif kind=='negative_volume':rows[0]=replace(rows[0],volume=-1.)
    elif kind=='nan':rows[0]=replace(rows[0],close=float('nan'))
    else:rows[0]=replace(rows[0],high=50.)
    with pytest.raises(ValueError,match='invalid_decision'):s.changed_setup(rows,at('10:30'),at('10:00'),100.5,p)


def test_zero_volume_vwap_unknown_while_price_evidence_remains(p):
    rows=[replace(b,volume=0.) for b in reclaimed()]
    d=s.changed_setup(rows,at('10:30'),at('10:00'),100.5,p)
    assert d['breakout_reclaim'] and not d['vwap_reclaim'] and d['vwap_state']=='unavailable'
    assert d['latest_vwap'] is None
    assert not s.rule_passes('combined_reclaim',d,at('10:30'),at('10:00'))


def test_vwap_needs_two_closes_and_rising_latest_close(p):
    rows=setbar(reclaimed(),'10:20',102.)
    d=s.changed_setup(rows,at('10:30'),at('10:00'),100.5,p)
    assert d['breakout_reclaim'] and not d['vwap_reclaim']


@pytest.mark.parametrize('clock',['10:00','09:30'])
def test_decision_must_follow_loss(p,clock):
    with pytest.raises(ValueError,match='retry_not_after'):s.changed_setup(bars(),at(clock),at('10:00'),100.,p)


def test_time_only_control_is_not_changed_setup(p):
    d=s.changed_setup(bars(),at('10:30'),at('10:00'),100.5,p)
    assert s.rule_passes('wait_30m',d,at('10:30'),at('10:00'))
    assert not s.rule_passes('breakout_reclaim',d,at('10:30'),at('10:00'))
    assert not s.rule_passes('vwap_reclaim',d,at('10:30'),at('10:00'))
    assert not s.rule_passes('wait_30m',d,at('10:30'),at('10:05'))


def test_unknown_rule_is_not_a_pass():
    with pytest.raises(ValueError,match='unknown_reentry'):s.rule_passes('magic_score',{},at('10:30'),at('10:00'))


def test_entry_atr_excludes_current_and_recent_bars(p):
    sample=seed_sample(p)
    assert sample.feature_last_bar==at('09:50')
    altered=[replace(b,high=500.,low=1.) if b.ts>=at('09:55') else b for b in bars()]
    assert seed_sample(p,altered).intraday_atr==sample.intraday_atr


def test_price_baseline_matches_actual_target_and_exit(p):
    sample=seed_sample(p)
    order=s.prepare(sample,5,p)
    prefix=bars(date(2026,3,6))+s.completed(bars(),at('10:00'),p)
    frame=pd.DataFrame([dict(Open=b.open,High=b.high,Low=b.low,Close=b.close) for b in prefix[-15:]])
    target,_=target_price_for('intraday',order.entry,frame,.025)
    assert order.current_target==target
    pos=SimpleNamespace(entry_price=order.entry,peak_price=order.entry,entry_score=80.,stop_price=order.stop,
                        target_price=order.current_target,entry_date=at('10:00'),tags=[])
    cfg=Config(stop_loss_pct=-.015,trailing_stop_pct=-.01,take_profit_pct=.025,score_decay_exit=40,max_hold_days=1)
    assert check_exit(pos,100.3,80,cfg,at('10:30'))==(False,'')
    assert check_exit(pos,99.1,80,cfg,at('11:00'))==(True,'TRAILING_STOP')


@pytest.mark.parametrize('bps',[5,15,30])
def test_costs_fill_size_and_net_arithmetic(p,bps):
    order=s.prepare(seed_sample(p),bps,p)
    r=s.leg(order,'sampled_30m',p)
    assert order.qty*order.entry+order.buy_fee<=p['stake_inr']
    assert r['net_pnl']==pytest.approx(r['gross_pnl']-r['slippage_inr']-r['buy_fees']-r['sell_fees'])
    assert r['sell_fees']==fees.charge(order.qty,r['exit_fill'],'SELL','intraday',DAY,'TCS.NS')['total']
    assert r['fee_model']==fees.MODEL
    assert 'net_break_even_quote' not in r


def training_summary(p):
    return [dict(arm=a['id'],scenario=sc,slippage_bps=5,mean_gain_bps=0.) for a in p['arms'] for sc in p['execution_scenarios']]


def test_train_selection_cannot_pick_time_only_or_no_retry(p):
    rows=training_summary(p)
    for r in rows:
        if r['arm'] in ('wait_30m','no_retry'):r['mean_gain_bps']=9999.
    assert s.choose(rows,p)[0]==s.BASE


def test_train_uses_worst_scenario_and_fixed_tie_order(p):
    rows=training_summary(p)
    for r in rows:
        if r['arm']=='breakout_reclaim':r['mean_gain_bps']=2.
        if r['arm']=='vwap_reclaim':r['mean_gain_bps']=9. if r['scenario']!='sampled_30m' else 1.
        if r['arm']=='combined_reclaim':r['mean_gain_bps']=2.
    assert s.choose(rows,p)[0]=='breakout_reclaim'


def test_missing_training_scenario_is_explicit(p):
    rows=training_summary(p)[1:]
    with pytest.raises(ValueError,match='scenario_unavailable'):s.choose(rows,p)


def test_empty_samples_do_not_become_zero_success(p):
    assert s.stats([])['mean_retry_net_bps'] is None
    assert s.paired([],p)['ci95_bps'] is None


def test_duplicate_or_unpaired_episodes_rejected(p):
    row=dict(episode='x',day='2026-03-09',paired_gain_inr=1.,retry_net_inr=1.,
             baseline_retry={'net_pnl':0.},paired_gain_bps=.1)
    with pytest.raises(ValueError,match='duplicate'):s.paired([row,row],p)
    row['paired_gain_inr']=100.
    with pytest.raises(ValueError,match='unpaired'):s.paired([row],p)


def test_bootstrap_uses_session_days_not_stock_independence(p):
    rows=[dict(episode=str(i),day='2026-03-09' if i<10 else '2026-03-10',paired_gain_inr=1.,
               retry_net_inr=1.,baseline_retry={'net_pnl':0.},paired_gain_bps=.1) for i in range(20)]
    result=s.paired(rows,p)
    assert result['count']==20 and result['sessions']==2 and result['ci95_bps']==[.1,.1]


def test_end_to_end_loss_and_missed_recovery_accounting(p):
    p=deepcopy(p);p['symbols']=['TCS.NS'];p['splits']['train']=[str(DAY),str(DAY)]
    p['entry_times']=['10:00'];p['execution_scenarios']=['sampled_30m'];p['costs']['slippage_bps_per_side']=[5]
    rows=bars()
    rows=setbar(rows,'10:25',98.)  # Seed hard stop, known at 10:30.
    rows=setbar(rows,'11:25',102.)  # Baseline 11:00 retry wins at next check.
    result,seeds,excluded=s.evaluate({('TCS.NS',DAY):rows,('TCS.NS',date(2026,3,6)):bars(date(2026,3,6))},p,'train')
    assert len(result)==6 and not excluded and seeds['sampled_30m|5|paired_episode']==1
    base=next(r for r in result if r['arm']==s.BASE)
    assert base['paired_gain_inr']==0 and base['retry_net_inr']>0
    no=next(r for r in result if r['arm']=='no_retry')
    assert no['recovery_missed'] and not no['loss_avoided'] and no['retry'] is None
    assert no['paired_gain_inr']==-no['recoveries_missed_inr']
    for r in result:
        assert r['paired_gain_inr']==pytest.approx(r['losses_avoided_inr']-r['recoveries_missed_inr']+r['delayed_retry_net_inr'])
        if r['retry_at']:
            assert datetime.fromisoformat(r['retry_at'])>datetime.fromisoformat(r['failure_known_at'])


def test_same_entry_old_score_cannot_pass_candidates(p):
    p=deepcopy(p);p['symbols']=['TCS.NS'];p['splits']['train']=[str(DAY),str(DAY)]
    p['entry_times']=['10:00'];p['execution_scenarios']=['sampled_30m'];p['costs']['slippage_bps_per_side']=[5]
    rows=bars()
    for clock in ('10:25','10:55','11:25','11:55','12:25','12:55','13:25','13:55','14:25','14:55'):
        rows=setbar(rows,clock,98.)
    result,_,_=s.evaluate({('TCS.NS',DAY):rows,('TCS.NS',date(2026,3,6)):bars(date(2026,3,6))},p,'train')
    for r in result:
        if r['arm'] in ('breakout_reclaim','vwap_reclaim','combined_reclaim'):
            assert r['retry'] is None and r['loss_avoided']
            assert all(not d['passed'] and not d['score_reused'] for d in r['decisions'])


def fake_seal(tmp_path,p,monkeypatch):
    s.write_json(tmp_path/'protocol.json',p)
    monkeypatch.setattr(s,'hashes',lambda directory:{'source':'frozen'})
    s.save_rows(tmp_path/'train_validation_episodes.json.gz',[])
    summary=training_summary(p);selected,scores=s.choose(summary,p)
    seal=dict(hashes={'source':'frozen'},selected_arm=selected,training_scores=scores,training=summary,
              episodes_sha256=s.sha((tmp_path/'train_validation_episodes.json.gz').read_bytes()))
    s.write_json(tmp_path/'selection.json',seal)
    return seal


def test_holdout_consumption_and_identical_resume(tmp_path,p,monkeypatch):
    fake_seal(tmp_path,p,monkeypatch)
    first=s.open_holdout(tmp_path)
    with pytest.raises(ValueError,match='previously_opened'):s.open_holdout(tmp_path)
    assert s.open_holdout(tmp_path,resume=True)==first
    s.write_json(tmp_path/'holdout.json',{})
    with pytest.raises(ValueError,match='consumed'):s.open_holdout(tmp_path,resume=True)


def test_changed_code_or_outcomes_cannot_resume_holdout(tmp_path,p,monkeypatch):
    seal=fake_seal(tmp_path,p,monkeypatch);s.open_holdout(tmp_path)
    monkeypatch.setattr(s,'hashes',lambda directory:{'source':'changed'})
    with pytest.raises(ValueError,match='sealed_inputs_changed'):s.open_holdout(tmp_path,resume=True)
    monkeypatch.setattr(s,'hashes',lambda directory:{'source':'frozen'})
    s.save_rows(tmp_path/'train_validation_episodes.json.gz',[{'fake':'trade'}])
    with pytest.raises(ValueError,match='outcomes_changed'):s.verify_seal(tmp_path,seal)


def test_failed_acquisition_marks_holdout_before_network(tmp_path,p,monkeypatch):
    fake_seal(tmp_path,p,monkeypatch)
    observed=[]
    def failed(*args,**kwargs):
        assert (tmp_path/'holdout_opened.json').exists()
        observed.append(args[0]);raise TimeoutError()
    monkeypatch.setattr(fetch.httpx,'get',failed)
    fetch.acquire('holdout',tmp_path)
    manifest=json.loads((tmp_path/'holdout/manifest.json').read_text())
    assert len(observed)>0 and all(r['rows']==0 and r['error']=='TimeoutError' for r in manifest['requests'])
    with pytest.raises(ValueError,match='snapshot_already_exists'):fetch.acquire('holdout',tmp_path,resume=True)


def test_training_cannot_reopen_after_holdout(tmp_path,p):
    s.write_json(tmp_path/'holdout_opened.json',{})
    with pytest.raises(ValueError,match='holdout_opened'):s.run_train(tmp_path)


def test_statistical_pass_is_never_automatic_promotion(p):
    seal={'selected_arm':s.BASE,'training':[],'validation':[]}
    result=s.holdout_report([],seal,p)
    assert not result['statistical_pass'] and not result['production_promotion']
    assert result['decision']=='RETAIN_PRODUCTION_POLICY'
    assert 'holdout_coverage_below_predeclared_minimum' in result['reasons']


def test_a_delayed_reentry_can_add_a_new_loss(p):
    p=deepcopy(p);p['symbols']=['TCS.NS'];p['splits']['train']=[str(DAY),str(DAY)]
    p['entry_times']=['10:00'];p['execution_scenarios']=['sampled_30m'];p['costs']['slippage_bps_per_side']=[5]
    rows=bars()
    rows=setbar(rows,'10:25',98.)
    rows=setbar(rows,'10:35',99.)
    rows=setbar(rows,'11:05',101.)
    rows=setbar(rows,'11:10',102.)
    rows=setbar(rows,'11:15',103.)
    rows=setbar(rows,'11:20',104.)
    rows=setbar(rows,'11:30',104.)
    rows=setbar(rows,'11:55',99.)
    result,_,_=s.evaluate({('TCS.NS',DAY):rows,('TCS.NS',date(2026,3,6)):bars(date(2026,3,6))},p,'train')
    row=next(r for r in result if r['arm']=='breakout_reclaim')
    assert row['retry_at']==at('11:30').isoformat() and row['disposition']=='delayed'
    assert row['delayed_retry_net_inr']<0
    assert row['paired_gain_inr']==pytest.approx(row['losses_avoided_inr']-row['recoveries_missed_inr']+row['delayed_retry_net_inr'])
    assert s.stats([row])['delayed_new_losses']==1


def test_late_failed_seed_is_not_counted_as_avoided_loss(p):
    p=deepcopy(p);p['symbols']=['TCS.NS'];p['splits']['train']=[str(DAY),str(DAY)]
    p['entry_times']=['13:00'];p['execution_scenarios']=['sampled_30m'];p['costs']['slippage_bps_per_side']=[5]
    # Flat raw prices still lose charges/slippage at the 15:25 deadline.
    result,counts,excluded=s.evaluate({('TCS.NS',DAY):bars(),('TCS.NS',date(2026,3,6)):bars(date(2026,3,6))},p,'train')
    assert result==[] and not excluded
    assert counts['sampled_30m|5|net_loss']==1 and counts['sampled_30m|5|no_later_retry_slot']==1


def test_unavailable_full_session_is_excluded_not_a_profitable_no_trade(p):
    p=deepcopy(p);p['symbols']=['TCS.NS'];p['splits']['train']=[str(DAY),str(DAY)]
    result,counts,excluded=s.evaluate({},p,'train')
    assert result==[] and counts=={}
    assert excluded==[{'symbol':'TCS.NS','day':str(DAY),'reason':'current_or_preceding_full_session_unavailable'}]


def test_changed_selected_arm_cannot_open_holdout(tmp_path,p,monkeypatch):
    seal=fake_seal(tmp_path,p,monkeypatch)
    seal['selected_arm']='combined_reclaim';s.write_json(tmp_path/'selection.json',seal)
    with pytest.raises(ValueError,match='selection_changed'):s.open_holdout(tmp_path)
    assert not (tmp_path/'holdout_opened.json').exists()


def test_acquisition_refuses_training_overwrite_after_seal(tmp_path,p,monkeypatch):
    fake_seal(tmp_path,p,monkeypatch)
    monkeypatch.setattr(fetch.httpx,'get',lambda *a,**k:pytest.fail('request must not happen'))
    with pytest.raises(ValueError,match='training_already_sealed'):fetch.acquire('training',tmp_path)


@pytest.mark.parametrize('when,schedule',[(date(2026,2,27),'2024-10-01'),(date(2026,3,9),'2026-03-01')])
def test_new_study_spans_dated_exchange_schedule(p,when,schedule):
    previous=when-timedelta(days=1)
    sample=s.sample_at('TCS.NS',when,at('10:00',when),bars(when),bars(previous),p)
    row=s.leg(s.prepare(sample,5,p),'sampled_30m',p)
    assert row['exchange_schedule']==schedule
    assert row['fee_model']==fees.MODEL
