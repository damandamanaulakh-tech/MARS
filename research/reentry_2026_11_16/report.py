"""Render only sealed results; --check detects committed report drift."""
import argparse
import csv
import gzip
import io
import json
from pathlib import Path

from . import study as s

NAMES={'next_scan':'Next scan (baseline)','wait_30m':'Wait 30 minutes (control)',
       'breakout_reclaim':'Post-loss breakout reclaim','vwap_reclaim':'Post-loss VWAP reclaim',
       'combined_reclaim':'Both reclaims','no_retry':'No retry (control)'}


def number(value,digits=2):
    return 'unavailable' if value is None else f'{value:,.{digits}f}'


def render(directory=s.HERE):
    directory=Path(directory)
    p=s.protocol_at(directory)
    seal=json.loads((directory/'selection.json').read_text())
    hold=json.loads((directory/'holdout.json').read_text())
    rows=[dict(phase=phase,**row) for phase,group in [('train',seal['training']),
          ('validation',seal['validation']),('holdout',hold['summary'])] for row in group]
    fields=list(rows[0])
    csv_text=io.StringIO(newline='')
    writer=csv.DictWriter(csv_text,fieldnames=fields,lineterminator='\n')
    writer.writeheader()
    for row in rows:
        writer.writerow({k:json.dumps(v,sort_keys=True) if isinstance(v,dict) else v for k,v in row.items()})
    main={r['arm']:r for r in hold['summary'] if r['scenario']=='sampled_30m' and r['slippage_bps']==5}
    chosen=main[seal['selected_arm']]
    pair=next(r for r in hold['paired'] if r['scenario']=='sampled_30m' and r['slippage_bps']==5)
    full=json.loads(gzip.decompress((directory/'holdout_episodes.json.gz').read_bytes()))
    chosen_trades=[r for r in full if r['arm']==seal['selected_arm'] and r['scenario']=='sampled_30m'
                   and r['slippage_bps']==5 and r['retry'] is not None]
    manifests={stage:json.loads((directory/stage/'manifest.json').read_text()) for stage in ('training','holdout')}
    raw_count=sum(r['rows'] for m in manifests.values() for r in m['requests'])
    requests=sum(len(m['requests']) for m in manifests.values())
    accepted=seal['coverage']['valid_full_sessions']+hold['coverage']['valid_full_sessions']
    lines=[
        '# Goal 09 — Changed-setup re-entry after an intraday loss','',
        '**Decision: retain production policy; no candidate promoted.** The locked rule improves the matched retry comparison, but fails the declared sample and cross-scenario expectancy criteria. Almost never trading is not evidence of a reliable recovery strategy.','',
        'This is an **isolated, one-next-retry experiment after a common failed fixed-clock trade**, not actual MARS signals, recursive retries, cross-day state or portfolio P&L. All modeled trades are long intraday shares. Existing risk gates, protective exits, settings and the original financial ledger are unchanged.','',
        '## Fresh data and chronology','',
        '- Baseline: `9ba36523fe0df77adbee49dbe40a6201118db4b5`. The production scan checks category loss halts and existing holdings, but has no dedicated post-loss setup-transition gate. The older Momentum cooldown experiment also lost on its holdout; it is not intraday evidence.',
        '- [Protocol commit](https://github.com/damandamanaulakh-tech/MARS/commit/75e3ac34ba8de8f702d16b73e36bbbc5b6bfa331) precedes the first training request.',
        '- [Training-choice/code seal](https://github.com/damandamanaulakh-tech/MARS/commit/2fe824533ec300e76ab373ff177827f31c5fc645) precedes the first holdout request. Selection did not change after validation or holdout.',
        f"- Holdout access marked at `{hold['opened_at']}`; first request `{manifests['holdout']['started_at']}`. Raw bytes, URLs, timestamps, hashes and failures are retained in the separate acquisition folders.",
        f'- {requests} successful public Upstox requests; {raw_count:,} source bars; {accepted:,} complete regular stock-sessions / {accepted*75:,} accepted five-minute bars. The 1 February Sunday special session is explicitly outside the verified regular-session schedule, not forward-filled.',
        '- All dates precede Goal08\'s earliest raw bar, 15 June 2026. The 15-stock universe was fixed before new acquisition; no price/outcome from Goal08 is fitted here.',
        '- **Historical-market caveat:** older repo research used daily prices through June 2026. These are fresh, previously unexamined five-minute observations and a separately sealed intraday holdout; the market period itself is not claimed to be unknown history. Forward paper evidence is still required.','',
        '| Stage | Dates (2026) | Complete calendar sessions | Paired failed-seed episodes* | Days with episodes* |',
        '|---|---|---:|---:|---:|',
    ]
    for phase,group in [('train',seal['training']),('validation',seal['validation']),('holdout',hold['summary'])]:
        row=next(r for r in group if r['arm']==s.BASE and r['scenario']=='sampled_30m' and r['slippage_bps']==5)
        start,end=p['splits'][phase]
        lines.append(f"| {phase} | {start} to {end} | {len(list(s.exits.days_between(start,end)))} | {row['count']} | {row['sessions']} |")
    lines += [
        '', '*At 30-minute observations and 5 bps per side. Seed losses and remaining retry opportunities vary by fill scenario; comparisons are paired within each stratum, not across different cohorts. Late losses without a remaining retry slot are reported separately, not credited as avoided losses.',
        '', '## Rules and causal decision evidence','',
        '| Policy | Requirement | Selection role |','|---|---|---|',
    ]
    for arm in p['arms']:
        lines.append(f"| {NAMES[arm['id']]} | {arm['rule']} | {'Eligible' if arm['selectable'] else 'Diagnostic control only'} |")
    lines += [
        '', '- Failure means negative net P&L after both order charges and adverse fills, regardless of exit label. Each arm receives the same failed seed within its scenario/slippage stratum.',
        '- Retry scans run on half-hour boundaries, strictly after the loss is known, through 14:30 IST. Features end at least five minutes before entry. An intrabar exit is known only at its bar end.',
        '- Both reset and reclaim must be witnessed in complete post-loss bars. A same-bar rebound, a pre-loss crossing, elapsed time or an old score cannot qualify. Missing prefixes and zero-volume VWAP remain unavailable.',
        '- The breakout threshold is frozen at the higher of the failed slipped entry and six pre-entry completed highs. VWAP is a causal cumulative HLC3-volume proxy, not certified tick VWAP.',
        '- Enter at the first qualifying slot or do not retry that session. Every entry retains current hard-stop/trail/5m-ATR-target formulas and exits by the 15:25 open. A second failure does not start another retry in this bounded study.',
        '', '## Holdout: losses avoided and recoveries missed','',
        'The table uses **30-minute observations, 5 bps (0.05%) adverse slippage per side**, dated Goal05 Upstox Basic buy/sell charges, and at most INR100,000 including buy fees per isolated entry. Totals sum counterfactual observations; they are not a capital-constrained portfolio or booked MARS trades.',
        '', '| Policy | Retries made | Losing immediate retries withheld | Loss amount withheld INR | Profitable immediate retries missed | Profit missed INR | Delayed retry net INR | Total retry net INR |',
        '|---|---:|---:|---:|---:|---:|---:|---:|',
    ]
    for arm in p['arms']:
        r=main[arm['id']]
        lines.append(f"| {NAMES[arm['id']]} | {r['retry_trades']} | {r['losses_avoided']} | {number(r['losses_avoided_inr'])} | {r['recoveries_missed']} | {number(r['recoveries_missed_inr'])} | {number(r['delayed_retry_net_inr'])} | {number(r['total_retry_net_inr'])} |")
    lines += [
        '', '**Attribution identity:** paired net change = losing immediate retry amounts withheld − profitable immediate retry amounts missed + delayed retry net P&L. A delayed retry can itself lose; those losses remain in the calculation. No-trade decisions contribute zero retry P&L, not a winning trade.',
        '', f"The locked **{NAMES[seal['selected_arm']]}** rule withheld {chosen['losses_avoided']} losing immediate retries totaling INR{number(chosen['losses_avoided_inr'])}, but missed {chosen['recoveries_missed']} profitable immediate recoveries totaling INR{number(chosen['recoveries_missed_inr'])}. Its {chosen['retry_trades']} later trades contributed INR{number(chosen['delayed_retry_net_inr'])}. Paired improvement: INR{number(chosen['total_gain_inr'])} in aggregate, or {number(pair['mean_gain_bps'],3)} bps per failed-seed episode; 95% day-cluster interval [{number(pair['ci95_bps'][0],3)}, {number(pair['ci95_bps'][1],3)}] bps.",
        '', f"**The initial failed trades still happened.** Adding those common losses gives INR{number(chosen['total_episode_net_inr'])} for the selected policy's complete seed-plus-one-retry episodes in this scenario. Retry-only gains must not be presented as total-strategy profits.",
        '', '## Every modeled retry of the locked candidate in the main holdout scenario','',
        '| Stock | Seed entry | Loss known | Retry entry | Net retry INR | Exit |',
        '|---|---|---|---|---:|---|',
    ]
    for r in chosen_trades:
        lines.append(f"| {r['symbol']} | {r['day']} {r['clock']} IST | {r['failure_known_at']} | {r['retry_at']} | {number(r['retry_net_inr'])} | {r['retry']['exit_reason']} |")
    lines += ['', 'The compressed episode file retains each decision, frozen threshold, reset/cross witness timestamps, feature cutoff, selected fill, initial loss and counterfactual outcome. These are all selected-candidate retries for this scenario, not chosen winners.',
        '', '## Execution and slippage sensitivity — locked candidate','',
        '| Scenario | Slippage bps/side | Paired episodes | Candidate retries | Candidate retry net INR | Mean paired gain bps | 95% day-cluster interval bps |',
        '|---|---:|---:|---:|---:|---:|---|']
    for pair in hold['paired']:
        r=next(x for x in hold['summary'] if x['arm']==seal['selected_arm'] and x['scenario']==pair['scenario'] and x['slippage_bps']==pair['slippage_bps'])
        ci=pair['ci95_bps']
        lines.append(f"| {pair['scenario']} | {pair['slippage_bps']} | {r['count']} | {r['retry_trades']} | {number(r['total_retry_net_inr'])} | {number(pair['mean_gain_bps'],3)} | [{number(ci[0],3)}, {number(ci[1],3)}] |")
    lines += ['', 'Both OHLC orderings are sensitivity paths, not known tick sequences or mathematical bounds. Gaps fill at the observed opening price. Sampled observations do not fill a target merely because an intervening high touched it. Confidence intervals resample whole session days (2,000 fixed-seed draws).',
        '', '## Decision, coverage and limits','',
        f"- Statistical pass: **{hold['statistical_pass']}**. Reasons: "+', '.join(f'`{reason}`' for reason in hold['reasons'])+'.',
        '- The selected rule is too sparse in the declared 30-minute scenario; positive paired improvement mostly reflects avoiding the unfiltered retry. It does not establish that the rule consistently identifies profitable recoveries. Validation had no breakout-reclaim trades at that cadence.',
        '- Required floors (fixed before acquisition): 25 training / 15 validation / 20 holdout days with episodes, 12 symbols, 150 holdout paired episodes and 50 candidate retries; positive candidate retry expectancy and gain across all scenarios at 5/15 bps, with positive paired confidence bounds. Failed floors were retained, not relaxed.',
        '- The CSV contains all 162 stage/policy/scenario/slippage summaries, including gross, fees, slippage, no-trade counts, payoff, and explicitly defined cumulative daily-mean retry drawdown. This drawdown is not portfolio or mark-to-market drawdown.',
        '- Current-name selection, provider revisions/corporate-action adjustments, actual spread/impact/queues, scanner entry gates, category budgets/loss halts, dynamic score/sector/danger exits and capital concurrency are not reconstructed. Missing coverage is reported, not replaced with a successful result.',
        '- The 30-minute scenario matches the code default; the live owner scheduler setting was not read for this study.',
        '- Same-session one-retry evidence is not recursive/cross-day re-entry or evidence for Momentum, Swing, Short or Positional. Full-strategy and forward paper validation remain separate.',
        '- Holdout is consumed. No retuning or selecting another arm from these results. Future candidates need a new untouched dataset; no apply-to-settings or ledger operation exists.',
        '', '## Reproduce offline','',
        'Use **CPython 3.12.16** and the study-specific `requirements.txt` for exact numerical reproduction. The full application CI remains on Python 3.11. `runtime.json` preserves the initial CI failure: one aggregate differed by about INR0.00000001 because Python 3.12 changed floating-point summation. Pinning the original runtime preserves all frozen source, inputs, decisions, outputs and strict equality checks; no result was regenerated or tolerance widened.',
        '', '```sh',
        'python -m pip install -r research/reentry_2026_11_16/requirements.txt',
        'python -m pytest trading/tests/test_reentry_research_2026_11_16.py -q -ra',
        'python -m research.reentry_2026_11_16.study verify',
        'python -m research.reentry_2026_11_16.report --check','```','',
        'CI reproduces every episode and decision, signed loss/recovery attribution, all periods and the frozen training choice, alongside the full application suite and zero-skip guard.',
        '', '## Source contracts','',
        '- [Upstox V3 historical candles](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/): five-minute intervals, month-bounded retrieval and timezone-bearing bar-start timestamps; rechecked 16 November 2026.',
        '- [Published Upstox charges](https://upstox.com/brokerage-charges/) and [NSE FA73061](https://nsearchives.nseindia.com/content/circulars/FA73061.pdf); existing Goal05 dated fee model. Historical Basic is an explicit counterfactual tariff assumption, not proof of the old account plan.',
        '- [Earlier Momentum cooldown result](../stop_cooldown_study/summary.md) and [Goal08 consumed intraday study](../profit_protection_2026_11_16/README.md) remain unchanged.','']
    return {'README.md':'\n'.join(lines),'results.csv':csv_text.getvalue()}


def run(check=False,directory=s.HERE):
    for name,text in render(directory).items():
        path=Path(directory)/name
        if check:
            if not path.exists() or path.read_text()!=text:
                raise ValueError('report_does_not_match_sealed_measurements:'+name)
        else:
            path.write_text(text)
    print('Goal09 report matches sealed measurements.' if check else 'Goal09 report generated from sealed measurements.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true')
    run(parser.parse_args().check)
