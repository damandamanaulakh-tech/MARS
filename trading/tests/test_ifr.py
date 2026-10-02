"""Tests for IFR (Internal Fracture & Repair) overlay.

Validates the DAMAGE_CLUSTER_5IN10 logic matches the 28-year backtest
specification: tail_down3_5d > 90pctile, sustained 5+ days in last 10.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from trading import ifr as _ifr
from trading.ifr import (
    ACTIVE_DAYS_AFTER_FIRE,
    CLUSTER_MIN_FIRES,
    CLUSTER_WINDOW,
    check_damage_cluster,
    compute_market_tail_down3_5d,
)


@pytest.fixture(autouse=True)
def _synthetic_names_are_the_reference_panel(monkeypatch):
    """Audit F-5 (2026-10-02): the live reading is taken over the frozen
    reference panel only. These fixtures use made-up names (T0, T1, ...), so
    they stand in for that panel here; the frozen THRESHOLDS stay the real
    measured ones."""
    assert _ifr.FROZEN is not None, "frozen thresholds file must load"
    monkeypatch.setattr(_ifr, "FROZEN", {
        **_ifr.FROZEN, "tickers": frozenset(f"T{i}" for i in range(200))})
    # the 15-name fixtures below are smaller than the live 30-name minimum;
    # test_a_thin_reference_panel_never_throttles pins that minimum itself
    monkeypatch.setattr(_ifr, "MIN_REFERENCE_NAMES", 5)


def _bars(n: int, slope: float = 0.0, jolts=None) -> pd.DataFrame:
    """Synthetic OHLCV. `jolts` is a list of (index, day_return)."""
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    closes = [100.0]
    rng = np.random.default_rng(0)
    for i in range(1, n):
        base = closes[-1] * (1 + slope + rng.normal(0, 0.005))
        if jolts:
            for idx, r in jolts:
                if i == idx:
                    base = closes[-1] * (1 + r)
        closes.append(base)
    return pd.DataFrame({"Close": closes}, index=dates)


# -------- compute_market_tail_down3_5d --------

def test_market_tail_zero_when_no_drops():
    hist = {f"T{i}": _bars(60, slope=0.001) for i in range(10)}
    series = compute_market_tail_down3_5d(hist)
    assert (series.dropna() == 0).all()


def test_market_tail_picks_up_drops():
    """Every stock takes a single -5% jolt on day 30. The 4 days
    immediately after must show ratio ~1.0 (every stock had a tail in last 5d)."""
    hist = {f"T{i}": _bars(60, slope=0.001, jolts=[(30, -0.05)]) for i in range(10)}
    series = compute_market_tail_down3_5d(hist)
    # Days 31-34 fall fully inside the 5-day lookback window after the drop
    assert series.iloc[31:35].mean() >= 0.95


# -------- check_damage_cluster --------

def test_no_history_returns_normal():
    snap = check_damage_cluster({}, now=datetime(2024, 6, 1))
    assert snap.sizing_multiplier == 1.0
    assert snap.fired_today is False
    assert snap.active is False


def test_cluster_does_not_fire_in_calm():
    """A calm uptrend should never fire the damage cluster."""
    hist = {f"T{i}": _bars(120, slope=0.001) for i in range(15)}
    snap = check_damage_cluster(hist, now=datetime(2024, 6, 1))
    assert snap.fired_today is False
    assert snap.sizing_multiplier == 1.0


def test_cluster_fires_during_repeated_damage():
    """Inject -3.5% drops across the universe on 6 of last 10 days → fires."""
    n = 120
    # Pick 6 indices in the last 10 bars to inject damage
    last_10 = list(range(n - 10, n))
    damage_days = last_10[:6]
    hist = {}
    for s in range(15):
        bars = _bars(n, slope=0.0005)
        for d in damage_days:
            # Drop close by 3.5% from previous close
            bars.iloc[d, 0] = bars.iloc[d - 1, 0] * 0.965
        hist[f"T{s}"] = bars
    snap = check_damage_cluster(hist, now=datetime(2024, 6, 1))
    assert snap.fires_in_window >= CLUSTER_MIN_FIRES
    assert snap.fired_today is True
    assert snap.sizing_multiplier == 0.50
    assert snap.active is True


def test_active_window_continues_after_fire():
    """If cluster fired 10 days ago (within window) but not today,
    sizing should still be throttled."""
    hist = {f"T{i}": _bars(120, slope=0.001) for i in range(15)}
    ten_days_ago = (datetime(2024, 6, 1) - timedelta(days=10)).date().isoformat()
    snap = check_damage_cluster(
        hist, now=datetime(2024, 6, 1),
        last_fire_date=ten_days_ago,
    )
    # Calm market today → no fire today, but persisted last_fire makes active
    assert snap.fired_today is False
    assert snap.active is True
    assert snap.sizing_multiplier == 0.50


def test_active_window_expires():
    """After ACTIVE_DAYS_AFTER_FIRE has passed, sizing returns to normal."""
    hist = {f"T{i}": _bars(120, slope=0.001) for i in range(15)}
    long_ago = (datetime(2024, 6, 1) - timedelta(days=ACTIVE_DAYS_AFTER_FIRE + 5)).date().isoformat()
    snap = check_damage_cluster(
        hist, now=datetime(2024, 6, 1),
        last_fire_date=long_ago,
    )
    assert snap.fired_today is False
    assert snap.active is False
    assert snap.sizing_multiplier == 1.0


def test_snapshot_dict_shape():
    hist = {f"T{i}": _bars(120) for i in range(15)}
    snap = check_damage_cluster(hist, now=datetime(2024, 6, 1))
    d = snap.as_dict()
    expected = {
        "fired_today", "active", "sizing_multiplier",
        "tail_down3_5d_today", "threshold_value",
        "fires_in_window", "last_fire_date", "reason",
    }
    assert expected.issubset(d.keys())


# -------- Q2: TAIL_DAMAGE_RATIO --------

def test_compute_tail_damage_ratio_basic():
    from trading.ifr import compute_tail_damage_ratio
    import pandas as pd
    down = pd.Series([0.1, 0.5, 0.9], index=pd.date_range("2024-01-01", periods=3))
    up   = pd.Series([0.9, 0.5, 0.1], index=pd.date_range("2024-01-01", periods=3))
    r = compute_tail_damage_ratio(down, up)
    # day 1: damage low; day 2: balanced; day 3: damage dominates
    assert r.iloc[0] < 0.2
    assert 0.4 < r.iloc[1] < 0.6
    assert r.iloc[2] > 0.8


def test_ratio_fires_on_damage_dominated_market():
    """Inject down-tails in the last 5 days (visible to today's 5d window).
    Sprinkle a few historical drops too so percentile threshold isn't degenerate.
    The damage-vs-up ratio for today should be high → ratio signal fires."""
    n = 200
    hist = {}
    for s in range(15):
        bars = _bars(n, slope=0.0005)
        # Sparse historical noise for varied percentile distribution
        for d in [40, 70, 90, 130]:
            bars.iloc[d, 0] = bars.iloc[d - 1, 0] * 0.97
        # Dense down-tails in the most recent 5-day window — visible to today
        for d in range(n - 5, n):
            bars.iloc[d, 0] = bars.iloc[d - 1, 0] * 0.96
        hist[f"T{s}"] = bars
    snap = check_damage_cluster(hist, now=datetime(2024, 6, 1))
    assert snap.tail_down3_5d_today > 0.5    # damage clearly present today
    assert snap.ratio_today > 0.7            # damage dominates up-thrust
    assert snap.active is True
    assert snap.sizing_multiplier == 0.50


def test_ratio_dormant_in_balanced_market():
    """Calm uptrend: neither signal should fire."""
    hist = {f"T{i}": _bars(150, slope=0.001) for i in range(15)}
    snap = check_damage_cluster(hist, now=datetime(2024, 6, 1))
    assert snap.fired_today is False
    assert snap.ratio_fired_today is False
    assert snap.sizing_multiplier == 1.0


def test_snapshot_includes_ratio_fields():
    hist = {f"T{i}": _bars(150) for i in range(15)}
    snap = check_damage_cluster(hist, now=datetime(2024, 6, 1))
    d = snap.as_dict()
    for k in ("ratio_today","ratio_threshold","ratio_fired_today",
              "ratio_last_fire_date","ratio_active","which_fired"):
        assert k in d


def test_ratio_active_window_persists():
    """Ratio fired 10d ago + dormant cluster → still throttled via ratio."""
    hist = {f"T{i}": _bars(150, slope=0.001) for i in range(15)}
    ten_days_ago = (datetime(2024, 6, 1) - timedelta(days=10)).date().isoformat()
    snap = check_damage_cluster(
        hist, now=datetime(2024, 6, 1),
        last_fire_date=None,
        ratio_last_fire_date=ten_days_ago,
    )
    assert snap.ratio_active is True
    assert snap.active is True
    assert snap.sizing_multiplier == 0.50
    assert "ratio" in snap.which_fired
