"""Tests for the Indian Ocean engine — synthetic OHLCV only, no network."""

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from trading.config import Config
from trading.engines.ocean import (
    climax_volume_ratio,
    evaluate,
    survival_check,
    weekly_rsi,
)


def _make_history(closes, volumes=None, days=None):
    days = days or len(closes)
    idx = pd.date_range("2024-01-01", periods=days, freq="D")
    vols = volumes if volumes is not None else [100_000] * days
    return pd.DataFrame({
        "Open": closes, "High": [c * 1.01 for c in closes],
        "Low": [c * 0.99 for c in closes], "Close": closes, "Volume": vols,
    }, index=idx)


def test_climax_volume_ratio_spike_detected():
    vols = [100_000] * 19 + [500_000]
    s = pd.Series(vols, index=pd.date_range("2024-01-01", periods=20, freq="D"))
    ratio = climax_volume_ratio(s, lookback=5, baseline=20)
    assert ratio > 4.0


def test_climax_volume_ratio_none_when_short():
    s = pd.Series([100, 200, 300])
    assert climax_volume_ratio(s) is None


def test_weekly_rsi_oversold_falling_series():
    # Steadily falling closes -> weekly RSI should be deeply oversold
    closes = pd.Series(
        np.linspace(100, 30, 300),
        index=pd.date_range("2023-01-01", periods=300, freq="D"),
    )
    rsi = weekly_rsi(closes)
    assert rsi is not None
    assert rsi < 30


def test_weekly_rsi_none_when_short():
    s = pd.Series([100, 99, 98])
    assert weekly_rsi(s) is None


def test_evaluate_returns_none_when_history_short():
    cfg = Config()
    hist = _make_history([100] * 50)
    assert evaluate("X.NS", hist, cfg) is None


def test_evaluate_returns_none_when_price_not_deep_below_200dma():
    cfg = Config()
    closes = [100] * 250
    hist = _make_history(closes)
    assert evaluate("X.NS", hist, cfg) is None


def test_evaluate_passes_when_all_conditions_met():
    """Crash from 200 -> 100 + RSI dive + climax volume on last 5 days."""
    cfg = Config()
    # 250 days: long high plateau, sharp crash, then climax day
    closes = [200] * 200 + list(np.linspace(200, 100, 45)) + [90, 85, 80, 75, 70]
    vols = [100_000] * 245 + [400_000, 100_000, 100_000, 100_000, 100_000]
    hist = _make_history(closes, vols, days=250)

    with patch("trading.engines.ocean.survival_check", return_value=(True, ["mocked"])):
        cand = evaluate("X.NS", hist, cfg)
    assert cand is not None
    assert cand.pct_below_200dma <= -30
    assert cand.climax_volume_ratio >= 3.0
    assert cand.survival_passed is True
    assert not hasattr(cand, "tranche_plan")  # AUDIT E: the placeholder staged-entry plan (zero consumers) is gone


def test_evaluate_skipped_when_survival_fails():
    cfg = Config()
    closes = [200] * 200 + list(np.linspace(200, 100, 45)) + [90, 85, 80, 75, 70]
    vols = [100_000] * 245 + [400_000, 100_000, 100_000, 100_000, 100_000]
    hist = _make_history(closes, vols, days=250)
    with patch(
        "trading.engines.ocean.survival_check",
        return_value=(False, ["high debt"]),
    ):
        assert evaluate("X.NS", hist, cfg) is None


def test_survival_check_skipped_in_backtest_mode():
    from trading import data
    data.set_backtest_mode(True)
    try:
        passed, reasons = survival_check("RELIANCE.NS")
        assert passed is False
        assert "backtest-mode" in reasons[0]
    finally:
        data.set_backtest_mode(False)
