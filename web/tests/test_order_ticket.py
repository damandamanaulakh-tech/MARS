"""Owner order ticket — qty/market/limit/stop/target + resting limit
orders with day validity (user ask 2026-07-21)."""

from fastapi.testclient import TestClient

from trading import orders as ord_mod
from web import auth as auth_mod, state
from web.main import app


def _owner() -> TestClient:
    c = TestClient(app)
    c.post("/login", data={"email": "owner@sourceborn.in",
                           "password": auth_mod._SEEDED_PWD})
    return c


def _cleanup(p, tkr):
    pos = p.positions.pop(tkr, None)
    if pos is not None:
        p.cash += pos.qty * pos.entry_price
    p.pending_orders.clear()


def _mock_quotes(monkeypatch, price):
    import trading.quote_router as qr
    monkeypatch.setattr(qr, "fetch_quotes",
                        lambda tickers, upstox_client=None:
                        {t: price for t in tickers})


def test_market_with_custom_qty_stop_target(monkeypatch):
    _mock_quotes(monkeypatch, 500.0)
    p = state.portfolio()
    try:
        r = _owner().post("/scan/buy",
                          data={"ticker": "TCS", "qty": "3",
                                "stop_pct": "8", "target_pct": "20"},
                          follow_redirects=False)
        assert r.status_code == 303 and "/positions" in r.headers["location"]
        pos = p.positions["TCS.NS"]
        assert pos.qty == 3
        assert abs(pos.stop_price - 460.0) < 0.01     # −8% of 500
        assert abs(pos.target_price - 600.0) < 0.01   # +20% of 500
        assert "Custom levels" in pos.reasoning
    finally:
        _cleanup(p, "TCS.NS")


def test_bad_numbers_rejected():
    r = _owner().post("/scan/buy", data={"ticker": "TCS", "qty": "three"},
                      follow_redirects=False)
    assert "bad_ticket" in r.headers["location"]
    r = _owner().post("/scan/buy", data={"ticker": "TCS", "order_type": "limit",
                                         "limit_price": ""},
                      follow_redirects=False)
    assert "bad_ticket" in r.headers["location"]


def test_limit_rests_then_fills_when_touched(monkeypatch):
    p = state.portfolio()
    try:
        r = _owner().post("/scan/buy",
                          data={"ticker": "INFY", "order_type": "limit",
                                "limit_price": "450", "qty": "2",
                                "stop_pct": "5", "target_pct": "10"},
                          follow_redirects=False)
        assert "order_placed" in r.headers["location"]
        assert "INFY.NS" not in p.positions
        o = p.pending_orders[-1]
        assert o["status"] == "open" and o["limit_price"] == 450.0
        assert any(e.get("action") == "OWNER_ORDER_PLACED"
                   for e in p.decision_journal)

        _mock_quotes(monkeypatch, 460.0)              # above limit — no fill
        assert ord_mod.process_open_orders(state.engine()) == []
        assert o["status"] == "open"

        _mock_quotes(monkeypatch, 440.0)              # touched — fills at quote
        filled = ord_mod.process_open_orders(state.engine())
        assert filled == [o["id"]] and o["status"] == "filled"
        pos = p.positions["INFY.NS"]
        assert pos.qty == 2 and pos.entry_price == 440.0
        assert abs(pos.stop_price - 418.0) < 0.01     # −5% of fill
        assert abs(pos.target_price - 484.0) < 0.01   # +10% of fill
        assert any(e.get("action") == "OWNER_ORDER_FILLED"
                   for e in p.decision_journal)
    finally:
        _cleanup(p, "INFY.NS")


def test_limit_auto_qty_sizes_at_fill(monkeypatch):
    p = state.portfolio()
    cfg = state.cfg()
    try:
        _owner().post("/scan/buy", data={"ticker": "SBIN", "order_type": "limit",
                                         "limit_price": "100"},
                      follow_redirects=False)
        _mock_quotes(monkeypatch, 90.0)
        ord_mod.process_open_orders(state.engine())
        pos = p.positions["SBIN.NS"]
        marks = {q.ticker: q.entry_price for q in p.positions.values()}
        assert pos.qty >= 1
        assert pos.qty * 90.0 <= cfg.max_position_pct * p.equity(marks) + 90.0
    finally:
        _cleanup(p, "SBIN.NS")


def test_day_validity_expiry_and_cancel():
    p = state.portfolio()
    try:
        c = _owner()
        c.post("/scan/buy", data={"ticker": "WIPRO", "order_type": "limit",
                                  "limit_price": "200"}, follow_redirects=False)
        c.post("/scan/buy", data={"ticker": "LT", "order_type": "limit",
                                  "limit_price": "300"}, follow_redirects=False)
        first, second = p.pending_orders[-2], p.pending_orders[-1]

        r = c.post("/scan/order/cancel", data={"order_id": first["id"]},
                   follow_redirects=False)
        assert "order_cancelled" in r.headers["location"]
        assert first["status"] == "cancelled"

        assert ord_mod.expire_day_orders(p) == 1
        assert second["status"] == "expired"
        assert any(e.get("action") == "OWNER_ORDER_EXPIRED"
                   for e in p.decision_journal)
    finally:
        _cleanup(p, "WIPRO.NS")
        _cleanup(p, "LT.NS")
