"""Owner order 2026-10-04 ("find the all app works in sequence or any flaw /
make sure both engine works / ask me anything and fix"). Three answers:

1. Ocean — "own-panel damage rule": rail R2 judges the damage cluster
   against the panel's own 90th percentile, not the frozen 0.213 (ON 84% of
   days on our list; Ocean opened ZERO positions in 12 years).
2. Weights — "use each category's weights": intraday / short / swing /
   positional score with their own modes.WEIGHTS preset (all four used the
   global weights before).
3. Candle gate — "off for momentum": momentum entries no longer wait for a
   candle pattern; the other categories still do.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from trading.config import AM07_WEIGHTS, Config
from trading.modes import WEIGHTS


# ------------------------------------------------------------------ 1 Ocean

def _calm_vix(n=200):
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    return pd.Series(np.full(n, 13.0), index=idx)


def _store(static, q90, asof=None):
    return {"asof": asof or date.today().isoformat(), "value": 0.3,
            "q90": 0.7, "static_cluster": static, "q90_cluster": q90}


def test_the_frozen_0213_no_longer_blocks_ocean():
    from trading.engines.ocean import crash_guard_market
    ok, why = crash_guard_market(_calm_vix(), _store(static=True, q90=False))
    assert ok and why == "ok"


def test_the_own_panel_cluster_still_blocks():
    from trading.engines.ocean import crash_guard_market
    ok, why = crash_guard_market(_calm_vix(), _store(static=False, q90=True))
    assert not ok and why == "damage_cluster_own_q90"


def test_an_unreadable_or_stale_reading_still_blocks():
    from trading.engines.ocean import crash_guard_market
    s = _store(static=False, q90=False)
    del s["q90_cluster"]
    assert crash_guard_market(_calm_vix(), s) == (False, "damage_reading_unreadable")
    stale = _store(static=False, q90=False, asof="2020-01-01")
    assert crash_guard_market(_calm_vix(), stale)[1] == "damage_reading_missing_or_stale"
    assert crash_guard_market(_calm_vix(), None)[1] == "damage_reading_missing_or_stale"


# ---------------------------------------------------------------- 2 weights

def test_each_category_scores_with_its_own_weights():
    from trading.scoring import weights_for
    for m in ("intraday", "short", "swing", "positional"):
        assert weights_for(m) is WEIGHTS[m]
    # momentum buys on its own blend; it and unknown modes keep the globals
    assert weights_for("nifty200_momentum") is AM07_WEIGHTS
    assert weights_for(None) is AM07_WEIGHTS
    assert WEIGHTS["swing"] == AM07_WEIGHTS      # swing unchanged by the order


def _frame(seed=3, n=140):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0.002, 0.018, n))
    idx = pd.bdate_range(end="2026-09-30", periods=n)
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                         "Close": close,
                         "Volume": rng.integers(2e5, 9e5, n).astype(float)},
                        index=idx)


@pytest.fixture
def quiet_feeds(monkeypatch):
    import trading.scoring as S
    import trading.signals.news as N
    monkeypatch.setattr(N, "fetch_sentiment",
                        lambda t: type("x", (), {"score": 50.0})())
    monkeypatch.setattr(S, "fetch_earnings_growth", lambda t: None)
    monkeypatch.setattr(S, "_analyst_revision_score", lambda t: 50.0)
    monkeypatch.setattr(S, "_short_interest_score", lambda t, c: 50.0)
    return S


def test_the_total_uses_the_category_weights(quiet_feeds):
    S = quiet_feeds
    h = _frame()
    for m in ("intraday", "short", "positional"):
        total, comps = S.compute_score("X.NS", h, None, {}, Config(mode=m), mode=m)
        want = sum(comps[k] * WEIGHTS[m][k] for k in WEIGHTS[m])
        assert total == pytest.approx(want)
        old = sum(comps[k] * AM07_WEIGHTS[k] for k in AM07_WEIGHTS)
        assert abs(total - old) > 1e-6, m           # it really changed


def test_the_exit_ruler_reads_the_same_weights(quiet_feeds):
    """score_for_mode (the backtest AND live exit ruler) must weigh a
    position with its own category, or entry and exit disagree."""
    S = quiet_feeds
    h = _frame()
    cfg = Config(mode="swing")                     # the shared live config
    a, _ = S.score_for_mode("positional", "X.NS", h, None, {}, cfg)
    b, _ = S.compute_score("X.NS", h, None, {}, Config(mode="positional"),
                           mode="positional")
    assert a == pytest.approx(b)


# ------------------------------------------------------------ 3 candle gate

def test_the_candle_gate_is_off_for_momentum_only():
    from trading.candles import candle_gate_applies
    assert not candle_gate_applies(Config(mode="nifty200_momentum"))
    for m in ("intraday", "short", "swing", "positional"):
        assert candle_gate_applies(Config(mode=m)), m
    off = Config(mode="swing")
    off.candle_confirm_enabled = False
    assert not candle_gate_applies(off)


def test_a_momentum_pick_buys_without_a_candle_pattern(tmp_path, monkeypatch):
    from trading.tests.test_audit_s4_s9_2026_10_02 import _scan_engine
    e = _scan_engine(tmp_path, monkeypatch, event=None)
    e.cfg.candle_confirm_enabled = True          # the live setting
    e.portfolio.cash = 5_00_00_000.0
    e.portfolio.positions.clear()
    import trading.engine as E
    monkeypatch.setattr(E, "candle_confirmation",
                        lambda df, cfg: (False, "no_pattern"))
    e.scan()
    assert "UPP.NS" in e.portfolio.positions


def test_a_swing_pick_still_waits_for_a_pattern(tmp_path, monkeypatch,
                                               quiet_feeds):
    """The pick must REACH the gate for this to mean anything: the fixture's
    swing score is ~54 (< 60) and swing timing would stop it first, so the
    floor and timing are opened up here and the candle is the only gate
    left (the 2026-10-04 lesson: the kill-switch test passed for months
    only because this gate happened to block its pick)."""
    from trading.tests.test_audit_s4_s9_2026_10_02 import _scan_engine
    e = _scan_engine(tmp_path, monkeypatch, event=None)
    e.cfg.mode = "swing"
    e.cfg.apply_mode_preset()
    e.cfg.min_score_to_enter = 0.0
    e.cfg.breakout_threshold_pct = 1.0
    e.cfg.volume_confirm_ratio = 0.0
    e.cfg.candle_confirm_enabled = True
    e.portfolio.cash = 5_00_00_000.0
    e.portfolio.positions.clear()
    import trading.engine as E
    monkeypatch.setattr(E, "candle_confirmation",
                        lambda df, cfg: (False, "no_pattern"))
    e.scan()
    assert not e.portfolio.positions
    reasons = {s["ticker"]: s["reason"] for s in e.portfolio.last_scan_skipped}
    assert reasons.get("UPP.NS") == "candle_wait_no_pattern"
    # and with the gate passing, the same pick buys — the gate is the cause
    monkeypatch.setattr(E, "candle_confirmation",
                        lambda df, cfg: (True, "engulfing"))
    e.scan()
    assert "UPP.NS" in e.portfolio.positions


def test_the_backtest_reads_the_same_predicate():
    import ast
    import inspect
    from trading import backtest
    tree = ast.parse(inspect.getsource(backtest))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "_cga" in names
