"""The owner's six allocations constrain every real engine entry boundary."""
from copy import deepcopy
from datetime import datetime

import pytest

from trading import category_budget as cb
from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import PaperPosition, Portfolio
from trading.portfolio_marks import remember
from trading.tests.test_audit_s4_s9_2026_10_02 import _scan_engine

MOM = "nifty200_momentum"
NOW = datetime(2026, 11, 20, 6)
BOOK = 50_000_000.


def _config(tmp_path):
    cfg = Config(state_dir=str(tmp_path), mode=MOM)
    cfg.apply_mode_preset()
    cfg.cash_reserve_pct = 0.
    cfg.budget_share_momentum = .30
    cfg.budget_share_intraday = .10
    cfg.budget_share_short = .24
    cfg.budget_share_swing = .24
    cfg.budget_share_positional = .10
    cfg.budget_share_ocean = .02
    return cfg


def _position(ticker, cost, mode=MOM, tags=()):
    pos = PaperPosition(ticker=ticker, qty=int(cost / 100.), entry_price=100.,
        entry_date=NOW, sector="Test", peak_price=100., entry_score=80.,
        reasoning="isolated allocation fixture", mode=mode, tags=list(tags),
        stop_price=90., target_price=120.)
    remember(pos, 100., "quote_response", NOW)
    return pos


def _engine(tmp_path, monkeypatch, positions, cash):
    cfg = _config(tmp_path)
    p = Portfolio.fresh(cfg)
    p.positions = {pos.ticker: pos for pos in positions}
    p.cash = cash
    e = TradingEngine(cfg, p)
    monkeypatch.setattr(e, "_trading_allowed", lambda now=None: True)
    return e


def _quotes(monkeypatch, values):
    monkeypatch.setattr("trading.engine.routed_fetch_quotes", lambda tickers, **k:
        {t: values[t] for t in tickers if t in values})


@pytest.mark.parametrize("spent,expected_buy", [(1_000_000., False), (800_000., True)])
def test_normal_scan_applies_the_ocean_share_and_explains_its_own_category(
        tmp_path, monkeypatch, spent, expected_buy):
    e = _scan_engine(tmp_path, monkeypatch, event=None)
    e.cfg.budget_share_ocean = .02
    e.cfg.rotation_auto_enabled = False
    e.portfolio.positions = {"OC.NS": _position("OC.NS", spent, tags=("ocean",))}
    e.portfolio.cash = BOOK - spent
    monkeypatch.setattr(e, "_breadth_gate_blocks", lambda *a, **k: True)
    def ocean(regime, history, universe, now):
        frame = history["UPP.NS"]
        return [{"ticker": "UPP.NS", "score": 80., "ocean": True,
                 "timing_pass": True, "components": {}, "history": frame,
                 "last_close": float(frame["Close"].iloc[-1]), "reasoning": "Ocean fixture"}]
    monkeypatch.setattr(e, "_ocean_picks", ocean)
    e.scan()
    assert ("UPP.NS" in e.portfolio.positions) == expected_buy
    if expected_buy:
        pos = e.portfolio.positions["UPP.NS"]
        assert "ocean" in pos.tags
        assert pos.qty * pos.entry_price <= e.cfg.ocean_position_value
        assert cb.used(e.portfolio.positions.values(), "ocean") <= .02 * BOOK
    else:
        refused = [s for s in e.portfolio.last_scan_skipped
                   if s["ticker"] == "UPP.NS"]
        assert refused == [{"ticker": "UPP.NS", "reason": "category_budget_full", "category": "ocean"}]
        record = next(row for row in e.portfolio.decision_journal
                      if row.get("action") == "CATEGORY_BUDGET_FULL")
        assert record["category"] == "ocean"
        assert record["share"] == .02
        assert not e.portfolio.trades


def test_ocean_full_cannot_rotate_an_unrelated_holding(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, [
        _position("OCEAN.NS", 1_000_000., tags=("ocean", "owner_pick")),
        _position("SWING.NS", 48_950_000., mode="swing")], 50_000.)
    _quotes(monkeypatch, {"SWING.NS": 119.5, "OCEAN.NS": 119.})
    assert e._rotation_exits([{"ticker": "NEW.NS", "score": 80., "ocean": True}], NOW) == []
    assert set(e.portfolio.positions) == {"OCEAN.NS", "SWING.NS"}
    assert not e.portfolio.trades


