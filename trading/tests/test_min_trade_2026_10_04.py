"""Owner order 2026-10-04: "leave the 1.5 minimum value no 25 k is needed".

A scan trade never opens below Rs 1,50,000. Before this, cash, the
category share, the sector cap or portfolio heat could trim a trade down to
the old Rs 25,000 dust guard. Now a trimmed trade is rounded UP to the
minimum when the money limits have room, and refused when they do not.
"""
from __future__ import annotations

from datetime import datetime

from trading.config import Config
from trading.paper import PaperPosition
from trading.risk import RiskState, can_open

BOOK = 5_00_00_000.0


def _cfg():
    cfg = Config(state_dir="/tmp/sb_min_trade", mode="nifty200_momentum")
    cfg.apply_mode_preset()
    cfg.cash_reserve_pct = 0.0
    cfg.category_budgets_enabled = False
    return cfg


def _gate(cfg, cash, want, price, positions=None):
    risk = RiskState(cfg=cfg, starting_capital=BOOK)
    return can_open(risk, cash, BOOK, positions or {}, "NEW.NS", price, cfg,
                    notional_override=want)


def test_the_minimum_is_the_slab():
    cfg = Config()
    assert cfg.min_position_value == 150_000.0 == cfg.position_slab_value


def test_whole_share_rounding_is_lifted_to_the_minimum():
    """Rs 1.5 lakh at Rs 1,600 a share is 93.75 shares; 93 = Rs 1,48,800 used
    to open just under the slab. Now 94 = Rs 1,50,400."""
    ok, why, qty = _gate(_cfg(), cash=BOOK, want=150_000.0, price=1_600.0)
    assert ok, why
    assert qty == 94 and qty * 1_600.0 >= 150_000.0


def test_cash_short_of_the_minimum_refuses_instead_of_opening_small():
    """Rs 60,000 of cash used to open a Rs 59,200 trade."""
    ok, why, qty = _gate(_cfg(), cash=60_000.0, want=150_000.0, price=1_600.0)
    assert not ok and why == "below_min_value_150000" and qty == 0


def test_an_ocean_ticket_with_thin_cash_is_refused():
    ok, why, _ = _gate(_cfg(), cash=1_00_000.0, want=168_750.0, price=1_600.0)
    assert not ok and why.startswith("below_min_value")


def test_heat_trim_below_the_minimum_refuses():
    cfg = _cfg()
    # cap Rs 50,000 of heat; existing Rs 9.6L at a 5% stop = Rs 48,000,
    # leaving room for only Rs 40,000 of new position
    cfg.max_portfolio_heat_pct = 0.001
    big = PaperPosition(
        ticker="H.NS", qty=9_600, entry_price=100.0,
        entry_date=datetime(2026, 9, 1), sector="IT", peak_price=100.0,
        entry_score=70.0, reasoning="fixture", mode="nifty200_momentum")
    ok, why, _ = _gate(cfg, cash=BOOK, want=5_00_000.0, price=1_000.0,
                       positions={"H.NS": big})
    assert not ok and why == "below_min_value_150000"


def test_the_legacy_fixed_mode_still_opens_its_own_size():
    """Fixed sizing's ticket (Rs 1,25,000) is below the floor; the floor never
    exceeds a trade's own size, so the Settings choice keeps working."""
    from trading.risk import min_trade_value
    cfg = _cfg()
    assert min_trade_value(cfg, 125_000.0) == 125_000.0
    ok, why, qty = _gate(cfg, cash=BOOK, want=125_000.0, price=500.0)
    assert ok and qty == 250
