"""Completed regular-session NSE equity candles, never a daily fallback.

Provider timestamps are bar STARTS. Engine naive clocks mean UTC; provider
timestamps must carry a timezone. Special sessions need a verified schedule
before they can be supported. No forward filling or invented zero-volume bars.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .calendars import is_trading_day, MARKET_OPEN_IST, MARKET_CLOSE_IST

IST = ZoneInfo('Asia/Kolkata')
INTERVAL = 5
SESSIONS = 5
PUBLICATION_GRACE_SECONDS = 60
# NSE CMTR71775 (2026 equity holidays), supplemented by CMTR72260 (Jan 15).
# The general calendar currently has only fixed holidays for 2027; do not
# treat that partial list as a verified intraday session schedule.
VERIFIED_CALENDAR_YEARS = frozenset({2026})
COLUMNS = ['Open', 'High', 'Low', 'Close', 'Volume']


def utc_now(value=None):
    value = value or datetime.now(timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def session_bounds(day):
    return (pd.Timestamp(datetime.combine(day, datetime.min.time()), tz=IST)
            + pd.Timedelta(hours=hour, minutes=minute)
            for hour, minute in (MARKET_OPEN_IST, MARKET_CLOSE_IST))


def session_days(now=None, count=SESSIONS):
    day = utc_now(now).astimezone(IST).date()
    days = []
    for _ in range(40):
        if is_trading_day(day):
            days.append(day)
        if len(days) == count:
            break
        day -= timedelta(days=1)
    return sorted(days)


def bar_status(start, minutes, now=None, *, current_session=True, fresh=True):
    """Return a reason code or None; no market-hours test override applies."""
    if minutes not in (1, INTERVAL):
        return 'wrong_interval'
    try:
        ts = pd.Timestamp(start)
        if pd.isna(ts) or ts.tzinfo is None:
            return 'timestamp_timezone_unavailable'
        ts = ts.tz_convert(IST)
    except (TypeError, ValueError, OverflowError):
        return 'invalid_timestamp'
    clock = pd.Timestamp(utc_now(now)).tz_convert(IST)
    if ts.year not in VERIFIED_CALENDAR_YEARS or clock.year not in VERIFIED_CALENDAR_YEARS:
        return 'session_calendar_unverified'
    if not is_trading_day(ts.date()):
        return 'non_trading_session'
    opened, closed = session_bounds(ts.date())
    end = ts + pd.Timedelta(minutes=minutes)
    if ts < opened or end > closed:
        return 'outside_regular_session'
    if (ts - opened).total_seconds() % (minutes * 60):
        return 'misaligned_interval'
    if ts > clock:
        return 'future_bar'
    if end > clock:
        return 'unfinished_bar'
    if current_session and ts.date() != clock.date():
        return 'previous_session'
    if fresh and (clock - end).total_seconds() > minutes * 60 + PUBLICATION_GRACE_SECONDS:
        return 'stale_bar'
    return None


def required_bars(cfg):
    # Retain configured window lengths; their unit on this path is 5m bars.
    return max(int(cfg.momentum_lookback_days) + 1, int(cfg.medium_lookback_days) + 1,
               int(cfg.breakout_lookback_days) + 1, int(cfg.volatility_days) + 1,
               int(cfg.volume_long_days), int(cfg.candle_breakout_lookback) + 1)


def validate(frame, now=None, *, minutes=INTERVAL, min_bars=1):
    clock = utc_now(now)
    diag = {'interval': f'{minutes}m', 'session_timezone': 'Asia/Kolkata',
            'asof': clock.isoformat(), 'timestamp_kind': 'bar_start',
            'completion': 'validated_closed_bars', 'required_bars': min_bars,
            'publication_grace_seconds': PUBLICATION_GRACE_SECONDS,
            'status': 'unavailable', 'reason': 'missing_data', 'rows': 0,
            'rejected': {}, 'source': (getattr(frame, 'attrs', {}) or {}).get('source', 'unknown')}
    empty = pd.DataFrame(columns=COLUMNS, index=pd.DatetimeIndex([], tz='UTC'))
    diag['calendar_status'] = 'verified_regular_sessions_2026'
    if clock.astimezone(IST).year not in VERIFIED_CALENDAR_YEARS:
        diag.update(calendar_status='unavailable', reason='session_calendar_unverified')
        return empty, diag
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return empty, diag
    if frame.attrs.get('interval') != f'{minutes}m':
        diag['reason'] = 'wrong_or_unproved_interval'
        return empty, diag
    if not set(COLUMNS).issubset(frame.columns):
        diag['reason'] = 'missing_ohlcv_columns'
        return empty, diag
    rejected = Counter()
    rows = {}
    conflicts = set()
    oldest = session_days(clock)[0]
    for idx, raw in frame[COLUMNS].iterrows():
        reason = bar_status(idx, minutes, clock, current_session=False, fresh=False)
        if reason:
            rejected[reason] += 1
            continue
        ts = pd.Timestamp(idx).tz_convert('UTC')
        if ts.tz_convert(IST).date() < oldest:
            rejected['outside_history_window'] += 1
            continue
        try:
            values = np.asarray(raw, dtype=float)
            o, h, l, c, v = values
            good = (np.isfinite(values).all() and min(o, h, l, c) > 0 and v >= 0
                    and h >= max(o, c, l) and l <= min(o, c, h))
        except (TypeError, ValueError):
            good = False
        if not good:
            rejected['invalid_ohlcv'] += 1
            continue
        if ts in rows:
            rejected['duplicate_bar'] += 1
            if not np.array_equal(rows[ts], values):
                conflicts.add(ts)
        else:
            rows[ts] = values
    for ts in conflicts:
        rows.pop(ts, None)
    if conflicts:
        rejected['conflicting_duplicates'] = len(conflicts)
    out = pd.DataFrame.from_dict(rows, orient='index', columns=COLUMNS).sort_index() if rows else empty
    out.attrs.update(source=diag['source'], interval=f'{minutes}m')
    diag.update(rows=len(out), rejected=dict(rejected), input_rows=len(frame))
    if not out.empty:
        last = out.index[-1]
        end = last + pd.Timedelta(minutes=minutes)
        diag.update(last_bar=last.isoformat(), last_bar_end=end.isoformat(),
                    session=last.tz_convert(IST).date().isoformat(),
                    age_since_close_seconds=(pd.Timestamp(clock) - end).total_seconds())
        reason = bar_status(last, minutes, clock)
        if reason:
            diag['reason'] = reason
        elif len(out) < min_bars:
            diag['reason'] = 'insufficient_completed_bars'
        else:
            # Continuity is required within the used window, across real
            # session breaks. A 30m frame cannot masquerade as 5m data.
            tail = out.tail(min_bars)
            expected = []
            for day in session_days(clock):
                opened, closed = session_bounds(day)
                expected.extend(pd.date_range(opened, closed, freq=f'{minutes}min', inclusive='left').tz_convert('UTC'))
            expected = [t for t in expected if tail.index[0] <= t <= last]
            missing = sorted(set(expected) - set(tail.index))
            diag['missing_bars_in_window'] = len(missing)
            diag['missing_bar_sample'] = [t.isoformat() for t in missing[:8]]
            diag['status'] = 'unavailable' if missing else 'ready'
            diag['reason'] = 'missing_bars_in_window' if missing else 'completed_current_session'
    out.attrs['intraday_validation'] = dict(diag)
    return out, diag


def window_description(cfg):
    return {'unit': 'completed_5m_bars', 'momentum': cfg.momentum_lookback_days,
            'relative_strength': cfg.medium_lookback_days,
            'breakout': cfg.breakout_lookback_days, 'volume_short': cfg.volume_short_days,
            'volume_long': cfg.volume_long_days, 'volatility': cfg.volatility_days,
            'required_bars': required_bars(cfg),
            'signal': 'existing_rolling_formulas; no new ORB or VWAP rule'}
