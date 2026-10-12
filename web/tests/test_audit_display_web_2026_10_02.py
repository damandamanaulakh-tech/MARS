"""Owner order 2026-10-02 ("go for next"): the page halves of audit rows
F-18 (per-position trail card), S-11 (Buy Desk reads the momentum pass,
liquidity judged per row) and S-13 (trigger/news scope)."""
from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from web import auth as auth_mod, buy_desk, scheduler, state
from web.main import app


def _owner() -> TestClient:
    c = TestClient(app)
    c.post("/login", data={"email": "owner@sourceborn.in",
                           "password": auth_mod._SEEDED_PWD})
    return c


# --------------------------------------------------------------------- F-18

def test_settings_names_held_positions_whose_trail_cannot_fire():
    from trading.paper import PaperPosition
    p = state.portfolio()
    saved = dict(p.positions)
    p.positions = {"QUIET.NS": PaperPosition(
        ticker="QUIET.NS", qty=1, entry_price=100.0,
        entry_date=datetime(2026, 9, 1), sector="IT", peak_price=100.0,
        entry_score=70.0, reasoning="fixture", mode="nifty200_momentum",
        stop_price=95.0, target_price=112.0)}          # a k x ATR +12% target
    try:
        # Owner order 2026-10-12 ("setting will only have the APP SETTINGS,
        # NOT THE ALGO, that will move to MARS Den"): the trail card moved
        # with the algo form to /den#algo-settings.
        html = _owner().get("/den").text
        assert "trail_dead_marker" in html and "QUIET.NS" in html
        assert "target +12.0%" in html
    finally:
        p.positions = saved


def test_no_card_when_every_trail_can_fire():
    from trading.paper import PaperPosition
    p = state.portfolio()
    saved = dict(p.positions)
    p.positions = {"WILD.NS": PaperPosition(
        ticker="WILD.NS", qty=1, entry_price=100.0,
        entry_date=datetime(2026, 9, 1), sector="IT", peak_price=100.0,
        entry_score=70.0, reasoning="fixture", mode="nifty200_momentum",
        stop_price=95.0, target_price=130.0)}
    try:
        # Owner order 2026-10-12: the trail card lives on /den now; the
        # page must render for the absence to mean anything.
        r = _owner().get("/den")
        assert r.status_code == 200 and "settings_algo_form_marker" in r.text
        html = r.text
        assert "<!-- trail_dead_marker -->" not in html
    finally:
        p.positions = saved


# --------------------------------------------------------------------- S-11

def _ms_book():
    p = state.portfolio()
    saved = (dict(getattr(p, "momentum_shortlist", {}) or {}),
             dict(p.scan_matrix or {}))
    p.momentum_shortlist = {
        "ts": "2026-10-05T03:55:00", "mode": "nifty200_momentum",
        "score_floor": 62.0, "top": ["LIQ.NS", "THIN.NS", "NOHIST.NS"],
        "rows": {
            "LIQ.NS":    {"score": 80.0, "abs_mom": True, "timing": True,
                          "liq": True, "liq_why": "", "outcome": "breadth_band", "c": {}},
            "THIN.NS":   {"score": 85.0, "abs_mom": True, "timing": True,
                          "liq": False, "liq_why": "rupee_vol 1.2Cr < 5Cr",
                          "outcome": "breadth_band", "c": {}},
            "NOHIST.NS": {"score": 75.0, "abs_mom": True, "timing": True,
                          "liq": None, "liq_why": "no_history",
                          "outcome": "", "c": {}},
        }}
    # the positional pass that ran last: a different name entirely
    p.scan_matrix = {"ts": "2026-10-05T04:10:00", "mode": "positional",
                     "score_floor": 68.0,
                     "rows": {"POS.NS": {"score": 90.0, "abs_mom": True,
                                         "outcome": ""}}}
    return p, saved


def test_the_desk_reads_the_momentum_pass_and_its_liquidity_verdict():
    p, saved = _ms_book()
    try:
        sl = buy_desk.shortlist(p, state.cfg())
        names = [r["ticker"] for r in sl["rows"]]
        assert names == ["LIQ.NS"], names        # not THIN, not NOHIST, not POS
        assert sl["source"] == "momentum_pass" and sl["mode"] == "nifty200_momentum"
        assert sl["liquidity_not_judged"] == 1
    finally:
        p.momentum_shortlist, p.scan_matrix = saved


def test_the_card_says_where_the_shortlist_came_from():
    p, saved = _ms_book()
    try:
        html = _owner().get("/scan").text
        assert "read from the momentum pass" in html
        assert "1 left out: no history to judge liquidity" in html
    finally:
        p.momentum_shortlist, p.scan_matrix = saved


# --------------------------------------------------------------------- S-13

def test_the_watch_scope_reads_held_then_the_momentum_pass():
    p, saved = _ms_book()
    saved_picks = list(p.last_scan_picks or [])
    p.last_scan_picks = [{"ticker": "POS.NS"}]
    try:
        scope = scheduler._watch_scope(p)
        held = list(p.positions.keys())
        assert scope[:len(held)] == held
        rest = [t for t in scope[len(held):]]
        assert rest.index("LIQ.NS") < rest.index("POS.NS")
    finally:
        p.momentum_shortlist, p.scan_matrix = saved
        p.last_scan_picks = saved_picks