def test_ocean_rotation_can_free_its_own_share(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, [
        _position("OCEAN.NS", 1_000_000., tags=("ocean",)),
        _position("SWING.NS", 48_950_000., mode="swing")], 50_000.)
    _quotes(monkeypatch, {"SWING.NS": 119.5, "OCEAN.NS": 119.})
    assert e._rotation_exits([{"ticker": "NEW.NS", "score": 80., "ocean": True}], NOW) == ["OCEAN.NS"]
    assert "SWING.NS" in e.portfolio.positions
    assert e.portfolio.trades[-1].reason == "ROTATION_EXIT"
    line = next(row for row in e.portfolio.decision_journal if row.get("action") == "ROTATION_EXIT")
    assert line["short_of"] == "category_share" and line["fund_ticker"] == "NEW.NS"


@pytest.mark.parametrize("ocean_first", [False, True])
def test_mixed_scan_cannot_use_a_blocked_ocean_pick_to_sell_an_extra_name(tmp_path, monkeypatch, ocean_first):
    # Momentum has room for its 1.5L ticket; Ocean is full. Cash covers the
    # Momentum ticket already. The extra Ocean ticket must not create a sale.
    e = _engine(tmp_path, monkeypatch, [
        _position("OCEAN.NS", 1_000_000., tags=("ocean", "owner_pick")),
        _position("SWING.NS", 48_840_000., mode="swing")], 160_000.)
    e.cfg.rotation_cash_trigger_pct = 0.
    _quotes(monkeypatch, {"SWING.NS": 119.5, "OCEAN.NS": 119.})
    picks = [{"ticker": "MOMNEW.NS", "score": 63.},
             {"ticker": "OCNEW.NS", "score": 80., "ocean": True}]
    if ocean_first:
        picks.reverse()
    assert e._rotation_exits(picks, NOW) == []
    assert not e.portfolio.trades


def test_locked_booking_rechecks_room_consumed_after_eligibility(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, [
        _position("OC.NS", 800_000., tags=("ocean",))], BOOK-800_000.)
    from trading.risk import can_open
    ok, why, qty = can_open(e.risk, e.portfolio.cash, BOOK, e.portfolio.positions,
        "NEW.NS", 100., e.cfg, notional_override=168_750., category="ocean")
    assert ok, why
    # Another entry consumes the approved room before this one acquires lock.
    e.portfolio.positions["OTHER.NS"] = _position("OTHER.NS", 200_000., tags=("ocean",))
    e.portfolio.cash -= 200_000.
    before = (e.portfolio.cash, deepcopy(e.portfolio.positions), len(e.portfolio.decision_journal))
    with pytest.raises(ValueError, match="^category_budget_full$"):
        e._open({"ticker": "NEW.NS", "score": 80., "ocean": True}, qty, 100., NOW)
    assert e.portfolio.cash == before[0]
    assert e.portfolio.positions == before[1]
    assert len(e.portfolio.decision_journal) == before[2]
    assert not e.portfolio.trades


def test_full_segment_can_still_close_protectively(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, [
        _position("OC.NS", 1_100_000., tags=("ocean",))], BOOK-1_100_000.)
    e._close("OC.NS", 90., "STOP_LOSS", NOW)
    assert not e.portfolio.positions
    assert e.portfolio.trades[-1].reason == "STOP_LOSS"


def test_budget_refusal_journal_separates_regular_and_ocean(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, [], BOOK)
    refused = [{"ticker": "M.NS", "reason": "category_budget_full", "category": MOM},
               {"ticker": "O.NS", "reason": "category_budget_full", "category": "ocean"}]
    e._journal_budget_full(refused, NOW)
    e._journal_budget_full(refused, NOW)
    rows = [r for r in e.portfolio.decision_journal if r.get("action") == "CATEGORY_BUDGET_FULL"]
    assert {r["category"]: r["refused"] for r in rows} == {MOM: ["M.NS"], "ocean": ["O.NS"]}
    assert len(rows) == 2


