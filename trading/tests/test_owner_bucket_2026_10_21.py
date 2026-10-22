"""Owner order 2026-10-21 (choice card): owner BUY / Buy Desk positions count
under their OWN "owner" bucket, outside the five category shares — like
Ocean. Before this, trading/category_budget.category_of put owner_pick /
buy_desk positions under momentum, so the 09-24 Buy Desk fills (up to
Rs 2.5 Cr) held momentum's 40% share over-full and every momentum scan pick
read category_budget_full.

NOT changed, pinned below: measured_kelly.trade_category still places owner
trades in the MOMENTUM record (FINAL SET V1.3) — the Kelly sample is
unchanged by this order.
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

def test_owner_tagged_positions_sit_in_the_owner_bucket():
    assert cb.category_of(_pos("A", 1e5, tags=["owner_pick"])) == cb.OWNER
    assert cb.category_of(_pos("A", 1e5, tags=["owner_pick", "buy_desk"])) == cb.OWNER
    assert cb.category_of(_pos("A", 1e5, tags=["buy_desk"])) == cb.OWNER
    # the rule is tag-based: the mode the ticket wrote does not matter
    assert cb.category_of(_pos("A", 1e5, mode="", tags=["owner_pick"])) == cb.OWNER
    assert cb.category_of(_pos("A", 1e5, mode="swing", tags=["owner_pick"])) == cb.OWNER
    assert cb.is_owner(_pos("A", 1e5, tags=["owner_pick"]))
    # and the owner bucket has no share, like Ocean
    cfg = _cfg()
    assert cb.share(cfg, cb.OWNER) is None
    assert cb.room(cfg, _desk_book(), cb.OWNER, BOOK) is None


def test_every_owner_entry_path_writes_the_tag():
    """The rule is tag-based, so every owner entry path must tag what it
    opens: the BUY button, the limit-order fill and the Buy Desk."""
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


# ---------------------------------------------------- momentum's share frees

def test_buy_desk_fills_no_longer_fill_momentums_share():
    """Rs 5 Cr book, Rs 2.5 Cr of Buy Desk positions, no scan momentum
    position: momentum used = 0 and its room is its WHOLE share — since
    2026-10-22 that is 40% of the Rs 2.5 Cr the owner bucket leaves, Rs 1 Cr.
    On the old convention those fills counted as momentum (Rs 2.5 Cr > the
    Rs 2 Cr share) and room read 0."""
    cfg = _cfg()
    book = _desk_book()
    assert cb.used(book.values(), MOM) == 0.0
    # REPOINTED 2026-10-22 (owner card "Shares from what is left after
    # owner + Ocean"): the share is cut from what the Rs 2.5 Cr owner
    # bucket leaves — 40% of Rs 2.5 Cr = Rs 1 Cr (was 40% of Rs 5 Cr).
    # Momentum still holds nothing and still has the whole of its share.
    assert cb.room(cfg, book, MOM, BOOK) == pytest.approx(0.40 * (BOOK - 2_50_00_000.0))
    assert cb.used(book.values(), cb.OWNER) == pytest.approx(2_50_00_000.0)
    # the Risk-page rows agree: momentum empty, owner row carries the cost
    rows = {r["category"]: r for r in cb.usage(cfg, book, BOOK)}
    assert rows[MOM]["used"] == 0.0 and rows[MOM]["open"] == 0
    assert not rows[MOM]["full"]
    own = cb.owner_usage(book)
    assert own["used"] == pytest.approx(2_50_00_000.0) and own["open"] == 5
    assert own["limit"] is None and own["full"] is False


def test_a_momentum_scan_entry_opens_beside_the_buy_desk_fills():
    """can_open(category=momentum) opens a Rs 1.5L trade. Before the order
    the same book was refused category_budget_full — proven by the twin
    below, which strips the owner tags (what the old code saw)."""
    cfg = _cfg()
    book = _desk_book()
    ok, why, qty = _gate(cfg, book, MOM, cash=BOOK - 2_50_00_000.0)
    assert ok, why
    assert qty * 100.0 == pytest.approx(1_50_000.0)


def test_the_same_fills_counted_as_momentum_were_refused():
    """The old convention, reproduced: untagged momentum-mode positions of
    the same size are momentum's, and the share is over-full."""
    cfg = _cfg()
    book = {k: _pos(k, 50_00_000.0) for k in ("A.NS", "B.NS", "C.NS", "D.NS", "E.NS")}
    assert cb.room(cfg, book, MOM, BOOK) == 0.0
    ok, why, _ = _gate(cfg, book, MOM, cash=BOOK - 2_50_00_000.0)
    assert not ok and why == "category_budget_full"


