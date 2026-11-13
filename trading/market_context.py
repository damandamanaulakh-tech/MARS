"""SEQ-06: price context over identical completed bars. Never an entry gate.

Daily timestamps below are derived NSE session CLOSES, not invented provider
tick times. No nearest-date joins, forward fill, zero returns or market-as-sector.
"""
from collections import Counter
from datetime import timedelta
import math

import pandas as pd

from . import intraday_bars as ib
from .calendars import is_trading_day

MODEL = 'same-window-context-2026-11-13-v1'


def daily_grid(now, count=90):
    clock = pd.Timestamp(ib.utc_now(now))
    day = clock.tz_convert(ib.IST).date()
    out = []
    for _ in range(count * 3 + 15):
        if day.year not in ib.VERIFIED_CALENDAR_YEARS:
            break
        end = list(ib.session_bounds(day))[1].tz_convert('UTC')
        if is_trading_day(day) and end <= clock:
            out.append(end)
            if len(out) == count:
                break
        day -= timedelta(days=1)
    return pd.DatetimeIndex(sorted(out), tz='UTC')


def completed(frame, now, interval='1d'):
    """Validated closes plus coverage; retain all explicit rejection reasons."""
    empty = pd.Series(dtype=float, index=pd.DatetimeIndex([], tz='UTC'))
    attrs = dict(getattr(frame, 'attrs', {}) or {})
    info = {'status': 'unknown', 'reason': 'missing_data', 'source': attrs.get('source', 'unknown'),
            'received_at': attrs.get('received_at'), 'interval': interval,
            'timestamp_kind': 'derived_session_close' if interval == '1d' else 'bar_close',
            'provider_timestamp': None, 'price_basis': attrs.get('price_basis', 'unverified'),
            'rejected': {}, 'last_available': None}
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return empty, info
    if attrs.get('interval') != interval:
        info['reason'] = 'wrong_or_unproved_interval'
        return empty, info
    if interval == '5m':
        clean, d = ib.validate(frame, now, min_bars=2)
        info.update(status='ready' if d['status'] == 'ready' else 'unknown',
                    reason=d['reason'], rejected=d['rejected'])
        if d['status'] != 'ready':
            return empty, info
        series = clean['Close'].copy()
        series.index = series.index + pd.Timedelta(minutes=5)
    else:
        if interval != '1d' or 'Close' not in frame:
            info['reason'] = 'unsupported_interval_or_columns'
            return empty, info
        if ib.utc_now(now).astimezone(ib.IST).year not in ib.VERIFIED_CALENDAR_YEARS:
            info['reason'] = 'session_calendar_unverified'
            return empty, info
        values, conflicts, rejected = {}, set(), Counter()
        for raw, value in frame['Close'].items():
            try:
                stamp = pd.Timestamp(raw)
                if pd.isna(stamp):
                    raise ValueError('invalid timestamp')
                if stamp.tzinfo:
                    stamp = stamp.tz_convert(ib.IST)
                day = stamp.date()
                if day.year not in ib.VERIFIED_CALENDAR_YEARS:
                    reason = 'session_calendar_unverified'
                elif stamp.time().isoformat() not in ('00:00:00', '09:15:00', '15:30:00'):
                    reason = 'unexpected_daily_timestamp'
                elif not is_trading_day(day):
                    reason = 'non_trading_session'
                else:
                    end = list(ib.session_bounds(day))[1].tz_convert('UTC')
                    reason = 'future_or_unfinished_bar' if end > pd.Timestamp(ib.utc_now(now)) else None
                number = float(value)
                if not math.isfinite(number) or number <= 0:
                    reason = 'invalid_close'
            except (ValueError, TypeError, OverflowError):
                reason = 'invalid_row'
            if reason:
                rejected[reason] += 1
                continue
            if end in values and values[end] != number:
                conflicts.add(end)
            values[end] = number
        for end in conflicts:
            values.pop(end, None)
        if conflicts:
            rejected['conflicting_duplicates'] = len(conflicts)
        info['rejected'] = dict(rejected)
        series = pd.Series(values, dtype=float).sort_index() if values else empty
        if series.empty:
            info['reason'] = 'no_completed_valid_bars'
            return empty, info
        info.update(status='ready', reason='completed_bars')
    info['last_available'] = series.index[-1].isoformat()
    return series, info


