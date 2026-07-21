"""Pins the exit rule against the two real live positions.

The rule was never wrong. It was never CALLED. These tests exist so a future
refactor can't silently break the rule, and so the bug (no caller) can't be
mistaken for a rule bug again.
"""
from dataclasses import dataclass
from datetime import datetime

from trading.exits import check_exit


@dataclass
class Pos:
    ticker: str; entry_price: float; peak_price: float
    entry_score: float; entry_date: datetime


@dataclass
class Cfg:
    stop_loss_pct: float = -0.05
    take_profit_pct: float = 0.10
    trailing_stop_pct: float = -0.08
    score_decay_exit: float = 45.0
    max_hold_days: int = 90


NOW = datetime(2026, 7, 16)


def test_cgcl_should_have_exited_take_profit():
    """LIVE: CGCL.NS entry 219.03, current 258.21 = +17.89% vs +10% target.
    This position is open on the live app. It should not be."""
    p = Pos("CGCL.NS", 219.03, 258.21, 84.5, datetime(2026, 6, 17))
    should, why = check_exit(p, 258.21, 84.5, Cfg(), NOW)
    assert should is True
    assert why == "TAKE_PROFIT"


def test_cholafin_correctly_still_open():
    """LIVE: CHOLAFIN.NS entry 1688.14, current 1803.90 = +6.86%, peak 1853.40.
    Below +10% target; trail is only -2.67% from peak vs -8% rule. HOLD is right."""
    p = Pos("CHOLAFIN.NS", 1688.14, 1853.40, 84.5, datetime(2026, 6, 17))
    should, why = check_exit(p, 1803.90, 84.5, Cfg(), NOW)
    assert should is False, f"should hold, got exit={why}"


def test_stop_loss_still_fires():
    p = Pos("X.NS", 100.0, 100.0, 80.0, datetime(2026, 6, 17))
    should, why = check_exit(p, 94.0, 80.0, Cfg(), NOW)
    assert should is True and why == "STOP_LOSS"


def test_trailing_fires_when_below_take_profit_but_far_off_peak():
    """Priority order matters: at +18% TAKE_PROFIT fires first, so the trailing
    case needs ret < +10% AND a big drop from peak. entry 100, peak 130,
    current 105 = +5% (under target) but -19% off peak -> TRAILING_STOP."""
    p = Pos("Y.NS", 100.0, 130.0, 80.0, datetime(2026, 6, 17))
    should, why = check_exit(p, 105.0, 80.0, Cfg(), NOW)
    assert should is True and why == "TRAILING_STOP"


if __name__ == "__main__":
    import pytest; raise SystemExit(pytest.main([__file__, "-q"]))
