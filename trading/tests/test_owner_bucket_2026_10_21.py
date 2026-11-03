"""Owner order 2026-11-03 supersedes the 2026-10-21 separate owner bucket.
Owner entries spend their stored strategy share. Provenance, Ocean, reserve,
heat and owner exit/rotation protections remain explicit.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from trading import category_budget as cb
from trading.config import Config
from trading.paper import PaperPosition
from trading.risk import RiskState, can_open

MOM = "nifty200_momentum"
BOOK = 5_00_00_000.0


def _pos(ticker, rupees, mode=MOM, tags=None, price=100.0):
    return PaperPosition(
        ticker=ticker, qty=int(rupees // price), entry_price=price,
        entry_date=datetime(2026, 9, 24), sector="IT", peak_price=price,
        entry_score=70.0, reasoning="fixture", mode=mode,
        stop_price=price * 0.95, target_price=price * 1.2,
        tags=list(tags or []))


def _cfg(tmp_path=None):
    cfg = Config(state_dir=str(tmp_path or "/tmp/sb_owner_bucket"), mode=MOM)
    cfg.apply_mode_preset()
    cfg.cash_reserve_pct = 0.0
    return cfg


def _desk_book(rupees=2_50_00_000.0, n=5):
    """Rs 2.5 Cr of Buy Desk fills (owner_pick + buy_desk tags, momentum
    mode — exactly what web/buy_desk.py writes), no scan momentum position."""
    each = rupees / n
    return {f"BD{i}.NS": _pos(f"BD{i}.NS", each, tags=["owner_pick", "buy_desk"])
            for i in range(n)}


def _gate(cfg, positions, category, cash, want=1_50_000.0, price=100.0):
    risk = RiskState(cfg=cfg, starting_capital=BOOK)
    return can_open(risk, cash, BOOK, positions, "NEW.NS", price, cfg,
                    notional_override=want, category=category)


# ------------------------------------------------------------ the bucket

@pytest.mark.parametrize("mode", list(cb.SHARE_FIELDS))
@pytest.mark.parametrize("tags", [["owner_pick"], ["owner_pick", "buy_desk"], ["buy_desk"]])
def test_owner_tagged_positions_count_in_their_strategy(mode, tags):
    pos = _pos("A", 1e5, mode=mode, tags=tags)
    assert cb.category_of(pos) == mode
    assert cb.is_owner(pos)
    assert cb.used([pos], mode) == 1e5
    assert cb.used([pos], cb.OWNER) == 0
    assert cb.category_of(_pos("A", 1e5, mode="", tags=tags)) == MOM


def test_every_owner_entry_path_writes_the_tag():
    """Provenance remains tagged on every owner entry path: the BUY button, the limit-order fill and the Buy Desk."""
    import inspect

    from trading import orders
    from web import buy_desk
    from web.routes import scan_routes

    def _code(fn):
        return "\n".join(l for l in inspect.getsource(fn).splitlines()
                         if not l.strip().startswith("#"))
    assert 'tags.append("owner_pick")' in _code(scan_routes.owner_buy)
    assert 'tags.append("owner_pick")' in _code(orders._fill_locked)
    assert 'tags.append("buy_desk")' in _code(buy_desk.execute)


def test_scan_positions_and_ocean_are_untouched():
    assert cb.category_of(_pos("A", 1e5, mode="swing")) == "swing"
    assert cb.category_of(_pos("A", 1e5, mode="")) == MOM
    assert cb.category_of(_pos("A", 1e5, mode=MOM, tags=["ocean"])) == cb.OCEAN
    cfg = _cfg()
    book = {"OC.NS": _pos("OC.NS", 2_10_00_000.0, tags=["ocean"])}
    ok, why, _ = _gate(cfg, book, MOM, cash=BOOK - 2_10_00_000.0)
    assert ok, why                                  # Ocean spends no share
    assert cb.usage(cfg, book, BOOK)[0]["used"] == 0.0


# ---------------------------------------------------- shared strategy budget

def test_buy_desk_fills_use_momentums_share_without_forced_sale():
    cfg = _cfg()
    book = _desk_book()
    before = dict(book)
    assert cb.used(book.values(), MOM) == pytest.approx(2_50_00_000.0)
    assert cb.room(cfg, book, MOM, BOOK) == 0
    assert cb.used(book.values(), cb.OWNER) == 0
    rows = {r["category"]: r for r in cb.usage(cfg, book, BOOK)}
    assert rows[MOM]["used"] == pytest.approx(2_50_00_000.0)
    assert rows[MOM]["open"] == 5 and rows[MOM]["full"]
    assert rows[MOM]["over_limit"] == pytest.approx(50_00_000.0)
    assert book == before


def test_a_momentum_scan_entry_waits_when_owner_entries_used_its_share():
    cfg = _cfg()
    book = _desk_book()
    ok, why, qty = _gate(cfg, book, MOM, cash=BOOK - 2_50_00_000.0)
    assert not ok and why == "category_budget_full" and qty == 0
    # Other strategies retain their own shares; attribution transfers no cash.
    for mode in ("intraday", "short", "swing", "positional"):
        ok, why, qty = _gate(cfg, book, mode, cash=BOOK - 2_50_00_000.0)
        assert ok, (mode, why)
        assert qty * 100 == pytest.approx(1_50_000.0)


def test_the_same_fills_counted_as_momentum_were_refused():
    """The old convention, reproduced: untagged momentum-mode positions of
    the same size are momentum's, and the share is over-full."""
    cfg = _cfg()
    book = {k: _pos(k, 50_00_000.0) for k in ("A.NS", "B.NS", "C.NS", "D.NS", "E.NS")}
    assert cb.room(cfg, book, MOM, BOOK) == 0.0
    ok, why, _ = _gate(cfg, book, MOM, cash=BOOK - 2_50_00_000.0)
    assert not ok and why == "category_budget_full"


