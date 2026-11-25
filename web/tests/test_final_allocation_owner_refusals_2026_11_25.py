"""Final allocation refusals are recorded without aborting owner workflows."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from trading import category_budget as cb, orders
from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import PaperPosition, Portfolio
from trading.portfolio_marks import remember
from web import auth, buy_desk, state
from web.main import app


NOW = datetime(2026, 11, 25, 5)


@pytest.fixture
def book(tmp_path, monkeypatch):
    cfg = Config(state_dir=str(tmp_path), starting_capital=1_000_000,
                 mode=cb.MOMENTUM, cash_reserve_pct=0.0)
    cfg.apply_mode_preset()
    cfg.cash_reserve_pct = 0.0
    p = Portfolio.fresh(cfg)
    eng = TradingEngine(cfg, portfolio=p)
    monkeypatch.setattr(p, "save", lambda: True)
    monkeypatch.setattr(eng, "_trading_allowed", lambda now=None: True)
    monkeypatch.setattr(state, "cfg", lambda: cfg)
    monkeypatch.setattr(state, "portfolio", lambda: p)
    monkeypatch.setattr(state, "engine", lambda: eng)
    monkeypatch.setattr(auth, "current_user", lambda request: {"role": "owner"})
    monkeypatch.setattr("trading.quote_router.fetch_quotes",
                        lambda tickers, **kwargs: {t: 100.0 for t in tickers})
    return TestClient(app), eng, p, cfg


def _retained_loss(p):
    pos = PaperPosition(ticker="LOSS.NS", qty=5000, entry_price=100.0,
        entry_date=NOW, sector="IT", peak_price=100.0, entry_score=80.0,
        reasoning="isolated retained-loss fixture", mode="positional",
        stop_price=90.0, target_price=120.0)
    remember(pos, 50.0, "quote_response", NOW)
    p.positions[pos.ticker] = pos
    p.cash = 500_000.0
    return pos


def _final_refusal(p, ticker):
    decisions = [row["evidence"] for row in p.decision_journal
                 if row.get("action") == "SCAN_DECISION"
                 and row.get("ticker") == ticker
                 and row.get("why") == "category_budget_full"]
    assert decisions
    decision = decisions[-1]
    assert decision["gates"]["category_budget"]["state"] == "passed"
    final = decision["gates"]["category_budget_final"]
    assert final["state"] == "failed"
    assert final["value"]["notional"] > final["value"]["room"]
    assert final["threshold"]
    return decision


def test_owner_market_handles_real_final_headroom_loss_without_http_500(book):
    client, _, p, _ = book
    held = _retained_loss(p)
    before = (p.cash, held.qty, held.stop_price, held.target_price)
    # Entry-mark equity allows 240k Swing, retained observations allow 180k.
    response = client.post("/scan/buy", data={
        "ticker": "NEW", "mode": "swing", "qty": "2000",
    }, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/scan?error=category_budget_full"
    assert (p.cash, held.qty, held.stop_price, held.target_price) == before
    assert "NEW.NS" not in p.positions
    assert _final_refusal(p, "NEW.NS")["outcome"] == "blocked"


def test_limit_refusal_keeps_order_retryable_and_processes_following_order(book):
    _, eng, p, _ = book
    held = _retained_loss(p)
    blocked = orders.place_limit(p, ticker="WAIT.NS", limit_price=101.0,
                                 qty=2000, mode="swing", now=NOW)
    small = orders.place_limit(p, ticker="SMALL.NS", limit_price=101.0,
                               qty=10, mode="short", now=NOW)
    actions = orders.process_open_orders(eng, NOW)
    assert blocked["status"] == "open"
    assert blocked["note"] == "category_budget_full"
    assert small["status"] == "filled"
    assert actions == [small["id"]]
    assert "WAIT.NS" not in p.positions
    assert _final_refusal(p, "WAIT.NS")["outcome"] == "waiting"
    # A later real valuation restores room; the same instruction can fill.
    remember(held, 100.0, "quote_response", NOW)
    assert orders.process_open_orders(eng, NOW) == [blocked["id"]]
    assert blocked["status"] == "filled"
    assert p.positions["WAIT.NS"].mode == "swing"


def test_pending_final_refusal_does_not_prevent_protective_close(book):
    _, eng, p, _ = book
    _retained_loss(p)
    order = orders.place_limit(p, ticker="WAIT.NS", limit_price=101.0,
                               qty=2000, mode="swing", now=NOW)
    orders.process_open_orders(eng, NOW)
    assert order["status"] == "open"
    eng._close("LOSS.NS", 50.0, "STOP_LOSS", NOW)
    assert "LOSS.NS" not in p.positions
    assert p.trades[-1].reason == "STOP_LOSS"
    assert order["status"] == "open"


def test_buy_desk_retains_successes_and_continues_after_final_refusal(book):
    _, eng, p, cfg = book
    _retained_loss(p)
    rows = [{"ticker": ticker, "score": 80.0, "price": 100.0,
             "qty": qty, "recommended": True}
            for ticker, qty in [("FIRST.NS", 100), ("LARGE.NS", 2800), ("LAST.NS", 100)]]
    result = buy_desk.execute(eng, p, cfg, rows, NOW, budget_rs=500_000.0,
                              market_hours_bypassed=False)
    assert [row["ticker"] for row in result["opened"]] == ["FIRST.NS", "LAST.NS"]
    assert result["skipped"] == [{"ticker": "LARGE.NS", "reason": "category_budget_full"}]
    assert "LARGE.NS" not in p.positions
    assert _final_refusal(p, "LARGE.NS")["outcome"] == "blocked"
    assert result["spent"] == pytest.approx(500_000.0 - p.cash, abs=0.01)


def test_owner_selected_segment_overrides_copied_ocean_signal(book):
    client, _, p, _ = book
    p.last_scan_picks = [{"ticker": "PICK.NS", "score": 80.0,
                         "components": {}, "ocean": True,
                         "reasoning": "prior Ocean selection"}]
    # 100k exceeds Ocean's 20k share but fits the selected Swing allocation.
    response = client.post("/scan/buy", data={
        "ticker": "PICK", "mode": "swing", "qty": "1000",
    }, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/positions"
    pos = p.positions["PICK.NS"]
    assert pos.mode == cb.category_of(pos) == "swing"
    assert pos.tags == ["owner_pick"]
    assert cb.used(p.positions.values(), cb.OCEAN) == 0.0
    assert cb.used(p.positions.values(), "swing") == 100_000.0
    assert p.last_scan_picks[0]["ocean"] is True
    assert pos.trade_evidence["entry"]["gates"]["category_budget_final"]["value"]["category"] == "swing"
