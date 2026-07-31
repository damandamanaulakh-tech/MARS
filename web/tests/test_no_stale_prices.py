"""The 2026-07-31 trust failure and its fixes.

The owner found the app showing TCS at 2431 when the market was 2362, and
SENSEX two months out of date, while the header said "Upstox real-time".
Root cause was a class of flaw, not one bug: **a failed fetch was
indistinguishable from a successful one**, so dead data got served as live.

These tests exist so that class of flaw cannot come back.
"""

import time
from datetime import datetime
from unittest.mock import patch

from fastapi.testclient import TestClient

from web import auth as auth_mod, state
from web.main import app
from trading.paper import PaperPosition


def _owner() -> TestClient:
    c = TestClient(app)
    c.post("/login", data={"email": "owner@sourceborn.in",
                           "password": auth_mod._SEEDED_PWD})
    return c


# ---- the quote cache ---------------------------------------------------

def test_a_failed_fetch_never_serves_an_old_price_as_live():
    """THE bug. Old code kept one shared timestamp and, when the fetch
    failed, returned every previously-cached price with live=True."""
    state._MARKS_CACHE["prices"].clear()
    state._MARKS_CACHE["prices"]["TCS.NS"] = (2431.0, time.time() - 9999)
    with patch("trading.quote_router.fetch_quotes", return_value={}):
        px, live = state.marks(["TCS.NS"])
    assert px == {}, "a stale price leaked out as live"
    assert live is False
    assert "TCS.NS" not in state._MARKS_CACHE["prices"], "stale entry not purged"


def test_a_fresh_price_is_served_and_reused():
    state._MARKS_CACHE["prices"].clear()
    with patch("trading.quote_router.fetch_quotes", return_value={"TCS.NS": 2362.0}):
        px, live = state.marks(["TCS.NS"])
    assert px == {"TCS.NS": 2362.0} and live is True
    # reused without a refetch while still inside the age window
    with patch("trading.quote_router.fetch_quotes",
               side_effect=AssertionError("must not refetch a fresh price")):
        px2, live2 = state.marks(["TCS.NS"])
    assert px2 == px and live2 is True


def test_each_price_ages_on_its_own_clock():
    """One ticker's fresh fetch must not resurrect another's dead price."""
    state._MARKS_CACHE["prices"].clear()
    state._MARKS_CACHE["prices"]["OLD.NS"] = (100.0, time.time() - 9999)
    with patch("trading.quote_router.fetch_quotes", return_value={"NEW.NS": 50.0}):
        px, _live = state.marks(["NEW.NS", "OLD.NS"])
    assert px == {"NEW.NS": 50.0}, "the old price rode in on the new fetch"


def test_marks_age_is_reportable():
    state._MARKS_CACHE["prices"].clear()
    assert state.marks_age(["TCS.NS"]) is None
    with patch("trading.quote_router.fetch_quotes", return_value={"TCS.NS": 2362.0}):
        state.marks(["TCS.NS"])
    assert 0 <= state.marks_age(["TCS.NS"]) < 5


def test_candle_cache_does_not_cache_a_failure():
    """A transient error must not blank the chart for the whole TTL, and each
    key must age on its own clock."""
    state._CANDLE_CACHE["data"].clear()
    with patch("trading.data.fetch_daily", return_value={}):
        assert state.recent_candles("X.NS") == []
    assert state._CANDLE_CACHE["data"] == {}, "an empty failure got cached"


# ---- per-row price honesty --------------------------------------------

def test_positions_page_marks_which_prices_are_live():
    p = state.portfolio()
    saved = dict(p.positions)
    state._MARKS_CACHE["prices"].clear()
    p.positions.clear()
    for t, entry in [("TCS.NS", 2431.0), ("RELIANCE.NS", 1300.0)]:
        p.positions[t] = PaperPosition(
            ticker=t, qty=1, entry_price=entry, entry_date=datetime(2026, 7, 1),
            sector="IT", peak_price=entry, entry_score=70.0, reasoning="t")
    try:
        with patch("trading.quote_router.fetch_quotes",
                   return_value={"TCS.NS": 2362.0}):
            page = _owner().get("/positions").text
        # the name with no live quote is visibly flagged as an entry price
        assert "entry</span>" in page or "ENTRY" in page.upper()
        assert "not the market" in page
    finally:
        p.positions = saved


# ---- the owner's orders -----------------------------------------------

def _two_positions(p):
    p.positions.clear()
    for t, entry in [("TCS.NS", 2431.0), ("RELIANCE.NS", 1300.0)]:
        p.positions[t] = PaperPosition(
            ticker=t, qty=10, entry_price=entry, entry_date=datetime(2026, 7, 1),
            sector="IT", peak_price=entry, entry_score=70.0, reasoning="t")


