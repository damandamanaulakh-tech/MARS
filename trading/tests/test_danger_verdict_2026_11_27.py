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
from trading import triggers as TR

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


@pytest.mark.parametrize("bars", [30, 60, 69])
def test_thin_history_fires_nothing_however_sharp_the_fall(bars):
    """Every danger tripwire needs 70 bars (triggers._MIN_BARS) and the engine
    admits a position at 60. The old version of this test compared the layer
    with the same functions it calls, which is true by construction; it ran at
    30 bars where the layer really did return three fires and an EXIT_HALF."""
    assert D.evaluate_stock(_frame(bars=bars)) == []


def test_the_minimum_history_is_each_tripwires_own_and_fires_right_above_it():
    need = max(TR._MIN_BARS.get(t, TR._MIN_BARS_DEFAULT) for t in D._FNS)
    assert D.evaluate_stock(_frame(bars=need - 1)) == []
    assert D.evaluate_stock(_frame(bars=need)), "must fire once it has the bars"


def test_a_tripwire_held_back_for_thin_history_is_recorded_as_unavailable(monkeypatch):
    seen = {}
    monkeypatch.setattr(D.de, "record",
                        lambda key, *a, **k: seen.__setitem__(key, (k.get("state"), k.get("reason"))))
    D.evaluate_stock(_frame(bars=30))
    for tid in D._FNS:
        state, reason = seen["danger:" + tid]
        assert state == "unavailable" and "bars" in str(reason), (tid, state, reason)


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

def test_the_two_panel_tripwires_still_return_a_real_bool():
    """0094 / 0095 already wrapped in bool(); the repair must not move them.

    Asserted on the VALUE they return, not on their source text — a comment
    saying "return bool(" would have satisfied the old check.
    """
    import types
    df = _frame()
    x = D._Ctx(df)
    ext = types.SimpleNamespace(peer_break_20d_low=9, peers=10, index_close=None,
                                target_frame=None, context={})
    for tid, fn in D._EXT_FNS.items():
        r = fn(x, ext, {}, {})
        assert r is None or type(r) is bool, (tid, type(r))


# ---------------------------------------------------------------- source pin

def test_every_tripwire_answer_is_read_through_the_verdict_reader():
    """The real invariant, pinned name-independently.

    Not "no `is True` anywhere" — comparing an ALREADY COERCED verdict by
    identity is correct and the repair does it. What must never happen is a
    RAW tripwire answer reaching a comparison. So: every call to a tripwire
    function inside evaluate_stock must be the argument of `_verdict(...)`.

    The first version of this pin looked for the variable name `result`, which
    the repair had already renamed to `verdict` — so it guarded nothing. Found
    in the adversarial review of this very change.
    """
    tree = ast.parse((ROOT / "trading" / "danger.py").read_text())
    fn_def = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "evaluate_stock")

    wrapped = set()
    for node in ast.walk(fn_def):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "_verdict":
            wrapped.update(id(a) for a in node.args)

    raw = [ast.unparse(node) for node in ast.walk(fn_def)
           if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
           and node.func.id == "fn" and id(node) not in wrapped]
    assert not raw, f"tripwire answers read without _verdict: {raw}"

    # and the loop really does call the tripwires through a name called `fn`,
    # so the check above cannot pass by looking for something that is not there
    assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "fn"
               for n in ast.walk(fn_def)), "evaluate_stock no longer calls fn(...)"


# ---------------------------------------------------------------------------
# XR-060 residual, found by the adversarial review of this very repair: the
# minimum-history gate was applied to the ten price tripwires and NOT to the
# two panel-fed ones (T12W_0094 sector contagion, T12W_0095 index overlay),
# so a position with 60-69 stored bars could still have its stop tightened on
# a reading the registry marks not computable. Owner's choice 2026-11-27:
# 70 bars, the same bar as the other ten.
# ---------------------------------------------------------------------------

def _panel_shock(bars: int):
    """A frame short of the 70-bar minimum, with a panel that says CONTAGION."""
    import numpy as np
    import pandas as pd
    from types import SimpleNamespace
    idx = pd.date_range('2026-06-01', periods=bars, freq='B')
    close = np.linspace(100.0, 60.0, bars)
    frame = pd.DataFrame({'Open': close * 1.01, 'High': close * 1.02,
                          'Low': close * 0.94, 'Close': close,
                          'Volume': np.full(bars, 5_000_000.0)}, index=idx)
    ext = SimpleNamespace(target_frame=frame,
                          context={'peer_reason': 'peers broken',
                                   'index_reason': 'index broken'})
    return frame, ext


@pytest.mark.parametrize('bars', [30, 60, 69])
def test_the_panel_pair_abstains_below_the_same_minimum_as_the_other_ten(monkeypatch, bars):
    seen = {}
    monkeypatch.setattr(D.de, "record",
                        lambda key, *a, **k: seen.__setitem__(key, (k.get("state"), k.get("reason"))))
    frame, ext = _panel_shock(bars)
    fired = D.evaluate_stock(frame, ext)
    for tid in D._EXT_FNS:
        assert tid not in fired, (tid, bars, fired)
        state, reason = seen["danger:" + tid]
        assert state == "unavailable" and "Needs 70 bars" in str(reason), (tid, bars, state, reason)


def test_the_gate_opens_once_the_history_is_long_enough(monkeypatch):
    """The gate must not silence the pair for good: at 70+ bars they are CALLED.

    This fixture's panel is a stub, so the call itself does not complete — what
    is pinned here is that the history gate no longer holds them back, which is
    the only thing this repair changed. Whether the pair then fires on a real
    panel is covered by the external-panel tests.
    """
    seen = {}
    monkeypatch.setattr(D.de, "record",
                        lambda key, *a, **k: seen.__setitem__(key, (k.get("state"), k.get("reason"))))
    frame, ext = _panel_shock(80)
    D.evaluate_stock(frame, ext)
    for tid in D._EXT_FNS:
        state, reason = seen["danger:" + tid]
        assert "Needs" not in str(reason), (tid, state, reason)


def test_the_two_loops_read_the_same_minimum_source():
    """One minimum for the whole layer — the owner's choice, pinned.

    Both loops must take the bar from triggers._MIN_BARS / _MIN_BARS_DEFAULT,
    so neither can drift to a different number without this failing.
    """
    src = pathlib.Path(D.__file__).read_text()
    tree = ast.parse(src)
    fn_def = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "evaluate_stock")
    reads = [n for n in ast.walk(fn_def)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "get" and isinstance(n.func.value, ast.Name)
             and n.func.value.id == "_MIN_BARS"]
    assert len(reads) == 2, f"expected both loops to read _MIN_BARS, found {len(reads)}"
    assert all(isinstance(r.args[1], ast.Name) and r.args[1].id == "_MIN_BARS_DEFAULT"
               for r in reads), "a loop stopped falling back to _MIN_BARS_DEFAULT"
