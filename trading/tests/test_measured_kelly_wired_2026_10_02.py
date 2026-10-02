"""Owner order 2026-10-02 ("F9- write it"): the measured-Kelly check runs.

The estimator (trading/measured_kelly.py) was adopted on 2026-09-10 and
switched on in config, but nothing called it. Now the 09:20 scan reads it:

  NO_POSITIVE_EDGE  -> every new scan entry refused (measured_kelly_no_edge)
  ACTIVE            -> each new entry capped at fraction x equity, never
                       below the Rs 1.5 lakh slab, never raised
  NO_SAMPLE         -> nothing changes
"""
from __future__ import annotations

from datetime import datetime

import pytest

from trading.paper import ClosedTrade
from trading.tests.test_audit_s4_s9_2026_10_02 import _scan_engine


def _trade(i: int, ret: float, reason: str = "STOP_LOSS") -> ClosedTrade:
    entry = 100.0
    exit_ = entry * (1 + ret)
    return ClosedTrade(
        ticker=f"T{i}.NS", qty=100, entry_date=datetime(2026, 1, 1),
        exit_date=datetime(2026, 2, 1), entry_price=entry, exit_price=exit_,
        gross_pnl=(exit_ - entry) * 100, costs=0.0,
        net_pnl=(exit_ - entry) * 100, reason=reason, sector="IT",
        entry_score=70.0, reasoning="fixture")


def _no_edge():
    # 10 small wins, 30 losses: Kelly is negative
    return ([_trade(i, 0.02, "TAKE_PROFIT") for i in range(10)]
            + [_trade(100 + i, -0.05) for i in range(30)])


def _edge():
    # 40 wins of +5%, 20 losses of -5%: positive, 20% confidence
    return ([_trade(i, 0.05, "TAKE_PROFIT") for i in range(40)]
            + [_trade(100 + i, -0.05) for i in range(20)])


def _fresh(tmp_path, monkeypatch, trades):
    e = _scan_engine(tmp_path, monkeypatch, event=None)
    e.portfolio.cash = 5_00_00_000.0
    e.portfolio.positions.clear()
    e.portfolio.trades = list(trades)
    return e


def _buys(e):
    return [x for x in e.portfolio.decision_journal if x.get("action") == "BUY"]


def test_no_measured_edge_blocks_every_scan_entry(tmp_path, monkeypatch):
    e = _fresh(tmp_path, monkeypatch, _no_edge())
    e.scan()
    assert "UPP.NS" not in e.portfolio.positions
    reasons = {s["ticker"]: s["reason"] for s in e.portfolio.last_scan_skipped}
    assert reasons.get("UPP.NS") == "measured_kelly_no_edge"
    lines = [x for x in e.portfolio.decision_journal
             if x.get("action") == "MEASURED_KELLY_BLOCKED"]
    assert lines and lines[-1]["usable"] == 40
    assert lines[-1]["kelly"] < 0


def test_a_measured_edge_caps_the_size_and_never_raises_it(tmp_path, monkeypatch):
    e = _fresh(tmp_path, monkeypatch, _edge())
    e.scan()
    buys = _buys(e)
    assert buys, "an edge must still buy"
    mk = buys[-1]["sizing"]["measured_kelly"]
    assert mk["status"] == "ACTIVE" and mk["usable"] == 60
    pos = e.portfolio.positions["UPP.NS"]
    spent = pos.qty * pos.entry_price
    assert spent <= mk["cap"] * 1.001
    assert spent >= e.cfg.position_slab_value * 0.98
    assert mk["capped"] is True and mk["wanted"] > mk["cap"]


def test_the_cap_never_goes_below_the_slab():
    from trading.config import Config
    from trading.engine import TradingEngine
    from trading.measured_kelly import measure
    cfg = Config(state_dir="/tmp/sb_mk_cap", mode="nifty200_momentum")
    cfg.apply_mode_preset()
    tiny = measure([_trade(i, 0.05, "TAKE_PROFIT") for i in range(3)]
                   + [_trade(10 + i, -0.05) for i in range(2)], fraction=0.29)
    assert tiny.status == "ACTIVE"
    cap = TradingEngine._measured_kelly_cap(tiny, cfg, 5_00_00_000.0)
    assert cap == cfg.position_slab_value
    assert TradingEngine._measured_kelly_cap(None, cfg, 1e8) is None


def test_owner_exits_do_not_count(tmp_path, monkeypatch):
    """Forty losing OWNER_SELLs are the owner, not the strategy: no block."""
    owner = [_trade(i, -0.05, "OWNER_SELL") for i in range(40)]
    e = _fresh(tmp_path, monkeypatch, owner)
    e.scan()
    assert "UPP.NS" in e.portfolio.positions
    assert "measured_kelly" not in (_buys(e)[-1].get("sizing") or {})


def test_switched_off_means_no_block(tmp_path, monkeypatch):
    e = _fresh(tmp_path, monkeypatch, _no_edge())
    e.cfg.measured_kelly_enabled = False
    e.scan()
    assert "UPP.NS" in e.portfolio.positions


def test_a_broken_estimator_never_stops_buying(tmp_path, monkeypatch):
    e = _fresh(tmp_path, monkeypatch, _no_edge())

    def boom(*a, **k):
        raise RuntimeError("estimator down")
    monkeypatch.setattr("trading.measured_kelly.measure", boom)
    e.scan()
    assert "UPP.NS" in e.portfolio.positions


def test_rotation_never_sells_for_a_blocked_pick(tmp_path, monkeypatch):
    """The near-target holding must not be sold to fund a pick the check
    refused (the S-4 rule: rotation only funds cleared picks)."""
    e = _scan_engine(tmp_path, monkeypatch, event=None)
    e.portfolio.trades = _no_edge()
    e.scan()
    assert "OLD.NS" in e.portfolio.positions
    assert not [t for t in e.portfolio.trades if t.reason == "ROTATION_EXIT"]


def test_the_scan_calls_the_check():
    """Guards against the F-9 state returning: switched on, never called."""
    import ast
    import inspect
    from trading import engine
    tree = ast.parse(inspect.getsource(engine.TradingEngine.scan).lstrip())
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "_measured_kelly" in names and "_measured_kelly_cap" in names


def test_a_target_exit_is_not_clipped_as_an_outlier():
    """+20% TAKE_PROFIT is the design's normal win. Clipped at 12% the payoff
    ratio read 12/5 instead of 20/5 — 40% short, toward a wrongful block."""
    from trading.measured_kelly import CLIP_PCT, live_clip_pct, measure
    assert live_clip_pct() == pytest.approx(20.0)
    trades = ([_trade(i, 0.20, "TAKE_PROFIT") for i in range(10)]
              + [_trade(50 + i, -0.05) for i in range(30)])
    old = measure(trades, clip_pct=CLIP_PCT)
    new = measure(trades, clip_pct=live_clip_pct())
    assert old.payoff_ratio == pytest.approx(12 / 5)
    assert new.payoff_ratio == pytest.approx(20 / 5)
    assert new.raw_kelly > old.raw_kelly
