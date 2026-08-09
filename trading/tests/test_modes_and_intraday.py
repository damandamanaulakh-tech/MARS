"""Tests for mode presets. (The intraday-signals half was deleted with
trading/intraday.py — an orphan module nothing imported; AUDIT E, 2026-08-09.)

All synthetic data, no network, < 100ms total.
"""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from trading.config import Config
from trading.modes import PRESETS, WEIGHTS, get_preset, get_weights


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



