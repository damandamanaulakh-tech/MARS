"""Intraday-specific signals: VWAP, Opening Range Breakout, time-based exits.

These are pure functions of OHLCV slices. No lookahead — every helper
takes a frozen DataFrame ending at "now" and returns a score in [0, 100].

Designed for 5-min or 15-min bars. The engine selects the bar interval
based on `cfg.mode` via `trading/modes.py`.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Optional

import numpy as np
import pandas as pd


# --- VWAP ---

def session_vwap(bars: pd.DataFrame) -> pd.Series:
    """VWAP series resetting at each session open.

    Expects a DataFrame indexed by datetime with High/Low/Close/Volume.
    For Indian sessions, the index dates determine the reset boundary.
    """
    typical = (bars["High"] + bars["Low"] + bars["Close"]) / 3.0
    pv = typical * bars["Volume"]
    if not isinstance(bars.index, pd.DatetimeIndex):
        # Fallback: single-session cumulative VWAP
        return pv.cumsum() / bars["Volume"].cumsum().replace(0, np.nan)
    # Group by date so VWAP resets each morning
    day = bars.index.date
    pv_csum = pv.groupby(day).cumsum()
    vol_csum = bars["Volume"].groupby(day).cumsum().replace(0, np.nan)
    return pv_csum / vol_csum


def vwap_reclaim_score(bars: pd.DataFrame) -> float:
    """100 = price just crossed above VWAP after dipping below; 50 = at VWAP."""
    if bars is None or len(bars) < 6:
        return 50.0
    vwap = session_vwap(bars)
    last_close = float(bars["Close"].iloc[-1])
    last_vwap = float(vwap.iloc[-1]) if pd.notna(vwap.iloc[-1]) else last_close
    if last_vwap == 0:
        return 50.0
    rel = (last_close - last_vwap) / last_vwap
    # +0.5% above VWAP -> 100, -0.5% below -> 0
    score = 50 + rel * 10_000
    return float(max(0.0, min(100.0, score)))


# --- Opening Range Breakout ---

def opening_range(bars: pd.DataFrame, minutes: int = 30) -> Optional[tuple[float, float]]:
    """High/low of the first `minutes` of today's session, in IST.

    If we can't identify today's open, returns None.
    """
    if bars is None or len(bars) == 0 or not isinstance(bars.index, pd.DatetimeIndex):
        return None
    last_date = bars.index[-1].date()
    today = bars[bars.index.date == last_date]
    if today.empty:
        return None
    # Indian market opens at 09:15. First N minutes = bars up to 09:15 + N.
    open_window_end = datetime.combine(last_date, time(9, 15)) + pd.Timedelta(minutes=minutes)
    open_window = today[today.index < open_window_end]
    if open_window.empty:
        # If the first bar is already past the open window, use first N bars instead
        n_bars = max(1, minutes // 5)
        open_window = today.iloc[:n_bars]
    hi = float(open_window["High"].max())
    lo = float(open_window["Low"].min())
    return hi, lo


def orb_breakout_score(bars: pd.DataFrame, minutes: int = 30) -> float:
    """100 = price > opening range high with momentum; 0 = below opening range low."""
    rng = opening_range(bars, minutes=minutes)
    if rng is None:
        return 50.0
    hi, lo = rng
    last_close = float(bars["Close"].iloc[-1])
    if last_close > hi:
        # Bullish breakout
        return min(100.0, 70.0 + (last_close - hi) / hi * 5_000)
    if last_close < lo:
        # Bearish breakdown — for long-only swing engine this is negative
        return max(0.0, 30.0 + (last_close - lo) / lo * 5_000)
    # Inside the opening range — neutral
    mid = (hi + lo) / 2
    span = max(hi - lo, 1e-9)
    return float(50.0 + (last_close - mid) / span * 20)


# --- EMA crossover ---

def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def ema_cross_score(close: pd.Series, fast: int = 5, slow: int = 20) -> float:
    """100 = fast just crossed above slow; 0 = fast crossed below."""
    if close is None or len(close) < slow + 2:
        return 50.0
    f = ema(close, fast)
    s = ema(close, slow)
    diff = f - s
    if len(diff) < 2:
        return 50.0
    prev = float(diff.iloc[-2])
    now = float(diff.iloc[-1])
    last = float(close.iloc[-1]) or 1.0
    norm_now = now / last
    if prev <= 0 and now > 0:
        return 90.0  # fresh bullish cross
    if prev >= 0 and now < 0:
        return 10.0  # fresh bearish cross
    # Drift score: how far apart the EMAs are
    return float(max(0.0, min(100.0, 50.0 + norm_now * 5_000)))


# --- Time-based exit ---

def should_square_off(now_ist: datetime, square_off_hhmm: str) -> bool:
    """True if `now` is past the mode's square-off time today."""
    try:
        hh, mm = (int(x) for x in square_off_hhmm.split(":"))
    except (ValueError, AttributeError):
        return False
    cutoff = now_ist.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return now_ist >= cutoff


# --- Composite intraday score override ---

def intraday_breakout_quality(bars: pd.DataFrame) -> float:
    """Blend ORB + VWAP reclaim + EMA cross for the breakout_quality slot."""
    orb = orb_breakout_score(bars)
    vwp = vwap_reclaim_score(bars)
    ema_s = ema_cross_score(bars["Close"]) if "Close" in bars.columns else 50.0
    return (orb + vwp + ema_s) / 3.0