# ------------------------------------------------- combined allocation

def test_owner_and_automatic_holdings_consume_one_combined_segment_share():
    cfg = _cfg()
    book = _desk_book(rupees=1_00_00_000.0)
    book["AUTO.NS"] = _pos("AUTO.NS", 99_00_000.0)
    assert cb.room(cfg, book, MOM, BOOK) == pytest.approx(1_00_000.0)
    ok, why, qty = _gate(cfg, book, MOM, cash=BOOK - 1_99_00_000.0)
    assert not ok and why == "category_budget_full" and qty == 0


def test_owner_positions_still_count_in_the_cap_a_scan_entry_meets():
    """Owner holdings also count in the unchanged global reserve and heat."""
    cfg = _cfg()
    cfg.cash_reserve_pct = 0.20                      # correction reserve
    book = _desk_book(rupees=4_00_00_000.0)          # 80% of 5 Cr deployed
    assert cb.room(cfg, book, MOM, BOOK) == 0
    ok, why, _ = _gate(cfg, book, MOM, cash=BOOK - 4_00_00_000.0)
    assert not ok and why == "reserve_breached"
    cfg = _cfg()
    cfg.max_portfolio_heat_pct = 0.001
    ok, why, _ = _gate(cfg, _desk_book(), "swing", cash=BOOK - 2_50_00_000.0)
    assert not ok and why == "portfolio_heat_cap"


def test_an_owner_limit_fill_cannot_overfill_its_segment(tmp_path):
    from trading import orders
    from trading.engine import TradingEngine
    from trading.paper import Portfolio
    cfg = _cfg(tmp_path)
    p = Portfolio.fresh(cfg)
    p.positions = _desk_book(rupees=2_50_00_000.0)
    p.cash = BOOK - 2_50_00_000.0
    before_cash = p.cash
    eng = TradingEngine(cfg, p)
    o = orders.place_limit(p, ticker="LIM.NS", limit_price=100.0, qty=1500)
    orders._fill(eng, o, 99.5, datetime.utcnow())
    assert "LIM.NS" not in p.positions
    assert p.cash == before_cash
    assert o["status"] == "cancelled"
    assert "category_budget" in o["note"]


# ------------------------------------------------------------- unchanged

def test_rotation_still_never_sells_an_owner_position():
    """owner_pick positions stay exempt from rotation (the decay-exit rule)
    — even though their costs now share the strategy allocation."""
    import inspect

    from trading.engine import TradingEngine
    code = "\n".join(l for l in inspect.getsource(TradingEngine._rotation_exits)
                     .splitlines() if not l.strip().startswith("#"))
    assert '"owner_pick" not in (getattr(p, "tags", None) or [])' in code
    assert cb.category_of(_pos("A", 1e5, tags=["owner_pick"])) == MOM


def test_the_measured_kelly_sample_is_unchanged():
    """FINAL SET V1.3: owner BUY / Buy Desk closes stay in the MOMENTUM
    record for the measured-Kelly check. This order moves the CASH bucket
    only; the Kelly convention is deliberately not touched."""
    from trading import measured_kelly as MK

    class _T:
        def __init__(self, tags, mode=""):
            self.tags = tags
            self.mode = mode
            self.reasoning = "OWNER BUY CALL — fixture"
    assert MK.trade_category(_T(["owner_pick", "buy_desk"])) == MOM
    assert MK.trade_category(_T(["owner_pick"])) == MOM
    assert MK._OWNER_TAGS == ("owner_pick", "buy_desk")
    # and the shape every close since 2026-10-05 really has: a ClosedTrade
    # carrying the position's momentum mode AND the owner tags
    from trading.paper import ClosedTrade
    ct = ClosedTrade(
        ticker="BD.NS", qty=10, entry_date=datetime(2026, 10, 1),
        exit_date=datetime(2026, 10, 21), entry_price=100.0, exit_price=95.0,
        gross_pnl=-50.0, costs=1.0, net_pnl=-51.0, reason="STOP_LOSS",
        sector="IT", entry_score=0.0, reasoning="OWNER BUY DESK — fixture",
        tags=["owner_pick", "buy_desk"], mode=MOM)
    assert MK.trade_category(ct) == MOM
    assert cb.category_of(ct) == MOM           # budget and strategy now agree


@pytest.mark.parametrize("mode", list(cb.SHARE_FIELDS))
def test_owner_stop_loss_counts_under_the_actual_strategy(tmp_path, mode):
    from trading.engine import TradingEngine
    from trading.paper import Portfolio
    cfg = _cfg(tmp_path)
    p = Portfolio.fresh(cfg)
    p.positions["OWNER.NS"] = _pos("OWNER.NS", 1e5, mode=mode, tags=["owner_pick"])
    eng = TradingEngine(cfg, p)
    now = datetime(2026, 11, 3, 6, 0)
    eng._close("OWNER.NS", 90.0, "STOP_LOSS", now)
    assert eng.loss_tracker.by_category == {mode: 1}
    assert p.loss_by_category == {mode: 1}
    assert p.trades[-1].mode == mode
    assert p.trades[-1].tags == ["owner_pick"]
    assert p.trades[-1].reason == "STOP_LOSS"
