"""XR-027: a stock whose recent volume is missing no longer passes the gate.

`rupee_vol = (close * volume).dropna().mean()` over an all-blank window gives
NaN, `NaN < 5.0` is False, so the refusal was skipped and control fell through
to `return True, ""`. The Rs 5 crore gate — the one thing standing between the
book and a stock nobody trades — passed it.

The partially-blank case is worse and is the one that reaches the automatic
09:20/14:30 scan: pandas .mean() SKIPS NaN, so blanks raise the average. A
measured name whose true 5-day turnover was Rs 4.08 crore (correctly refused)
read Rs 20.00 crore and was accepted.

Owner's choice 2026-11-27, from an options card: all 5 of the last 5 days must
carry a real volume. (3 of 5 was offered and declined.)
"""
import math

import numpy as np
import pandas as pd
import pytest

from trading.risk_v2 import LiquidityRules, passes_liquidity


def series(n=30, price=1000.0, vol=100_000.0):
    return (pd.Series(np.full(n, price)), pd.Series(np.full(n, vol)))


def test_a_genuinely_liquid_name_still_passes():
    close, volume = series(vol=100_000.0)          # Rs 10 Cr/day
    assert passes_liquidity(close, volume) == (True, "")


def test_a_genuinely_thin_name_is_still_refused_on_turnover():
    close, volume = series(vol=100.0)              # Rs 0.01 Cr/day
    ok, why = passes_liquidity(close, volume)
    assert ok is False and "rupee_vol" in why


@pytest.mark.parametrize('shape', ['all_blank', 'last_five_blank', 'infinite',
                                   'zero_and_blank', 'negative'])
def test_a_window_without_five_real_days_is_refused_not_passed(shape):
    close, volume = series(vol=100_000.0)
    if shape == 'all_blank':
        volume[:] = np.nan
    elif shape == 'last_five_blank':
        volume.iloc[-5:] = np.nan
    elif shape == 'infinite':
        volume.iloc[-5:] = np.inf
    elif shape == 'zero_and_blank':
        volume.iloc[-5:-2] = np.nan
        volume.iloc[-2:] = 0.0
    else:
        volume.iloc[-5:] = -1.0
    ok, why = passes_liquidity(close, volume)
    assert ok is False, shape
    assert 'volume_missing' in why, (shape, why)


def test_the_partly_blank_case_can_no_longer_read_as_liquid():
    """The measured live shape: 4 thin days blanked, 1 big day left.

    True average is Rs 4.08 crore and must be refused. With the blanks
    skipped it averaged Rs 20.00 crore and was accepted.
    """
    close = pd.Series(np.full(30, 1000.0))
    volume = pd.Series(np.full(30, 10_000.0))
    volume.iloc[-5:-1] = 1_000.0                   # four genuinely thin days
    volume.iloc[-1] = 200_000.0                    # one big day
    truth = float((close.iloc[-5:] * volume.iloc[-5:]).mean()) / 1e7
    assert 3.9 < truth < 4.2, truth                # Rs ~4.08 Cr — must refuse
    assert passes_liquidity(close, volume)[0] is False

    blanked = volume.copy()
    blanked.iloc[-5:-1] = np.nan                   # the same name, data missing
    ok, why = passes_liquidity(close, blanked)
    assert ok is False, "a blank window must never read as liquid"
    assert 'volume_missing' in why, why


def test_the_requirement_is_a_rule_not_a_hardcoded_five():
    """The owner can be shown the knob, and 3-of-5 is reachable if they move."""
    close, volume = series(vol=100_000.0)
    volume.iloc[-5:-3] = np.nan                    # 3 of 5 real
    assert passes_liquidity(close, volume)[0] is False
    looser = LiquidityRules(min_real_volume_days=3)
    assert passes_liquidity(close, volume, rules=looser) == (True, "")


def test_the_two_liquidity_definitions_agree_in_kind():
    """XR-027's bar must not contradict the weekly ranker's own rule."""
    from trading import universe_select as us
    assert us.MIN_BARS >= 1
    assert LiquidityRules().min_real_volume_days >= 1
    # the ranker refuses to judge a name with too few real pairs; so do we
    assert us.median_turnover([[None] * 6] * 30) is None


def test_insufficient_close_history_is_still_its_own_reason():
    close = pd.Series(np.full(10, 1000.0))
    volume = pd.Series(np.full(10, 100_000.0))
    assert passes_liquidity(close, volume) == (False, "insufficient_history")
