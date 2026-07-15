"""Smoke tests for the live trading risk gates and exit logic.

These are the bug-catchers that must pass before any real money is committed.
No network, no yfinance, ~1 second total runtime.
"""

from datetime import datetime, timedelta

import pytest

from trading.config import Config
from trading.exits import check_exit
from trading.paper import PaperPosition, Portfolio
from trading.risk import RiskState, can_open


# ------------- helpers -------------

def _cfg(**over):
    c = Config()
    for k, v in over.items():
        setattr(c, k, v)
    return c


def _pos(ticker="RELIANCE.NS", entry=100.0, qty=100, sector="Energy"):
    return PaperPosition(
        ticker=ticker, qty=qty, entry_price=entry, entry_date=datetime(2026, 1, 1),
        sector=sector, peak_price=entry, entry_score=75.0, reasoning="test",
    )


# ------------- 20% reserve / 10% per-stock cap -------------

def test_reserve_20pct_respected():
    cfg = _cfg(starting_capital=10_00_000, cash_reserve_pct=0.20,
               max_position_pct=0.10, max_positions=10, max_sector_positions=99)
    risk = RiskState(cfg=cfg, starting_capital=cfg.starting_capital)
    positions = {f"S{i}.NS": _pos(ticker=f"S{i}.NS", entry=100, qty=800)
                 for i in range(8)}  # 8 positions * 800 * 100 = 6,40,000 deployed
    # Already 64% deployed; 80% cap leaves 1,60,000 headroom => qty <= 1600 @ 100
    ok, reason, qty = can_open(
        risk, cash=cfg.starting_capital, current_equity=cfg.starting_capital,
        open_positions=positions, ticker="NEW.NS", fill_price=100.0, cfg=cfg,
    )
    # 10% cap = 1,00,000; that's tighter than reserve headroom -> qty=1000
    assert ok
    assert qty == 1000


def test_reserve_blocks_when_full():
    cfg = _cfg(starting_capital=10_00_000, cash_reserve_pct=0.20,
               max_position_pct=0.10, max_positions=20, max_sector_positions=99)
    risk = RiskState(cfg=cfg, starting_capital=cfg.starting_capital)
    # Deploy 80% across 8 positions, hitting the reserve wall exactly.
    positions = {f"S{i}.NS": _pos(ticker=f"S{i}.NS", entry=100, qty=1000)
                 for i in range(8)}  # 8 * 1000 * 100 = 8,00,000 deployed = 80%
    ok, reason, qty = can_open(
        risk, cash=2_00_000, current_equity=cfg.starting_capital,
        open_positions=positions, ticker="NEW.NS", fill_price=100.0, cfg=cfg,
    )
    assert not ok
    assert reason == "reserve_breached"


def test_10pct_per_stock_cap():
    cfg = _cfg(starting_capital=10_00_000, cash_reserve_pct=0.20,
               max_position_pct=0.10)
    risk = RiskState(cfg=cfg, starting_capital=cfg.starting_capital)
    ok, _, qty = can_open(
        risk, cash=cfg.starting_capital, current_equity=cfg.starting_capital,
        open_positions={}, ticker="EXPENSIVE.NS", fill_price=50.0, cfg=cfg,
    )
    # 10% of 10L = 1L; at price 50 -> 2000 qty max
    assert ok
    assert qty == 2000


def test_sector_cap():
    cfg = _cfg(max_sector_positions=2)
    risk = RiskState(cfg=cfg, starting_capital=cfg.starting_capital)
    positions = {
        "TCS.NS": _pos(ticker="TCS.NS", sector="IT"),
        "INFY.NS": _pos(ticker="INFY.NS", sector="IT"),
    }
    ok, reason, _ = can_open(
        risk, cash=20_00_000, current_equity=25_00_000,
        open_positions=positions, ticker="HCLTECH.NS", fill_price=1500.0, cfg=cfg,
    )
    assert not ok
    assert reason == "sector_cap_reached"


# ------------- daily loss kill switch -------------

