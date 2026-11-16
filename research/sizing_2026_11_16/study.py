"""Frozen net-stop sizing experiment; no production/ledger mutation interface."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import date, datetime, time, timezone
import gzip
import json
import math
from pathlib import Path
import platform
import importlib.metadata
import subprocess
import numpy as np
from trading import fees
from research.profit_protection_2026_11_16 import study as exits
from research.reentry_2026_11_16 import study as retry

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
BASE='fixed_slab'
sha,write_json=exits.sha,exits.write_json
clean,save_rows=retry.clean,retry.save_rows


def protocol_at(directory):
    directory=Path(directory)
    p=json.loads((directory/'protocol.json').read_text())
    u=json.loads((directory/'universe.json').read_text())
    if p['symbols']!=u['symbols'] or len(set(p['symbols']))!=15:
        raise ValueError('universe_changed')
    for old in (exits.HERE,retry.HERE):
        prior=json.loads((old/'protocol.json').read_text())
        if set(p['symbols'])&set(prior['symbols']):
            raise ValueError('previously_consumed_symbol_panel')
    if not (p['data_start']<p['splits']['train'][0]<=p['splits']['train'][1]<p['splits']['validation'][0]
            <=p['splits']['validation'][1]<p['splits']['holdout'][0]<=p['splits']['holdout'][1]==p['data_end']):
        raise ValueError('overlapping_periods')
    if p['interval_minutes']!=5 or p['feature_lag_minutes']<5:
        raise ValueError('unproved_feature_timing')
    return p


def require_runtime(directory):
    r=json.loads((Path(directory)/'runtime.json').read_text())
    if (platform.python_implementation()!=r['implementation'] or platform.python_version()!=r['python'] or
        any(importlib.metadata.version(k)!=v for k,v in r['packages'].items())):
        raise ValueError('frozen_numerical_runtime_required')


def net_stop_loss(qty,entry,stop,bps,day,symbol):
    if qty==0:
        return 0.
    if not (isinstance(qty,int) and qty>0 and all(math.isfinite(v) for v in (entry,stop,bps))
            and 0<stop<entry and 0<=bps<10000):
        raise ValueError('invalid_stop_risk_inputs')
    buy=fees.charge(qty,entry,'BUY','intraday',day,symbol)['total']
    net,_,_=exits.sale_net(qty,entry,buy,stop,bps,day,symbol)
    return -net


def risk_quantity(entry,stop,bps,day,symbol,budget,notional_cap,minimum):
    if not all(math.isfinite(x) and x>=0 for x in (budget,notional_cap,minimum)):
        raise ValueError('invalid_risk_budget')
    # Validate even a zero-quantity request; bad stops cannot masquerade as no risk.
    net_stop_loss(1,entry,stop,bps,day,symbol)
    lo,hi=0,int(notional_cap//entry)
    while lo<hi:
        mid=(lo+hi+1)//2
        if net_stop_loss(mid,entry,stop,bps,day,symbol)<=budget:
            lo=mid
        else:
            hi=mid-1
    if lo*entry<minimum:
        return 0,'below_minimum_within_risk'
    return lo,'sized'


def performance(rows,asof,p,*,complete=True):
    """Only finished prior-day shadow exits; all reasons and costs count."""
    if any(r['day']>=str(asof) for r in rows):
        raise ValueError('future_or_same_day_performance')
    known=[]
    for r in rows:
        fields=[r.get(k) for k in ('net_pnl','buy_fees','sell_fees','qty','entry_fill')]
        if any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in fields):
            continue
        if r['qty']<=0 or r['entry_fill']<=0 or r['buy_fees']<0 or r['sell_fees']<0:
            continue
        known.append(r)
    returns=[r['net_pnl']/(r['qty']*r['entry_fill']) for r in known]
    wins=[r for r in returns if r>0];losses=[r for r in returns if r<0]
    rules=p['performance'];n=len(returns);sessions=len({r['day'] for r in known})
    detail={'n':n,'sessions':sessions,'wins':len(wins),'losses':len(losses),
            'unknown':len(rows)-n,'complete_window':complete,'last_day':max((r['day'] for r in known),default=None),
            'net_pnl':math.fsum(r['net_pnl'] for r in known) if known else None,
            'costs':math.fsum(r['buy_fees']+r['sell_fees'] for r in known) if known else None,
            'fraction':None,'payoff':None,'kelly':None,'reasons':dict(sorted(Counter(r['exit_reason'] for r in known).items()))}
    if not complete or len(rows)!=n:
        return {**detail,'status':'UNAVAILABLE'}
    if not n:
        return {**detail,'status':'EMPTY'}
    if n<rules['minimum_trades'] or sessions<rules['minimum_sessions']:
        return {**detail,'status':'INSUFFICIENT'}
    if not wins:
        return {**detail,'status':'ALL_LOSS' if losses else 'ALL_FLAT','fraction':0.}
    if not losses:
        return {**detail,'status':'NO_LOSS'}
    b=(math.fsum(wins)/len(wins))/(-math.fsum(losses)/len(losses))
    prob=(len(wins)+rules['prior_equivalent_trades']*rules['prior_win_rate'])/(n+rules['prior_equivalent_trades'])
    f=prob-(1-prob)/b
    return {**detail,'status':'ACTIVE' if f>0 else 'NO_POSITIVE_EDGE','payoff':b,'kelly':f,
            'fraction':max(0.,f)*rules['kelly_fraction']*min(1.,n/rules['full_confidence'])}


def order_at(sample,bps,p,qty=None):
    entry=sample.bars[0].open*(1+bps/10000)
    if qty is None:
        qty=math.ceil(p['minimum_notional_inr']/entry)
    buy=fees.charge(qty,entry,'BUY','intraday',sample.day,sample.symbol)['total']
    cfg=p['baseline'];pct=min(cfg['target_max_fraction'],max(cfg['target_min_fraction'],cfg['target_atr_k']*sample.intraday_atr/entry))
    target=round(entry*(1+pct),2)
    return exits.Order(sample,bps,qty,entry,buy,0.,round(entry*(1-cfg['stop_fraction']),2),target,target,0.)


def size(arm,order,reading,loss_reading,p):
    if arm==BASE:
        return order.qty,'fixed_slab'
    if arm=='no_entry':
        return 0,'diagnostic_no_entry'
    cap=p['maximum_notional_inr']
    if arm=='measured_stop_risk':
        if reading['status']!='ACTIVE':
            return 0,'performance_'+reading['status'].lower()
        cap=min(cap,reading['fraction']*p['reference_equity_inr'])
    elif arm=='loss_budget_stop':
        if not loss_reading['complete'] or loss_reading['net_pnl'] is None:
            return 0,'loss_window_unavailable'
        if loss_reading['net_pnl']<=-p['loss_control']['net_loss_budget_inr']:
            return 0,'prior_net_loss_budget'
    elif arm!='stop_risk':
        raise ValueError('unknown_arm')
    return risk_quantity(order.entry,order.stop,order.bps,order.sample.day,order.sample.symbol,
                         p['net_stop_budget_inr'],cap,p['minimum_notional_inr'])


def evaluate(sessions,p,phase):
    start,end=p['splits'][phase];calendar=list(exits.days_between(p['data_start'],end))
    output=[];exclusions=[];coverage={}
    # Reference observations are common to every policy. Rejected trades do not
    # become claimed fills; their counterfactual outcomes remain labelled shadow.
    for scenario in p['execution_scenarios']:
        for bps in p['costs']['slippage_bps_per_side']:
            shadow={}
            for index,day in enumerate(calendar):
                window=calendar[max(0,index-p['performance']['window_sessions']):index]
                past=[r for d in window for r in shadow.get(d,[])]
                complete=len(window)>=p['performance']['minimum_sessions'] and all(len(shadow.get(d,[]))==len(p['symbols']) for d in window)
                reading=performance(past,day,p,complete=complete)
                ld=calendar[max(0,index-p['loss_control']['window_sessions']):index]
                lr=[r for d in ld for r in shadow.get(d,[])]
                loss={'net_pnl':math.fsum(r['net_pnl'] for r in lr) if lr else None,
                      'complete':len(ld)==p['loss_control']['window_sessions'] and all(len(shadow.get(d,[]))==len(p['symbols']) for d in ld),
                      'last_day':str(ld[-1]) if ld else None,'observations':len(lr)}
                current=[]
                for symbol in p['symbols']:
                    if index==0 or (symbol,day) not in sessions or (symbol,calendar[index-1]) not in sessions:
                        if start<=str(day)<=end and scenario==p['execution_scenarios'][0] and bps==5:
                            exclusions.append({'symbol':symbol,'day':str(day),'reason':'missing_current_or_previous_full_session'})
                        continue
                    at=datetime.combine(day,time(10),exits.IST)
                    sample=retry.sample_at(symbol,day,at,sessions[symbol,day],sessions[symbol,calendar[index-1]],p)
                    order=order_at(sample,bps,p)
                    ref=exits.simulate(order,retry.PRICE_ARM,scenario,p)
                    current.append(ref)
                    if not start<=str(day)<=end:
                        continue
                    for arm in p['arms']:
                        qty,reason=size(arm['id'],order,reading,loss,p)
                        actual=None
                        if qty:
                            changed=replace(order,qty=qty,buy_fee=fees.charge(qty,order.entry,'BUY','intraday',day,symbol)['total'])
                            actual=exits.simulate(changed,retry.PRICE_ARM,scenario,p)
                        net=actual['net_pnl'] if actual else 0.
                        planned=net_stop_loss(qty,order.entry,order.stop,bps,day,symbol)
                        output.append({'sample':sample.id,'symbol':symbol,'day':str(day),'arm':arm['id'],
                            'scenario':scenario,'slippage_bps':bps,'qty':qty,'reason':reason,'trade':actual,
                            'reference_net':ref['net_pnl'],'reference_qty':order.qty,'net_pnl':net,
                            'net_stop_risk':planned,'stop_risk_exceeded':bool(qty and -net>planned+1e-8),
                            'performance':reading,'loss_reading':loss,
                            'loss_reduction':net-ref['net_pnl'] if ref['net_pnl']<0 else 0.,
                            'profit_forgone':ref['net_pnl']-net if ref['net_pnl']>0 else 0.,
                            'flat_reference_difference':net if ref['net_pnl']==0 else 0.})
                shadow[day]=current
    return clean(output),exclusions


def stats(rows):
    trades=[r for r in rows if r['trade']];net=[r['net_pnl'] for r in rows]
    days=defaultdict(float)
    for day in sorted({r['day'] for r in rows}):
        days[day]=math.fsum(r['net_pnl'] for r in rows if r['day']==day)
    equity=peak=dd=0.
    for value in days.values():
        equity+=value;peak=max(peak,equity);dd=max(dd,peak-equity)
    return clean({'opportunities':len(rows),'trades':len(trades),'days':len(days),'symbols':len({r['symbol'] for r in rows}),
        'total_net':math.fsum(net),'net_per_opportunity':math.fsum(net)/len(rows) if rows else None,
        'net_per_trade':math.fsum(net)/len(trades) if trades else None,
        'win_pct':100*sum(r['net_pnl']>0 for r in trades)/len(trades) if trades else None,
        'losses':sum(r['net_pnl']<0 for r in trades),'max_closed_day_drawdown':dd,
        'gross':math.fsum(r['trade']['gross_pnl'] for r in trades),
        'fees':math.fsum(r['trade']['buy_fees']+r['trade']['sell_fees'] for r in trades),
        'slippage':math.fsum(r['trade']['slippage_inr'] for r in trades),
        'average_notional':math.fsum(r['qty']*r['trade']['entry_fill'] for r in trades)/len(trades) if trades else None,
        'planned_stop_risk_sum':math.fsum(r['net_stop_risk'] for r in trades),
        'stop_risk_exceeded':sum(r['stop_risk_exceeded'] for r in rows),
        'loss_reduction':math.fsum(r['loss_reduction'] for r in rows),'profit_forgone':math.fsum(r['profit_forgone'] for r in rows),
        'paired_gain':math.fsum(r['net_pnl']-r['reference_net'] for r in rows),
        'reasons':dict(sorted(Counter(r['reason'] for r in rows).items())),
        'performance_states':dict(sorted(Counter(r['performance']['status'] for r in rows).items()))})


def summarize(rows):
    groups=defaultdict(list)
    for row in rows:
        groups[row['arm'],row['scenario'],row['slippage_bps']].append(row)
    return [dict(arm=k[0],scenario=k[1],slippage_bps=k[2],**stats(v)) for k,v in sorted(groups.items())]


def choose(summary,p):
    scores={}
    for arm in p['arms']:
        if arm['selectable']:
            parts=[r['paired_gain']/r['opportunities'] for r in summary if r['arm']==arm['id'] and r['slippage_bps']==5 and r['opportunities']]
            if len(parts)!=len(p['execution_scenarios']):
                raise ValueError('missing_selection_stratum')
            scores[arm['id']]=min(parts)
    return max(scores,key=scores.get),clean(scores)


def paired(rows,p):
    groups=defaultdict(list)
    for r in rows:
        groups[r['arm'],r['scenario'],r['slippage_bps']].append(r)
    out=[]
    for (arm,scenario,bps),group in sorted(groups.items()):
        days=sorted({r['day'] for r in group})
        totals=np.array([math.fsum(r['net_pnl']-r['reference_net'] for r in group if r['day']==d) for d in days])
        counts=np.array([sum(r['day']==d for r in group) for d in days])
        rng=np.random.default_rng(p['evidence_thresholds']['bootstrap_seed'])
        idx=rng.integers(0,len(days),size=(p['evidence_thresholds']['paired_bootstrap_resamples'],len(days)))
        dist=totals[idx].sum(axis=1)/counts[idx].sum(axis=1)
        out.append({'arm':arm,'scenario':scenario,'slippage_bps':bps,'mean_gain':float(totals.sum()/counts.sum()),
                    'lower95':float(np.quantile(dist,.025)),'upper95':float(np.quantile(dist,.975)),'day_clusters':len(days)})
    return clean(out)


def holdout_report(rows,seal,p):
    summary=summarize(rows);bounds=paired(rows,p);selected=seal['selected_arm'];reasons=[]
    limits=p['evidence_thresholds']
    if selected==BASE:
        reasons.append('training_selected_control')
    for phase,s in [('train',seal['training']),('validation',seal['validation']),('holdout',summary)]:
        for r in s:
            if r['arm']!=selected or r['slippage_bps'] not in (5,15):
                continue
            key=f"{phase}/{r['scenario']}/{r['slippage_bps']}"
            if r['days']<limits[f'minimum_{phase}_sessions'] or r['symbols']<limits['minimum_symbols']:
                reasons.append(key+':coverage')
            if phase!='train':
                control=next(b for b in s if b['arm']==BASE and b['scenario']==r['scenario'] and b['slippage_bps']==r['slippage_bps'])
                if r['net_per_trade'] is None or r['net_per_trade']<=0:reasons.append(key+':no_positive_net_expectancy')
                if r['paired_gain']<=0:reasons.append(key+':no_paired_gain')
                if r['max_closed_day_drawdown']>control['max_closed_day_drawdown']:reasons.append(key+':larger_drawdown')
            if phase=='holdout':
                if r['trades']<limits['minimum_holdout_candidate_trades']:reasons.append(key+':too_few_executed_trades')
                b=next(v for v in bounds if v['arm']==selected and v['scenario']==r['scenario'] and v['slippage_bps']==r['slippage_bps'])
                if b['lower95']<=0:reasons.append(key+':gain_not_established')
    return {'selected_arm':selected,'summary':summary,'paired':bounds,'statistical_pass':not reasons,
            'reasons':reasons,'promotion':'NO_AUTOMATIC_PROMOTION; actual strategy and forward paper proof still required'}


def hashes(directory):
    d=Path(directory)
    paths={n:d/n for n in ('protocol.json','runtime.json','requirements.txt','universe.json',
                            'training/protocol.json','training/manifest.json','training/raw_responses.json.gz')}
    for name in ('research/sizing_2026_11_16/study.py','research/sizing_2026_11_16/fetch.py',
                 'research/reentry_2026_11_16/study.py','research/profit_protection_2026_11_16/study.py',
                 'research/profit_protection_2026_11_16/fetch.py','trading/fees.py','trading/calendars.py',
                 'trading/intraday_bars.py','trading/targets.py','trading/exits.py','trading/modes.py','requirements.txt'):
        paths['repo:'+name]=ROOT/name
    return {k:sha(v.read_bytes()) for k,v in paths.items()}


def verify_seal(directory,seal):
    if seal['hashes']!=hashes(directory):raise ValueError('sealed_inputs_changed')
    selected,scores=choose(seal['training'],protocol_at(directory))
    if selected!=seal['selected_arm'] or scores!=seal['training_scores']:raise ValueError('selection_changed')
    if sha((Path(directory)/'train_validation_outcomes.json.gz').read_bytes())!=seal['outcomes_sha256']:
        raise ValueError('training_outcomes_changed')


def run_train(directory=HERE):
    d=Path(directory);require_runtime(d)
    if (d/'selection.json').exists() or (d/'holdout_opened.json').exists() or (d/'holdout').exists():
        raise ValueError('training_already_sealed_or_holdout_opened')
    p=protocol_at(d);sessions,coverage=retry.load_stage(d,'training',p)
    train,te=evaluate(sessions,p,'train');summary=summarize(train);selected,scores=choose(summary,p)
    validation,ve=evaluate(sessions,p,'validation')
    save_rows(d/'train_validation_outcomes.json.gz',[dict(phase='train',**r) for r in train]+[dict(phase='validation',**r) for r in validation])
    seal=dict(schema=1,sealed_at=datetime.now(timezone.utc).isoformat(),hashes=hashes(d),
              selected_arm=selected,training_scores=scores,training=summary,validation=summarize(validation),
              coverage=coverage,exclusions={'train':te,'validation':ve},
              outcomes_sha256=sha((d/'train_validation_outcomes.json.gz').read_bytes()),
              holdout='NOT_REQUESTED; commit this selection and all hashed files before acquisition')
    write_json(d/'selection.json',seal)
    print(json.dumps({'selected':selected,'scores':scores,'train_rows':len(train),'validation_rows':len(validation)}),flush=True)


def open_holdout(directory=HERE,*,resume=False):
    d=Path(directory);require_runtime(d)
    if (d/'holdout.json').exists():raise ValueError('holdout_consumed')
    seal=json.loads((d/'selection.json').read_text());verify_seal(d,seal)
    selection_hash=sha((d/'selection.json').read_bytes());marker=d/'holdout_opened.json'
    if marker.exists():
        value=json.loads(marker.read_text())
        if not resume or value['selection_sha256']!=selection_hash:raise ValueError('holdout_previously_opened')
    else:
        # Require the actual retained source/data/selection bytes in Git before
        # making the first request. Local unstaged edits cannot open a holdout.
        for name in [*seal['hashes'],'selection.json','train_validation_outcomes.json.gz']:
            path=ROOT/name[5:] if name.startswith('repo:') else d/name
            committed=subprocess.check_output(['git','show','HEAD:'+str(path.relative_to(ROOT))],cwd=ROOT)
            if committed!=path.read_bytes():raise ValueError('uncommitted_holdout_inputs')
        value={'opened_at':datetime.now(timezone.utc).isoformat(),'selection_sha256':selection_hash,
               'selection_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
               'meaning':'First acquisition begins now. Interruption cannot restore unseen status; only identical sealed inputs can resume.'}
        write_json(marker,value)
    return value


def run_holdout(directory=HERE):
    d=Path(directory);marker=open_holdout(d,resume=True)
    seal=json.loads((d/'selection.json').read_text());p=protocol_at(d)
    sessions,_=retry.load_stage(d,'training',p);held,coverage=retry.load_stage(d,'holdout',p)
    if set(sessions)&set(held):raise ValueError('holdout_overlaps_training')
    manifest=json.loads((d/'holdout/manifest.json').read_text())
    if (manifest['selection_sha256_before_request']!=marker['selection_sha256'] or
        datetime.fromisoformat(manifest['started_at'])<datetime.fromisoformat(marker['opened_at'])):
        raise ValueError('holdout_requested_before_seal')
    sessions.update(held);rows,excluded=evaluate(sessions,p,'holdout');result=holdout_report(rows,seal,p)
    save_rows(d/'holdout_outcomes.json.gz',rows)
    result.update(coverage=coverage,exclusions=excluded,holdout_consumed=True,
                  opened_at=marker['opened_at'],selection_sha256=marker['selection_sha256'],selection_commit=marker['selection_commit'],
                  holdout_hashes={n:sha((d/'holdout'/n).read_bytes()) for n in ('protocol.json','manifest.json','raw_responses.json.gz')},
                  outcomes_sha256=sha((d/'holdout_outcomes.json.gz').read_bytes()))
    write_json(d/'holdout.json',result)
    print(json.dumps({'rows':len(rows),'selected':result['selected_arm'],'statistical_pass':result['statistical_pass'],'reasons':result['reasons']}),flush=True)


def verify(directory=HERE):
    d=Path(directory);require_runtime(d);p=protocol_at(d)
    seal=json.loads((d/'selection.json').read_text());verify_seal(d,seal)
    result=json.loads((d/'holdout.json').read_text());marker=json.loads((d/'holdout_opened.json').read_text())
    if (result['selection_sha256']!=sha((d/'selection.json').read_bytes()) or
        marker['selection_sha256']!=result['selection_sha256'] or marker['selection_commit']!=result['selection_commit'] or
        result['opened_at']!=marker['opened_at'] or not result['holdout_consumed']):raise ValueError('holdout_selection_link_changed')
    for name,digest in result['holdout_hashes'].items():
        if sha((d/'holdout'/name).read_bytes())!=digest:raise ValueError('holdout_input_changed')
    if sha((d/'holdout_outcomes.json.gz').read_bytes())!=result['outcomes_sha256']:raise ValueError('holdout_outcomes_changed')
    training,coverage=retry.load_stage(d,'training',p)
    if coverage!=seal['coverage']:raise ValueError('training_coverage_changed')
    stored=json.loads(gzip.decompress((d/'train_validation_outcomes.json.gz').read_bytes()))
    for phase,label in [('train','training'),('validation','validation')]:
        rows,excluded=evaluate(training,p,phase)
        observed=[{k:v for k,v in r.items() if k!='phase'} for r in stored if r['phase']==phase]
        if rows!=observed or summarize(rows)!=seal[label] or excluded!=seal['exclusions'][phase]:
            raise ValueError('training_or_validation_replay_changed')
    held,coverage=retry.load_stage(d,'holdout',p)
    if set(training)&set(held):raise ValueError('holdout_overlaps_training')
    manifest=json.loads((d/'holdout/manifest.json').read_text())
    if (manifest['selection_sha256_before_request']!=marker['selection_sha256'] or
        datetime.fromisoformat(manifest['started_at'])<datetime.fromisoformat(marker['opened_at'])):
        raise ValueError('holdout_requested_before_seal')
    training.update(held);rows,excluded=evaluate(training,p,'holdout')
    if rows!=json.loads(gzip.decompress((d/'holdout_outcomes.json.gz').read_bytes())):raise ValueError('holdout_decisions_or_outcomes_changed')
    replay=holdout_report(rows,seal,p)
    if any(result[k]!=v for k,v in replay.items()) or excluded!=result['exclusions'] or coverage!=result['coverage']:
        raise ValueError('holdout_summary_changed')
    print('Goal10 protocol, source, separate acquisition, causal sizes, accounting and all outcomes reproduce exactly.',flush=True)


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('phase',choices=['train','holdout','verify'])
    {'train':run_train,'holdout':run_holdout,'verify':verify}[ap.parse_args().phase]()