def test_close_all_uses_live_prices_and_refuses_stale_ones():
    """Owner order: "close all positions right away / put in the closed
    trade". Names without a live quote stay OPEN — closing them at a stale
    mark would be the very fault being fixed."""
    p = state.portfolio()
    saved_pos, saved_trades, saved_cash = dict(p.positions), list(p.trades), p.cash
    state._MARKS_CACHE["prices"].clear()
    _two_positions(p)
    try:
        with patch("trading.quote_router.fetch_quotes",
                   return_value={"TCS.NS": 2362.0}):
            r = _owner().post("/positions/close-all", follow_redirects=False)
        assert r.status_code == 303
        assert "closed_all=1" in r.headers["location"]
        assert "no_price=1" in r.headers["location"]
        assert "TCS.NS" not in p.positions            # closed
        assert "RELIANCE.NS" in p.positions           # left open, not faked
        tr = [t for t in p.trades if t.ticker == "TCS.NS"][-1]
        assert abs(tr.exit_price - 2362.0) < 5.0, "did not exit at the live price"
        assert tr.reason == "OWNER_CLOSE_ALL"
        j = [e for e in p.decision_journal if e.get("action") == "CLOSE_ALL"][-1]
        assert j["closed"] == 1 and j["skipped_no_live_price"] == ["RELIANCE.NS"]
    finally:
        p.positions, p.trades, p.cash = saved_pos, saved_trades, saved_cash


def test_close_all_is_owner_only_and_needs_positions():
    anon = TestClient(app)
    r = anon.post("/positions/close-all", follow_redirects=False)
    assert r.status_code == 303 and "owner_only" in r.headers["location"]

    p = state.portfolio()
    saved = dict(p.positions)
    p.positions.clear()
    try:
        r = _owner().post("/positions/close-all", follow_redirects=False)
        assert "nothing_to_close" in r.headers["location"]
    finally:
        p.positions = saved


def test_purge_universe_clears_every_cached_artefact():
    """Owner order: "remove all the old universe right away"."""
    p = state.portfolio()
    saved = (p.scan_matrix, list(p.last_scan_picks or []), dict(p.virtual_book or {}),
             p.last_scan)
    p.scan_matrix = {"rows": {"OLD.NS": {"score": 70.0}}}
    p.last_scan_picks = [{"ticker": "OLD.NS", "score": 70.0}]
    p.virtual_book = {"OLD.NS": {"status": "open"}}
    p.last_scan = "2026-06-01T09:20:00"
    try:
        with patch("trading.universe_refresh.refresh_universe",
                   return_value={"count": 2400, "file_date": "2026-07-31"}), \
             patch("trading.brokers.upstox_instruments.get_lookup",
                   return_value={}):
            r = _owner().post("/positions/purge-universe", follow_redirects=False)
        assert r.status_code == 303 and "universe_purged" in r.headers["location"]
        assert p.scan_matrix == {}
        assert p.last_scan_picks == [] and p.last_scan_skipped == []
        assert p.virtual_book == {}
        assert p.last_scan is None
        j = [e for e in p.decision_journal if e.get("action") == "UNIVERSE_PURGE"][-1]
        assert j["cleared"]["scan_matrix_rows"] == 1
        assert j["refreshed"]["count"] == 2400
    finally:
        p.scan_matrix, p.last_scan_picks, p.virtual_book, p.last_scan = saved


def test_purge_universe_is_owner_only():
    r = TestClient(app).post("/positions/purge-universe", follow_redirects=False)
    assert r.status_code == 303 and "denied" in r.headers["location"]


def test_price_check_covers_the_whole_book_not_a_sample():
    """Owner 2026-07-31: "hope not only 3 are updated, rest all stocks have
    real prices". With no ?tickers= given it checks EVERY held position plus
    the four indices, and summarises how many came back live."""
    p = state.portfolio()
    saved = dict(p.positions)
    p.positions.clear()
    for t in ("VSSL.NS", "ULTRACEMCO.NS", "TCS.NS"):
        p.positions[t] = PaperPosition(
            ticker=t, qty=1, entry_price=100.0, entry_date=datetime(2026, 7, 1),
            sector="X", peak_price=100.0, entry_score=70.0, reasoning="t")
    try:
        d = _owner().get("/upstox/price-check").json()
        checked = [r["ticker"] for r in d["results"]]
        for t in ("VSSL.NS", "ULTRACEMCO.NS", "TCS.NS"):
            assert t in checked, f"{t} not checked"
        for ix in ("^NSEI", "^BSESN", "^NSEBANK", "^INDIAVIX"):
            assert ix in checked, f"{ix} not checked"
        assert "of 7 symbols" in d["summary"]
        assert "not_live" in d and isinstance(d["not_live"], list)
    finally:
        p.positions = saved
