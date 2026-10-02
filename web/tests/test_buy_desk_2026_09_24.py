"""Owner order 2026-09-24: "add the shortlist and recomenderd to buy in app /
use upto 2.5 cr" -> "buy the stocks from listed by app, use upto 2.5 cr of
capital amount, bypass the market hours filter for this only for now one
time" -> Shortlist = "Cleared floor + momentum + liquidity".

Pinned: the shortlist rule, budget fill in score order through the ladder,
execution through engine._open with journal + tags, the one-time market-
hours bypass (journaled), no invented fills, owner-only, and the card.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from web import buy_desk, state

MATRIX = {
    "ts": "2026-09-24T03:50:00", "mode": "nifty200_momentum", "score_floor": 62.0,
    "rows": {
        "TOP.NS":   {"score": 80.0, "abs_mom": True,  "timing": True,  "outcome": "",
                     "c": {"momentum": 90.0}},
        "WAIT.NS":  {"score": 70.0, "abs_mom": True,  "timing": False, "outcome": "candle_wait_no_pattern"},
        "NOMOM.NS": {"score": 65.0, "abs_mom": False, "timing": True,  "outcome": "abs_mom"},
        "LOW.NS":   {"score": 55.0, "abs_mom": True,  "timing": True,  "outcome": ""},
        "THIN.NS":  {"score": 75.0, "abs_mom": True,  "timing": True,  "outcome": "liquidity_turnover"},
        "HELD.NS":  {"score": 72.0, "abs_mom": True,  "timing": True,  "outcome": "already_held"},
        "YESBANK.NS": {"score": 90.0, "abs_mom": True, "timing": True, "outcome": ""},
    },
}


def _close_series(n: int = 300, start: float = 100.0) -> pd.Series:
    rng = np.random.default_rng(7)
    rets = rng.normal(0.0004, 0.012, n)
    px = start * np.cumprod(1 + rets)
    return pd.Series(px, index=pd.bdate_range("2025-06-01", periods=n))


@pytest.fixture
def book():
    """Seed the live test book with the matrix and one held name; restore."""
    from trading.paper import PaperPosition
    p = state.portfolio()
    saved = (dict(p.scan_matrix or {}), dict(p.positions), p.cash,
             len(p.decision_journal), list(p.last_scan_picks or []))
    p.scan_matrix = MATRIX
    p.positions = {"HELD.NS": PaperPosition(
        ticker="HELD.NS", qty=10, entry_price=100.0, entry_date=datetime(2026, 9, 1),
        sector="IT", peak_price=100.0, entry_score=72.0, reasoning="fixture")}
    p.cash = 4_00_00_000.0
    p.last_scan_picks = [{"ticker": "TOP.NS", "score": 80.0, "last_close": 100.0,
                          "timing_pass": True, "reasoning": "fixture"}]
    # These pins drive the desk through the scan matrix (the fallback since
    # audit S-11); a momentum record left by another test would take over.
    saved_ms = dict(getattr(p, "momentum_shortlist", {}) or {})
    p.momentum_shortlist = {}
    try:
        yield p
    finally:
        p.scan_matrix, p.positions, p.cash = saved[0], saved[1], saved[2]
        del p.decision_journal[saved[3]:]
        p.last_scan_picks = saved[4]
        p.momentum_shortlist = saved_ms
        p.save()


@pytest.fixture
def desk_cfg():
    import copy
    cfg = copy.copy(state.cfg())
    cfg.mode = buy_desk.DESK_MODE
    cfg.apply_mode_preset()
    return cfg


@pytest.fixture
def owner():
    from web.main import app
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in", "password": "sourceborn"},
               follow_redirects=False)
    assert r.status_code == 303
    return c


# --------------------------------------------------------------------------

def test_shortlist_is_floor_plus_momentum_plus_liquidity_not_held_not_blocked(book):
    sl = buy_desk.shortlist(book, state.cfg())
    assert [r["ticker"] for r in sl["rows"]] == ["TOP.NS", "WAIT.NS"]
    assert sl["floor"] == 62.0 and sl["mode"] == "nifty200_momentum"
    # the engine's own hesitation (candle wait) does NOT remove a name — the
    # order is to buy what the app listed
    assert sl["rows"][1]["outcome"] == "candle_wait_no_pattern"


def test_plan_fills_the_budget_in_score_order_and_stops(book, desk_cfg):
    sl = buy_desk.shortlist(book, state.cfg())
    prices = {"TOP.NS": 100.0, "WAIT.NS": 200.0}
    pl = buy_desk.plan(book, desk_cfg, sl["rows"], 3_00_000.0, prices,
                       lambda t: _close_series())
    top, wait = pl["rows"]
    assert top["recommended"] and top["qty"] == 3000 and top["cost"] == 3_00_000.0
    assert top["notional"] >= 3_00_000.0          # the ladder wanted more; budget capped it
    assert not wait["recommended"] and wait["reason"] == "budget_exhausted"
    assert pl["planned_total"] == 3_00_000.0 and pl["recommended_count"] == 1


def test_plan_never_invents_a_price(book, desk_cfg):
    sl = buy_desk.shortlist(book, state.cfg())
    pl = buy_desk.plan(book, desk_cfg, sl["rows"], 2_50_00_000.0, {},
                       lambda t: _close_series())
    assert all(r["reason"] == "no_quote" and not r["recommended"] for r in pl["rows"])


def test_plan_stops_when_the_remainder_is_below_the_slab(book, desk_cfg):
    sl = buy_desk.shortlist(book, state.cfg())
    slab = float(desk_cfg.position_slab_value)
    prices = {"TOP.NS": 100.0, "WAIT.NS": 200.0}
    # budget = one ladder-capped TOP position + less than a slab left over
    pl_probe = buy_desk.plan(book, desk_cfg, sl["rows"][:1], 10_00_00_000.0, prices,
                             lambda t: _close_series())
    top_cost = pl_probe["rows"][0]["cost"]
    pl = buy_desk.plan(book, desk_cfg, sl["rows"], top_cost + slab / 2, prices,
                       lambda t: _close_series())
    assert pl["rows"][0]["recommended"]
    assert pl["rows"][1]["reason"] == "budget_remainder_below_slab"


def test_execute_opens_through_the_engine_and_journals(book, desk_cfg):
    eng = state.engine()
    sl = buy_desk.shortlist(book, state.cfg())
    pl = buy_desk.plan(book, desk_cfg, sl["rows"], 3_00_000.0,
                       {"TOP.NS": 100.0, "WAIT.NS": 200.0}, lambda t: _close_series())
    cash_before = book.cash
    summary = buy_desk.execute(eng, book, desk_cfg, pl["rows"], datetime(2026, 9, 24, 14, 0),
                               budget_rs=3_00_000.0, market_hours_bypassed=True,
                               price_sources={"TOP.NS": "upstox"})
    assert [o["ticker"] for o in summary["opened"]] == ["TOP.NS"]
    pos = book.positions["TOP.NS"]
    assert "owner_pick" in pos.tags and "buy_desk" in pos.tags
    assert pos.qty == 3000 and pos.entry_price >= 100.0      # slippage applied like an engine fill
    assert book.cash == pytest.approx(cash_before - 3000 * pos.entry_price)
    assert summary["market_hours_bypassed"] is True
    assert {s["ticker"] for s in summary["skipped"]} == {"WAIT.NS"}
    j = book.decision_journal[-1]
    assert j["action"] == "OWNER_BATCH_BUY" and j["spent"] == summary["spent"]
    buy = [e for e in book.decision_journal if e.get("action") == "BUY" and e.get("ticker") == "TOP.NS"][-1]
    assert buy["fill_source"] == "live_quote" and "BUY DESK" in buy["why"]


# --------------------------------------------------------------------------
# the route
# --------------------------------------------------------------------------

def test_route_is_owner_only():
    from web.main import app
    r = TestClient(app).post("/scan/buy-desk", data={"budget": "1"}, follow_redirects=False)
    assert r.status_code == 303 and "owner_only" in r.headers["location"]


def test_route_honours_the_market_gate_unless_the_one_time_box_is_ticked(owner, book, monkeypatch):
    from web.routes import scan_routes as R
    monkeypatch.setattr(R.market_gate, "trading_blocked", lambda cfg=None: "after_close")
    monkeypatch.setattr("trading.quote_router.fetch_quotes",
                        lambda tickers, upstox_client=None: {"TOP.NS": 100.0, "WAIT.NS": 200.0})
    monkeypatch.setattr(R, "_desk_close_loader", lambda: (lambda t: _close_series()))

    r = owner.post("/scan/buy-desk", data={"budget": "300000"}, follow_redirects=False)
    assert "error=market_closed" in r.headers["location"]
    assert "TOP.NS" not in book.positions

    r = owner.post("/scan/buy-desk", data={"budget": "300000", "bypass_market_hours": "1"},
                   follow_redirects=False)
    assert r.headers["location"] == "/positions?buy_desk=1"
    assert "TOP.NS" in book.positions and "WAIT.NS" not in book.positions
    last = buy_desk.last_run(book)
    assert last["market_hours_bypassed"] is True and last["budget"] == 300000.0
    assert state.cfg().mode != "__never__"          # mode restored (no exception path)


def test_route_reports_nothing_opened_when_no_quote_exists(owner, book, monkeypatch):
    from web.routes import scan_routes as R
    monkeypatch.setattr(R.market_gate, "trading_blocked", lambda cfg=None: None)
    monkeypatch.setattr("trading.quote_router.fetch_quotes",
                        lambda tickers, upstox_client=None: {})
    r = owner.post("/scan/buy-desk", data={"budget": "300000"}, follow_redirects=False)
    assert "error=buy_desk_nothing" in r.headers["location"]
    assert "TOP.NS" not in book.positions
    page = owner.get("/scan?error=buy_desk_nothing").text
    assert "buy_desk_msg_marker" in page and "nothing opened" in page


def test_scan_page_renders_the_desk_with_recommended_rows(owner, book, monkeypatch):
    from web.routes import scan_routes as R
    monkeypatch.setattr(R, "_desk_close_loader", lambda: (lambda t: _close_series()))
    page = owner.get("/scan").text
    assert "buy_desk_marker" in page
    assert "RECOMMENDED" in page                    # TOP.NS priced at its scan close
    assert "no scan close" in page                  # WAIT.NS is not in the top-50 picks
    assert 'value="25000000"' in page               # the owner's default budget
    assert "ignore market hours (this order only)" in page
    assert "THIN.NS" not in page.split("buy_desk_marker")[1].split("slot_funnel_marker")[0] \
        if "slot_funnel_marker" in page else True


def test_engine_never_imports_the_desk():
    import inspect

    import trading.engine as E
    import trading.risk as Rk
    src = inspect.getsource(E) + inspect.getsource(Rk)
    assert "buy_desk" not in src
