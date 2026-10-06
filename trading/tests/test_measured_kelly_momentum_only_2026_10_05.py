"""Owner order 2026-10-05: "make it momentum only".

The measured-Kelly check (F-9) pooled all five categories' exits into one
reading, so a run of losing intraday or swing exits could stop momentum
buying. Now it reads momentum exits only and gates momentum buys only.
A closed trade records its category at close (ClosedTrade.mode); a legacy
trade without it is placed by its own record, never guessed.
"""
from __future__ import annotations

import ast
import inspect
from datetime import datetime

import pytest

from trading import measured_kelly as MK
from trading.config import Config
from trading.paper import ClosedTrade, PaperPosition, Portfolio
from trading.tests.test_audit_s4_s9_2026_10_02 import _scan_engine

MOM = "nifty200_momentum"


def _trade(i, ret, reason="STOP_LOSS", mode="", reasoning="fixture", tags=None):
    entry = 100.0
    exit_ = entry * (1 + ret)
    return ClosedTrade(
        ticker=f"T{i}.NS", qty=100, entry_date=datetime(2026, 1, 1),
        exit_date=datetime(2026, 2, 1), entry_price=entry, exit_price=exit_,
        gross_pnl=(exit_ - entry) * 100, costs=0.0,
        net_pnl=(exit_ - entry) * 100, reason=reason, sector="IT",
        entry_score=70.0, reasoning=reasoning, tags=list(tags or []),
        mode=mode)


def _losers(n, mode, start=0):
    return [_trade(start + i, -0.05, mode=mode) for i in range(n)]


def _winners(n, mode, start=0, ret=0.20):
    return [_trade(start + i, ret, "TAKE_PROFIT", mode=mode) for i in range(n)]


def _no_edge(mode, start=0):
    """10 tiny wins against 30 full stops: Kelly negative (the 10-02 fixture)."""
    return _winners(10, mode, start, ret=0.02) + _losers(30, mode, start + 100)


# ------------------------------------------------------------ the record

def test_the_default_is_momentum_only():
    assert Config().measured_kelly_modes == (MOM,)


def test_a_closed_trade_records_its_category(tmp_path):
    from trading.engine import TradingEngine
    cfg = Config(state_dir=str(tmp_path), mode=MOM)
    cfg.apply_mode_preset()
    p = Portfolio.fresh(cfg)
    p.positions["SW.NS"] = PaperPosition(
        ticker="SW.NS", qty=10, entry_price=100.0,
        entry_date=datetime(2026, 9, 1), sector="IT", peak_price=100.0,
        entry_score=65.0, reasoning="fixture", mode="swing",
        stop_price=95.0, target_price=110.0)
    e = TradingEngine(cfg, p)
    e._close("SW.NS", 94.0, "STOP_LOSS", datetime(2026, 10, 6, 5, 0))
    assert p.trades[-1].mode == "swing"
    assert MK.trade_category(p.trades[-1]) == "swing"


def test_a_legacy_record_round_trips_without_the_field():
    d = _trade(1, -0.05).to_dict()
    d.pop("mode")
    t = ClosedTrade.from_dict(d)
    assert t.mode == ""


def test_legacy_trades_are_placed_by_their_own_record():
    assert MK.trade_category(_trade(1, 0.1, reasoning="MOM 72 + QUALITY 60 (blend 35%) = 68")) == MOM
    assert MK.trade_category(_trade(2, 0.1, reasoning="NIFTY200_MOMENTUM: 6m+12m sharpe 68.0/100")) == MOM
    assert MK.trade_category(_trade(3, 0.1, reasoning="OWNER BUY CALL — user pushed")) == MOM
    assert MK.trade_category(_trade(4, 0.1, tags=["owner_pick", "buy_desk"])) == MOM
    assert MK.trade_category(_trade(5, 0.1, reasoning="OCEAN: 31% off 200DMA")) == "ocean"
    assert MK.trade_category(_trade(6, 0.1, tags=["ocean"], mode=MOM)) == "ocean"
    assert MK.trade_category(_trade(7, 0.1, reasoning="Momentum 40 · Trend 55 ...")) == "other"
    assert MK.trade_category(_trade(8, 0.1, mode="positional")) == "positional"


