"""Tests for the 5 critical risk controls in risk_v2.py."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from trading.risk_v2 import (
    ConsecutiveLossTracker, DrawdownGovernor, LiquidityRules,
    correlation, correlation_gate_blocks, daily_returns,
    passes_liquidity,
)


def _series(values):
    return pd.Series(values, index=pd.date_range("2024-01-01", periods=len(values), freq="D"))


# -------- 1. Drawdown governor --------

def test_drawdown_normal_when_at_peak():
    gov = DrawdownGovernor()
    gov.update(1_000_000)
    assert gov.active_level == "normal"
    assert gov.sizing_multiplier == 1.0
    assert gov.halt_new_entries is False


def test_drawdown_watchful_at_minus_5pct():
    gov = DrawdownGovernor()
    gov.update(1_000_000)
    gov.update(950_000)
    assert gov.active_level == "watchful"
    assert gov.sizing_multiplier == 0.75
    assert gov.halt_new_entries is False


def test_drawdown_stop_adding_at_minus_8pct():
    gov = DrawdownGovernor()
    gov.update(1_000_000)
    gov.update(920_000)
    assert gov.active_level == "stop_adding"
    assert gov.halt_new_entries is True
    assert gov.forced_cut == 0.0


def test_drawdown_cut_half_at_minus_10pct():
    gov = DrawdownGovernor()
    gov.update(1_000_000)
    gov.update(900_000)
    assert gov.active_level == "cut_half"
    assert gov.forced_cut == 0.50


def test_drawdown_mostly_cash_at_minus_15pct():
    gov = DrawdownGovernor()
    gov.update(1_000_000)
    gov.update(850_000)
    assert gov.active_level == "mostly_cash"
    assert gov.forced_cut == 0.80


def test_drawdown_full_halt_at_minus_20pct():
    gov = DrawdownGovernor()
    gov.update(1_000_000)
    gov.update(800_000)
    assert gov.active_level == "full_halt"
    assert gov.forced_cut == 1.0


def test_drawdown_peak_updates_on_new_high():
    gov = DrawdownGovernor()
    gov.update(1_000_000)
    gov.update(1_100_000)
    assert gov.peak_equity == 1_100_000
    gov.update(1_050_000)
    # 1,050,000 vs peak 1,100,000 = -4.5% -> normal
    assert gov.active_level == "normal"


# -------- 2. Correlation gate --------

def test_correlation_high_for_identical_series():
    s = _series(list(range(1, 80)))
    r = daily_returns(s)
    assert correlation(r, r) == pytest.approx(1.0)


def test_correlation_low_for_unrelated_series():
    rng = np.random.default_rng(42)
    s1 = _series(rng.normal(0, 1, 100).cumsum() + 100)
    s2 = _series(rng.normal(0, 1, 100).cumsum() + 100)
    r1 = daily_returns(s1)
    r2 = daily_returns(s2)
    c = correlation(r1, r2)
    assert c is not None
    assert abs(c) < 0.5   # uncorrelated random walks


def test_correlation_gate_blocks_when_above_threshold():
    s = _series(list(np.linspace(100, 130, 80)))
    r = daily_returns(s)
    open_pos = {"EXISTING.NS": r}
    blocker = correlation_gate_blocks(r, open_pos, threshold=0.70)
    assert blocker is not None
    assert blocker[0] == "EXISTING.NS"
    assert blocker[1] > 0.70


def test_correlation_gate_passes_when_uncorrelated():
    rng = np.random.default_rng(7)
    candidate = pd.Series(rng.normal(0, 1, 80))
    existing = pd.Series(rng.normal(0, 1, 80))
    blocker = correlation_gate_blocks(candidate, {"E.NS": existing}, threshold=0.70)
    assert blocker is None


# -------- 4. Liquidity filters --------

def test_liquidity_fails_low_volume():
    closes = _series([100.0] * 30)
    vols = _series([50_000] * 30)   # Rs 0.5 Cr a day — thin in rupees
    ok, reason = passes_liquidity(closes, vols)
    assert ok is False
    assert "rupee_vol" in reason


def test_high_priced_liquid_stock_passes_on_rupee_turnover():
    """Owner order 2026-09-08 ("fix both faults"): THERMAX traded ~136k
    shares but Rs 48 Cr a day and was blocked by a 200k-share floor — a
    share count says nothing about liquidity. Rupee turnover is the rule."""
    closes = _series([3_622.0] * 30)
    vols = _series([136_000] * 30)
    ok, reason = passes_liquidity(closes, vols)
    assert ok is True, reason


def test_share_count_floor_never_returns():
    """The share-count field is deleted, not zeroed — a zeroed knob invites
    someone to 'turn it back on'."""
    import inspect
    from trading import risk_v2
    src = "\n".join(ln for ln in inspect.getsource(risk_v2).splitlines()
                    if not ln.lstrip().startswith("#"))
    assert "min_avg_daily_volume_shares" not in src
    assert not hasattr(LiquidityRules(), "min_avg_daily_volume_shares")


def test_liquidity_passes_good_volume():
    closes = _series([100.0] * 30)
    vols = _series([1_000_000] * 30)   # ₹10 Cr daily
    ok, reason = passes_liquidity(closes, vols)
    assert ok, reason


def test_liquidity_fails_low_rupee_volume():
    # 250k shares at ₹100 = ₹2.5 Cr (below 5 Cr floor)
    closes = _series([100.0] * 30)
    vols = _series([250_000] * 30)
    ok, reason = passes_liquidity(closes, vols)
    assert ok is False
    assert "rupee_vol" in reason


def test_liquidity_detects_circuit_stuck():
    closes = _series([100.0] * 30)
    vols = _series([1_000_000] * 30)
    ok, reason = passes_liquidity(
        closes, vols,
        last_open=105, last_high=105, last_low=105, last_close=105,
    )
    assert ok is False
    assert "circuit" in reason


# -------- 5. Consecutive loss halt --------

def test_consecutive_loss_halts_after_two_in_one_day():
    t = ConsecutiveLossTracker()
    today = date(2026, 1, 5)
    t.record_close(today, -5000)
    assert t.halted() is False
    t.record_close(today, -3000)
    assert t.halted() is True


def test_consecutive_loss_does_not_halt_on_wins():
    t = ConsecutiveLossTracker()
    today = date(2026, 1, 5)
    t.record_close(today, 10_000)
    t.record_close(today, 5_000)
    assert t.halted() is False


def test_consecutive_loss_resets_next_day():
    t = ConsecutiveLossTracker()
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    t.record_close(d1, -5000)
    t.record_close(d1, -3000)
    assert t.halted(today=d1) is True
    # New day, fresh count
    t.record_close(d2, -1000)
    assert t.halted(today=d2) is False
