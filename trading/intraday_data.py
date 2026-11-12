"""Bounded, per-request intraday history with explicit coverage for every name."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from datetime import timedelta
import threading

import pandas as pd

from . import intraday_bars as bars
from .brokers.upstox_instruments import to_instrument_keys

_HISTORY = OrderedDict()
_LOCK = threading.Lock()
HISTORY_CACHE_CAP = 1600
FETCH_BUDGET_SECONDS = 90


def clear_cache():
    with _LOCK:
        _HISTORY.clear()


def _one(client, key, now):
    days = bars.session_days(now)
    today = bars.utc_now(now).astimezone(bars.IST).date()
    cache_key = (key, today.isoformat(), bars.INTERVAL)
    with _LOCK:
        prior = _HISTORY.get(cache_key)
        if prior is not None:
            prior = prior.copy(deep=True)
    # Fetch today's endpoint separately: historical candles do not promise
    # today's partial session. Never call a daily endpoint as a substitute.
    current = client.get_intraday_frame(key, interval_minutes=bars.INTERVAL, timeout=8.0)
    # Do not turn an adapter's daily/30-minute frame into proved five-minute
    # data by overwriting its metadata at concat time.
    if isinstance(current, pd.DataFrame) and not current.empty and current.attrs.get('interval') != '5m':
        return current
    if prior is None:
        prior = client.get_intraday_frame(key, interval_minutes=bars.INTERVAL,
            from_date=days[0].isoformat(), to_date=(today - timedelta(days=1)).isoformat(), timeout=8.0)
        if isinstance(prior, pd.DataFrame) and not prior.empty and prior.attrs.get('interval') != '5m':
            return prior
        if prior is not None and not prior.empty:
            prior, _ = bars.validate(prior, now)
            prior = prior.loc[[t.tz_convert(bars.IST).date() < today for t in prior.index]]
            if not prior.empty:
                with _LOCK:
                    _HISTORY[cache_key] = prior.copy(deep=True)
                    _HISTORY.move_to_end(cache_key)
                    while len(_HISTORY) > HISTORY_CACHE_CAP:
                        _HISTORY.popitem(last=False)
    frames = [f for f in (prior, current) if isinstance(f, pd.DataFrame) and not f.empty]
    merged = pd.concat(frames) if frames else pd.DataFrame()
    merged.attrs = {'interval': f'{bars.INTERVAL}m', 'source': 'upstox_v3'}
    return merged


def fetch(tickers, cfg, client=None, now=None, *, budget_seconds=FETCH_BUDGET_SECONDS, cap=None,
          offset=0, priority=()):
    from .data import history_broker, MAX_NETWORK_FALLBACK
    now = bars.utc_now(now)
    names = list(dict.fromkeys(tickers))
    limit = MAX_NETWORK_FALLBACK if cap is None else max(0, int(cap))
    needed = bars.required_bars(cfg)
    details = {t: bars.validate(None, now, min_bars=needed)[1] for t in names}
    out = {}
    client = client or history_broker()
    if client is None or not callable(getattr(client, 'get_intraday_frame', None)):
        for d in details.values():
            d['reason'] = 'intraday_provider_unavailable'
        return out, details
    first = [t for t in dict.fromkeys(priority) if t in names]
    rest = [t for t in names if t not in first]
    offset = int(offset) % max(1, len(rest))
    order = first + rest[offset:] + rest[:offset]
    selected = order[:limit]
    for t in order[limit:]:
        details[t]['reason'] = 'intraday_fetch_cap'
    mapping = to_instrument_keys(selected)
    for t in selected:
        if t not in mapping:
            details[t]['reason'] = 'instrument_mapping_unavailable'
    # Four workers bound concurrency; provider 429s remain explicit failures.
    # Both API calls have finite timeouts. A deadline cancels queued work; late completions
    # cannot enter this result or mutate a portfolio.
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='intraday-data')
    pending = {pool.submit(_one, client, mapping[t], now): t for t in selected if t in mapping}
    try:
        for future in as_completed(pending, timeout=max(0, budget_seconds)):
            t = pending[future]
            try:
                frame = future.result()
                clean, d = bars.validate(frame, now, min_bars=needed)
                details[t] = d
                if d['status'] == 'ready':
                    out[t] = clean
            except Exception as exc:
                status = getattr(getattr(exc, 'response', None), 'status_code', None)
                details[t]['reason'] = f'provider_http_{status}' if status else 'intraday_fetch_failed'
                details[t]['error_type'] = type(exc).__name__
    except TimeoutError:
        for future, t in pending.items():
            if details[t]['reason'] == 'missing_data':
                details[t]['reason'] = 'intraday_fetch_deadline'
            future.cancel()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return out, details
