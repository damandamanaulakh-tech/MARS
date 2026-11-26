"""XR-060 — the danger layer counted only the tripwires that happened to
return a Python bool.

`evaluate_stock` decided a tripwire had fired with `result is True`. Eight of
the ten price tripwires return `numpy.bool`, which is never `True` by
identity, so they fired and were thrown away. Under NumPy 2 that type is
*named* "bool" and prints as "True", which is why it survived every reading.

These tests fail on the code before the repair: the shock below fires four
tripwires by value and `evaluate_stock` returns an empty list.
"""
from __future__ import annotations

import ast
import pathlib

import numpy as np
import pandas as pd
import pytest

from trading import danger as D

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _frame(crash=True, bars=260):
    """Calm uptrend, then one hard break on heavy volume."""
    idx = pd.bdate_range("2025-01-01", periods=bars)
    close = np.linspace(100.0, 140.0, bars)
    high, low = close * 1.01, close * 0.99
    vol = np.full(bars, 1_000_000.0)
    if crash:
        close[-1] = close[-2] * 0.88
        low[-1], high[-1] = close[-1] * 0.995, close[-2] * 0.995
        vol[-1] = 9_000_000.0
    return pd.DataFrame({"Open": close, "High": high, "Low": low,
                         "Close": close, "Volume": vol}, index=idx)


# ------------------------------------------------- the fault, reproduced

def test_a_shock_that_fires_tripwires_is_actually_counted():
    df = _frame()
    x = D._Ctx(df)
    by_value = [tid for tid, fn in D._FNS.items() if fn(x) is not None and bool(fn(x))]
    assert by_value, "fixture must fire at least one tripwire by value"
    assert set(D.evaluate_stock(df)) == set(by_value)


def test_the_numpy_verdicts_are_the_majority_so_this_was_not_a_corner_case():
    x = D._Ctx(_frame())
    kinds = {tid: type(fn(x)) for tid, fn in D._FNS.items()}
    numpy_bools = [t for t, k in kinds.items() if k is not bool]
    assert len(numpy_bools) >= 6, kinds          # 8 of 10 at the time of the repair
    # and the trap that hid it: NumPy's bool is *called* bool
    assert np.bool_.__name__ == "bool" and np.bool_(True) is not True


# ------------------------------------------------------ the reading rule

@pytest.mark.parametrize("value,expected", [
    (True, True), (False, False), (None, None),
    (np.bool_(True), True), (np.bool_(False), False),
    (np.True_, True), (np.False_, False),
])
def test_a_verdict_is_read_by_value_for_both_bool_kinds(value, expected):
    assert D._verdict(value) is expected


@pytest.mark.parametrize("junk", [
    1, 0, 1.0, "True", "", [], [True], np.array([True, False]),
    np.float64(1.0), object(),
])
def test_anything_that_is_not_a_boolean_is_unknown_never_a_fire(junk):
    """A stop must never tighten on a value nobody meant as a verdict."""
    assert D._verdict(junk) is None


def test_a_tripwire_that_abstains_never_becomes_a_fire():
    short = _frame(bars=30)                      # too few bars for most rules
    fired = D.evaluate_stock(short)
    x = D._Ctx(short)
    for tid in fired:
        assert bool(D._FNS[tid](x)) is True, tid


def test_a_quiet_stock_fires_nothing():
    assert D.evaluate_stock(_frame(crash=False)) == []


# -------------------------------------------------------- what the journal says

def test_a_numpy_fire_is_recorded_as_passed_not_unavailable(monkeypatch):
    seen = {}
    monkeypatch.setattr(D.de, "record",
                        lambda key, *a, **k: seen.__setitem__(key, k.get("state")))
    fired = D.evaluate_stock(_frame())
    assert fired
    for tid in fired:
        assert seen["danger:" + tid] == "passed", (tid, seen["danger:" + tid])
    quiet = set(D._FNS) - set(fired)
    assert all(seen["danger:" + t] in ("failed", "unavailable") for t in quiet)


# ------------------------------------------------------------- the panel pair

def test_the_two_panel_tripwires_still_read_the_same():
    """0094 / 0095 already wrapped in bool(); the repair must not move them."""
    import inspect
    for tid, fn in D._EXT_FNS.items():
        assert "return bool(" in inspect.getsource(fn), tid


# ---------------------------------------------------------------- source pin

def test_no_trigger_verdict_is_compared_by_identity_any_more():
    """Comment- and docstring-proof: walk the syntax tree, not the text."""
    tree = ast.parse((ROOT / "trading" / "danger.py").read_text())
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        if "result" not in names:
            continue
        for op, cmp in zip(node.ops, node.comparators):
            if isinstance(op, (ast.Is, ast.IsNot)) and isinstance(cmp, ast.Constant) \
                    and cmp.value in (True, False):
                bad.append(ast.unparse(node))
    assert not bad, bad
