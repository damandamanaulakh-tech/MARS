"""
Chityy / Sourceborn - Pre 1-2 Week Trigger Engine v0.2

Purpose:
- Detect short lead-time trigger families before rise/fall/collapse/reversal.
- Paper-only scoring. No broker orders. No real trading execution.
- Designed for Upstox 15Y OHLCV once uploaded.

Input:
    candles: list[dict] sorted oldest -> newest
    Required keys for OHLCV-only functions: date, open, high, low, close, volume

Output:
    trigger flags + scores for the latest row/window.

Reality lock:
- A trigger hit is not proof.
- Proof requires reverse backtest over 15Y data, baseline comparison, and false-positive audit.
"""

from __future__ import annotations
from typing import List, Dict, Any
import math

Candle = Dict[str, float]

def _safe_float(x, default=0.0):
    try:
        if x is None or x == "":
            return default
        return float(x)
    except Exception:
        return default

def _sma(vals, n):
    vals = vals[-n:]
    return sum(vals) / len(vals) if vals else 0.0

def _stdev(vals):
    if len(vals) < 2:
        return 0.0
    m = sum(vals) / len(vals)
    return math.sqrt(sum((x - m) ** 2 for x in vals) / (len(vals) - 1))

def _clv(c):
    high = _safe_float(c.get("high"))
    low = _safe_float(c.get("low"))
    close = _safe_float(c.get("close"))
    rng = high - low
    return 0.5 if rng <= 0 else (close - low) / rng

def _true_range(c, prev_close):
    h = _safe_float(c.get("high"))
    l = _safe_float(c.get("low"))
    return max(h - l, abs(h - prev_close), abs(l - prev_close))

def _atr(candles, n=14):
    if len(candles) < 2:
        return 0.0
    trs = []
    for i in range(max(1, len(candles)-n), len(candles)):
        prev_close = _safe_float(candles[i-1].get("close"))
        trs.append(_true_range(candles[i], prev_close))
    return sum(trs)/len(trs) if trs else 0.0

def _obv(candles):
    total = 0.0
    out = []
    for i, c in enumerate(candles):
        vol = _safe_float(c.get("volume"))
        if i == 0:
            out.append(total)
            continue
        close = _safe_float(c.get("close"))
        prev = _safe_float(candles[i-1].get("close"))
        if close > prev:
            total += vol
        elif close < prev:
            total -= vol
        out.append(total)
    return out

def _slope(vals, n=10):
    vals = vals[-n:]
    if len(vals) < 2:
        return 0.0
    xs = list(range(len(vals)))
    xbar = sum(xs)/len(xs)
    ybar = sum(vals)/len(vals)
    den = sum((x-xbar)**2 for x in xs)
    if den == 0:
        return 0.0
    return sum((x-xbar)*(y-ybar) for x,y in zip(xs, vals))/den

def _pct_return(candles, n):
    if len(candles) <= n:
        return 0.0
    a = _safe_float(candles[-n-1].get("close"))
    b = _safe_float(candles[-1].get("close"))
    return (b/a - 1.0) if a else 0.0

