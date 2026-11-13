"""Live readers for SEQ-06. Read pages never call these provider adapters."""
from copy import deepcopy
from datetime import timedelta
import pandas as pd

from . import sector_identity as si, market_context as mc, intraday_data
from .data_refresh import SECTOR_INDEX_KEYS
from .quote_evidence import utc

_sector_reader = None


def set_sector_reader(reader):
    global _sector_reader
    _sector_reader = reader


def daily_sectors(now):
    try:
        rows = _sector_reader((utc(now)-timedelta(days=180)).date().isoformat()) if _sector_reader else []
        groups = {}
        for r in rows:
            if r.get('sector') in SECTOR_INDEX_KEYS:
                groups.setdefault(r['sector'], []).append(r)
        out = {}
        for name, rows in groups.items():
            f = pd.DataFrame({'Close': [r.get('close') for r in rows]},
                             index=pd.to_datetime([r.get('date') for r in rows], errors='coerce'))
            f.attrs.update(interval='1d', source='stored_upstox_sector_daily', price_basis='price_index',
                           received_at=max((r.get('received_at') or '' for r in rows), default='') or None)
            out[name] = f
        return out
    except Exception:
        return {}  # caller records each missing leg, never manufactures a return


def identities(tickers, cfg, now, snapshot=None):
    from .universe_refresh import read_existing, default_universe_path
    master = read_existing(default_universe_path())
    data = si.snapshot() if snapshot is None else snapshot
    return {t: si.resolve(t, data, now, configured_label=cfg.sector_map.get(t),
                         instrument_isin=(master.get(t) or {}).get('isin')) for t in tickers}


def _prepare(tickers, cfg, now, *, client=None, interval=None):
    """Bounded sector work once per scan/refresh, never once per stock."""
    interval = interval or ('5m' if cfg.mode == 'intraday' else '1d')
    ids = identities(tickers, cfg, now)
    details = {}
    if interval == '5m':
        keys = {i['sector_index']: SECTOR_INDEX_KEYS[i['sector_index']] for i in ids.values()
                if i.get('sector_status') == 'verified' and i.get('sector_index') in SECTOR_INDEX_KEYS}
        frames, details = intraday_data.fetch(list(keys), cfg, client, now,
            instrument_keys=keys, cap=len(keys), budget_seconds=20) if keys else ({}, {})
    else:
        frames = daily_sectors(now)
    return {'identities': ids, 'frames': frames, 'details': details, 'interval': interval}


def prepare(tickers, cfg, now, *, client=None, interval=None):
    try:
        return _prepare(tickers, cfg, now, client=client, interval=interval)
    except Exception as exc:
        return {'identities': {}, 'frames': {}, 'details': {},
                'interval': interval or ('5m' if cfg.mode == 'intraday' else '1d'),
                'error': type(exc).__name__}


def _observation(inputs, ticker, stock, market, cfg, now):
    identity = inputs['identities'].get(ticker) or {'status': 'unknown', 'reason': 'identity_not_loaded'}
    sector_name = identity.get('sector_index')
    context = mc.compare(ticker, stock, inputs['frames'].get(sector_name), market, identity, now,
                         lookback=cfg.momentum_lookback_days, interval=inputs['interval'], market_ticker=cfg.benchmark)
    if inputs.get('details', {}).get(sector_name):
        context['sector_fetch'] = deepcopy(inputs['details'][sector_name])
        if context['sector']['status'] == 'unknown':
            context['sector']['reason'] = inputs['details'][sector_name]['reason']
    context['legacy_score_sector'] = {
        'status': 'unverified_legacy_component',
        'reason': 'Configured peer labels and policy/rotation bonuses are not verified index returns. Existing score rules are unchanged.',
        'configured_label': cfg.sector_map.get(ticker), 'measured_coverage_pct': None}
    return context


def observation(inputs, ticker, stock, market, cfg, now):
    try:
        return _observation(inputs, ticker, stock, market, cfg, now)
    except Exception as exc:
        return {**unavailable(ticker, now, 'context_comparison_failed'), 'error_type': type(exc).__name__}


def unavailable(ticker, now, reason='context_not_captured_on_this_path'):
    return {'schema': 1, 'model': mc.MODEL, 'role': 'context_only', 'ticker': ticker,
            'observed_at': utc(now).isoformat(), 'status': 'unknown', 'reason': reason}