def _leg(series, info, grid):
    out = {**info, 'return_pct': None, 'start_price': None, 'end_price': None,
           'present_bars': int(series.index.isin(grid).sum()), 'required_bars': len(grid)}
    if info['status'] != 'ready':
        return out
    if not len(grid) or not grid.isin(series.index).all():
        out.update(status='unknown', reason='missing_exact_window_bars')
        return out
    a, b = float(series.loc[grid[0]]), float(series.loc[grid[-1]])
    out.update(status='ready', reason='identical_completed_window', start_price=a,
               end_price=b, return_pct=(b / a - 1) * 100)
    return out


def compare(ticker, stock, sector, market, identity, now=None, *, lookback=21,
            interval='1d', market_ticker='^NSEI'):
    now = ib.utc_now(now)
    lookback = max(1, int(lookback))
    s, sd = completed(stock, now, interval)
    sec, scd = completed(sector, now, interval)
    m, md = completed(market, now, interval)
    from .brokers.upstox_instruments import INDEX_KEYS
    if not str(INDEX_KEYS.get(market_ticker, '')).startswith('NSE_INDEX|'):
        md.update(status='unknown', reason='market_session_calendar_unverified')
    if interval == '1d':
        grid = daily_grid(now, lookback + 1)
    else:
        # The stock defines the end. Never trim it back to a stale benchmark.
        grid = s.tail(lookback + 1).index
    if len(grid) < lookback + 1:
        for diag in (sd, scd, md):
            diag.update(status='unknown', reason='insufficient_completed_window')
    if identity.get('sector_status') != 'verified':
        scd.update(status='unknown', reason=identity.get('sector_reason') or identity.get('reason', 'sector_identity_unknown'))
    legs = [_leg(s, sd, grid), _leg(sec, scd, grid), _leg(m, md, grid)]
    # All gaps within an intraday return window must also be visible.
    if interval == '5m' and len(grid):
        expected = []
        for day in ib.session_days(now):
            opened, closed = ib.session_bounds(day)
            expected.extend(pd.date_range(opened + pd.Timedelta(minutes=5), closed, freq='5min').tz_convert('UTC'))
        window_grid = pd.DatetimeIndex([t for t in expected if grid[0] <= t <= grid[-1]])
        if not grid.equals(window_grid):
            for leg in legs:
                leg.update(status='unknown', reason='missing_bars_in_window', return_pct=None)
    a, b, c = (x['return_pct'] for x in legs)
    adjusted = any(leg.get('price_basis') == 'auto_adjusted' for leg in legs)
    # Yahoo auto_adjust can include distributions. Do not subtract it from a
    # price-index return and call that measured relative price strength.
    comparable_basis = not adjusted
    n = sum(x['status'] == 'ready' for x in legs)
    return {'schema': 1, 'model': MODEL, 'ticker': ticker, 'observed_at': now.isoformat(),
            'role': 'context_only', 'status': 'ready' if n == 3 and comparable_basis else 'partial' if n else 'unknown',
            'identity': dict(identity), 'market_ticker': market_ticker,
            'window': {'interval': interval, 'lookback_bars': lookback, 'timezone': 'Asia/Kolkata',
                       'start': grid[0].isoformat() if len(grid) else None,
                       'end': grid[-1].isoformat() if len(grid) else None},
            'stock': legs[0], 'sector': legs[1], 'market': legs[2],
            'relative_basis': 'incompatible_auto_adjusted_series' if adjusted else 'provider_price_series; adjustment_history_not_independently_verified',
            'stock_vs_sector_pp': a-b if comparable_basis and a is not None and b is not None else None,
            'stock_vs_market_pp': a-c if comparable_basis and a is not None and c is not None else None,
            'sector_vs_market_pp': b-c if comparable_basis and b is not None and c is not None else None,
            'agreement_market': (a > 0) == (c > 0) if comparable_basis and a not in (None, 0) and c not in (None, 0) else None,
            'agreement_sector': (a > 0) == (b > 0) if comparable_basis and a not in (None, 0) and b not in (None, 0) else None,
            'note': 'Price returns, not total returns or a causal explanation. Current membership is an observed comparison basket, not historical membership.'}
