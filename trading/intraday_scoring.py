"""Existing rolling score formulas applied explicitly to completed 5m bars.

Weights, window counts, score floors and timing thresholds are unchanged.
The legacy *_days component/config names are not claims about this interval.
ORB/VWAP would be a different strategy and are not silently introduced here.
"""
from copy import copy
import numpy as np

from . import intraday_bars as bars


def compute(ticker, frame, benchmark, history, cfg, now=None):
    from .scoring import compute_score
    clean, diag = bars.validate(frame, now, min_bars=bars.required_bars(cfg))
    diag['windows'] = bars.window_description(cfg)
    if diag['status'] != 'ready':
        return None, {}, diag
    n = int(cfg.medium_lookback_days) + 1
    benchmark, bd = bars.validate(benchmark, now, min_bars=n)
    aligned_bench = (bd['status'] == 'ready' and
                     benchmark.tail(n).index.equals(clean.tail(n).index))
    diag['benchmark'] = {**bd, 'aligned': aligned_bench}
    # Preserve the existing neutral fallback, but make unavailable/misaligned
    # benchmark data explicit instead of presenting it as measured strength.
    if not aligned_bench:
        benchmark = None
    peers = {}
    for symbol, peer in history.items():
        if peer is None or peer.empty:
            continue
        npeer = int(cfg.momentum_lookback_days) + 1
        if peer.tail(npeer).index.equals(clean.tail(npeer).index):
            peers[symbol] = peer
    icfg = copy(cfg)
    icfg.mode = 'intraday'
    total, components = compute_score(ticker, clean, benchmark, peers, icfg, mode='intraday')
    # The old helper annualizes daily volatility with sqrt(252). Five-minute
    # equity bars have 75 observations per normal session; retain its ratio
    # formula and 0..100 mapping with the correct annualization unit.
    close = clean['Close']
    ret = close.pct_change(fill_method=None).dropna().tail(cfg.volatility_days)
    vol = float(ret.std() * np.sqrt(252 * 75) * 100)
    lookback = int(cfg.momentum_lookback_days)
    change = (float(close.iloc[-1]) / float(close.iloc[-lookback - 1]) - 1) * 100
    upside = max(0., min(100., 50. + change / vol * 50.)) if np.isfinite(vol) and vol > 0 else 50.
    from .scoring import weights_for
    total += (upside - components['vol_adj_upside']) * weights_for('intraday')['vol_adj_upside']
    components['vol_adj_upside'] = upside
    diag['component_coverage'] = {
        'relative_strength': 'measured' if aligned_bench else 'unavailable_neutral_50',
        'sector_alignment': 'unverified_legacy_peer_component; see separate verified market_context; neutral fallback is not measured strength',
        'vol_adj_upside': 'annualized_75_bars_per_session' if vol > 0 else 'unavailable_neutral_50',
        'earnings_growth': 'zero_weight', 'short_interest': 'zero_weight',
        'analyst_revision': 'zero_weight'}
    diag['context_interval'] = {
        'fii_tide': 'daily_flow_context', 'hot_sector_bonus': 'daily_context',
        'news_and_policy': 'dated_external_context; not intraday bars'}
    return total, components, diag