# ------------------------------------------------- owner buys: no share cap

def test_an_owner_buy_is_never_share_capped():
    """Rs 2.5 Cr already in the owner bucket: an owner entry (category=None,
    the gate owner buys would pass) still opens — no share applies."""
    cfg = _cfg()
    book = _desk_book()
    ok, why, qty = _gate(cfg, book, None, cash=BOOK - 2_50_00_000.0,
                         want=25_00_000.0)
    assert ok, why
    assert qty * 100.0 == pytest.approx(25_00_000.0)


def test_owner_positions_still_count_in_the_cap_a_scan_entry_meets():
    """Outside the shares is not outside the book: what the owner bucket
    holds still counts in the deploy cap and the heat every SCAN entry is
    judged against — the pick is refused on the book-wide limits (and since
    2026-10-22 the owner bucket also comes off the base momentum's share is
    cut from, so here its room is 0 too)."""
    cfg = _cfg()
    cfg.cash_reserve_pct = 0.20                      # correction reserve
    book = _desk_book(rupees=4_00_00_000.0)          # 80% of 5 Cr deployed
    # REPOINTED 2026-10-22 (owner card "Shares from what is left after
    # owner + Ocean"): the Rs 4 Cr owner bucket uses the whole Rs 4 Cr
    # deployable, so the share base — and momentum's room — is 0 (was 40% of
    # the Rs 4 Cr deployable). The gate's first word is still the deploy cap.
    assert cb.share_base(cfg, book, BOOK) == pytest.approx(0.0)
    assert cb.room(cfg, book, MOM, BOOK) == pytest.approx(0.0)
    ok, why, _ = _gate(cfg, book, MOM, cash=BOOK - 4_00_00_000.0)
    assert not ok and why == "reserve_breached"
    cfg = _cfg()
    cfg.max_portfolio_heat_pct = 0.001
    ok, why, _ = _gate(cfg, _desk_book(), MOM, cash=BOOK - 2_50_00_000.0)
    assert not ok and why == "portfolio_heat_cap"


def test_an_owner_limit_fill_is_checked_against_cash_only(tmp_path):
    """What the screens now say, pinned on behaviour: the owner's own entry
    paths never call risk.can_open (as before this order) — a limit fill
    opens past the deploy cap when cash covers it, and lands in the owner
    bucket."""
    from trading import orders
    from trading.engine import TradingEngine
    from trading.paper import Portfolio
    cfg = _cfg(tmp_path)
    cfg.cash_reserve_pct = 0.20
    p = Portfolio.fresh(cfg)
    p.positions = _desk_book(rupees=4_00_00_000.0)
    p.cash = BOOK - 4_00_00_000.0
    risk = RiskState(cfg=cfg, starting_capital=BOOK)
    assert can_open(risk, p.cash, BOOK, p.positions, "LIM.NS", 100.0, cfg,
                    notional_override=1_50_000.0)[1] == "reserve_breached"
    eng = TradingEngine(cfg, p)
    o = orders.place_limit(p, ticker="LIM.NS", limit_price=100.0, qty=1500)
    orders._fill(eng, o, 99.5, datetime.utcnow())
    assert "LIM.NS" in p.positions
    assert cb.category_of(p.positions["LIM.NS"]) == cb.OWNER


# ------------------------------------------------------------- unchanged

def test_rotation_still_never_sells_an_owner_position():
    """owner_pick positions stay exempt from rotation (the decay-exit rule)
    — and being in their own bucket they are never 'same category'."""
    import inspect

    from trading.engine import TradingEngine
    code = "\n".join(l for l in inspect.getsource(TradingEngine._rotation_exits)
                     .splitlines() if not l.strip().startswith("#"))
    assert '"owner_pick" not in (getattr(p, "tags", None) or [])' in code
    assert cb.category_of(_pos("A", 1e5, tags=["owner_pick"])) != MOM


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
    assert cb.category_of(ct) == cb.OWNER        # the cash bucket moved only
