"""Dashboard market panels (owner ask 2026-07-30: "make some changes in the
dashboard, adding few option u can adopt some of each kind" + 5 reference
mockups). Every panel must be backed by REAL committed/computed data, hide
itself when there is no honest source, and never take the page down.
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

from web import auth as auth_mod, market_panels as mp, state
from web.main import app


def _owner() -> TestClient:
    c = TestClient(app)
    c.post("/login", data={"email": "owner@sourceborn.in",
                           "password": auth_mod._SEEDED_PWD})
    return c


# ---- index strip ---------------------------------------------------------

def test_index_strip_never_shows_a_stale_price_as_live():
    """Owner caught this 2026-07-31: the strip printed the committed CSV's
    05-Jun close as if it were today's market (Sensex 74,243 on screen while
    the real level was 78,196). A "stored to" caption does not undo a wrong
    big number. Rule now: the headline is LIVE or it is a dash."""
    with patch("trading.quote_router.fetch_quotes", return_value={}):
        strip = mp.index_strip()
    assert [r["label"] for r in strip] == \
        ["NIFTY 50", "SENSEX", "NIFTY BANK", "INDIA VIX"]
    for r in strip:
        assert r["live"] is False
        assert r["last"] is None, "a stale close leaked into the headline"
        assert r["chg"] is None


def test_index_strip_shows_live_levels_when_the_feed_works():
    real = {"^NSEI": 24400.0, "^BSESN": 78196.0,
            "^NSEBANK": 57000.0, "^INDIAVIX": 12.4}
    with patch("trading.quote_router.fetch_quotes", return_value=real), \
         patch("web.state.engine", return_value=type("E", (), {"broker": None})()):
        strip = mp.index_strip()
    got = {r["label"]: r["last"] for r in strip}
    assert got["SENSEX"] == 78196.0 and got["NIFTY 50"] == 24400.0
    assert all(r["live"] for r in strip)


def test_day_change_is_never_computed_against_a_stored_close():
    """A live price minus a two-month-old close is not a day move. Without a
    real previous close from the feed, no change is shown at all."""
    real = {"^NSEI": 24400.0}
    with patch("trading.quote_router.fetch_quotes", return_value=real), \
         patch("web.state.engine", return_value=type("E", (), {"broker": None})()):
        strip = mp.index_strip()
    nifty = next(r for r in strip if r["label"] == "NIFTY 50")
    assert nifty["live"] is True and nifty["last"] == 24400.0
    assert nifty["chg"] is None and nifty["pct"] is None


def test_index_symbols_resolve_to_upstox_instrument_keys():
    """The root cause: indices are not in the NSE_EQ instrument file, so they
    never resolved and every index quote fell through to the Render-blocked
    Yahoo path."""
    from trading.brokers.upstox_instruments import INDEX_KEYS, to_instrument_keys
    for sym in ("^NSEI", "^BSESN", "^NSEBANK", "^INDIAVIX", "^CNX200"):
        assert sym in INDEX_KEYS
    keys = to_instrument_keys(["^NSEI", "^BSESN"])
    assert keys["^NSEI"] == "NSE_INDEX|Nifty 50"
    assert keys["^BSESN"] == "BSE_INDEX|SENSEX"


def test_dashboard_prints_a_dash_and_a_reconnect_link_when_offline():
    with patch("trading.quote_router.fetch_quotes", return_value={}), \
         patch("web.db.is_connected", return_value=False):
        page = _owner().get("/").text
    assert "no live quote" in page
    assert "Reconnect Upstox" in page
    assert "74,243" not in page and "23,366" not in page   # the stale levels


# ---- breadth -------------------------------------------------------------

def test_breadth_panel_counts_the_engines_own_scan_panel():
    p = state.portfolio()
    saved = p.scan_matrix
    p.scan_matrix = {
        "ts": "2026-07-30T09:20:00", "score_floor": 55.0,
        "rows": {
            "A.NS": {"score": 82.0, "timing": True, "abs_mom": True},
            "B.NS": {"score": 61.0, "timing": False, "abs_mom": True},
            "C.NS": {"score": 44.0, "timing": False, "abs_mom": False},
            "D.NS": {"score": 20.0, "timing": False, "abs_mom": False},
        },
    }
    try:
        b = mp.breadth_panel()
        assert b["scanned"] == 4
        assert (b["strong"], b["healthy"], b["neutral"], b["weak"]) == (1, 1, 1, 1)
        assert b["passing"] == 2 and b["pass_pct"] == 50.0
        assert b["abs_mom_pct"] == 50.0 and b["timing_pct"] == 25.0
        assert b["avg_score"] == 51.8
        # Re-cut 2026-09-09: the gate is a measured BAND, not one threshold.
        # Both edges reach the page so its copy cannot drift from the rule.
        assert (b["gate_band_low"], b["gate_band_high"]) == (40, 65)
    finally:
        p.scan_matrix = saved


def test_breadth_panel_returns_none_before_any_scan():
    p = state.portfolio()
    saved = p.scan_matrix
    p.scan_matrix = {}
    try:
        assert mp.breadth_panel() is None      # hidden, not faked with zeros
    finally:
        p.scan_matrix = saved


# ---- events / evidence / horizons ---------------------------------------

def test_events_panel_is_sorted_and_dated_from_the_calendars():
    evs = mp.events_panel(days=60, limit=8)
    assert evs, "curated calendars should always have something in 60 days"
    assert [e["days"] for e in evs] == sorted(e["days"] for e in evs)
    for e in evs:
        assert e["days"] >= 0 and e["desc"] and e["date"]


def test_evidence_panel_matches_the_committed_walk_forward_file():
    import json
    from pathlib import Path
    raw = json.loads((Path("docs") / "param_search_v3_results.json").read_text())
    ev = mp.evidence_panel()
    assert ev["windows"] == len(raw["windows"])
    assert ev["grid"] == raw["grid_size"]
    # alphas are passed through untouched — no rounding-up of the bad window
    assert [r["alpha"] for r in ev["rows"]] == \
        [round(float(w["oos_alpha"]), 2) for w in raw["windows"]]
    assert any(r["alpha"] < 0 for r in ev["rows"]), "the losing window must show"


def test_horizons_panel_carries_the_real_presets_and_marks_the_live_one():
    hs = mp.horizons_panel()
    names = {h["key"] for h in hs}
    assert {"nifty200_momentum", "short", "swing", "positional", "intraday"} <= names
    mom = next(h for h in hs if h["key"] == "nifty200_momentum")
    assert mom["stop"] == -5.0 and mom["target"] == 20.0   # owner's numbers (Node 12, 2026-08-24)
    assert sum(1 for h in hs if h["active"]) <= 1


# ---- ATR ----------------------------------------------------------------

def test_atr_panel_compares_volatility_stop_to_the_fixed_stop():
    with patch("web.db.is_connected", return_value=False):     # repo-zip path
        rows = mp.atr_panel(["RELIANCE.NS", "TCS.NS"])
    assert len(rows) == 2
    for r in rows:
        assert r["atr"] > 0 and r["last"] > 0
        assert r["fixed_stop"] < r["last"]
        assert r["atr_stop"] < r["last"]
        assert r["fixed_stop_pct"] == 5.0                      # owner's -5%
        assert isinstance(r["tighter"], bool)


def test_atr_panel_skips_tickers_with_no_stored_history():
    with patch("web.db.is_connected", return_value=False):
        assert mp.atr_panel(["NOSUCHTICKER.NS"]) == []


# ---- the page itself ----------------------------------------------------

def test_dashboard_renders_the_always_available_panels():
    """These three need no scan — they come from committed files and presets,
    so they must render on a brand-new book (CI starts with an empty one)."""
    with patch("web.db.is_connected", return_value=False):
        page = _owner().get("/").text
    for needle in ("NIFTY 50", "INDIA VIX",                    # index strip
                   "s Coming",                                 # What's Coming
                   "Trade Horizons", "Walk-Forward Evidence"):
        assert needle in page, needle


def test_dashboard_breadth_panel_appears_once_a_scan_exists():
    """Breadth is scan-derived: hidden on a fresh book (never faked with
    zeros), shown after a scan. Seeded here so the test does not depend on
    leftover state from other tests."""
    p = state.portfolio()
    saved = p.scan_matrix
    p.scan_matrix = {
        "ts": "2026-07-30T09:20:00", "score_floor": 55.0,
        "rows": {"A.NS": {"score": 82.0, "timing": True, "abs_mom": True},
                 "B.NS": {"score": 30.0, "timing": False, "abs_mom": False}},
    }
    try:
        with patch("web.db.is_connected", return_value=False):
            page = _owner().get("/").text
        assert "Market Breadth" in page
        assert "Breadth gate ON" in page          # the live A3 gate state
    finally:
        p.scan_matrix = saved


def test_a_broken_panel_cannot_take_the_dashboard_down():
    from web.routes import dashboard as dash
    with patch("web.market_panels.index_strip", side_effect=RuntimeError("boom")), \
         patch("web.db.is_connected", return_value=False):
        r = _owner().get("/")
    assert r.status_code == 200                  # page survives
    assert "NIFTY 50" not in r.text              # the broken panel is gone...
    assert "Trade Horizons" in r.text            # ...the others still render
    assert dash._panel(lambda: 1 / 0) is None


# ---- the 2026-07-31 lesson, applied everywhere ------------------------

def test_every_stored_price_on_the_page_is_dated():
    """Owner 2026-07-31: an undated stored number beside live-looking data
    reads as current. Any surface showing a stored close must date it."""
    with patch("web.db.is_connected", return_value=False):
        rows = mp.atr_panel(["RELIANCE.NS"])
    assert rows and rows[0]["asof"], "ATR panel must date its stored close"


def test_lens_dates_the_history_its_numbers_come_from():
    from unittest.mock import patch as _p
    from web import auth as _a
    from fastapi.testclient import TestClient
    from web.main import app
    c = TestClient(app)
    c.post("/login", data={"email": "owner@sourceborn.in",
                           "password": _a._SEEDED_PWD})
    with _p("web.db.is_connected", return_value=False):
        page = c.get("/lens?ticker=RELIANCE").text
    assert "stored daily bars to" in page, "lens must date its stored inputs"


def test_breadth_panel_carries_the_full_store_reading_when_present():
    """F3 fix: the gate prefers the full-store 200DMA reading — the panel
    must show that number and its as-of date next to the bucket facts."""
    p = state.portfolio()
    saved_m, saved_b = p.scan_matrix, p.breadth_store
    p.scan_matrix = {"ts": "2026-08-20T09:20:00", "score_floor": 62.0,
                     "rows": {"A.NS": {"score": 70.0, "timing": True,
                                       "abs_mom": True}}}
    p.breadth_store = {"pct": 0.548, "sample": 2641, "asof": "2026-08-19"}
    try:
        b = mp.breadth_panel()
        assert b["store"]["pct"] == 0.548 and b["store"]["sample"] == 2641
    finally:
        p.scan_matrix, p.breadth_store = saved_m, saved_b


def test_dashboard_renders_the_store_breadth_line():
    from fastapi.testclient import TestClient
    from web import auth as _a
    from web.main import app
    p = state.portfolio()
    saved_m, saved_b = p.scan_matrix, p.breadth_store
    p.scan_matrix = {"ts": "2026-08-20T09:20:00", "score_floor": 62.0,
                     "rows": {"A.NS": {"score": 70.0, "timing": True,
                                       "abs_mom": True}}}
    p.breadth_store = {"pct": 0.548, "sample": 2641, "asof": "2026-08-19"}
    p.save()
    try:
        c = TestClient(app)
        c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": _a._SEEDED_PWD})
        html = c.get("/").text
        assert "breadth_store_marker" in html
        assert "2641" in html and "2026-08-19" in html
    finally:
        p.scan_matrix, p.breadth_store = saved_m, saved_b
        p.save()
