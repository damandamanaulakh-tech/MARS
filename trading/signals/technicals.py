"""Technical indicator library. Pure functions of OHLCV bars, no I/O.

All functions:
  - Take a pandas Series or DataFrame
  - Return a float (latest value) OR pandas Series (full history)
  - Never raise on short/missing data — return None instead
  - Are deterministic (no randomness, no external lookups)

This lets the same module power both live scoring AND the backtester
with zero risk of lookahead.
"""

# AUDIT E (2026-08-09): honest status — the only PRODUCTION consumer of this
# module is adx() (engine nifty-chop gate + backtest). Every other scorer here
# is a maintained LIBRARY awaiting an owner order to wire; none feeds a live
# score today. The fake "supertrend" (it had no band ratcheting and returned
# "up" almost always) is deleted rather than left reading as capability.
# AUDIT R-10 (2026-10-02): five scorers that nothing named or called
# (golden_cross_active, rsi_score, macd_score, adx_trend_score,
# obv_trend_score) are deleted. The helpers that stay are the ones the
# /register page names as where an item would come from.


from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


# ---------------- Moving averages ----------------

def sma(s: pd.Series, n: int) -> Optional[float]:
    if s is None or len(s.dropna()) < n:
        return None
    return float(s.dropna().rolling(n).mean().iloc[-1])


def ema(s: pd.Series, n: int) -> Optional[float]:
    if s is None or len(s.dropna()) < n:
        return None
    return float(s.dropna().ewm(span=n, adjust=False).mean().iloc[-1])


def ma_stack_score(close: pd.Series) -> float:
    """0-100 score for MA stack alignment.

    100 = price > 20-EMA > 50-EMA > 200-EMA (perfect bull alignment).
    0   = price < 20-EMA < 50-EMA < 200-EMA (perfect bear alignment).
    50  = mixed.
    """
    if close is None or len(close.dropna()) < 200:
        return 50.0
    last = float(close.iloc[-1])
    e20 = ema(close, 20)
    e50 = ema(close, 50)
    e200 = ema(close, 200)
    if None in (e20, e50, e200):
        return 50.0
    score = 50.0
    score += 15 if last > e20 else -15
    score += 15 if e20 > e50 else -15
    score += 20 if e50 > e200 else -20
    return max(0.0, min(100.0, score))


# ---------------- RSI ----------------

def rsi(close: pd.Series, length: int = 14) -> Optional[float]:
    if close is None or len(close.dropna()) < length + 1:
        return None
    delta = close.diff().dropna()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    roll_up = up.ewm(alpha=1 / length, adjust=False).mean()
    roll_down = down.ewm(alpha=1 / length, adjust=False).mean()
    # Avoid divide-by-zero AND avoid NaN propagation for monotone series:
    # if downside is zero, RSI is unambiguously 100.
    eps = 1e-9
    rs = roll_up / roll_down.where(roll_down > 0, eps)
    rsi_series = 100 - (100 / (1 + rs))
    val = rsi_series.dropna()
    return float(val.iloc[-1]) if not val.empty else None


# ---------------- MACD ----------------

def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """Returns (macd_line_last, signal_line_last, histogram_last) or (None,None,None)."""
    if close is None or len(close.dropna()) < slow + signal:
        return None, None, None
    fast_ema = close.ewm(span=fast, adjust=False).mean()
    slow_ema = close.ewm(span=slow, adjust=False).mean()
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return float(macd_line.iloc[-1]), float(signal_line.iloc[-1]), float(hist.iloc[-1])


# ---------------- Bollinger ----------------

def bollinger_bandwidth(close: pd.Series, length: int = 20, std_mult: float = 2.0) -> Optional[float]:
    """Width = (upper - lower) / middle. Smaller = squeeze."""
    if close is None or len(close.dropna()) < length:
        return None
    mid = close.rolling(length).mean()
    sd = close.rolling(length).std()
    upper = mid + std_mult * sd
    lower = mid - std_mult * sd
    width = ((upper - lower) / mid).dropna()
    return float(width.iloc[-1]) if not width.empty else None


def bollinger_squeeze_score(close: pd.Series, length: int = 20) -> float:
    """High score when bandwidth is at 20-day low (squeeze = pre-breakout)."""
    if close is None or len(close.dropna()) < length * 2:
        return 50.0
    mid = close.rolling(length).mean()
    sd = close.rolling(length).std()
    width = ((mid + 2 * sd) - (mid - 2 * sd)) / mid
    width = width.dropna()
    if len(width) < length:
        return 50.0
    last_w = float(width.iloc[-1])
    recent = width.iloc[-length:]
    pct_rank = (recent <= last_w).sum() / len(recent)   # 0.0 = lowest, 1.0 = highest
    # Lowest 20% width -> score 90, highest 20% -> score 30
    return float(max(30, 90 - pct_rank * 60))


# ---------------- ADX ----------------

def adx(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> Optional[float]:
    """Returns the latest ADX value."""
    if high is None or low is None or close is None:
        return None
    if len(close.dropna()) < length * 2:
        return None
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = ((up_move > down_move) & (up_move > 0)).astype(float) * up_move
    minus_dm = ((down_move > up_move) & (down_move > 0)).astype(float) * down_move

    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr_s = tr.ewm(alpha=1 / length, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / length, adjust=False).mean() / atr_s.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1 / length, adjust=False).mean() / atr_s.replace(0, np.nan)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)) * 100
    adx_series = dx.ewm(alpha=1 / length, adjust=False).mean()
    val = adx_series.dropna()
    return float(val.iloc[-1]) if not val.empty else None


# ---------------- OBV ----------------

def obv(close: pd.Series, volume: pd.Series) -> Optional[pd.Series]:
    if close is None or volume is None:
        return None
    if len(close.dropna()) < 2:
        return None
    diff = close.diff().fillna(0)
    sign = np.sign(diff)
    return (sign * volume.fillna(0)).cumsum()


# ---------------- 52-week high ----------------

def pct_off_52w_high(close: pd.Series) -> Optional[float]:
    """Returns negative % (e.g., -3.5 = 3.5% off the 52w high)."""
    if close is None or len(close.dropna()) < 50:
        return None
    last_252 = close.dropna().iloc[-252:]
    if last_252.empty:
        return None
    hi = float(last_252.max())
    if hi <= 0:
        return None
    return (float(last_252.iloc[-1]) - hi) / hi * 100


def proximity_to_52w_high_score(close: pd.Series) -> float:
    """100 = at the high, 0 = > 30% off. George & Hwang (2004)."""
    p = pct_off_52w_high(close)
    if p is None:
        return 50.0
    if p >= -2:
        return 100.0
    if p >= -5:
        return 85.0
    if p >= -10:
        return 65.0
    if p >= -20:
        return 40.0
    return 15.0


# ---------------- Beta ----------------

def beta_vs_benchmark(stock_close: pd.Series, bench_close: pd.Series, days: int = 90) -> Optional[float]:
    if stock_close is None or bench_close is None:
        return None
    s = stock_close.pct_change().dropna().iloc[-days:]
    b = bench_close.pct_change().dropna().iloc[-days:]
    df = pd.concat([s, b], axis=1, keys=["s", "b"]).dropna()
    if len(df) < 30:
        return None
    var_b = df["b"].var()
    if var_b <= 0:
        return None
    cov = df["s"].cov(df["b"])
    return float(cov / var_b)
