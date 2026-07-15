"""Entry timing overlay. AM 07 picks the stock; this decides when to enter.

Rule: enter only if the stock is also breaking out (within N% of 20-day high)
AND volume is confirming (5-day vol >= 1.3x 20-day vol).
"""

from __future__ import annotations

import pandas as pd

from .config import Config


def passes_breakout(close: pd.Series, cfg: Config) -> tuple[bool, float]:
    """True iff price is within `breakout_threshold_pct` of the N-day high."""
    if len(close) < cfg.breakout_lookback_days + 1:
        return False, 0.0
    window = close.iloc[-cfg.breakout_lookback_days:]
    hi = float(window.max())
    if hi <= 0:
        return False, 0.0
    last = float(close.iloc[-1])
    pct_from_high = (last - hi) / hi
    return pct_from_high >= -cfg.breakout_threshold_pct, pct_from_high


def passes_volume_confirm(volume: pd.Series, cfg: Config) -> tuple[bool, float]:
    """True iff 5-day avg volume >= ratio * 20-day avg volume."""
    if len(volume) < cfg.volume_long_days:
        return False, 0.0
    short = float(volume.iloc[-cfg.volume_short_days:].mean())
    long_ = float(volume.iloc[-cfg.volume_long_days:].mean())
    if long_ <= 0:
        return False, 0.0
    ratio = short / long_
    return ratio >= cfg.volume_confirm_ratio, ratio


def timing_ok(history: pd.DataFrame, cfg: Config) -> tuple[bool, dict]:
    """Combine the two checks. Returns (pass, diagnostics)."""
    b_ok, b_val = passes_breakout(history["Close"], cfg)
    v_ok, v_val = passes_volume_confirm(history["Volume"], cfg)
    return b_ok and v_ok, {
        "breakout_ok": b_ok, "pct_from_high": b_val,
        "volume_ok": v_ok, "vol_ratio": v_val,
    }
