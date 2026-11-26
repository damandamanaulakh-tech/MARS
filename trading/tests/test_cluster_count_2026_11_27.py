"""XR-060, the display half — the owner's cluster counters read their members
by identity too, so they under-reported.

`triggers._cluster_fired` counts how many members of an accumulation or
distribution family fired today. It tested `_FNS[t](x) is True`, and most
trigger functions answer with a numpy bool, so those members were dropped.
T12W_0032 (accumulation cluster) and T12W_0081 (distribution cluster) read
low, and they feed the owner's own gates, the selection-sequence ladder and
the /triggers screens.

Display only — the standing boundary test keeps trading.triggers out of the
money path. These tests fail on the code before the repair.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from trading import triggers as T

ACCUMULATION = [f"T12W_{i:04d}" for i in range(17, 32)]
DISTRIBUTION = ["T12W_0069", "T12W_0070", "T12W_0071", "T12W_0072", "T12W_0073",
                "T12W_0074", "T12W_0075", "T12W_0076", "T12W_0077", "T12W_0078",
                "T12W_0079", "T12W_0080", "T12W_0082", "T12W_0083", "T12W_0084"]


def _frame(seed=7, bars=300):
    rng = np.random.default_rng(seed)
    c = 100 * np.cumprod(1 + rng.normal(0, 0.02, bars))
    c[-30:] *= np.linspace(1, 0.80, 30)
    return pd.DataFrame(
        {"Open": c, "High": c * (1 + abs(rng.normal(0, 0.01, bars))),
         "Low": c * (1 - abs(rng.normal(0, 0.01, bars))), "Close": c,
         "Volume": abs(rng.normal(2e6, 8e5, bars))},
        index=pd.bdate_range("2025-01-01", periods=bars))


def _by_value(x, fam):
    return sum(1 for t in fam
               if t in T._FNS and x.n >= T._MIN_BARS.get(t, T._MIN_BARS_DEFAULT)
               and bool(T._FNS[t](x)))


def test_the_cluster_count_matches_what_the_members_really_say():
    x = T._Ctx(_frame())
    for fam in (ACCUMULATION, DISTRIBUTION):
        members = [t for t in fam if t in T._FNS]
        assert T._cluster_fired(x, members) == _by_value(x, members)


def test_the_members_really_do_answer_with_numpy_so_this_is_not_a_corner_case():
    x = T._Ctx(_frame())
    members = [t for t in DISTRIBUTION if t in T._FNS
               and x.n >= T._MIN_BARS.get(t, T._MIN_BARS_DEFAULT)]
    kinds = {type(T._FNS[t](x)) for t in members}
    assert any(k is not bool for k in kinds), kinds


def test_a_member_that_abstains_or_answers_oddly_is_not_counted(monkeypatch):
    x = T._Ctx(_frame())
    patched = dict(T._FNS)
    patched["T12W_0069"] = lambda ctx: None
    patched["T12W_0070"] = lambda ctx: np.array([True, True])
    patched["T12W_0071"] = lambda ctx: 1          # an int is not a verdict
    monkeypatch.setattr(T, "_FNS", patched)
    monkeypatch.setattr(T, "_MIN_BARS", dict(T._MIN_BARS))
    counted = T._cluster_fired(x, ["T12W_0069", "T12W_0070", "T12W_0071"])
    assert counted == 0


def test_a_member_below_its_own_minimum_history_is_skipped():
    short = T._Ctx(_frame(bars=40))
    members = [t for t in DISTRIBUTION if t in T._FNS]
    assert T._cluster_fired(short, members) == _by_value(short, members)