@pytest.mark.parametrize("category,tags,initial_cost", [
    (MOM, (), 15_000_000.), ("swing", ("owner_pick",), 12_000_000.),
    (MOM, ("ocean",), 1_000_000.),
])
def test_refresh_pyramid_cannot_bypass_the_holdings_segment_budget(
        tmp_path, monkeypatch, category, tags, initial_cost):
    pos = _position("WIN.NS", initial_cost, mode=category, tags=tags)
    e = _engine(tmp_path, monkeypatch, [pos], BOOK-initial_cost)
    e.cfg.disable_exits = True
    e.cfg.pyramid_enabled = True
    monkeypatch.setattr(e, "_absorb_corporate_actions", lambda *a: None)
    monkeypatch.setattr(e, "_danger_pass", lambda *a: None)
    monkeypatch.setattr(e, "_valuation_pass", lambda *a, **k: None)
    monkeypatch.setattr("trading.engine.fetch_daily", lambda *a, **k: {})
    _quotes(monkeypatch, {"WIN.NS": 110.})
    before = (pos.qty, pos.entry_price, pos.stop_price, pos.target_price, e.portfolio.cash)
    e.refresh()
    assert (pos.qty, pos.entry_price, pos.stop_price, pos.target_price, e.portfolio.cash) == before
    row = next(r for r in e.portfolio.decision_journal if r.get("action") == "PYRAMID_CATEGORY_BUDGET_FULL")
    assert row["category"] == ("ocean" if "ocean" in tags else category)
    assert row["wanted"] > row["room"]
    assert not e.portfolio.trades


@pytest.mark.parametrize("consume_after_plan", [False, True])
def test_pyramid_room_is_checked_at_booking_and_allows_a_fitting_tranche(
        tmp_path, monkeypatch, consume_after_plan):
    pos = _position("WIN.NS", 500_000., tags=("ocean",))
    e = _engine(tmp_path, monkeypatch, [pos], BOOK-500_000.)
    e.cfg.disable_exits = True
    e.cfg.pyramid_enabled = True
    monkeypatch.setattr(e, "_absorb_corporate_actions", lambda *a: None)
    monkeypatch.setattr(e, "_danger_pass", lambda *a: None)
    monkeypatch.setattr(e, "_valuation_pass", lambda *a, **k: None)
    monkeypatch.setattr("trading.engine.fetch_daily", lambda *a, **k: {})
    _quotes(monkeypatch, {"WIN.NS": 110.})
    if consume_after_plan:
        from trading.pyramid import pyramid_plan
        def intervening_entry(*args):
            plan = pyramid_plan(*args)
            e.portfolio.positions["OTHER.NS"] = _position("OTHER.NS", 500_000., tags=("ocean",))
            e.portfolio.cash -= 500_000.
            return plan
        monkeypatch.setattr("trading.pyramid.pyramid_plan", intervening_entry)
    e.refresh()
    assert pos.qty == (5000 if consume_after_plan else 7500)
    assert pos.pyramid_adds == (0 if consume_after_plan else 1)
    assert (pos.stop_price, pos.target_price) == (90., 120.)
    if consume_after_plan:
        assert any(r.get("action") == "PYRAMID_CATEGORY_BUDGET_FULL"
                   for r in e.portfolio.decision_journal)
    else:
        assert cb.used(e.portfolio.positions.values(), "ocean") <= cb.limit(e.cfg, "ocean", e.portfolio.equity({}))
        assert pos.cost_basis["orders"][-1]["fee"]["total"] > 0


@pytest.mark.parametrize("ocean_first", [False, True])
@pytest.mark.parametrize("cash,expect_rotation", [(3_000_000., True), (3_200_000., False)])
def test_mixed_rotation_cannot_spend_the_same_cash_for_both_groups(
        tmp_path, monkeypatch, ocean_first, cash, expect_rotation):
    # Both shares have room; each group's need separately fits cash. The
    # combined 20*1.5L + 1.6875L needs one rotation only on the 30L book.
    e = _engine(tmp_path, monkeypatch, [
        _position("HOLD.NS", BOOK-cash-200_000., mode="swing", tags=("owner_pick",)),
        _position("ROT.NS", 200_000., mode="swing")], cash)
    _quotes(monkeypatch, {"ROT.NS": 119.})
    regular = [{"ticker": f"NEW{i}.NS", "score": 63.} for i in range(20)]
    ocean = [{"ticker": "OCNEW.NS", "score": 80., "ocean": True}]
    picks = ocean + regular if ocean_first else regular + ocean
    rotated = e._rotation_exits(picks, NOW)
    assert rotated == (["ROT.NS"] if expect_rotation else [])
    if expect_rotation:
        # Reservations are estimates only: cash changes solely by the sale.
        assert e.portfolio.cash == pytest.approx(cash + e.portfolio.trades[-1].cost_evidence["cash_credit"])
        assert e.portfolio.cash >= 20 * 150_000. + 168_750.
        line = next(r for r in e.portfolio.decision_journal if r.get("action") == "ROTATION_EXIT")
        assert line["cash_reserved_for_earlier_picks"] == (168_750. if ocean_first else 3_000_000.)
        assert line["short_of"] == "cash"
    else:
        assert e.portfolio.cash == cash
        assert not e.portfolio.trades
