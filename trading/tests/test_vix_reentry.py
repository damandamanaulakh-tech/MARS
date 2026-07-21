"""v4 correction playbook — VIX-crush re-entry signal."""

import pandas as pd

from trading.regime import vix_crush_ok


def _s(vals):
    return pd.Series(vals, index=pd.date_range("2026-01-01", periods=len(vals)))


def test_crush_fires_after_spike_fades():
    # spike to 30, faded to 20 -> 20 <= 0.75*30 = 22.5 -> True
    assert vix_crush_ok(_s([14] * 100 + [30] + [25, 23, 21, 20])) is True


def test_no_fire_while_fear_still_high():
    # spike to 30, still at 26 -> 26 > 22.5 -> False
    assert vix_crush_ok(_s([14] * 100 + [30, 29, 27, 26])) is False


def test_no_fire_in_calm_market():
    # meandering 12-14, no spike: 12 > 0.75*14 = 10.5 -> False (never
    # triggers in mild conditions — cash stays the default stance)
    assert vix_crush_ok(_s([13, 14, 12.5, 13.5] * 30 + [12])) is False


def test_insufficient_data_is_false():
    assert vix_crush_ok(_s([15] * 5)) is False
