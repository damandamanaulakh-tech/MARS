"""½-Kelly position sizing (owner order 2026-07-30, after reading
docs/ALGO_V1_V2.md: "Switch to ½-Kelly sizing").

½-Kelly earns 75% of the maximum compound growth rate for a 12.5% chance of
ever halving the book, vs full Kelly's 50%. These tests pin the maths, the
safety clamps, and that every pre-existing risk gate still applies.
"""

import math

import pandas as pd
import pytest

from trading.config import Config
from trading.risk import RiskState, can_open
from trading.sizing import (edge_estimate, kelly_fraction, kelly_notional,
                            position_notional_for)


def _close(n=300, daily=0.0, vol=0.01):
    idx = pd.bdate_range("2025-01-01", periods=n)
    px, out = 100.0, []
    for i in range(n):
        px *= (1.0 + daily + (vol if i % 2 else -vol))
        out.append(px)
    return pd.Series(out, index=idx)


def _cfg(**kw):
    kw.setdefault("sizing_mode", "half_kelly")   # the maths under test
    c = Config(state_dir="/tmp/kelly_tests", **kw)
    c.apply_mode_preset()
    return c


# ---- the config the owner chose ----------------------------------------

def test_live_config_is_fixed_at_the_owners_number():
    """REVERTED 2026-08-04 (owner: "still u r making 30000 positions"). Kelly
    sizes Rs 20k-60k just above the 62 floor; the owner had ordered a FIXED
    Rs 1.25 lakh per trade and their number wins. Kelly stays selectable and
    its maths stays tested below."""
    cfg = _cfg(sizing_mode=Config.sizing_mode)     # the real shipped default
    assert cfg.sizing_mode == "fixed"
    assert cfg.position_size_value == 125_000.0
    n, diag = position_notional_for(cfg, 50_00_000.0, score=65.0, close=_close())
    assert n == 125_000.0                     # not Kelly's Rs 62k
    assert diag["mode"] == "fixed"


def test_the_kelly_knobs_keep_their_owner_approved_values():
    cfg = _cfg()
    assert cfg.kelly_fraction == 0.5          # 75% of max growth, 12.5% ruin
    assert cfg.kelly_ic == 0.05               # the assumption, named
    assert cfg.kelly_max_pct == 0.05          # hard ceiling per name


# ---- the maths ---------------------------------------------------------

def test_size_is_half_kelly_of_equity():
    cfg = _cfg()
    close = _close()
    equity = 50_00_000.0
    notional, diag = kelly_notional(cfg, equity, score=80.0, close=close)

    sigma = close.pct_change().dropna().tail(63).std() * math.sqrt(252)
    z = (80.0 - cfg.min_score_to_enter) / 30.0
    mu = 0.05 * sigma * z
    expected_f = min(0.05, 0.5 * mu / sigma ** 2)
    assert notional == pytest.approx(expected_f * equity, rel=1e-9)
    assert diag["mode"] == "half_kelly"
    assert diag["ic_assumed"] == 0.05
    assert diag["floor"] == cfg.min_score_to_enter


def test_half_kelly_is_exactly_double_quarter_kelly():
    close = _close()
    mu, sigma = edge_estimate(80.0, close, ic=0.05, floor=62.0)
    q = kelly_fraction(mu, sigma, fraction=0.25, cap=1.0)
    h = kelly_fraction(mu, sigma, fraction=0.5, cap=1.0)
    assert h == pytest.approx(2 * q)


def test_lower_volatility_earns_a_bigger_bet():
    """Kelly divides by variance, so a steady riser gets more money than a
    wild one at the same score. That is the intended behaviour."""
    cfg = _cfg()
    calm = kelly_notional(cfg, 50_00_000.0, 80.0, _close(vol=0.005))[0]
    wild = kelly_notional(cfg, 50_00_000.0, 80.0, _close(vol=0.03))[0]
    assert calm > wild


def test_a_score_at_the_floor_gets_no_money():
    """z = 0 at the floor, so mu = 0 and Kelly says do not bet. The owner's
    "buy above 62" is therefore enforced twice: by the gate and by the size."""
    cfg = _cfg()
    notional, diag = kelly_notional(cfg, 50_00_000.0, cfg.min_score_to_enter, _close())
    assert notional == 0.0
    n2, d2 = position_notional_for(cfg, 50_00_000.0,
                                   score=cfg.min_score_to_enter, close=_close())
    assert n2 == 0.0 and d2["reason"] == "no_estimated_edge"