# ----------------------------------------------------------- the measure

def test_other_categories_exits_do_not_feed_the_momentum_reading():
    """30 losing swing stops beside a healthy momentum record: the old
    pooled reading said NO EDGE; the momentum reading says ACTIVE."""
    trades = _winners(40, MOM) + _losers(20, MOM, 100) + _losers(30, "swing", 200)
    pooled = MK.measure(trades, fraction=0.29)
    mom = MK.measure(trades, fraction=0.29, category=MOM)
    assert mom.status == "ACTIVE" and mom.usable == 60
    assert mom.other_category == 30 and mom.category == MOM
    assert pooled.usable == 90 and pooled.raw_kelly < mom.raw_kelly


def test_a_momentum_losing_streak_still_blocks_momentum():
    trades = _no_edge(MOM) + _winners(40, "swing", 200)
    mom = MK.measure(trades, fraction=0.29, category=MOM)
    assert mom.status == "NO_POSITIVE_EDGE" and mom.block_new_entries
    assert mom.usable == 40 and mom.other_category == 40


def test_legacy_unplaceable_trades_are_left_out_not_guessed():
    legacy = [_trade(i, -0.05) for i in range(30)]       # "fixture" reasoning
    mom = MK.measure(legacy, category=MOM)
    assert mom.status == "NO_SAMPLE" and mom.other_category == 30


# ------------------------------------------------------------- the engine

def _fresh(tmp_path, monkeypatch, trades):
    e = _scan_engine(tmp_path, monkeypatch, event=None)
    e.portfolio.cash = 5_00_00_000.0
    e.portfolio.positions.clear()
    e.portfolio.trades = list(trades)
    return e


def test_losing_swing_exits_no_longer_block_the_momentum_scan(tmp_path, monkeypatch):
    e = _fresh(tmp_path, monkeypatch, _no_edge("swing"))
    e.scan()
    assert "UPP.NS" in e.portfolio.positions
    reasons = {s["ticker"]: s["reason"] for s in e.portfolio.last_scan_skipped}
    assert reasons.get("UPP.NS") != "measured_kelly_no_edge"


def test_losing_momentum_exits_still_block_the_momentum_scan(tmp_path, monkeypatch):
    e = _fresh(tmp_path, monkeypatch, _no_edge(MOM))
    e.scan()
    assert "UPP.NS" not in e.portfolio.positions
    reasons = {s["ticker"]: s["reason"] for s in e.portfolio.last_scan_skipped}
    assert reasons.get("UPP.NS") == "measured_kelly_no_edge"
    line = [x for x in e.portfolio.decision_journal
            if x.get("action") == "MEASURED_KELLY_BLOCKED"][-1]
    assert line["category"] == MOM and line["usable"] == 40


def test_the_check_does_not_run_in_another_categorys_pass(tmp_path, monkeypatch):
    e = _fresh(tmp_path, monkeypatch, _no_edge(MOM))
    assert e._measured_kelly(datetime(2026, 10, 6, 4, 0)).block_new_entries
    e.cfg.mode = "swing"
    e.cfg.apply_mode_preset()
    assert e._measured_kelly(datetime(2026, 10, 6, 4, 0)) is None


def test_an_empty_list_restores_the_pooled_check(tmp_path, monkeypatch):
    e = _fresh(tmp_path, monkeypatch, _no_edge("swing"))
    e.cfg.measured_kelly_modes = ()
    r = e._measured_kelly(datetime(2026, 10, 6, 4, 0))
    assert r is not None and r.block_new_entries and r.category is None


def test_an_ocean_pick_is_not_judged_by_the_momentum_record():
    """Source pin (the ocean fixture needs a whole crash regime): the block
    branch must read pick['ocean']."""
    from trading import engine
    src = inspect.getsource(engine.TradingEngine.scan)
    tree = ast.parse(src.lstrip() if not src.startswith("    ") else
                     "\n".join(l[4:] for l in src.splitlines()))
    hits = [n for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and n.value == "measured_kelly_no_edge"]
    assert hits, "the block branch must still exist"
    assert 'and not pick.get("ocean")' in src
