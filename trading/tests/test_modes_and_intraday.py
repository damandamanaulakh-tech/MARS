"""Tests for mode presets + intraday signals (VWAP, ORB, EMA cross).

All synthetic data, no network, < 100ms total.
"""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from trading.config import Config
from trading.modes import PRESETS, WEIGHTS, get_preset, get_weights
from trading.intraday import (
    ema_cross_score,
    intraday_breakout_quality,
    opening_range,
    orb_breakout_score,
    session_vwap,
    should_square_off,
    vwap_reclaim_score,
)


# ------------- modes -------------

def test_all_mode_weights_sum_to_one():
    for mode, weights in WEIGHTS.items():
        total = sum(weights.values())
        assert abs(total - 1.0) < 1e-6, f"{mode} weights sum {total}, not 1.0"


def test_all_modes_have_preset():
    for mode in ("intraday", "short", "swing", "positional"):
        p = get_preset(mode)
        assert p.name == mode
        assert p.stop_loss_pct < 0
        assert p.take_profit_pct > 0
        # R:R at least 1:1 (target / stop magnitude)
        assert p.take_profit_pct >= abs(p.stop_loss_pct)


def test_intraday_has_square_off():
    assert get_preset("intraday").square_off_time == "15:25"
    for mode in ("short", "swing", "positional"):
        assert get_preset(mode).square_off_time is None


def test_unknown_mode_falls_back_to_swing():
    assert get_preset("nonsense").name == "swing"
    assert get_weights("nonsense") == WEIGHTS["swing"]


def test_apply_mode_preset_changes_config():
    cfg = Config()
    cfg.mode = "intraday"
    cfg.apply_mode_preset()
    assert cfg.stop_loss_pct == PRESETS["intraday"].stop_loss_pct
    assert cfg.max_hold_days == 1
    # Switching back to swing reverts the values
    cfg.mode = "swing"
    cfg.apply_mode_preset()
    assert cfg.stop_loss_pct == PRESETS["swing"].stop_loss_pct
    assert cfg.max_hold_days == 60


# ------------- intraday helpers -------------

def _make_session_bars(opens, highs, lows, closes, vols, start="2026-05-29 09:15"):
    idx = pd.date_range(start=start, periods=len(opens), freq="5min")
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols},
        index=idx,
    )


def test_session_vwap_monotone_for_constant_price():
    bars = _make_session_bars(
        opens=[100]*10, highs=[100]*10, lows=[100]*10, closes=[100]*10, vols=[1000]*10,
    )
    vwap = session_vwap(bars)
    assert np.allclose(vwap, 100, atol=0.01)


def test_vwap_reclaim_score_above_vwap():
    bars = _make_session_bars(
        opens=[100,99,98,97,99,101],
        highs=[100,99,98,97,99,101],
        lows=[100,99,98,97,99,101],
        closes=[100,99,98,97,99,101],
        vols=[1000]*6,
    )
    s = vwap_reclaim_score(bars)
    assert s > 50  # last close above session VWAP


def test_orb_breakout_score_above_high():
    bars = _make_session_bars(
        opens=[100,100,100,100,100,100,100,105],
        highs=[101,101,101,101,101,101,101,106],
        lows= [99, 99, 99, 99, 99, 99, 99, 104],
        closes=[100,100,100,100,100,100,100,105],
        vols=[1000]*8,
    )
    # Opening range high in first ~30 min ≈ 101; last close 105 = strong breakout
    assert orb_breakout_score(bars, minutes=30) > 70


def test_orb_breakout_score_inside_range():
    bars = _make_session_bars(
        opens=[100,100,100,100,100,100,100,100],
        highs=[102,102,102,102,101,101,101,101],
        lows= [98, 98, 98, 98, 99, 99, 99, 99],
        closes=[100,100,100,100,100,100,100,100],
        vols=[1000]*8,
    )
    score = orb_breakout_score(bars, minutes=30)
    assert 40 <= score <= 60  # neutral


def test_ema_cross_score_fresh_bullish_cross():
    # Falling then rising — last bar should produce bullish cross
    closes = [100, 99, 98, 97, 96, 97, 99, 101, 103, 105,
              107, 109, 111, 113, 115, 117, 119, 121, 123, 125,
              127, 129, 131, 133, 135]
    s = ema_cross_score(pd.Series(closes), fast=5, slow=20)
    assert s > 70


def test_should_square_off_at_cutoff():
    today = datetime(2026, 5, 29, 15, 25, 0)
    assert should_square_off(today, "15:25")
    before = datetime(2026, 5, 29, 15, 24, 0)
    assert not should_square_off(before, "15:25")


def test_intraday_composite_returns_in_range():
    bars = _make_session_bars(
        opens=[100]*8, highs=[101]*8, lows=[99]*8,
        closes=[100,100,100,100,100,100,100,102],
        vols=[1000]*8,
    )
    s = intraday_breakout_quality(bars)
    assert 0 <= s <= 100


def test_opening_range_returns_none_for_empty():
    assert opening_range(None) is None
    empty = pd.DataFrame(columns=["High", "Low", "Close", "Volume"])
    assert opening_range(empty) is None