def test_kill_switch_trips_at_threshold():
    cfg = _cfg(starting_capital=10_00_000, daily_loss_halt_pct=-0.02)
    risk = RiskState(cfg=cfg, starting_capital=cfg.starting_capital)
    risk.roll_day(10_00_000)
    assert not risk.check_kill_switch(9_80_001)   # just above -2%
    assert risk.check_kill_switch(9_80_000)        # at -2%
    # Once tripped, new entries blocked
    ok, reason, _ = can_open(
        risk, cash=5_00_000, current_equity=9_80_000,
        open_positions={}, ticker="RELIANCE.NS", fill_price=2500.0, cfg=cfg,
    )
    assert not ok
    assert reason == "kill_switch_active"


# ------------- exit triggers -------------

def test_hard_stop_at_minus_5pct():
    cfg = _cfg(stop_loss_pct=-0.05, take_profit_pct=0.10,
               trailing_stop_pct=-0.08, score_decay_exit=50.0)
    pos = _pos(entry=100.0)
    should, reason = check_exit(pos, current_price=95.0, current_score=80.0,
                                cfg=cfg, now=datetime(2026, 1, 5))
    assert should and reason == "STOP_LOSS"


def test_take_profit_at_plus_10pct():
    cfg = _cfg(stop_loss_pct=-0.05, take_profit_pct=0.10,
               trailing_stop_pct=-0.08, score_decay_exit=50.0)
    pos = _pos(entry=100.0)
    should, reason = check_exit(pos, current_price=110.0, current_score=80.0,
                                cfg=cfg, now=datetime(2026, 1, 5))
    assert should and reason == "TAKE_PROFIT"


def test_trailing_stop_after_peak():
    cfg = _cfg(stop_loss_pct=-0.05, take_profit_pct=0.10,
               trailing_stop_pct=-0.08, score_decay_exit=50.0)
    pos = _pos(entry=100.0)
    # First push price to 108 (8% above entry) — peak gets recorded.
    check_exit(pos, 108.0, 80.0, cfg, datetime(2026, 1, 5))
    assert pos.peak_price == 108.0
    # Now drop 8% from peak -> 99.36
    should, reason = check_exit(pos, current_price=99.36, current_score=80.0,
                                cfg=cfg, now=datetime(2026, 1, 6))
    assert should and reason == "TRAILING_STOP"


def test_score_decay_exit():
    cfg = _cfg(stop_loss_pct=-0.50, take_profit_pct=0.50,
               trailing_stop_pct=-0.50, score_decay_exit=50.0)
    pos = _pos(entry=100.0)
    should, reason = check_exit(pos, current_price=101.0, current_score=49.0,
                                cfg=cfg, now=datetime(2026, 1, 5))
    assert should and reason == "SCORE_DECAY"


def test_max_hold_cap():
    cfg = _cfg(stop_loss_pct=-0.50, take_profit_pct=0.50,
               trailing_stop_pct=-0.50, score_decay_exit=0.0,
               max_hold_days=30)
    pos = _pos(entry=100.0)
    pos.entry_date = datetime(2026, 1, 1)
    should, reason = check_exit(pos, current_price=101.0, current_score=80.0,
                                cfg=cfg, now=datetime(2026, 1, 1) + timedelta(days=30))
    assert should and reason == "MAX_HOLD"


def test_no_exit_when_all_clear():
    cfg = _cfg(stop_loss_pct=-0.05, take_profit_pct=0.10,
               trailing_stop_pct=-0.08, score_decay_exit=50.0)
    pos = _pos(entry=100.0)
    should, _ = check_exit(pos, current_price=102.0, current_score=80.0,
                           cfg=cfg, now=datetime(2026, 1, 5))
    assert not should


# ------------- portfolio basics -------------

def test_portfolio_equity_and_persistence(tmp_path):
    cfg = _cfg(starting_capital=10_00_000, state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.positions["RELIANCE.NS"] = _pos(ticker="RELIANCE.NS", entry=2500.0, qty=10)
    p.cash = 10_00_000 - 2500 * 10
    eq = p.equity({"RELIANCE.NS": 2600.0})
    assert eq == pytest.approx(10_00_000 - 2500 * 10 + 2600 * 10)
    p.save()

    p2 = Portfolio.load_or_fresh(cfg)
    assert "RELIANCE.NS" in p2.positions
    assert p2.positions["RELIANCE.NS"].qty == 10
    assert p2.cash == p.cash
