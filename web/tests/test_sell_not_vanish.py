"""A sold position must land in Closed Trades — it may never just disappear.

Owner, 2026-08-04: "i just did exist from divilabs, it just vanished, why not
going to closed trade, or instead of coss give sell option so it go to closed
trade".

The ✕ on every row called POST /positions/{t}/remove, which popped the position
and refunded its ENTRY cash. No ClosedTrade, no exit price, no P&L — the trade
left the owner's record completely. ✕ is gone; SELL books the trade.
"""

from datetime import datetime

from fastapi.testclient import TestClient

from web import auth as auth_mod, state
from web.main import app
from trading.paper import PaperPosition


def _client() -> TestClient:
    c = TestClient(app)
    c.post("/login", data={"email": "owner@sourceborn.in",
                           "password": auth_mod._SEEDED_PWD})
    return c


def _seed(ticker="SELLME.NS", qty=10, entry=100.0):
    p = state.portfolio()
    p.positions[ticker] = PaperPosition(
        ticker=ticker, qty=qty, entry_price=entry,
        entry_date=datetime(2026, 7, 1), sector="IT", peak_price=entry,
        entry_score=70.0, reasoning="test seed", mode="nifty200_momentum",
        stop_price=entry * 0.95, target_price=entry * 1.12,
    )
    return p


def _cleanup(p, ticker="SELLME.NS"):
    p.positions.pop(ticker, None)
    p.trades[:] = [t for t in p.trades if t.ticker != ticker]


def test_selling_at_a_typed_price_books_a_closed_trade():
    p = _seed()
    try:
        before = len(p.trades)
        r = _client().post("/positions/SELLME.NS/sell", data={"price": "120"},
                           follow_redirects=False)
        assert r.status_code == 303 and "sold=SELLME.NS" in r.headers["location"]
        assert "SELLME.NS" not in p.positions
        assert len(p.trades) == before + 1
        t = p.trades[-1]
        assert t.ticker == "SELLME.NS"
        assert t.reason == "OWNER_SELL"
        assert t.exit_price > 0
        assert t.net_pnl > 0                      # sold at 120 against a 100 entry
    finally:
        _cleanup(p)


def test_the_sold_trade_is_visible_on_the_closed_trades_page():
    p = _seed()
    try:
        c = _client()
        c.post("/positions/SELLME.NS/sell", data={"price": "120"})
        html = c.get("/trades").text
        assert "SELLME" in html, "the trade vanished instead of being recorded"
    finally:
        _cleanup(p)


def test_a_sell_without_a_price_is_not_silently_dropped():
    """No live quote in tests (conftest stubs quotes empty). The position must
    still be there afterwards — never half-closed, never deleted."""
    p = _seed()
    try:
        r = _client().post("/positions/SELLME.NS/sell", data={"price": ""},
                           follow_redirects=False)
        assert r.status_code == 303
        assert "need_price" in r.headers["location"]
        assert "SELLME.NS" in p.positions      # still open, nothing lost
        assert not [t for t in p.trades if t.ticker == "SELLME.NS"]
    finally:
        _cleanup(p)


def test_a_junk_price_sells_nothing():
    p = _seed()
    try:
        r = _client().post("/positions/SELLME.NS/sell", data={"price": "abc"},
                           follow_redirects=False)
        assert "bad_price" in r.headers["location"]
        assert "SELLME.NS" in p.positions
    finally:
        _cleanup(p)


def test_the_sell_is_journaled_with_where_the_price_came_from():
    p = _seed()
    try:
        _client().post("/positions/SELLME.NS/sell", data={"price": "120"})
        entries = [e for e in p.decision_journal
                   if e.get("action") == "OWNER_SELL" and e.get("ticker") == "SELLME.NS"]
        assert entries and entries[-1]["price_source"] == "owner_typed"
        # the engine's own SELL entry carries the full exit context
        assert [e for e in p.decision_journal
                if e.get("action") == "SELL" and e.get("ticker") == "SELLME.NS"]
    finally:
        _cleanup(p)


def test_the_cross_that_deleted_trades_is_gone_from_the_page():
    p = _seed()
    try:
        html = _client().get("/positions").text
        assert "/positions/SELLME.NS/remove" not in html, \
            "the delete-a-trade button is back on the page"
        assert "/positions/SELLME.NS/sell" in html
        assert "SELL" in html
    finally:
        _cleanup(p)


def test_the_old_remove_endpoint_can_no_longer_destroy_a_position():
    """An old bookmark or cached page must not still delete a trade."""
    p = _seed()
    try:
        r = _client().post("/positions/SELLME.NS/remove", follow_redirects=False)
        assert r.status_code == 303
        assert "SELLME.NS" in p.positions, "remove still deleted the position"
        assert not [t for t in p.trades if t.ticker == "SELLME.NS"]
    finally:
        _cleanup(p)
