"""Batch C: candle BUY-CONFIRMATION gate tests (no network)."""

import numpy as np
import pandas as pd

from trading.candles import (breakout_candle, bullish_engulfing,
                             candle_confirmation, urr_confirmation)
from trading.config import Config


def _flat(n=60, px=100.0, vol=1000.0):
    idx = pd.bdate_range("2026-01-01", periods=n)
    return pd.DataFrame({"Open": px, "High": px * 1.005, "Low": px * 0.995,
                         "Close": px, "Volume": vol}, index=idx)


def test_breakout_candle_fires_on_volume_breakout():
    df = _flat()
    # last bar: breaks the 20d high with 2x volume, strong close
    df.iloc[-1] = [101.0, 106.0, 100.5, 105.5, 2500.0]
    assert breakout_candle(df)


def test_breakout_rejected_without_volume():
    df = _flat()
    df.iloc[-1] = [101.0, 106.0, 100.5, 105.5, 900.0]   # no volume surge
    assert not breakout_candle(df)


def test_bullish_engulfing():
    df = _flat()
    df.iloc[-2] = [102.0, 102.5, 99.0, 100.0, 1000.0]   # red
    df.iloc[-1] = [99.5, 103.5, 99.0, 103.0, 1500.0]    # green engulfing
    assert bullish_engulfing(df)
    assert not bullish_engulfing(_flat())


def test_urr_upthrust_retest_hold():
    df = _flat(n=80)
    # window: upthrust above the 20d level (100.5 high), retest holds, close above
    df.iloc[-8] = [100.0, 104.0, 100.0, 103.5, 1600.0]   # upthrust
    df.iloc[-4] = [102.0, 102.5, 100.6, 101.5, 1200.0]   # retest holds level
    df.iloc[-1] = [102.0, 103.0, 101.5, 102.5, 1300.0]   # still above
    assert urr_confirmation(df)
    assert not urr_confirmation(_flat(n=80))              # nothing happened


def test_gate_wiring_and_disable():
    cfg = Config()
    assert cfg.candle_confirm_enabled is True             # default ON
    ok, why = candle_confirmation(_flat(), cfg)
    assert not ok and why == "no_pattern"                 # flat market waits
    cfg.candle_confirm_enabled = False
    assert candle_confirmation(_flat(), cfg) == (True, "disabled")
    cfg.candle_confirm_enabled = True
    ok, why = candle_confirmation(_flat(n=10), cfg)
    assert not ok and why == "insufficient_bars"


def test_new_patterns_from_reference_pics():
    from trading.candles import (hammer, morning_star, piercing_line,
                                 tweezer_bottom, three_white_soldiers)
    df = _flat(n=20)
    # hammer after a downtrend: long lower shadow
    for off, i in enumerate(range(-7, -1)):
        px = 104.0 - off          # falling closes: 104 -> 99
        df.iloc[i] = [px + 0.5, px + 1.0, px - 0.5, px, 1000.0]
    df.iloc[-1] = [96.0, 96.3, 92.0, 96.2, 1500.0]
    assert hammer(df)
    # morning star: big red, small pause, green above midpoint
    df2 = _flat(n=10)
    df2.iloc[-3] = [104.0, 104.5, 99.5, 100.0, 1000.0]
    df2.iloc[-2] = [99.8, 100.4, 99.2, 100.0, 800.0]
    df2.iloc[-1] = [100.2, 103.5, 100.0, 103.0, 1400.0]
    assert morning_star(df2)
    # piercing line
    df3 = _flat(n=10)
    df3.iloc[-2] = [104.0, 104.2, 99.8, 100.0, 1000.0]
    df3.iloc[-1] = [99.9, 103.0, 99.5, 102.5, 1200.0]
    assert piercing_line(df3)
    # tweezer bottom
    df4 = _flat(n=10)
    df4.iloc[-2] = [103.0, 103.2, 99.0, 100.0, 1000.0]
    df4.iloc[-1] = [100.2, 102.5, 99.0, 102.0, 1100.0]
    assert tweezer_bottom(df4)
    # three white soldiers
    df5 = _flat(n=10)
    df5.iloc[-3] = [100.0, 101.6, 99.9, 101.5, 1000.0]
    df5.iloc[-2] = [101.4, 103.1, 101.3, 103.0, 1100.0]
    df5.iloc[-1] = [102.9, 104.6, 102.8, 104.5, 1200.0]
    assert three_white_soldiers(df5)
    # flat market still confirms nothing
    from trading.config import Config
    from trading.candles import candle_confirmation
    ok, why = candle_confirmation(_flat(n=60), Config())
    assert not ok and why == "no_pattern"


def test_min_position_value_gate():
    from trading.risk import RiskState, can_open
    from trading.config import Config
    cfg = Config()
    cfg.sector_map = {}
    risk = RiskState(cfg=cfg, starting_capital=cfg.starting_capital)
    # qty*price below 25k -> blocked (2.5% sizing on tiny price is fine,
    # so force with a huge price making qty=1 and value < min)
    cfg.min_position_value = 1e9   # absurd floor: everything blocked
    ok, reason, qty = can_open(risk, cash=cfg.starting_capital,
                               current_equity=cfg.starting_capital,
                               open_positions={}, ticker="X.NS",
                               fill_price=100.0, cfg=cfg)
    assert not ok and reason.startswith("below_min_value")
    cfg.min_position_value = 25000.0
    ok, reason, qty = can_open(risk, cash=cfg.starting_capital,
                               current_equity=cfg.starting_capital,
                               open_positions={}, ticker="X.NS",
                               fill_price=100.0, cfg=cfg)
    assert ok and qty * 100.0 >= 25000.0
