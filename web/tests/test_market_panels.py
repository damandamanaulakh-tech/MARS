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

def test_index_strip_reads_the_committed_index_history():
    strip = mp.index_strip(bars=40)
    labels = [r["label"] for r in strip]
    assert labels == ["NIFTY 50", "SENSEX", "NIFTY BANK", "INDIA VIX"]
    for r in strip:
        assert r["last"] > 0
        assert len(r["points"].split()) == 40        # one sparkline point/bar
        assert r["low"] <= r["last"] <= r["high"]
        assert r["asof"]                              # dated, never "live"
    # VIX is the one card where UP is BAD — must be flagged for the template
    assert [r["invert"] for r in strip] == [False, False, False, True]


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
        assert b["gate_min_pct"] == 50            # the real A3 gate threshold
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
    assert mom["stop"] == -5.0 and mom["target"] == 12.0   # the owner's numbers
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

def test_dashboard_renders_the_new_panels():
    with patch("web.db.is_connected", return_value=False):
        page = _owner().get("/").text
    for needle in ("NIFTY 50", "INDIA VIX", "stored to",      # index strip
                   "Market Breadth", "s Coming",              # What's Coming
                   "Trade Horizons", "Walk-Forward Evidence"):
        assert needle in page, needle


def test_a_broken_panel_cannot_take_the_dashboard_down():
    from web.routes import dashboard as dash
    with patch("web.market_panels.index_strip", side_effect=RuntimeError("boom")), \
         patch("web.db.is_connected", return_value=False):
        r = _owner().get("/")
    assert r.status_code == 200          # page survives, panel just hides
    assert "NIFTY 50" not in r.text or "Market Breadth" in r.text
    assert dash._panel(lambda: 1 / 0) is None
