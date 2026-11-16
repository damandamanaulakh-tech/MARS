"""Offline exit-only research. No engine, portfolio, broker or settings writes.

The frozen protocol defines the estimand: matched fixed-clock long entries,
not historical MARS signals. Holdout access requires a sealed train selection.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
from statistics import mean, median
from zoneinfo import ZoneInfo

import numpy as np

from trading import fees
from trading.calendars import is_trading_day
from trading.intraday_bars import bar_status

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
IST = ZoneInfo('Asia/Kolkata')
BASE = 'current_price_rules'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n').encode()


def write_json(path, value):
    Path(path).write_bytes(encoded(value))


def days_between(start, end):
    day, end = date.fromisoformat(start), date.fromisoformat(end)
    while day <= end:
        if is_trading_day(day):
            yield day
        day += timedelta(days=1)


@dataclass(frozen=True)
class Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def validate_session(rows, day, asof, interval=5):
    """Reject an incomplete session as a unit; no dropping bad price rows."""
    issues = Counter()
    parsed = []
    for row in rows:
        try:
            ts = datetime.fromisoformat(str(row[0]).replace('Z', '+00:00'))
            reason = bar_status(ts, interval, asof, current_session=False, fresh=False)
            if reason:
                issues[reason] += 1
                continue
            ts = ts.astimezone(IST)
            if ts.date() != day:
                issues['wrong_session'] += 1
                continue
            o, h, low, c, v = map(float, row[1:6])
            if not all(math.isfinite(x) for x in (o, h, low, c, v)):
                raise ValueError('nonfinite')
            if min(o, h, low, c) <= 0 or v < 0 or low > min(o, c) or h < max(o, c) or h < low:
                raise ValueError('incoherent')
            parsed.append(Bar(ts, o, h, low, c, v))
        except (ValueError, TypeError, IndexError, OverflowError):
            issues['invalid_ohlcv_or_timestamp'] += 1
    opened = datetime.combine(day, time(9, 15), IST)
    expected = {opened + timedelta(minutes=5 * i) for i in range(75)}
    actual = [b.ts for b in parsed]
    if len(set(actual)) != len(actual):
        issues['duplicate_timestamp'] += len(actual) - len(set(actual))
    missing = expected - set(actual)
    if missing:
        issues['missing_bars'] += len(missing)
    if set(actual) - expected:
        issues['unexpected_bars'] += len(set(actual) - expected)
    return ([] if issues else sorted(parsed, key=lambda b: b.ts)), dict(issues)


def load_data(directory, protocol):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text())
    raw = (directory / 'raw_responses.json.gz').read_bytes()
    if sha(raw) != manifest['raw_archive_sha256']:
        raise ValueError('raw_archive_hash_mismatch')
    payload = gzip.decompress(raw)
    if sha(payload) != manifest['raw_payload_sha256']:
        raise ValueError('raw_payload_hash_mismatch')
    if manifest['interval_minutes'] != 5 or manifest['timestamp_kind'] != 'bar_start':
        raise ValueError('unproved_bar_contract')
    acquisition = directory / ('protocol-v1.json' if protocol.get('amendment') else 'protocol.json')
    if sha(acquisition.read_bytes()) != manifest['protocol_sha256']:
        raise ValueError('acquisition_protocol_hash_mismatch')
    if protocol.get('amendment') and protocol['amendment']['original_protocol_sha256'] != manifest['protocol_sha256']:
        raise ValueError('protocol_amendment_lineage_mismatch')
    grouped = defaultdict(list)
    diagnostics = Counter()
    request_meta = {(r['symbol'], r['start'], r['end']): r for r in manifest['requests']}
    seen = set()
    for item in json.loads(payload):
        key = item['symbol'], item['start'], item['end']
        if key in seen or key not in request_meta:
            raise ValueError('duplicate_or_unmanifested_request')
        seen.add(key)
        if item.get('http_status') != 200:
            diagnostics['failed_requests'] += 1
            continue
        body = item['body'].encode()
        if sha(body) != request_meta[key]['body_sha256']:
            raise ValueError('response_hash_mismatch')
        for row in (json.loads(body).get('data') or {}).get('candles', []):
            try:
                ts = datetime.fromisoformat(str(row[0]).replace('Z', '+00:00'))
                if ts.tzinfo is None:
                    # Date attribution is unproved: invalidate the request range.
                    raise ValueError('naive')
                day = ts.astimezone(IST).date()
                if not item['start'] <= day.isoformat() <= item['end']:
                    raise ValueError('outside_request')
                grouped[(item['symbol'], day)].append(row)
            except (ValueError, TypeError, IndexError):
                diagnostics['unattributable_rows'] += 1
                for d in days_between(item['start'], item['end']):
                    grouped[(item['symbol'], d)].append(row)
    if seen != set(request_meta):
        raise ValueError('missing_raw_request')
    sessions, exclusions = {}, []
    asof = datetime.fromisoformat(manifest['finished_at'])
    for symbol in protocol['symbols']:
        for day in days_between(protocol['data_start'], protocol['data_end']):
            bars, issues = validate_session(grouped.pop((symbol, day), []), day, asof)
            if issues:
                exclusions.append({'symbol': symbol, 'day': day.isoformat(), 'reasons': issues})
            else:
                sessions[(symbol, day)] = bars
    for (symbol, day), rows in grouped.items():
        diagnostics['rows_outside_verified_regular_sessions'] += len(rows)
        exclusions.append({'symbol': symbol, 'day': day.isoformat(),
                           'reasons': {'outside_verified_regular_session': len(rows)}})
    return sessions, {'valid_full_sessions': len(sessions), 'exclusions': exclusions,
                      'diagnostics': dict(diagnostics)}


@dataclass(frozen=True)
class Sample:
    symbol: str
    day: date
    clock: str
    bars: tuple[Bar, ...]
    daily_atr: float
    intraday_atr: float
    reachable_fraction: float
    feature_last_bar: datetime
    calibration_last_day: date

    @property
    def id(self):
        return f'{self.symbol}|{self.day}|{self.clock}'


def true_range(bars):
    return mean(max(b.high-b.low, abs(b.high-p.close), abs(b.low-p.close))
                for p, b in zip(bars[:-1], bars[1:]))


def make_samples(sessions, protocol, phase):
    if phase not in protocol['splits']:
        raise ValueError('unknown_phase')
    start, end = protocol['splits'][phase]
    calendar = list(days_between(protocol['data_start'], end))
    samples, exclusions = [], []
    for symbol in protocol['symbols']:
        for i, day in enumerate(calendar):
            if not start <= day.isoformat() <= end:
                continue
            prior_days = calendar[max(0, i-20):i]
            if (symbol, day) not in sessions or len(prior_days) != 20 or any((symbol, d) not in sessions for d in prior_days):
                exclusions.append({'symbol': symbol, 'day': str(day), 'reason': 'missing_current_or_20_prior_full_sessions'})
                continue
            current = sessions[(symbol, day)]
            prior = [sessions[(symbol, d)] for d in prior_days]
            daily = [Bar(b[0].ts, b[0].open, max(x.high for x in b), min(x.low for x in b), b[-1].close,
                         sum(x.volume for x in b)) for b in prior[-15:]]
            for clock in protocol['entry_times']:
                at = datetime.combine(day, time.fromisoformat(clock), IST)
                completed = prior[-1] + [b for b in current if b.ts + timedelta(minutes=5) <= at]
                last = completed[-15:]
                if len(last) != 15 or last[-1].ts + timedelta(minutes=5) > at:
                    raise ValueError('feature_window_not_completed')
                upside = []
                for previous in prior:
                    window = [b for b in previous if clock <= b.ts.strftime('%H:%M') <= protocol['deadline']]
                    peak = max([b.high for b in window[:-1]] + [window[-1].open, window[0].open])
                    upside.append(peak / window[0].open - 1)
                path = tuple(b for b in current if at <= b.ts and b.ts.strftime('%H:%M') <= protocol['deadline'])
                samples.append(Sample(symbol, day, clock, path, true_range(daily), true_range(last),
                                      median(upside), last[-1].ts, prior_days[-1]))
    return samples, exclusions


@dataclass(frozen=True)
class Order:
    sample: Sample
    bps: float
    qty: int
    entry: float
    buy_fee: float
    break_even: float
    stop: float
    current_target: float
    reachable_target: float
    atr_distance: float


def sale_net(qty, entry, buy_fee, raw_exit, bps, when, symbol):
    fill = raw_exit * (1 - bps / 10000)
    sell = fees.charge(qty, fill, 'SELL', 'intraday', when, symbol)
    return qty * (fill-entry) - buy_fee - sell['total'], fill, sell


def break_even_quote(qty, entry, buy_fee, bps, when, symbol):
    """Smallest paise quote that covers both fees and adverse sell slippage."""
    lo, hi = entry, entry * 2
    if sale_net(qty, entry, buy_fee, hi, bps, when, symbol)[0] < 0:
        raise ValueError('break_even_not_bracketed')
    for _ in range(45):
        mid = (lo+hi)/2
        if sale_net(qty, entry, buy_fee, mid, bps, when, symbol)[0] >= 0:
            hi = mid
        else:
            lo = mid
    quote = math.ceil(hi * 100) / 100
    while sale_net(qty, entry, buy_fee, quote, bps, when, symbol)[0] < 0:
        quote = round(quote + .01, 2)
    return quote


def prepare(sample, bps, protocol):
    if not 0 <= bps < 10000:
        raise ValueError('invalid_slippage')
    entry = sample.bars[0].open * (1 + bps/10000)
    stake = protocol['stake_inr']
    qty = fees.affordable_qty(int(stake // entry), entry, stake, 'intraday', sample.day, sample.symbol)
    if qty < 1:
        raise ValueError('stake_cannot_afford_one_share')
    buy = fees.charge(qty, entry, 'BUY', 'intraday', sample.day, sample.symbol)['total']
    be = break_even_quote(qty, entry, buy, bps, sample.day, sample.symbol)
    cfg = protocol['baseline']
    pct = min(cfg['target_max_fraction'], max(cfg['target_min_fraction'], cfg['target_atr_k'] * sample.intraday_atr / entry))
    target = round(entry * (1+pct), 2)
    reachable = round(max(be + 0.001*entry, min(target, entry*(1+sample.reachable_fraction))), 2)
    return Order(sample, bps, qty, entry, buy, be, round(entry * (1-cfg['stop_fraction']), 2),
                 target, reachable, max(2*sample.intraday_atr, .002*entry))


def simulate(order, arm, scenario, protocol):
    """Explicit path order, gap fills and completed-observation cadence."""
    if scenario not in protocol['execution_scenarios']:
        raise ValueError('unknown_execution_scenario')
    if arm['trail'] not in ('current', 'net_activation', 'atr_lock') or arm['target'] not in ('entry_atr', 'past_session_median'):
        raise ValueError('unknown_arm')
    sample = order.sample
    peak = order.entry
    trail = None
    highest = lowest = sample.bars[0].open
    target = order.current_target if arm['target'] == 'entry_atr' else order.reachable_target
    fraction = protocol['baseline']['trail_fraction']

    def observe(px):
        nonlocal peak, trail, highest, lowest
        highest, lowest = max(highest, px), min(lowest, px)
        peak = max(peak, px)
        proposed = peak - order.atr_distance if arm['trail'] == 'atr_lock' else peak * (1-fraction)
        enabled = peak > order.entry if arm['trail'] == 'current' else proposed >= order.break_even
        if enabled:
            trail = max(trail or 0, proposed)

    def reason_at(px):
        if px <= order.stop:
            return 'STOP_LOSS'
        if px >= target:
            return 'TAKE_PROFIT'
        if trail is not None and px <= trail:
            return 'TRAILING_STOP'
        return None

    def finish(raw, reason, ts, precision):
        observe(raw)
        net, fill, sell = sale_net(order.qty, order.entry, order.buy_fee, raw, order.bps, sample.day, sample.symbol)
        gross = order.qty * (raw-sample.bars[0].open)
        slip = order.qty * ((order.entry-sample.bars[0].open)+(raw-fill))
        activation = (order.entry if arm['trail']=='current' else
                      order.break_even/(1-fraction) if arm['trail']=='net_activation' else
                      order.break_even+order.atr_distance)
        return {
            'sample': sample.id, 'symbol': sample.symbol, 'day': str(sample.day), 'clock': sample.clock,
            'arm': arm['id'], 'scenario': scenario, 'slippage_bps': order.bps, 'qty': order.qty,
            'raw_entry': sample.bars[0].open, 'entry_fill': order.entry, 'raw_exit': raw, 'exit_fill': fill,
            'stop': order.stop, 'target': target, 'net_break_even_quote': order.break_even,
            'trail_activation_quote': activation, 'activation_below_target': activation < target,
            'target_distance_bps': (target/order.entry-1)*10000,
            'buy_fees': order.buy_fee, 'sell_fees': sell['total'], 'fee_model': sell['model'],
            'exchange_schedule': sell['exchange_schedule'], 'gross_pnl': gross, 'slippage_inr': slip,
            'net_pnl': net, 'net_bps': net/(order.qty*order.entry)*10000,
            'exit_reason': reason, 'exit_time': ts.isoformat(), 'exit_precision': precision,
            'held_minutes_lower': (ts-sample.bars[0].ts).total_seconds()/60,
            'observed_mfe_bps': (highest/order.entry-1)*10000,
            'observed_mae_bps': (lowest/order.entry-1)*10000,
            'observed_giveback_bps': (highest-raw)/order.entry*10000,
            'feature_last_bar': sample.feature_last_bar.isoformat(),
            'calibration_last_day': str(sample.calibration_last_day)
        }

    for bar in sample.bars:
        if bar.ts.strftime('%H:%M') == protocol['deadline']:
            return finish(bar.open, 'SQUARE_OFF', bar.ts, 'bar_open_proxy')
        if scenario == 'sampled_30m':
            end = bar.ts + timedelta(minutes=5)
            if end.minute % 30:
                continue
            observe(bar.close)
            reason = reason_at(bar.close)
            if reason:
                return finish(bar.close, reason, end, 'completed_close_proxy')
            continue
        # Gaps cross levels without filling at a better historical barrier.
        observe(bar.open)
        reason = reason_at(bar.open)
        if reason:
            return finish(bar.open, reason, bar.ts, 'bar_open_proxy')
        points = (bar.high, bar.low, bar.close) if scenario == 'open_high_low_close' else (bar.low, bar.high, bar.close)
        previous = bar.open
        for px in points:
            if px >= previous:
                if previous < target <= px:
                    return finish(target, 'TAKE_PROFIT', bar.ts, 'within_5m_bar')
            else:
                barriers = [(order.stop, 'STOP_LOSS')]
                if trail is not None:
                    barriers.append((trail, 'TRAILING_STOP'))
                crossed = [(level, reason) for level, reason in barriers if px <= level <= previous]
                if crossed:
                    # Stable tie order gives the hard stop priority.
                    level, reason = max(crossed, key=lambda pair: pair[0])
                    return finish(level, reason, bar.ts, 'within_5m_bar')
            observe(px)
            previous = px
    raise ValueError('deadline_bar_unavailable; no fabricated closing price')


def summary(rows):
    if not rows:
        return {'count': 0, 'status': 'unavailable', 'mean_net_bps': None, 'win_pct': None}
    net = [r['net_pnl'] for r in rows]
    wins, losses = [x for x in net if x > 1e-8], [x for x in net if x < -1e-8]
    days = defaultdict(list)
    for r in rows:
        days[r['day']].append(r['net_bps'])
    level = peak = drawdown = 0.
    for day in sorted(days):
        level += mean(days[day])
        peak = max(peak, level)
        drawdown = max(drawdown, peak-level)
    result = dict(count=len(rows), sessions=len(days), symbols=len({r['symbol'] for r in rows}),
                  mean_net_bps=mean(r['net_bps'] for r in rows), total_net_inr=sum(net),
                  mean_net_inr=mean(net), mean_gross_inr=mean(r['gross_pnl'] for r in rows),
                  mean_fees_inr=mean(r['buy_fees']+r['sell_fees'] for r in rows),
                  mean_slippage_inr=mean(r['slippage_inr'] for r in rows),
                  wins=len(wins), losses=len(losses), flat=len(rows)-len(wins)-len(losses),
                  win_pct=100*len(wins)/len(rows),
                  payoff_ratio=mean(wins)/abs(mean(losses)) if wins and losses else None,
                  profit_factor=sum(wins)/abs(sum(losses)) if losses else None,
                  ratio_status='measured' if wins and losses else 'no_wins' if not wins else 'no_losses',
                  target_hit_pct=100*sum(r['exit_reason']=='TAKE_PROFIT' for r in rows)/len(rows),
                  activation_below_target_pct=100*sum(r['activation_below_target'] for r in rows)/len(rows),
                  mean_target_distance_bps=mean(r['target_distance_bps'] for r in rows),
                  daily_mean_net_drawdown_bps=drawdown,
                  mean_held_minutes_lower=mean(r['held_minutes_lower'] for r in rows),
                  mean_observed_mfe_bps=mean(r['observed_mfe_bps'] for r in rows),
                  mean_observed_mae_bps=mean(r['observed_mae_bps'] for r in rows),
                  mean_observed_giveback_bps=mean(r['observed_giveback_bps'] for r in rows),
                  exits=dict(Counter(r['exit_reason'] for r in rows)))
    return {k: round(v, 8) if isinstance(v, float) else v for k, v in result.items()}


def evaluate(samples, protocol, arms=None):
    results = []
    for sample in samples:
        for bps in protocol['costs']['slippage_bps_per_side']:
            order = prepare(sample, bps, protocol)
            for arm in arms or protocol['arms']:
                for scenario in protocol['execution_scenarios']:
                    results.append(simulate(order, arm, scenario, protocol))
    return results


def summarize(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[(r['arm'], r['scenario'], r['slippage_bps'])].append(r)
    return [{'arm': a, 'scenario': s, 'slippage_bps': b, **summary(v)} for (a, s, b), v in sorted(groups.items())]


def choose(train, protocol):
    """Training only; validation and holdout cannot silently pick a new arm."""
    scores = {}
    for arm in protocol['arms']:
        matching = [r for r in train if r['arm'] == arm['id'] and r['slippage_bps'] == 5]
        values = [r['mean_net_bps'] for r in matching]
        if (len(values) != len(protocol['execution_scenarios']) or
                {r['scenario'] for r in matching} != set(protocol['execution_scenarios']) or any(v is None for v in values)):
            raise ValueError('training_scenario_unavailable')
        scores[arm['id']] = min(values)
    return max(scores, key=scores.get), scores


def paired(candidate, baseline, protocol):
    a, b = {r['sample']: r for r in candidate}, {r['sample']: r for r in baseline}
    if len(a) != len(candidate) or len(b) != len(baseline) or set(a) != set(b):
        raise ValueError('unpaired_or_duplicate_observations')
    if not a:
        return {'count': 0, 'mean_gain_bps': None, 'ci95_bps': None}
    days = defaultdict(list)
    for key in sorted(a):
        days[a[key]['day']].append(a[key]['net_bps'] - b[key]['net_bps'])
    sums = np.array([sum(days[d]) for d in sorted(days)])
    counts = np.array([len(days[d]) for d in sorted(days)])
    cfg = protocol['evidence_thresholds']
    rng = np.random.default_rng(cfg['bootstrap_seed'])
    ix = rng.integers(0, len(days), (cfg['paired_bootstrap_resamples'], len(days)))
    boot = sums[ix].sum(axis=1) / counts[ix].sum(axis=1)
    return {'count': len(a), 'sessions': len(days), 'mean_gain_bps': round(float(sums.sum()/counts.sum()), 8),
            'ci95_bps': [round(float(x), 8) for x in np.quantile(boot, [.025, .975])],
            'unit': 'paired same-entry differences; resample whole session days',
            'fraction_improved': sum(a[k]['net_bps'] > b[k]['net_bps'] for k in a)/len(a)}


def source_hashes(directory):
    paths = [Path(directory)/'protocol.json', Path(directory)/'protocol-v1.json', Path(directory)/'manifest.json',
             Path(directory)/'raw_responses.json.gz', HERE/'study.py', HERE/'fetch.py',
             ROOT/'trading/fees.py', ROOT/'trading/calendars.py', ROOT/'trading/intraday_bars.py',
             ROOT/'trading/targets.py', ROOT/'trading/exits.py', ROOT/'trading/modes.py', ROOT/'requirements.txt']
    return {p.name: sha(p.read_bytes()) for p in paths}


def verify_seal(directory, seal):
    if seal['hashes'] != source_hashes(directory):
        raise ValueError('sealed_inputs_changed; do not retune using an opened holdout')


def rounded_rows(rows):
    return [{k: round(v, 8) if isinstance(v, float) else v for k, v in r.items()} for r in rows]


def save_rows(path, rows):
    payload = json.dumps(rounded_rows(rows), sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    Path(path).write_bytes(gzip.compress(payload, mtime=0))


def run_train(directory=HERE):
    directory = Path(directory)
    seal_path = directory/'selection.json'
    if seal_path.exists():
        raise ValueError('selection_already_sealed; no silent overwrite')
    protocol = json.loads((directory/'protocol.json').read_text())
    sessions, coverage = load_data(directory, protocol)
    train, missing_train = make_samples(sessions, protocol, 'train')
    train_rows = evaluate(train, protocol)
    train_summary = summarize(train_rows)
    selected, scores = choose(train_summary, protocol)
    validation, missing_validation = make_samples(sessions, protocol, 'validation')
    validation_rows = evaluate(validation, protocol)
    seal = dict(schema=1, sealed_at=datetime.now(timezone.utc).isoformat(), hashes=source_hashes(directory),
                selected_arm=selected, training_scores=scores, training=train_summary,
                validation=summarize(validation_rows), coverage=coverage,
                sample_exclusions={'train': missing_train, 'validation': missing_validation},
                holdout='NOT_EVALUATED; selection may not change')
    save_rows(directory/'train_validation_trades.json.gz', [dict(r, phase='train') for r in train_rows] +
              [dict(r, phase='validation') for r in validation_rows])
    seal['train_validation_trades_sha256'] = sha((directory/'train_validation_trades.json.gz').read_bytes())
    write_json(seal_path, seal)
    print(json.dumps({'selected_arm': selected, 'training_scores': scores,
                      'train_samples': len(train), 'validation_samples': len(validation)}))


def run_holdout(directory=HERE, *, resume=False):
    directory = Path(directory)
    if (directory/'holdout.json').exists():
        raise ValueError('holdout_already_opened; this period is now consumed')
    seal = json.loads((directory/'selection.json').read_text())
    verify_seal(directory, seal)
    protocol = json.loads((directory/'protocol.json').read_text())
    selected, scores = choose(seal['training'], protocol)
    if selected != seal['selected_arm'] or scores != seal['training_scores']:
        raise ValueError('training_selection_changed')
    if sha((directory/'train_validation_trades.json.gz').read_bytes()) != seal['train_validation_trades_sha256']:
        raise ValueError('training_trade_artifact_changed')
    marker_path = directory/'holdout_opened.json'
    selection_sha = sha((directory/'selection.json').read_bytes())
    if marker_path.exists():
        marker = json.loads(marker_path.read_text())
        if not resume or marker['selection_sha256'] != selection_sha:
            raise ValueError('holdout_previously_opened; resume only the identical sealed study')
    else:
        marker = {'selection_sha256': selection_sha, 'opened_at': datetime.now(timezone.utc).isoformat(),
                  'meaning': 'Holdout access started. Failures do not make this period unseen again.'}
        write_json(marker_path, marker)
    sessions, _ = load_data(directory, protocol)
    samples, exclusions = make_samples(sessions, protocol, 'holdout')
    rows = evaluate(samples, protocol)
    report = holdout_report(rows, seal, protocol)
    report.update(opened_at=marker['opened_at'],
                  selection_sha256=selection_sha,
                  exclusions=exclusions, holdout_consumed=True)
    save_rows(directory/'holdout_trades.json.gz', rows)
    report['trades_sha256'] = sha((directory/'holdout_trades.json.gz').read_bytes())
    write_json(directory/'holdout.json', report)
    print(json.dumps({'selected_arm': seal['selected_arm'], 'samples': len(samples),
                      'statistical_pass': report['statistical_pass'], 'reasons': report['reasons']}))


def holdout_report(rows, seal, protocol):
    selected = seal['selected_arm']
    report = summarize(rows)
    pairs, reasons = [], []
    cfg = protocol['evidence_thresholds']
    if selected == BASE:
        reasons.append('training_selected_baseline')
    for phase in ('training', 'validation'):
        floor = cfg['minimum_train_sessions' if phase == 'training' else 'minimum_validation_sessions']
        matching = [r for r in seal[phase] if r['arm'] == selected]
        if not matching or min(r['sessions'] for r in matching) < floor or min(r['symbols'] for r in matching) < cfg['minimum_symbols']:
            reasons.append(phase + '_coverage_below_predeclared_minimum')
    for scenario in protocol['execution_scenarios']:
        for bps in protocol['costs']['slippage_bps_per_side']:
            current = [r for r in rows if r['arm']==selected and r['scenario']==scenario and r['slippage_bps']==bps]
            base = [r for r in rows if r['arm']==BASE and r['scenario']==scenario and r['slippage_bps']==bps]
            comparison = paired(current, base, protocol)
            pairs.append({'scenario': scenario, 'slippage_bps': bps, **comparison})
            stats = summary(current)
            if bps > 15:
                continue
            if (stats['count'] < cfg['minimum_holdout_pairs'] or stats.get('sessions', 0) < cfg['minimum_holdout_sessions'] or stats.get('symbols', 0) < cfg['minimum_symbols']):
                reasons.append('holdout_coverage_below_predeclared_minimum')
            if stats['mean_net_bps'] is None or stats['mean_net_bps'] <= 0:
                reasons.append('holdout_net_expectancy_not_positive')
            if comparison['mean_gain_bps'] is None or comparison['mean_gain_bps'] <= 0 or comparison['ci95_bps'][0] <= 0:
                reasons.append('holdout_paired_gain_not_robust')
            val = {r['arm']: r for r in seal['validation'] if r['scenario']==scenario and r['slippage_bps']==bps}
            if selected not in val or BASE not in val or val[selected]['mean_net_bps'] <= 0 or val[selected]['mean_net_bps'] <= val[BASE]['mean_net_bps']:
                reasons.append('validation_net_gain_not_positive')
    concentration = {}
    reference = [r for r in rows if r['scenario']=='sampled_30m' and r['slippage_bps']==5 and r['arm']==selected]
    for field in ('symbol', 'clock'):
        concentration[field] = {key: summary([r for r in reference if r[field]==key]) for key in sorted({r[field] for r in reference})}
    return dict(selected_arm=selected, results=report, paired=pairs, concentration=concentration,
                statistical_pass=not reasons, reasons=sorted(set(reasons)), promotion='NOT_AUTHORIZED_BY_THIS_STUDY',
                settings_changed=False, limitations=['Fixed-clock exit-only study, not actual strategy entries or portfolio replay.',
                'Actual score/danger/sector exits and tick/liquidity paths unavailable; no price-only equivalence claim.',
                'Two OHLC orderings are sensitivity paths, not guaranteed bounds; sampled-close fills are proxies.',
                'Selected historical Basic tariff is a counterfactual assumption; provider revisions/adjustments unverified.',
                'Current 15-name sample carries survivorship/large-cap bias; no evidence for other segments.',
                'No forward paper validation in this study; holdout is consumed once evaluated.'])


def verify(directory=HERE):
    """Offline reproducibility guard; no new selection or holdout rewriting."""
    directory = Path(directory)
    protocol = json.loads((directory/'protocol.json').read_text())
    seal = json.loads((directory/'selection.json').read_text())
    saved = json.loads((directory/'holdout.json').read_text())
    verify_seal(directory, seal)
    if sha((directory/'selection.json').read_bytes()) != saved['selection_sha256']:
        raise ValueError('selection_changed_after_holdout')
    marker = json.loads((directory/'holdout_opened.json').read_text())
    if marker['selection_sha256'] != saved['selection_sha256'] or marker['opened_at'] != saved['opened_at']:
        raise ValueError('holdout_opening_record_changed')
    for path, expected in [('train_validation_trades.json.gz', seal['train_validation_trades_sha256']),
                           ('holdout_trades.json.gz', saved['trades_sha256'])]:
        if sha((directory/path).read_bytes()) != expected:
            raise ValueError('trade_artifact_hash_mismatch')
    sessions, coverage = load_data(directory, protocol)
    if coverage != seal['coverage']:
        raise ValueError('coverage_changed')
    for phase, key in [('train','training'), ('validation','validation'), ('holdout','results')]:
        samples, excluded = make_samples(sessions, protocol, phase)
        rows = evaluate(samples, protocol)
        expected = saved[key] if phase=='holdout' else seal[key]
        if summarize(rows) != expected:
            raise ValueError(f'{phase}_results_not_reproduced')
        trades_path = 'holdout_trades.json.gz' if phase=='holdout' else 'train_validation_trades.json.gz'
        recorded = json.loads(gzip.decompress((directory/trades_path).read_bytes()))
        actual = rounded_rows(rows) if phase=='holdout' else [dict(r, phase=phase) for r in rounded_rows(rows)]
        if phase != 'holdout':
            recorded = [r for r in recorded if r['phase']==phase]
        if actual != recorded:
            raise ValueError(f'{phase}_individual_trades_not_reproduced')
        if phase=='holdout':
            regenerated = holdout_report(rows, seal, protocol)
            if any(saved[k] != v for k, v in regenerated.items()) or saved['exclusions'] != excluded:
                raise ValueError('holdout_verdict_not_reproduced')
        elif seal['sample_exclusions'][phase] != excluded:
            raise ValueError('sample_exclusions_changed')
    selected, scores = choose(seal['training'], protocol)
    if selected != seal['selected_arm'] or scores != seal['training_scores']:
        raise ValueError('training_selection_changed')
    print('Goal08 source, data, coverage, all three periods and frozen selection reproduce exactly.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['train', 'holdout', 'verify'])
    parser.add_argument('--resume', action='store_true', help='Resume an interrupted holdout with identical sealed inputs only')
    args = parser.parse_args()
    if args.resume and args.phase != 'holdout':
        parser.error('--resume only applies to holdout')
    if args.phase == 'holdout':
        run_holdout(resume=args.resume)
    else:
        {'train': run_train, 'verify': verify}[args.phase]()
