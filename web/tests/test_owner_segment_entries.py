"""Owner requests 2026-11-03: strategy identity and shared segment funds."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from trading import orders
from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import Portfolio
from web import auth, state, buy_desk
from web.main import app


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    cfg = Config(state_dir=str(tmp_path), mode="intraday", starting_capital=1_000_000)
    cfg.apply_mode_preset()
    cfg.cash_reserve_pct = 0
    p = Portfolio(cfg=cfg, cash=1_000_000)
    monkeypatch.setattr(p, "save", lambda: None)
    eng = TradingEngine(cfg, portfolio=p)
    monkeypatch.setattr(state, "cfg", lambda: cfg)
    monkeypatch.setattr(state, "portfolio", lambda: p)
    monkeypatch.setattr(state, "engine", lambda: eng)
    monkeypatch.setattr(auth, "current_user", lambda request: {"role": "owner"})
    monkeypatch.setattr("trading.quote_router.fetch_quotes",
                        lambda tickers, upstox_client=None: {t: 100.0 for t in tickers})
    return TestClient(app), eng, p, cfg


@pytest.mark.parametrize("mode", ["nifty200_momentum", "intraday", "short", "swing", "positional"])
def test_ticket_uses_selected_segment_and_preserves_config(isolated, mode):
    c, eng, p, cfg = isolated
    response = c.post("/scan/buy", data={"ticker": "TEST", "qty": "10", "mode": mode}, follow_redirects=False)
    assert response.headers["location"] == "/positions"
    assert p.positions["TEST.NS"].mode == mode
    assert "owner_pick" in p.positions["TEST.NS"].tags
    assert cfg.mode == "intraday"
    assert p.decision_journal[-1]["mode"] == mode


def test_ticket_rejects_exceeding_segment_without_debit(isolated):
    c, eng, p, cfg = isolated
    before = p.cash
    response = c.post("/scan/buy", data={"ticker": "TEST", "qty": "1501", "mode": "swing"}, follow_redirects=False)
    assert "category_budget_full" in response.headers["location"]
    assert p.cash == before and not p.positions


@pytest.mark.parametrize("mode", ["owner", "ocean", "made_up"])
def test_invalid_ticket_segment_cannot_bypass_budget(isolated, mode):
    c, eng, p, cfg = isolated
    response = c.post("/scan/buy", data={"ticker": "TEST", "mode": mode}, follow_redirects=False)
    assert "bad_segment" in response.headers["location"]
    assert not p.positions


def test_limit_keeps_selected_segment_until_fill(isolated):
    c, eng, p, cfg = isolated
    response = c.post("/scan/buy", data={"ticker": "TEST", "qty": "10", "mode": "short", "order_type": "limit", "limit_price": "101"}, follow_redirects=False)
    assert "order_placed" in response.headers["location"]
    order = p.pending_orders[-1]
    assert order["mode"] == "short"
    orders._fill(eng, order, 100.0, datetime(2026, 11, 3, 5))
    assert order["status"] == "filled"
    assert p.positions["TEST.NS"].mode == "short"
    assert cfg.mode == "intraday"


def test_limit_rechecks_budget_at_slipped_fill(isolated):
    c, eng, p, cfg = isolated
    order = orders.place_limit(p, ticker="TEST.NS", limit_price=101, qty=1500, mode="short")
    orders._fill(eng, order, 100.0, datetime(2026, 11, 3, 5))
    assert order["status"] == "cancelled"
    assert "category_budget_full" in order["note"]
    assert not p.positions and p.cash == 1_000_000


def test_buy_desk_caps_cumulative_actual_cost(isolated):
    c, eng, p, cfg = isolated
    cfg.mode = buy_desk.DESK_MODE
    cfg.apply_mode_preset()
    rows = [{"ticker": t, "score": 80, "price": 100, "qty": 1000, "recommended": True} for t in ("A.NS", "B.NS")]
    result = buy_desk.execute(eng, p, cfg, rows, datetime(2026, 11, 3, 5), budget_rs=200_000, market_hours_bypassed=False)
    assert result["spent"] <= 200_000
    assert len(result["opened"]) == 1
    assert result["skipped"][0]["reason"] == "budget_exhausted"


@pytest.mark.parametrize("field", ["qty", "stop_pct", "target_pct", "limit_price"])
def test_nonfinite_ticket_cannot_bypass_funding_checks(isolated, field):
    c, eng, p, cfg = isolated
    response = c.post("/scan/buy", data={"ticker": "TEST", field: "nan"}, follow_redirects=False)
    assert "bad_ticket" in response.headers["location"]
    assert not p.positions


@pytest.mark.parametrize("qty", ["-1", "0.5", "1.5"])
def test_invalid_quantity_never_becomes_auto_order(isolated, qty):
    c, eng, p, cfg = isolated
    response = c.post("/scan/buy", data={"ticker": "TEST", "qty": qty}, follow_redirects=False)
    assert "bad_ticket" in response.headers["location"]
    assert not p.positions and not p.decision_journal and p.cash == 1_000_000


def test_nonfinite_quote_is_refused(isolated, monkeypatch):
    c, eng, p, cfg = isolated
    monkeypatch.setattr("trading.quote_router.fetch_quotes", lambda *a, **kw: {"TEST.NS": float("nan")})
    response = c.post("/scan/buy", data={"ticker": "TEST"}, follow_redirects=False)
    assert "no_quote" in response.headers["location"]
    assert not p.positions and p.cash == 1_000_000


def test_legacy_limit_keeps_momentum_identity(isolated):
    c, eng, p, cfg = isolated
    order = orders.place_limit(p, ticker="TEST.NS", limit_price=101, qty=10)
    del order["mode"]
    orders._fill(eng, order, 100, datetime(2026, 11, 3, 5))
    assert p.positions["TEST.NS"].mode == "nifty200_momentum"


def test_desk_second_fill_uses_remaining_segment_funds(isolated):
    c, eng, p, cfg = isolated
    cfg = eng.mode_cfg(buy_desk.DESK_MODE)
    rows = [{"ticker": t, "score": 80, "price": 100, "qty": 2500, "recommended": True} for t in ("A.NS", "B.NS")]
    result = buy_desk.execute(eng, p, cfg, rows, datetime(2026, 11, 3, 5), budget_rs=1_000_000, market_hours_bypassed=False)
    assert len(result["opened"]) == 1
    assert result["skipped"][0]["reason"] == "category_budget_full"
    assert p.positions["A.NS"].mode == buy_desk.DESK_MODE
    assert cfg.mode == buy_desk.DESK_MODE


def test_owner_market_rejects_unfunded_buy_fees(isolated):
    c,eng,p,cfg=isolated
    cfg.category_budgets_enabled=False  # Isolate cash fees; budget refusals are covered above.
    p.cash=1000.; before=p.cash
    # The share notional fits; the Basic entry charge does not.
    r=c.post('/scan/buy',data={'ticker':'TEST','qty':'10','mode':'swing'},follow_redirects=False)
    assert 'costs_cash_short' in r.headers['location']
    assert p.cash==before and not p.positions


def test_resting_limit_retains_work_when_buy_fees_cannot_be_funded(isolated):
    c,eng,p,cfg=isolated
    cfg.category_budgets_enabled=False
    p.cash=1000.5  # 10 x 100.05 slipped fill, no room for its fees
    o=orders.place_limit(p,ticker='TEST.NS',limit_price=101,qty=10,mode='short')
    orders._fill(eng,o,100.,datetime(2026,11,12,5))
    assert o['status']=='open' and o['note']=='costs_cash_short'
    assert p.cash==1000.5 and not p.positions