def compute_short_trigger_flags(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Calculates a compact v0.2 short-window trigger set.
    Full 112 parameter table is in trigger_params_v0_2.csv.
    """
    if len(candles) < 25:
        return {
            "status": "INSUFFICIENT_HISTORY",
            "needed": "At least 25 daily candles; preferred 252+ for percentiles.",
            "flags": {},
            "scores": {}
        }

    cs = candles
    latest = cs[-1]
    closes = [_safe_float(c.get("close")) for c in cs]
    highs = [_safe_float(c.get("high")) for c in cs]
    lows = [_safe_float(c.get("low")) for c in cs]
    vols = [_safe_float(c.get("volume")) for c in cs]
    opens = [_safe_float(c.get("open")) for c in cs]
    ranges = [h-l for h,l in zip(highs,lows)]
    atr14 = _atr(cs, 14)
    vol20 = _sma(vols, 20)
    vol5 = _sma(vols, 5)
    ret10 = _pct_return(cs, 10)
    obv_vals = _obv(cs)

    latest_range = ranges[-1]
    clv_vals = [_clv(c) for c in cs]
    clv5 = _sma(clv_vals, 5)
    clv7 = _sma(clv_vals, 7)

    high20 = max(highs[-20:])
    low20 = min(lows[-20:])
    close = closes[-1]
    open_ = opens[-1]
    volume = vols[-1]

    flags = {}

    # Pre-rise / accumulation
    flags["nr7_compression"] = latest_range <= min(ranges[-7:])
    flags["nr14_compression"] = latest_range <= min(ranges[-14:])
    flags["base_width_under_8pct"] = ((max(highs[-10:]) - min(lows[-10:])) / close <= 0.08) if close else False
    flags["close_above_base_midline"] = close > ((max(highs[-10:]) + min(lows[-10:])) / 2)
    flags["volume_dry_up"] = vol5 < 0.75 * vol20 if vol20 else False
    flags["volume_wakeup"] = vol5 < 0.75 * vol20 and volume > 1.5 * vol20 if vol20 else False
    flags["positive_clv_accumulation"] = clv7 >= 0.65
    flags["up_volume_dominance_7d"] = _up_volume_ratio(cs, 7) >= 0.62
    flags["obv_rising_flat_price"] = (_slope(obv_vals, 10) > 0 and abs(ret10) <= 0.03)
    flags["near_20d_high_without_breakout"] = close >= 0.97 * high20 and close <= high20

    # Reclaim / reversal
    prior_support = min(lows[-21:-1])
    flags["support_sweep_reclaim"] = lows[-1] < prior_support and close > prior_support
    flags["gap_down_reclaim"] = opens[-1] < lows[-2] and close >= closes[-2]
    flags["low_undercut_high_close"] = lows[-1] < min(lows[-6:-1]) and _clv(latest) >= 0.65
    flags["lower_wick_demand"] = _lower_wick_ratio(latest) > 0.45

    # Pre-fall / distribution
    flags["new_high_negative_clv"] = highs[-1] >= high20 and _clv(latest) < 0.45
    flags["gap_up_volume_rejection"] = opens[-1] > highs[-2] and close < opens[-1] and volume > 1.5 * vol20 if vol20 else False
    flags["high_volume_no_progress"] = volume > 1.8 * vol20 and close <= closes[-2] if vol20 else False
    flags["bearish_wide_range_close"] = latest_range > 1.5 * atr14 and close < open_ and _clv(latest) < 0.30 if atr14 else False
    flags["down_volume_ratio_spike"] = _down_volume_ratio(cs, 5) >= 0.65
    flags["supply_wick_warning"] = _upper_wick_ratio(latest) > 0.45 and close >= 0.95 * high20

    # Collapse
    flags["support_break_with_volume"] = close < low20 and volume > 1.8 * vol20 if vol20 else False
    flags["atr_shock_close_low"] = latest_range > 2.0 * atr14 and _clv(latest) < 0.20 if atr14 else False
    flags["two_gap_down_sequence"] = sum(1 for i in range(max(1, len(cs)-10), len(cs)) if _safe_float(cs[i].get("open")) < _safe_float(cs[i-1].get("low"))) >= 2

    rise_score = _score(flags, [
        "nr7_compression","nr14_compression","base_width_under_8pct","close_above_base_midline",
        "volume_dry_up","volume_wakeup","positive_clv_accumulation","up_volume_dominance_7d",
        "obv_rising_flat_price","near_20d_high_without_breakout"
    ])
    reversal_score = _score(flags, ["support_sweep_reclaim","gap_down_reclaim","low_undercut_high_close","lower_wick_demand"])
    fall_score = _score(flags, [
        "new_high_negative_clv","gap_up_volume_rejection","high_volume_no_progress",
        "bearish_wide_range_close","down_volume_ratio_spike","supply_wick_warning"
    ])
    collapse_score = _score(flags, ["support_break_with_volume","atr_shock_close_low","two_gap_down_sequence"])

    action = "HOLD_REVIEW"
    if collapse_score >= 60:
        action = "PAPER_BLOCK_LONG_COLLAPSE_RISK"
    elif fall_score >= 55:
        action = "PAPER_AVOID_OR_EXIT_REVIEW"
    elif rise_score >= 65 and fall_score < 35:
        action = "PAPER_WATCH_OR_ENTRY_REVIEW"
    elif reversal_score >= 50 and fall_score < 40:
        action = "PAPER_REVERSAL_WATCH"

    return {
        "status": "PAPER_ONLY",
        "flags": flags,
        "scores": {
            "rise_trigger_score": rise_score,
            "reversal_trigger_score": reversal_score,
            "fall_warning_score": fall_score,
            "collapse_risk_score": collapse_score,
        },
        "paper_action": action,
        "proof_status": "REQUIRES_15Y_BACKTEST"
    }

def _score(flags, keys):
    if not keys:
        return 0
    return round(100.0 * sum(1 for k in keys if flags.get(k)) / len(keys), 2)

def _up_volume_ratio(candles, n):
    total = 0.0
    up = 0.0
    start = max(1, len(candles)-n)
    for i in range(start, len(candles)):
        vol = _safe_float(candles[i].get("volume"))
        total += vol
        if _safe_float(candles[i].get("close")) > _safe_float(candles[i-1].get("close")):
            up += vol
    return up/total if total else 0.0

def _down_volume_ratio(candles, n):
    total = 0.0
    down = 0.0
    start = max(1, len(candles)-n)
    for i in range(start, len(candles)):
        vol = _safe_float(candles[i].get("volume"))
        total += vol
        if _safe_float(candles[i].get("close")) < _safe_float(candles[i-1].get("close")):
            down += vol
    return down/total if total else 0.0

def _lower_wick_ratio(c):
    h = _safe_float(c.get("high"))
    l = _safe_float(c.get("low"))
    o = _safe_float(c.get("open"))
    close = _safe_float(c.get("close"))
    rng = h - l
    if rng <= 0:
        return 0.0
    lower_body = min(o, close)
    return (lower_body - l) / rng

def _upper_wick_ratio(c):
    h = _safe_float(c.get("high"))
    l = _safe_float(c.get("low"))
    o = _safe_float(c.get("open"))
    close = _safe_float(c.get("close"))
    rng = h - l
    if rng <= 0:
        return 0.0
    upper_body = max(o, close)
    return (h - upper_body) / rng
