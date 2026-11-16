"""Render the sealed measurements, without choosing another candidate."""
import argparse
import csv
import io
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
LABELS = {'current_price_rules': 'Current price rules', 'net_activation': 'Net-cost activation',
          'atr_profit_lock': 'ATR profit lock', 'reachable_target': 'Prior-session target',
          'combined': 'Combined (locked selection)'}


def render():
    p = json.loads((HERE/'protocol.json').read_text())
    selection = json.loads((HERE/'selection.json').read_text())
    holdout = json.loads((HERE/'holdout.json').read_text())
    phases = {'train': selection['training'], 'validation': selection['validation'], 'holdout': holdout['results']}
    rows = [dict(phase=phase, **r) for phase, values in phases.items() for r in values]
    stream = io.StringIO(newline='')
    fields = ['phase', 'arm', 'scenario', 'slippage_bps'] + sorted(set(rows[0]) - {'phase', 'arm', 'scenario', 'slippage_bps'})
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
    writer.writeheader()
    writer.writerows([{k: json.dumps(v, sort_keys=True) if isinstance(v, dict) else v for k, v in r.items()} for r in rows])
    lines = [
        '# Goal 08 — Profit protection and target study', '',
        '**Decision: retain production settings.** The training-selected combined candidate did not pass validation or the untouched chronological holdout. A higher target-hit/win rate did not produce positive net expectancy or robust improvement.', '',
        'This is an **intraday exit-only experiment with fixed-clock entries**, not a replay of actual MARS selections, portfolio returns, or the original closed-trade ledger. All five arms receive identical stock/date/time entries. The result does not establish the profitability of the full application.', '',
        '## Evidence and fixed design', '',
        '- Protocol committed before acquisition: `dd6b5b2aa89c2d9e7f57d8f5efbd1a6250d4f35a`.',
        '- Source-only correction before any return evaluation: `23d35cee3c6dbd8c2496f2f80ac19941ff70576f`. Goal04 routes completed five-minute history to intraday targets; the baseline uses that ATR and its 1–6% band. The original protocol remains in `protocol-v1.json`; the amendment records the correction.',
        '- Runner, source/data hashes and training choice sealed before holdout: `8ea0f8ae38cac69df413bbf2b441e30d48c0fa72`. Validation did not switch the winner. The holdout-opening record remains even after an interrupted evaluation.',
        '- All 15 names from the pre-existing 15-stock panel; 90/90 Upstox historical requests returned HTTP 200 and 118,335 rows. Exactly 1,575 complete regular stock-sessions contain 118,125 bars. Another 210 special-session rows (15 names × 14 bars) are explicitly outside the verified regular-session schedule.',
        '- Entry clocks: 10:00 and 13:00 IST; square-off: 15:25 open. INR 100,000 fee-inclusive independent observation size, integer shares, no reinvestment or re-entry. Two clocks overlap; sums are not a deployable capital curve.',
        '- Complete sessions and exactly 20 preceding complete sessions are required. ATR and target calibration use only bars completed before entry. No missing bar is forward-filled and no current-session future price sets a target.',
        '- Existing Goal05 Upstox Basic estimates, dated NSE charges, both executed orders, and 5/15/30 basis points of adverse slippage **per side**. Historical Basic use is a counterfactual plan assumption, not an old contract-note claim.',
        '- Execution sensitivity: open-high-low-close, open-low-high-close, and completed-close observations every 30 minutes. The OHLC paths are scenarios, not guaranteed mathematical bounds or known tick ordering. Gaps fill at the observed open, not a better old barrier.', '',
        '| Period | Dates | Sessions | Matched entries per arm/scenario | Symbols |',
        '|---|---|---:|---:|---:|'
    ]
    for phase,values in phases.items():
        r=values[0]
        lines.append(f"| {phase} | {' to '.join(p['splits'][phase])} | {r['sessions']} | {r['count']} | {r['symbols']} |")
    lines += ['', 'Each entry is replayed under five arms × three execution scenarios × three slippage assumptions: **95,850 simulated outcomes**, including 31,050 holdout outcomes. They are not 95,850 independent trades; the holdout has 690 paired entries across 23 days.', '',
        '## Holdout comparison — 30-minute observations, 5 bps per side', '',
        'All figures include both order charges and adverse buy/sell fills. Net return is per entry notional. One basis point is 0.01%. Mean INR amounts reflect integer sizing below the INR 100,000 cap.', '',
        '| Exit policy | Mean net bps | Mean net INR | Win % | Target-hit % | Payoff ratio | Daily-mean drawdown bps* |',
        '|---|---:|---:|---:|---:|---:|---:|']
    ref=[r for r in holdout['results'] if r['scenario']=='sampled_30m' and r['slippage_bps']==5]
    for a in p['arms']:
        r=next(r for r in ref if r['arm']==a['id'])
        lines.append(f"| {LABELS[a['id']]} | {r['mean_net_bps']:.3f} | {r['mean_net_inr']:.2f} | {r['win_pct']:.2f} | {r['target_hit_pct']:.2f} | {r['payoff_ratio']:.3f} | {r['daily_mean_net_drawdown_bps']:.3f} |")
    lines += ['', '*Drawdown is the peak-to-trough fall in the cumulative series of equal-weight daily mean net trade returns. It is neither mark-to-market nor portfolio drawdown; capital concurrency, unrealized excursions and actual strategy selection are outside this experiment.', '',
        '## What the comparison shows', '',
        '1. The combined policy increased the measured win rate and target-hit rate but reduced average winning payoff enough to worsen net expectancy. More target exits are not, by themselves, a profitable improvement.',
        '2. A 1% trailing distance generally cannot lock net profit before a 1% target: charges and sell slippage require extra room. In the holdout, the net-activation arm could activate below its target in only '+f"{next(r for r in ref if r['arm']=='net_activation')['activation_below_target_pct']:.3f}%"+' of entries. It therefore behaves mostly like removing the early trailing exit, not adding effective profit protection.',
        '3. ATR-based activation can become reachable earlier, but that alone did not create positive net expectancy. Stop-to-entry locks also do not protect against price gaps.',
        '4. Observation cadence matters. A candle high touching a target between refreshes is not evidence that MARS could have sold there. The delayed-observation scenario retains missed targets and adverse observed fills.',
        '5. These fixed-clock entries have no tested selection edge. A full-strategy replay must preserve real point-in-time entry gates, score changes, sector/danger exits, quote cadence and portfolio limits before considering promotion.', '',
        '## Locked candidate across periods', '',
        '| Period | Current mean net bps | Combined mean net bps | Difference bps |',
        '|---|---:|---:|---:|']
    for phase,values in phases.items():
        d={r['arm']:r for r in values if r['scenario']=='sampled_30m' and r['slippage_bps']==5}
        base,candidate=d['current_price_rules']['mean_net_bps'],d[selection['selected_arm']]['mean_net_bps']
        lines.append(f'| {phase} | {base:.3f} | {candidate:.3f} | {candidate-base:+.3f} |')
    lines += ['', '## Holdout execution and slippage sensitivity', '',
        'The following compares only the candidate selected on training. Confidence intervals resample whole session days (2,000 draws, fixed seed) so simultaneous stocks are not treated as independent days. All intervals include zero; none establishes robust improvement.', '',
        '| Execution scenario | Slippage bps/side | Current net bps | Combined net bps | Paired gain bps | 95% day-cluster interval bps |',
        '|---|---:|---:|---:|---:|---|']
    for pair in holdout['paired']:
        d={r['arm']:r for r in holdout['results'] if r['scenario']==pair['scenario'] and r['slippage_bps']==pair['slippage_bps']}
        lo,hi=pair['ci95_bps']
        lines.append(f"| {pair['scenario']} | {pair['slippage_bps']} | {d['current_price_rules']['mean_net_bps']:.3f} | {d[selection['selected_arm']]['mean_net_bps']:.3f} | {pair['mean_gain_bps']:+.3f} | [{lo:.3f}, {hi:.3f}] |")
    lines += ['', '## Costs, excursions and concentration', '',
        'The CSV includes gross return, separate fees and slippage, flat/loss/win counts, target distance, target-hit rate, profit factor/payoff, hold duration lower bounds, observed adverse/favorable excursions, giveback and defined drawdown for all 135 period/arm/scenario/slippage combinations. Individual compressed outcome files retain every entry, barrier, fill, cost model and feature timestamp. Within-bar exit time is a five-minute window; only its lower bound is reported for duration.', '',
        'The sealed holdout JSON additionally includes per-symbol and per-entry-clock concentration for the selected candidate at 30-minute cadence and 5 bps. Neither those tables nor better-looking nonselected holdout arms may be used to retune and reuse this holdout.', '',
        '## Decision and remaining evidence', '',
        '- Statistical rejection reasons: `'+ '`, `'.join(holdout['reasons'])+'`.',
        '- No application settings, existing stops/targets, ledger entries, sizing or re-entry rules are changed. There is no apply operation in this research tool.',
        '- This holdout is now consumed. Future optimization needs a new untouched period. Full-strategy replay and forward paper validation remain required; a later candidate also needs owner review before changing thresholds.',
        '- Current large-cap universe selection, corporate-action adjustments/provider revisions, exchange tick ordering, spread/impact/queue fills, score-triggered trail tightening and other dynamic exits are not independently reconstructed. These are limits on promotion, not zeros or assumed passes.',
        '- This is intraday evidence. Swing, Short, Momentum and Positional require their own horizon-specific studies before settings changes.', '',
        '## Reproduce offline', '',
        'From the repository root, with the pinned requirements installed:', '',
        '```sh',
        'python -m pytest trading/tests/test_profit_protection_research_2026_11_16.py -q -ra',
        'python -m research.profit_protection_2026_11_16.study verify',
        'python -m research.profit_protection_2026_11_16.report --check',
        '```', '',
        'The verifier replays all three periods, checks every rounded individual outcome and aggregate, verifies the frozen selection and source hashes, and performs no network or settings writes. Acquisition, training and holdout commands refuse silent replacement; an interrupted holdout can resume only the same sealed inputs.', '',
        '## Sources', '',
        '- [Upstox V3 historical-candle contract](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/): minute intervals and timezone-bearing bar-start timestamps; source rechecked 16 November 2026. Exact request URLs, receipt times and raw-byte hashes are in the manifest.',
        '- [Upstox published charges](https://upstox.com/brokerage-charges/): intraday brokerage and statutory components, rechecked 16 November 2026; [Goal05 schedule and assumptions](../../docs/repairs/evidence/2026-11-12-cost-sources.json).',
        '- [NSE FA73061](https://nsearchives.nseindia.com/content/circulars/FA73061.pdf): cash exchange/IPFT revision effective 1 March 2026, rechecked 16 November 2026.',
        '- Baseline source: `trading/engine.py` scan -> `pick.history=signal_history` -> `_target_for`; `trading/targets.py`; `trading/exits.py`; `trading/modes.py`. Source hashes and the pinned baseline revision are retained.', ''
    ]
    return {'results.csv':stream.getvalue(),'README.md':'\n'.join(lines)}


def main(check=False):
    for name,text in render().items():
        p=HERE/name
        if check:
            if not p.exists() or p.read_text()!=text:
                raise ValueError(f'research_report_outdated: {name}')
        else:
            p.write_text(text)
    print('Goal08 report and 135-row comparison match sealed measurements.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true')
    main(parser.parse_args().check)
