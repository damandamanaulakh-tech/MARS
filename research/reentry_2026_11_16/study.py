"""Causal one-retry experiments, isolated from production and its ledger.

Every arm shares the same failed seed within an execution/slippage stratum.
Future retry outcomes are labels for analysis, never inputs to setup decisions.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
import gzip
import json
import math
from pathlib import Path
from statistics import mean

import numpy as np

from research.profit_protection_2026_11_16 import study as exits
from trading import fees

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
IST = exits.IST
BASE = 'next_scan'
PRICE_ARM = {'id': 'current_price_rules', 'trail': 'current', 'target': 'entry_atr'}
sha, encoded, write_json = exits.sha, exits.encoded, exits.write_json


def protocol_at(directory):
    p = json.loads((Path(directory)/'protocol.json').read_text())
    prior = json.loads((exits.HERE/'protocol.json').read_text())
    if (p['freshness']['goal08_data_start'] != prior['data_start'] or
            p['freshness']['goal08_data_end'] != prior['data_end']):
        raise ValueError('previous_study_range_misrepresented')
    if (p['data_end'] >= p['freshness']['goal08_data_start'] or
            not (p['splits']['train'][1] < p['splits']['validation'][0] <=
                 p['splits']['validation'][1] < p['splits']['holdout'][0])):
        raise ValueError('overlapping_or_nonchronological_periods')
    if p['interval_minutes'] != 5 or p['feature_lag_minutes'] < 5:
        raise ValueError('unsupported_interval_or_unavailable_latency')
    return p


def load_stage(directory, stage, p):
    path = Path(directory)/stage
    if (path/'protocol.json').read_bytes() != (Path(directory)/'protocol.json').read_bytes():
        raise ValueError('acquisition_protocol_changed')
    m = json.loads((path/'manifest.json').read_text())
    start, end = ((p['data_start'], p['splits']['validation'][1])
                  if stage == 'training' else p['splits']['holdout'])
    if m['data_start'] != start or m['data_end'] != end or m['stage'] != stage:
        raise ValueError('stage_range_changed')
    if any(r['start'] < start or r['end'] > end for r in m['requests']):
        raise ValueError('request_outside_stage')
    sessions, coverage = exits.load_data(path, {**p, 'data_start': start, 'data_end': end})
    return sessions, coverage


def known_exit(trade):
    ts = datetime.fromisoformat(trade['exit_time'])
    if ts.tzinfo is None:
        raise ValueError('exit_timezone_unavailable')
    if trade['exit_precision'] == 'within_5m_bar':
        return ts + timedelta(minutes=5)
    if trade['exit_precision'] not in ('bar_open_proxy', 'completed_close_proxy'):
        raise ValueError('exit_precision_unavailable')
    return ts


def retry_slots(day, failure_known, p):
    end = datetime.combine(day, time.fromisoformat(p['last_retry']), IST)
    ts = datetime.combine(day, time(9, 30), IST)
    slots = []
    while ts <= end:
        if ts > failure_known:
            slots.append(ts)
        ts += timedelta(minutes=p['scan_interval_minutes'])
    return slots


def completed(bars, at, p):
    if at.tzinfo is None:
        raise ValueError('decision_timezone_unavailable')
    cutoff = at - timedelta(minutes=p['feature_lag_minutes'])
    return [b for b in bars if b.ts + timedelta(minutes=5) <= cutoff]


def crossing(values, levels):
    """A reset and subsequent reclaim must both be observed after the loss."""
    reset, crossed = None, None
    for bar, level in zip(values, levels):
        if level is None:
            reset = crossed = None
        elif bar.close <= level:
            reset, crossed = bar.ts, None
        elif reset is not None and crossed is None:
            crossed = bar.ts
    return reset, crossed


def changed_setup(bars, at, failure_known, threshold, p):
    """Decision inputs contain history and the known loss only, never its retry."""
    if at <= failure_known or at.date() != failure_known.date():
        raise ValueError('retry_not_after_same_session_failure')
    prefix = completed(bars, at, p)
    post = [b for b in prefix if b.ts >= failure_known]
    detail = {'decision_at': at.isoformat(), 'failure_known_at': failure_known.isoformat(),
              'feature_cutoff': (at-timedelta(minutes=p['feature_lag_minutes'])).isoformat(),
              'last_completed_bar': prefix[-1].ts.isoformat() if prefix else None,
              'post_loss_bars': len(post), 'breakout_threshold': threshold,
              'score_reused': False}
    if not prefix or not post:
        return {**detail, 'breakout_reclaim': False, 'vwap_reclaim': False,
                'state': 'unavailable', 'reason': 'no_fully_completed_post_loss_bar'}
    if (len({b.ts for b in prefix}) != len(prefix) or prefix != sorted(prefix, key=lambda b: b.ts)
            or any(b.ts.date() != at.date() or b.ts.tzinfo is None for b in prefix)):
        raise ValueError('invalid_decision_history')
    opened = datetime.combine(at.date(), time(9,15), IST)
    expected = []
    ts = opened
    while ts + timedelta(minutes=5) <= at-timedelta(minutes=p['feature_lag_minutes']):
        expected.append(ts)
        ts += timedelta(minutes=5)
    if [b.ts for b in prefix] != expected:
        return {**detail, 'breakout_reclaim': False, 'vwap_reclaim': False,
                'state': 'unavailable', 'reason': 'incomplete_completed_session_prefix'}
    if not math.isfinite(threshold) or threshold <= 0:
        raise ValueError('invalid_frozen_threshold')
    numerator = volume = 0.
    vwaps = {}
    for bar in prefix:
        if (not all(math.isfinite(v) for v in (bar.open, bar.high, bar.low, bar.close, bar.volume))
                or min(bar.open, bar.high, bar.low, bar.close) <= 0 or bar.volume < 0
                or bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close)):
            raise ValueError('invalid_decision_ohlcv')
        numerator += (bar.high+bar.low+bar.close)/3 * bar.volume
        volume += bar.volume
        vwaps[bar.ts] = numerator/volume if volume > 0 and bar.volume > 0 else None
    reset, cross = crossing(post, [threshold]*len(post))
    vreset, vcross = crossing(post, [vwaps[b.ts] for b in post])
    breakout = cross is not None and post[-1].close > threshold
    vwap = bool(vcross is not None and len(post) >= 2
                and all(vwaps[b.ts] is not None and b.close > vwaps[b.ts] for b in post[-2:])
                and post[-1].close > post[-2].close)
    unavailable = any(vwaps[b.ts] is None for b in post[-2:])
    stamp = lambda ts: ts.isoformat() if ts else None
    return {**detail, 'state': 'observed', 'reason': 'completed_post_loss_evidence',
            'latest_close': post[-1].close, 'previous_close': post[-2].close if len(post)>1 else None,
            'latest_vwap': vwaps[post[-1].ts],
            'breakout_reset_bar': stamp(reset), 'breakout_cross_bar': stamp(cross),
            'vwap_reset_bar': stamp(vreset), 'vwap_cross_bar': stamp(vcross),
            'breakout_reclaim': breakout, 'vwap_reclaim': vwap,
            'vwap_state': 'unavailable' if unavailable else 'observed'}


def rule_passes(arm, detail, at, failure_known):
    if arm == BASE:
        return True
    if arm == 'wait_30m':
        return at >= failure_known + timedelta(minutes=30)
    if arm == 'no_retry':
        return False
    if arm in ('breakout_reclaim', 'vwap_reclaim'):
        return bool(detail[arm])
    if arm == 'combined_reclaim':
        return bool(detail['breakout_reclaim'] and detail['vwap_reclaim'])
    raise ValueError('unknown_reentry_rule')


def sample_at(symbol, day, at, current, prior, p):
    prefix = prior + completed(current, at, p)
    if len(prefix) < 15:
        raise ValueError('insufficient_completed_atr_history')
    last = prefix[-15:]
    path = tuple(b for b in current if b.ts >= at and b.ts.strftime('%H:%M') <= p['deadline'])
    if not path or path[0].ts != at:
        raise ValueError('entry_bar_unavailable')
    return exits.Sample(symbol, day, at.strftime('%H:%M'), path, 0., exits.true_range(last),
                        0., last[-1].ts, prior[-1].ts.date())


def prepare(sample, bps, p):
    """Current price rules only: cost-aware integer size and frozen 5m ATR target.

    The shared exit Order has extra candidate fields unused by the current rule.
    They are not published as measurements in this study.
    """
    if not 0 <= bps < 10000:
        raise ValueError('invalid_slippage')
    entry = sample.bars[0].open*(1+bps/10000)
    qty = fees.affordable_qty(int(p['stake_inr']//entry), entry, p['stake_inr'],
                             'intraday', sample.day, sample.symbol)
    if qty < 1:
        raise ValueError('stake_cannot_afford_one_share')
    buy = fees.charge(qty, entry, 'BUY', 'intraday', sample.day, sample.symbol)['total']
    cfg = p['baseline']
    pct = min(cfg['target_max_fraction'], max(cfg['target_min_fraction'],
              cfg['target_atr_k']*sample.intraday_atr/entry))
    target = round(entry*(1+pct), 2)
    return exits.Order(sample, bps, qty, entry, buy, 0., round(entry*(1-cfg['stop_fraction']),2),
                       target, target, 0.)


def leg(order, scenario, p):
    row = exits.simulate(order, PRICE_ARM, scenario, p)
    for key in ('net_break_even_quote', 'trail_activation_quote', 'activation_below_target',
                'calibration_last_day', 'arm', 'sample'):
        row.pop(key)
    return row


def evaluate(sessions, p, phase):
    start, end = p['splits'][phase]
    calendar = list(exits.days_between(p['data_start'], end))
    rows, exclusions, seeds = [], [], Counter()
    for symbol in p['symbols']:
        for index, day in enumerate(calendar):
            if not start <= str(day) <= end:
                continue
            prior_day = calendar[index-1] if index else None
            if (symbol, day) not in sessions or (symbol, prior_day) not in sessions:
                exclusions.append(dict(symbol=symbol, day=str(day), reason='current_or_preceding_full_session_unavailable'))
                continue
            current, prior = sessions[(symbol, day)], sessions[(symbol, prior_day)]
            orders, trades = {}, {}

            def trade_at(at, bps, scenario):
                key = (at, bps)
                if key not in orders:
                    orders[key] = prepare(sample_at(symbol, day, at, current, prior, p), bps, p)
                if (key, scenario) not in trades:
                    trades[(key, scenario)] = leg(orders[key], scenario, p)
                return trades[(key, scenario)]

            for clock in p['entry_times']:
                at = datetime.combine(day, time.fromisoformat(clock), IST)
                prefix = prior + completed(current, at, p)
                frozen_high = max(b.high for b in prefix[-p['breakout_lookback_bars']:])
                for bps in p['costs']['slippage_bps_per_side']:
                    for scenario in p['execution_scenarios']:
                        tag = f'{scenario}|{bps}'
                        seeds[tag+'|evaluated'] += 1
                        first = trade_at(at, bps, scenario)
                        if first['net_pnl'] >= 0:
                            seeds[tag+'|not_net_loss'] += 1
                            continue
                        seeds[tag+'|net_loss'] += 1
                        failure_known = known_exit(first)
                        slots = retry_slots(day, failure_known, p)
                        if not slots:
                            seeds[tag+'|no_later_retry_slot'] += 1
                            continue
                        seeds[tag+'|paired_episode'] += 1
                        threshold = max(frozen_high, first['entry_fill'])
                        details = {slot: changed_setup(current, slot, failure_known, threshold, p) for slot in slots}
                        baseline = trade_at(slots[0], bps, scenario)
                        for arm in p['arms']:
                            attempts, chosen = [], None
                            for slot in slots:
                                passed = rule_passes(arm['id'], details[slot], slot, failure_known)
                                attempts.append({**details[slot], 'passed': passed, 'rule': arm['id']})
                                if passed:
                                    chosen = slot
                                    break
                            outcome = trade_at(chosen, bps, scenario) if chosen else None
                            net = outcome['net_pnl'] if outcome else 0.
                            withheld = chosen != slots[0]
                            avoided = max(-baseline['net_pnl'], 0.) if withheld else 0.
                            missed = max(baseline['net_pnl'], 0.) if withheld else 0.
                            delayed = net if withheld else 0.
                            gain = net-baseline['net_pnl']
                            if not math.isclose(gain, avoided-missed+delayed, abs_tol=1e-7):
                                raise ValueError('counterfactual_attribution_does_not_reconcile')
                            rows.append(dict(
                                episode=f'{symbol}|{day}|{clock}', symbol=symbol, day=str(day), clock=clock,
                                arm=arm['id'], scenario=scenario, slippage_bps=bps, seed_trade=first,
                                failure_known_at=failure_known.isoformat(), frozen_breakout_threshold=threshold,
                                baseline_retry_at=slots[0].isoformat(), baseline_retry=baseline,
                                retry_at=chosen.isoformat() if chosen else None, retry=outcome,
                                decisions=attempts, disposition='same_slot' if not withheld else 'delayed' if chosen else 'no_retry',
                                retry_net_inr=net, retry_net_bps=net/p['stake_inr']*10000,
                                episode_total_net_inr=first['net_pnl']+net,
                                paired_gain_inr=gain, paired_gain_bps=gain/p['stake_inr']*10000,
                                losses_avoided_inr=avoided, recoveries_missed_inr=missed,
                                delayed_retry_net_inr=delayed,
                                loss_avoided=bool(avoided>0), recovery_missed=bool(missed>0)))
    return rows, dict(seeds), exclusions


def stats(rows):
    if not rows:
        return dict(count=0, status='unavailable', mean_retry_net_bps=None, mean_gain_bps=None)
    trades = [r for r in rows if r['retry'] is not None]
    wins = [r for r in trades if r['retry_net_inr']>1e-8]
    losses = [r for r in trades if r['retry_net_inr'] < -1e-8]
    day_returns = defaultdict(list)
    for r in rows:
        day_returns[r['day']].append(r['retry_net_bps'])
    level = peak = drawdown = 0.
    for day in sorted(day_returns):
        level += mean(day_returns[day]); peak = max(peak, level)
        drawdown = max(drawdown, peak-level)
    result = dict(count=len(rows), sessions=len(day_returns), symbols=len({r['symbol'] for r in rows}),
                  retry_trades=len(trades), wins=len(wins), losses=len(losses),
                  flat=len(trades)-len(wins)-len(losses),
                  win_pct=100*len(wins)/len(trades) if trades else None,
                  mean_retry_net_inr=mean(r['retry_net_inr'] for r in rows),
                  mean_retry_net_bps=mean(r['retry_net_bps'] for r in rows),
                  mean_gain_bps=mean(r['paired_gain_bps'] for r in rows),
                  total_retry_net_inr=sum(r['retry_net_inr'] for r in rows),
                  total_gain_inr=sum(r['paired_gain_inr'] for r in rows),
                  total_episode_net_inr=sum(r['episode_total_net_inr'] for r in rows),
                  losses_avoided=sum(r['loss_avoided'] for r in rows),
                  losses_avoided_inr=sum(r['losses_avoided_inr'] for r in rows),
                  recoveries_missed=sum(r['recovery_missed'] for r in rows),
                  recoveries_missed_inr=sum(r['recoveries_missed_inr'] for r in rows),
                  delayed_retry_net_inr=sum(r['delayed_retry_net_inr'] for r in rows),
                  delayed_new_losses=sum(r['disposition']=='delayed' and r['retry_net_inr']<0 for r in rows),
                  delayed_recoveries=sum(r['disposition']=='delayed' and r['retry_net_inr']>0 for r in rows),
                  dispositions=dict(Counter(r['disposition'] for r in rows)),
                  gross_inr=sum(r['retry']['gross_pnl'] for r in trades),
                  fees_inr=sum(r['retry']['buy_fees']+r['retry']['sell_fees'] for r in trades),
                  slippage_inr=sum(r['retry']['slippage_inr'] for r in trades),
                  payoff_ratio=(mean(r['retry_net_inr'] for r in wins)/abs(mean(r['retry_net_inr'] for r in losses)) if wins and losses else None),
                  payoff_status='measured' if wins and losses else 'no_trades' if not trades else 'no_wins' if not wins else 'no_losses',
                  daily_mean_retry_drawdown_bps=drawdown)
    return {k: round(v,8) if isinstance(v,float) else v for k,v in result.items()}


def summarize(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[(r['arm'],r['scenario'],r['slippage_bps'])].append(r)
    return [dict(arm=a,scenario=s,slippage_bps=b,**stats(v)) for (a,s,b),v in sorted(groups.items())]


def choose(summary, p):
    scores = {}
    for arm in p['arms']:
        if not arm['selectable']:
            continue
        rows = [r for r in summary if r['arm']==arm['id'] and r['slippage_bps']==5]
        if (len(rows)!=len(p['execution_scenarios']) or
                {r['scenario'] for r in rows}!=set(p['execution_scenarios']) or
                any(r['mean_gain_bps'] is None for r in rows)):
            raise ValueError('training_scenario_unavailable')
        scores[arm['id']] = min(r['mean_gain_bps'] for r in rows)
    return max(scores, key=scores.get), scores


def paired(rows, p):
    ids = [r['episode'] for r in rows]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate_paired_episode')
    if not rows:
        return dict(count=0, mean_gain_bps=None, ci95_bps=None)
    days = defaultdict(list)
    for r in rows:
        if not math.isclose(r['paired_gain_inr'],r['retry_net_inr']-r['baseline_retry']['net_pnl'],abs_tol=1e-6):
            raise ValueError('unpaired_counterfactual')
        days[r['day']].append(r['paired_gain_bps'])
    sums = np.array([sum(days[d]) for d in sorted(days)])
    counts = np.array([len(days[d]) for d in sorted(days)])
    cfg=p['evidence_thresholds']
    rng=np.random.default_rng(cfg['bootstrap_seed'])
    ix=rng.integers(0,len(days),(cfg['paired_bootstrap_resamples'],len(days)))
    boot=sums[ix].sum(axis=1)/counts[ix].sum(axis=1)
    return dict(count=len(rows), sessions=len(days),mean_gain_bps=round(float(sums.sum()/counts.sum()),8),
                ci95_bps=[round(float(x),8) for x in np.quantile(boot,[.025,.975])],
                unit='Matched failed-seed episodes; resample whole session days')


def holdout_report(rows, seal, p):
    selected=seal['selected_arm']; cfg=p['evidence_thresholds']; reasons=[]; pairs=[]
    if selected==BASE:
        reasons.append('training_selected_baseline')
    for phase in ('training','validation'):
        group=[r for r in seal[phase] if r['arm']==selected]
        floor=cfg['minimum_train_sessions' if phase=='training' else 'minimum_validation_sessions']
        if not group or any(r.get('sessions',0)<floor or r.get('symbols',0)<cfg['minimum_symbols'] for r in group):
            reasons.append(phase+'_coverage_below_predeclared_minimum')
    for scenario in p['execution_scenarios']:
        for bps in p['costs']['slippage_bps_per_side']:
            chosen=[r for r in rows if r['arm']==selected and r['scenario']==scenario and r['slippage_bps']==bps]
            comparison=paired(chosen,p)
            pairs.append(dict(scenario=scenario,slippage_bps=bps,**comparison))
            if bps>15:
                continue
            result=stats(chosen)
            if (result['count']<cfg['minimum_holdout_pairs'] or result.get('sessions',0)<cfg['minimum_holdout_sessions']
                    or result.get('symbols',0)<cfg['minimum_symbols'] or result.get('retry_trades',0)<cfg['minimum_holdout_candidate_trades']):
                reasons.append('holdout_coverage_below_predeclared_minimum')
            if result['mean_retry_net_bps'] is None or result['mean_retry_net_bps']<=0:
                reasons.append('holdout_retry_expectancy_not_positive')
            if comparison['mean_gain_bps'] is None or comparison['mean_gain_bps']<=0 or comparison['ci95_bps'][0]<=0:
                reasons.append('holdout_gain_not_robust')
            val=[r for r in seal['validation'] if r['arm']==selected and r['scenario']==scenario and r['slippage_bps']==bps]
            if len(val)!=1 or val[0]['mean_retry_net_bps'] is None or val[0]['mean_retry_net_bps']<=0 or val[0]['mean_gain_bps']<=0:
                reasons.append('validation_retry_expectancy_or_gain_not_positive')
    concentration={}
    subset=[r for r in rows if r['arm']==selected and r['scenario']=='sampled_30m' and r['slippage_bps']==5]
    for key in ('symbol','clock'):
        concentration[key]={v:stats([r for r in subset if r[key]==v]) for v in sorted({r[key] for r in subset})}
    return dict(selected_arm=selected,summary=summarize(rows),paired=pairs,
                concentration=concentration,statistical_pass=not reasons,reasons=sorted(set(reasons)),
                production_promotion=False,decision='NOMINATE_FOR_FURTHER_VALIDATION' if not reasons else 'RETAIN_PRODUCTION_POLICY')


def hashes(directory):
    directory=Path(directory)
    paths={str(p.relative_to(directory)):p for p in [directory/'protocol.json',directory/'training/protocol.json',
           directory/'training/manifest.json',directory/'training/raw_responses.json.gz']}
    for name in ['research/reentry_2026_11_16/study.py','research/reentry_2026_11_16/fetch.py',
                 'research/profit_protection_2026_11_16/study.py','research/profit_protection_2026_11_16/fetch.py',
                 'research/profit_protection_2026_11_16/protocol.json',
                 'trading/fees.py','trading/calendars.py','trading/intraday_bars.py','trading/targets.py',
                 'trading/exits.py','trading/modes.py','requirements.txt']:
        paths['repo:'+name]=ROOT/name
    return {k:sha(path.read_bytes()) for k,path in paths.items()}


def verify_seal(directory,seal):
    if seal['hashes']!=hashes(directory):
        raise ValueError('sealed_inputs_changed; no retuning an opened holdout')
    selected,scores=choose(seal['training'],protocol_at(directory))
    if selected!=seal['selected_arm'] or scores!=seal['training_scores']:
        raise ValueError('selection_changed')
    if sha((Path(directory)/'train_validation_episodes.json.gz').read_bytes())!=seal['episodes_sha256']:
        raise ValueError('training_outcomes_changed')


def clean(value):
    if isinstance(value,float):
        return round(value,8)
    if isinstance(value,dict):
        return {k:clean(v) for k,v in value.items()}
    if isinstance(value,list):
        return [clean(v) for v in value]
    return value


def save_rows(path,rows):
    Path(path).write_bytes(gzip.compress(json.dumps(clean(rows),sort_keys=True,separators=(',',':'),allow_nan=False).encode(),mtime=0))


def run_train(directory=HERE):
    directory=Path(directory)
    if (directory/'selection.json').exists() or (directory/'holdout_opened.json').exists() or (directory/'holdout').exists():
        raise ValueError('training_already_sealed_or_holdout_opened')
    p=protocol_at(directory)
    sessions,coverage=load_stage(directory,'training',p)
    train,train_seeds,train_excluded=evaluate(sessions,p,'train')
    summary=summarize(train)
    selected,scores=choose(summary,p)
    validation,val_seeds,val_excluded=evaluate(sessions,p,'validation')
    save_rows(directory/'train_validation_episodes.json.gz',
              [dict(phase='train',**r) for r in train]+[dict(phase='validation',**r) for r in validation])
    seal=dict(schema=1,sealed_at=datetime.now(timezone.utc).isoformat(),hashes=hashes(directory),
              selected_arm=selected,training_scores=scores,training=summary,validation=summarize(validation),
              coverage=coverage,seeds={'train':train_seeds,'validation':val_seeds},
              exclusions={'train':train_excluded,'validation':val_excluded},
              episodes_sha256=sha((directory/'train_validation_episodes.json.gz').read_bytes()),
              holdout='NOT_REQUESTED; fresh raw data must be acquired only after this seal is committed')
    write_json(directory/'selection.json',seal)
    print(json.dumps({'selected':selected,'scores':scores,'train_rows':len(train),'validation_rows':len(validation)}))


def open_holdout(directory=HERE,*,resume=False):
    directory=Path(directory)
    if (directory/'holdout.json').exists():
        raise ValueError('holdout_consumed')
    seal=json.loads((directory/'selection.json').read_text())
    verify_seal(directory,seal)
    selection_hash=sha((directory/'selection.json').read_bytes())
    marker=directory/'holdout_opened.json'
    if marker.exists():
        value=json.loads(marker.read_text())
        if not resume or value['selection_sha256']!=selection_hash:
            raise ValueError('holdout_previously_opened; resume identical inputs only')
    else:
        value=dict(opened_at=datetime.now(timezone.utc).isoformat(),selection_sha256=selection_hash,
                   meaning='Holdout acquisition/evaluation began. Failure does not make this data unseen again.')
        write_json(marker,value)
    return value


def run_holdout(directory=HERE):
    directory=Path(directory)
    marker=open_holdout(directory,resume=True)
    seal=json.loads((directory/'selection.json').read_text()); p=protocol_at(directory)
    sessions,_=load_stage(directory,'training',p)
    held,coverage=load_stage(directory,'holdout',p)
    if set(sessions)&set(held):
        raise ValueError('holdout_overlaps_training')
    manifest=json.loads((directory/'holdout/manifest.json').read_text())
    if (manifest['selection_sha256_before_request']!=marker['selection_sha256'] or
            datetime.fromisoformat(manifest['started_at'])<datetime.fromisoformat(marker['opened_at'])):
        raise ValueError('holdout_requested_before_seal')
    sessions.update(held)
    rows,seeds,excluded=evaluate(sessions,p,'holdout')
    result=holdout_report(rows,seal,p)
    save_rows(directory/'holdout_episodes.json.gz',rows)
    result.update(coverage=coverage,seeds=seeds,exclusions=excluded,holdout_consumed=True,
                  opened_at=marker['opened_at'],selection_sha256=marker['selection_sha256'],
                  holdout_hashes={name:sha((directory/'holdout'/name).read_bytes()) for name in
                                  ('protocol.json','manifest.json','raw_responses.json.gz')},
                  episodes_sha256=sha((directory/'holdout_episodes.json.gz').read_bytes()))
    write_json(directory/'holdout.json',result)
    print(json.dumps({'rows':len(rows),'selected':result['selected_arm'],'statistical_pass':result['statistical_pass'],'reasons':result['reasons']}))


def verify(directory=HERE):
    directory=Path(directory); p=protocol_at(directory)
    seal=json.loads((directory/'selection.json').read_text()); verify_seal(directory,seal)
    result=json.loads((directory/'holdout.json').read_text())
    marker=json.loads((directory/'holdout_opened.json').read_text())
    if result['selection_sha256']!=sha((directory/'selection.json').read_bytes()) or marker['selection_sha256']!=result['selection_sha256']:
        raise ValueError('holdout_selection_link_changed')
    if result['opened_at']!=marker['opened_at'] or not result['holdout_consumed']:
        raise ValueError('holdout_access_record_changed')
    for name,digest in result['holdout_hashes'].items():
        if sha((directory/'holdout'/name).read_bytes())!=digest:
            raise ValueError('holdout_input_changed')
    if sha((directory/'holdout_episodes.json.gz').read_bytes())!=result['episodes_sha256']:
        raise ValueError('holdout_outcomes_changed')
    training,coverage=load_stage(directory,'training',p)
    if coverage!=seal['coverage']:
        raise ValueError('training_coverage_changed')
    stored=json.loads(gzip.decompress((directory/'train_validation_episodes.json.gz').read_bytes()))
    for phase,label in [('train','training'),('validation','validation')]:
        rows,seeds,excluded=evaluate(training,p,phase)
        observed=[{k:v for k,v in r.items() if k!='phase'} for r in stored if r['phase']==phase]
        if clean(rows)!=observed or summarize(rows)!=seal[label] or seeds!=seal['seeds'][phase] or excluded!=seal['exclusions'][phase]:
            raise ValueError('training_or_validation_replay_changed')
    held,coverage=load_stage(directory,'holdout',p)
    if set(training)&set(held):
        raise ValueError('holdout_overlaps_training')
    manifest=json.loads((directory/'holdout/manifest.json').read_text())
    if (manifest['selection_sha256_before_request']!=marker['selection_sha256'] or
            datetime.fromisoformat(manifest['started_at'])<datetime.fromisoformat(marker['opened_at'])):
        raise ValueError('holdout_requested_before_seal')
    training.update(held)
    rows,seeds,excluded=evaluate(training,p,'holdout')
    if clean(rows)!=json.loads(gzip.decompress((directory/'holdout_episodes.json.gz').read_bytes())):
        raise ValueError('holdout_episode_replay_changed')
    replay=holdout_report(rows,seal,p)
    if any(result[k]!=v for k,v in replay.items()) or seeds!=result['seeds'] or excluded!=result['exclusions'] or coverage!=result['coverage']:
        raise ValueError('holdout_summary_replay_changed')
    print('Goal09 source, separate acquisition, decisions, episodes, attribution and all periods reproduce exactly.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['train','holdout','verify'])
    args=parser.parse_args()
    {'train':run_train,'holdout':run_holdout,'verify':verify}[args.phase]()
