"""Owner order 2026-10-02 ("go for next"): audit display rows F-15..F-18,
S-11, S-12 (engine side). F-14 is pinned in test_slots.py; the page halves
of F-18 / S-11 / S-13 in web/tests/test_audit_display_web_2026_10_02.py.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import PaperPosition, Portfolio
from trading.tests.test_audit_s4_s9_2026_10_02 import _scan_engine


# --------------------------------------------------------------------- F-15

def test_the_fii_score_has_no_jumps():
    from trading.signals.flows import fii_flow_score as f
    for x in (0.0, 5000.0, -5000.0):
        assert abs(f(x + 1) - f(x - 1)) < 0.05, x
    assert f(-1.0) > 49.9 and f(1.0) < 50.1      # was 40 and 60
    assert f(-4999.0) < 10.1                      # was 30 against 10 at -5000


def test_the_documented_end_points_are_kept():
    from trading.signals.flows import fii_flow_score as f
    assert f(0.0) == 50.0 and f(None) == 50.0
    assert f(5000.0) == 90.0 and f(-5000.0) == 10.0
    assert f(20000.0) == 90.0 and f(-20000.0) == 10.0
    assert f(1200.0, days=1) == 90.0             # one day scaled to five


# --------------------------------------------------------------------- F-16

def test_vol_adjusted_upside_is_labelled_for_what_it_is():
    from web.routes import strategy_routes
    label = strategy_routes.__dict__
    text = repr(label)
    assert "Sortino" not in text
    assert "not a Sharpe" in text


def test_vol_adjusted_upside_value_is_unchanged():
    """Relabel only: the composite the presets were set against must not
    move."""
    from trading.scoring import score_vol_adj_upside
    cfg = Config(state_dir="/tmp/sb_f16")
    rng = np.random.default_rng(4)
    close = pd.Series(100 * np.cumprod(1 + rng.normal(0.001, 0.015, 120)))
    pct = close.pct_change(fill_method=None).dropna()
    vol = float(pct.iloc[-cfg.volatility_days:].std() * np.sqrt(252) * 100)
    r = (float(close.iloc[-1]) / float(close.iloc[-1 - cfg.momentum_lookback_days]) - 1) * 100
    want = max(0.0, min(100.0, 50 + (r / vol) * 50))
    assert score_vol_adj_upside(close, cfg) == pytest.approx(want)


# --------------------------------------------------------------------- F-17

def _fill_engine(tmp_path):
    cfg = Config(state_dir=str(tmp_path), mode="swing")
    cfg.apply_mode_preset()
    p = Portfolio.fresh(cfg)
    return TradingEngine(cfg, p), cfg, p


def test_a_limit_fill_pays_slippage_capped_at_the_limit(tmp_path):
    from trading import orders
    from trading.costs import slippage_price
    eng, cfg, p = _fill_engine(tmp_path)
    o = {"ticker": "INFY.NS", "id": "o1", "qty": 2, "limit_price": 450.0,
         "stop_pct": None, "target_pct": None}
    orders._fill(eng, o, 440.0, datetime(2026, 10, 5, 5, 0))
    want = slippage_price(440.0, "BUY", cfg)
    assert want > 440.0
    assert p.positions["INFY.NS"].entry_price == pytest.approx(want)
    assert o["filled_price"] == round(want, 2)
    # a touch right at the limit can never fill above it
    o2 = {"ticker": "TCS.NS", "id": "o2", "qty": 1, "limit_price": 100.0,
          "stop_pct": None, "target_pct": None}
    orders._fill(eng, o2, 100.0, datetime(2026, 10, 5, 5, 0))
    assert p.positions["TCS.NS"].entry_price == 100.0


def test_a_limit_fill_never_flips_the_shared_mode(tmp_path):
    """The position is momentum; the live config stays where it was."""
    from trading import orders
    eng, cfg, p = _fill_engine(tmp_path)
    seen = []
    real = Config.apply_mode_preset

    def spy(self):
        if self is cfg:
            seen.append(self.mode)
        return real(self)
    Config.apply_mode_preset = spy
    try:
        orders._fill(eng, {"ticker": "INFY.NS", "id": "o1", "qty": 2,
                           "limit_price": 450.0, "stop_pct": None,
                           "target_pct": None},
                     440.0, datetime(2026, 10, 5, 5, 0))
    finally:
        Config.apply_mode_preset = real
    assert seen == [], f"the shared config was re-preset: {seen}"
    assert cfg.mode == "swing"
    assert p.positions["INFY.NS"].mode == "nifty200_momentum"
    momentum_stop = 1 + Config(state_dir=str(tmp_path),
                               mode="nifty200_momentum").stop_loss_pct
    assert p.positions["INFY.NS"].stop_price == pytest.approx(
        round(p.positions["INFY.NS"].entry_price * momentum_stop, 2))


# --------------------------------------------------------------------- F-18

def _pos(target_pct: float, stop_pct: float = -0.05) -> PaperPosition:
    return PaperPosition(
        ticker="Q.NS", qty=1, entry_price=100.0,
        entry_date=datetime(2026, 9, 1), sector="IT", peak_price=100.0,
        entry_score=70.0, reasoning="fixture", mode="nifty200_momentum",
        stop_price=100.0 * (1 + stop_pct),
        target_price=100.0 * (1 + target_pct))


def test_a_quiet_names_atr_target_can_kill_the_trail():
    from trading.exits import trail_reach
    cfg = Config(state_dir="/tmp/sb_f18", mode="nifty200_momentum")
    cfg.apply_mode_preset()
    assert cfg.trail_is_reachable()[0], "the mode-level check reads live"
    quiet = trail_reach(_pos(0.12), cfg)       # a k x ATR target of +12%
    assert not quiet["live"] and quiet["need_pct"] > quiet["target_pct"] - 1
    wild = trail_reach(_pos(0.30), cfg)
    assert wild["live"]


def test_the_mode_level_answer_is_unchanged_without_levels():
    cfg = Config(state_dir="/tmp/sb_f18", mode="nifty200_momentum")
    cfg.apply_mode_preset()
    assert cfg.trail_is_reachable() == cfg.trail_is_reachable(
        stop=cfg.stop_loss_pct, target=cfg.take_profit_pct)


# --------------------------------------------------------------------- S-12

def test_the_buy_line_carries_the_target_diagnostics(tmp_path, monkeypatch):
    e = _scan_engine(tmp_path, monkeypatch, event=None)
    e.portfolio.cash = 5_00_00_000.0
    e.portfolio.positions.clear()
    e.scan()
    buys = [x for x in e.portfolio.decision_journal if x.get("action") == "BUY"]
    assert buys, "fixture must buy"
    t = buys[-1].get("target")
    assert t and t["price"] == pytest.approx(
        e.portfolio.positions[buys[-1]["ticker"]].target_price)
    assert t.get("source") in ("atr", "fixed")
    if t["source"] == "atr":
        assert "k" in t and "atr" in t


# --------------------------------------------------------------------- S-11

def test_the_momentum_pass_keeps_its_own_record(tmp_path, monkeypatch):
    e = _scan_engine(tmp_path, monkeypatch, event=None)
    e.scan()
    ms = e.portfolio.momentum_shortlist
    assert ms.get("mode") == "nifty200_momentum" and ms.get("ts")
    assert "UPP.NS" in ms["top"]
    row = ms["rows"]["UPP.NS"]
    assert row["liq"] is True and row["score"] >= ms["score_floor"]
    # another category's pass must not overwrite it
    stamp = ms["ts"]
    e.cfg.mode = "positional"
    e.cfg.apply_mode_preset()
    e.scan()
    assert e.portfolio.momentum_shortlist["ts"] == stamp


def test_liquidity_is_judged_even_when_a_gate_stopped_the_pass(tmp_path,
                                                                monkeypatch):
    """The Budget halt refuses every pick before the liquidity gate runs —
    the stored verdict must still say whether the name is liquid."""
    class _Event:
        event_type = "budget"
        event_date = "2026-10-05"
    e = _scan_engine(tmp_path, monkeypatch, event=_Event())
    e.scan()
    row = e.portfolio.momentum_shortlist["rows"]["UPP.NS"]
    assert row["outcome"].startswith("halt_pre_budget")
    assert row["liq"] is True


def test_the_record_survives_a_save_and_reload(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.momentum_shortlist = {"ts": "2026-10-05T04:00:00", "rows": {}, "top": []}
    p.save()
    assert Portfolio.load_or_fresh(cfg).momentum_shortlist["ts"] == \
        "2026-10-05T04:00:00"
