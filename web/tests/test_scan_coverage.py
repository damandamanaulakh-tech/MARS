"""Scan coverage card — the owner's "system is not reading all 2400 stocks".

Owner ask 2026-09-10, verbatim: "what i felt system is not reading all 2400
stocks every time, that is the flaw ... we can do sector wise and on dashboard
after doing each sector's each share it show show data".

The answer to "is it reading everything" has to be a COUNT, not a
reassurance. These tests pin that the count is real, that the shortfall is
named rather than hidden, and that the card never invents a full-coverage
reading it cannot back.
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


def _seed(rows: dict):
    p, cfg = state.portfolio(), state.cfg()
    saved_m, saved_u = p.scan_matrix, cfg.custom_universe
    p.scan_matrix = {"ts": "2026-09-10T09:20:00", "score_floor": 62.0,
                     "rows": rows}
    return p, cfg, saved_m, saved_u


def test_coverage_counts_scanned_against_the_real_universe():
    p, cfg, sm, su = _seed({"TCS.NS": {"score": 71.0},
                            "TATASTEEL.NS": {"score": 55.0}})
    try:
        cfg.custom_universe = ("TCS.NS", "TATASTEEL.NS", "MARUTI.NS")
        c = mp.coverage_panel()
        assert c["universe"] == 3
        assert c["scanned"] == 2            # MARUTI has no scored row
        assert c["passing"] == 1            # only TCS clears the 62 floor
        assert c["pct"] == 66.7
    finally:
        p.scan_matrix, cfg.custom_universe = sm, su


def test_a_name_with_no_stored_history_is_counted_as_UNREAD_not_dropped():
    """The whole point. A name the app could not score must show in the
    denominator — dropping it would make coverage read 100% while the owner's
    stock was never looked at."""
    p, cfg, sm, su = _seed({"TCS.NS": {"score": 71.0}})
    try:
        cfg.custom_universe = ("TCS.NS", "NOHISTORY.NS")
        c = mp.coverage_panel()
        assert c["universe"] == 2 and c["scanned"] == 1
        assert c["pct"] == 50.0, "an unread name must not vanish from the count"
    finally:
        p.scan_matrix, cfg.custom_universe = sm, su


def test_the_shortfall_against_the_target_is_named():
    p, cfg, sm, su = _seed({"TCS.NS": {"score": 71.0}})
    try:
        cfg.custom_universe = ("TCS.NS",)
        c = mp.coverage_panel()
        assert c["target"] == cfg.universe_target_size
        assert c["short_by"] == cfg.universe_target_size - 1
    finally:
        p.scan_matrix, cfg.custom_universe = sm, su


def test_the_panel_hides_before_any_scan_rather_than_showing_zeros():
    p = state.portfolio()
    saved = p.scan_matrix
    p.scan_matrix = {}
    try:
        assert mp.coverage_panel() == {}
    finally:
        p.scan_matrix = saved


def test_sectors_are_broken_out_and_ordered_by_size():
    p, cfg, sm, su = _seed({"TCS.NS": {"score": 71.0},
                            "TATASTEEL.NS": {"score": 70.0},
                            "JSWSTEEL.NS": {"score": 40.0}})
    try:
        cfg.custom_universe = ("TCS.NS", "TATASTEEL.NS", "JSWSTEEL.NS")
        rows = mp.coverage_panel()["rows"]
        by = {r["sector"]: r for r in rows}
        assert by["Metal"]["universe"] == 2 and by["Metal"]["passing"] == 1
        assert by["IT"]["universe"] == 1
        assert rows[0]["universe"] >= rows[-1]["universe"]   # biggest first
    finally:
        p.scan_matrix, cfg.custom_universe = sm, su


def test_the_card_renders_on_the_real_dashboard():
    """VERIFIED BY A REAL RENDER, not only by the panel function — the
    /positions 500 of 2026-08-21 was a template fault that every unit test
    missed because no test ever rendered the page with live state."""
    p, cfg, sm, su = _seed({"TCS.NS": {"score": 71.0},
                            "M&M.NS": {"score": 64.0}})
    try:
        cfg.custom_universe = ("TCS.NS", "M&M.NS", "MARUTI.NS")
        p.save()
        with patch("web.db.is_connected", return_value=False):
            r = _owner().get("/")
        assert r.status_code == 200
        assert "coverage_marker" in r.text
        assert "Scan coverage" in r.text
        assert "SHORT BY" in r.text          # the honest shortfall line
    finally:
        p.scan_matrix, cfg.custom_universe = sm, su
        p.save()


def test_coverage_is_display_only():
    """Boundary: the money path must never import a display module."""
    import inspect

    from trading import engine, orders, risk
    for mod in (engine, orders, risk):
        src = inspect.getsource(mod)
        assert "market_panels" not in src, mod.__name__
