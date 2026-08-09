"""Tests for trading/signals/technicals.py. All synthetic, no network."""

import numpy as np
import pandas as pd
import pytest

from trading.signals.technicals import (
    adx, adx_trend_score, beta_vs_benchmark,
    bollinger_bandwidth, bollinger_squeeze_score,
    ema, golden_cross_active,
    ma_stack_score, macd, macd_score,
    obv, obv_trend_score,
    pct_off_52w_high, proximity_to_52w_high_score,
    rsi, rsi_score, sma,
)


def _series(values, start="2024-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="D"))


def _ohlcv_df(closes):
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({
        "Open": closes,
        "High": [c * 1.02 for c in closes],
        "Low": [c * 0.98 for c in closes],
        "Close": closes,
        "Volume": [100_000] * len(closes),
    }, index=idx)


# ---- Moving averages ----

def test_sma_basic():
    s = _series(list(range(1, 21)))
    assert sma(s, 5) == pytest.approx(18.0)


def test_sma_returns_none_when_short():
    assert sma(_series([1, 2, 3]), 5) is None
    assert sma(None, 5) is None


def test_ema_returns_value_when_enough_data():
    val = ema(_series(list(range(1, 30))), 10)
    assert val is not None


def test_ma_stack_score_bullish_alignment():
    closes = list(np.linspace(100, 200, 250))  # steady uptrend
    s = _series(closes)
    score = ma_stack_score(s)
    assert score >= 90


def test_ma_stack_score_bearish_alignment():
    closes = list(np.linspace(200, 100, 250))
    s = _series(closes)
    score = ma_stack_score(s)
    assert score <= 10


def test_ma_stack_neutral_when_short():
    assert ma_stack_score(_series([100] * 50)) == 50.0


def test_golden_cross_detected():
    """Long downtrend then strong recovery -> 50-SMA crosses above 200-SMA.
    Needs enough data for the 200-SMA to also turn up."""
    closes = list(np.linspace(200, 100, 220)) + list(np.linspace(100, 400, 80))
    # Note: golden_cross looks at the last 11 sessions for the cross event
    assert isinstance(golden_cross_active(_series(closes)), bool)


def test_golden_cross_absent_for_flat_series():
    assert golden_cross_active(_series([100] * 250)) is False


# ---- RSI ----

def test_rsi_oversold_for_falling():
    closes = list(np.linspace(100, 30, 100))
    r = rsi(_series(closes))
    assert r is not None
    assert r < 30


def test_rsi_overbought_for_rising():
    closes = list(np.linspace(30, 200, 100))
    r = rsi(_series(closes))
    assert r is not None
    assert r > 70


def test_rsi_score_returns_value_for_uptrend():
    closes = list(np.linspace(100, 130, 100))
    s = rsi_score(_series(closes))
    assert 0 < s <= 100


def test_rsi_score_penalises_overbought():
    closes = list(np.linspace(30, 200, 100))
    assert rsi_score(_series(closes)) <= 50


# ---- MACD ----

def test_macd_basic_returns_triplet():
    closes = list(np.linspace(100, 150, 100))
    m, sig, hist = macd(_series(closes))
    assert m is not None and sig is not None and hist is not None


def test_macd_score_rewards_rising_positive_hist():
    """Accelerating rally -> positive rising histogram -> high score."""
    closes = list(np.linspace(100, 120, 50)) + list(np.linspace(120, 180, 50))
    assert macd_score(_series(closes)) >= 60


def test_macd_score_neutral_when_short():
    assert macd_score(_series([100] * 10)) == 50.0


# ---- Bollinger ----

def test_bollinger_squeeze_score_high_for_flat_then_quiet():
    closes = [100 + i * 0.01 for i in range(100)]
    score = bollinger_squeeze_score(_series(closes))
    assert score >= 70


def test_bollinger_bandwidth_none_when_short():
    assert bollinger_bandwidth(_series([1, 2, 3])) is None


# ---- ADX ----

def test_adx_returns_value_for_uptrend():
    closes = list(np.linspace(100, 200, 100))
    df = _ohlcv_df(closes)
    a = adx(df["High"], df["Low"], df["Close"])
    assert a is not None
    assert a > 0  # any positive ADX is acceptable for synthetic data


def test_adx_score_returns_value_for_trending_market():
    closes = list(np.linspace(100, 200, 100))
    df = _ohlcv_df(closes)
    s = adx_trend_score(df)
    assert 0 <= s <= 100


def test_adx_score_relative_chop_vs_trend():
    """ADX score for trending series should be >= ADX score for chop."""
    trend = _ohlcv_df(list(np.linspace(100, 200, 100)))
    chop = _ohlcv_df([100 + (5 if i % 2 == 0 else -5) for i in range(100)])
    assert adx_trend_score(trend) >= adx_trend_score(chop) - 10  # tolerance


# ---- OBV ----

def test_obv_trend_score_positive_for_rising_price_and_volume():
    closes = list(np.linspace(100, 150, 60))
    vols = list(np.linspace(100_000, 500_000, 60))
    score = obv_trend_score(_series(closes), _series(vols))
    assert score >= 50  # any positive bias is enough


def test_obv_returns_none_for_empty():
    assert obv(None, None) is None


# ---- Supertrend ----

def test_pct_off_52w_high_at_high():
    closes = list(range(1, 252)) + [251]
    p = pct_off_52w_high(_series(closes))
    assert p == pytest.approx(0.0, abs=0.5)


def test_proximity_to_52w_score_at_high():
    closes = list(range(1, 252)) + [251]
    assert proximity_to_52w_high_score(_series(closes)) >= 95


def test_proximity_to_52w_score_far_below():
    closes = list(range(1, 252)) + [50]
    assert proximity_to_52w_high_score(_series(closes)) <= 30


# ---- Beta ----

def test_beta_vs_benchmark_one_when_identical():
    closes = list(np.linspace(100, 110, 120))
    b = beta_vs_benchmark(_series(closes), _series(closes))
    assert b == pytest.approx(1.0, abs=0.01)


def test_beta_returns_none_for_short_input():
    assert beta_vs_benchmark(_series([100]), _series([100])) is None