def test_the_cap_stops_one_name_eating_the_book():
    cfg = _cfg()
    # a very calm stock with a top score would otherwise size enormously
    notional, diag = kelly_notional(cfg, 50_00_000.0, 100.0, _close(vol=0.002))
    assert notional == pytest.approx(0.05 * 50_00_000.0)     # 5% ceiling
    assert diag["capped_at_max"] is True


# ---- fallbacks: never guess an edge you cannot estimate ----------------

def test_fixed_mode_ignores_kelly_entirely():
    cfg = _cfg(sizing_mode="fixed")
    n, diag = position_notional_for(cfg, 50_00_000.0, score=95.0, close=_close())
    assert n == 125_000.0 and diag["mode"] == "fixed"


def test_missing_history_or_score_falls_back_to_fixed():
    cfg = _cfg()
    assert position_notional_for(cfg, 50_00_000.0, score=95.0, close=None)[1]["mode"] == "fixed"
    assert position_notional_for(cfg, 50_00_000.0, score=None, close=_close())[1]["mode"] == "fixed"
    assert position_notional_for(cfg, 50_00_000.0)[0] == 125_000.0


def test_too_little_history_means_no_bet_not_a_guess():
    cfg = _cfg()
    assert kelly_notional(cfg, 50_00_000.0, 90.0, _close(n=15))[0] == 0.0


# ---- every risk gate still applies ------------------------------------

def _open(cfg, notional, cash=50_00_000.0, positions=None, price=500.0):
    return can_open(RiskState(cfg=cfg, starting_capital=50_00_000.0),
                    cash=cash, current_equity=50_00_000.0,
                    open_positions=positions or {}, ticker="TCS.NS",
                    fill_price=price, cfg=cfg, notional_override=notional)


def test_override_sizes_the_position():
    cfg = _cfg()
    ok, why, qty = _open(cfg, 2_50_000.0)
    assert ok, why
    assert qty == int(2_50_000.0 // 500.0)          # 500 shares, not the fixed 250


def test_zero_override_blocks_the_trade():
    ok, why, qty = _open(_cfg(), 0.0)
    assert not ok and why == "no_estimated_edge" and qty == 0


def test_override_is_still_bounded_by_cash():
    cfg = _cfg()
    ok, why, qty = _open(cfg, 10_00_000.0, cash=1_00_000.0)
    assert ok, why
    assert qty * 500.0 <= 1_00_000.0                # cash wins over Kelly


def test_kill_switch_still_beats_kelly():
    cfg = _cfg()
    risk = RiskState(cfg=cfg, starting_capital=50_00_000.0)
    risk.kill_switch = True
    ok, why, qty = can_open(risk, 50_00_000.0, 50_00_000.0, {}, "TCS.NS",
                            500.0, cfg, notional_override=2_50_000.0)
    assert not ok and why == "kill_switch_active"


def test_no_override_keeps_the_fixed_behaviour():
    """Callers that cannot estimate an edge get the old flat size."""
    cfg = _cfg()
    ok, why, qty = can_open(RiskState(cfg=cfg, starting_capital=50_00_000.0),
                            50_00_000.0, 50_00_000.0, {}, "TCS.NS", 500.0, cfg)
    assert ok and qty == int(125_000.0 // 500.0)


# ---- the engine wiring -------------------------------------------------

def test_engine_sizes_through_the_shared_module_and_drops_the_double_count():
    """Under Kelly the score already sets the size, so the old strength
    ladder (0.5x/0.7x/1.0x by score) must not also apply — that would count
    the score twice."""
    import inspect
    from trading import engine
    src = inspect.getsource(engine)
    assert "from .sizing import position_notional_for" in src
    assert "notional_override=" in src
    assert "kelly_sized = size_diag" in src
    assert 'strength_mult = 1.0 if kelly_sized else (' in src
    # and the engine still must not import the read-only research lens
    assert "lens_v2" not in src


def test_sizing_decision_is_journaled():
    import inspect
    from trading import engine
    assert '"sizing": pick.get("size_diag")' in inspect.getsource(engine)
