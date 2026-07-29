"""Signal page (standing waves) — renders live components, stubs flat."""

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


def test_signal_empty_state_renders():
    p = state.portfolio()
    saved_pos, saved_picks = dict(p.positions), list(p.last_scan_picks or [])
    saved_matrix = p.scan_matrix          # matrix rows join the pool now
    p.positions, p.last_scan_picks, p.scan_matrix = {}, [], {}
    try:
        r = _client().get("/signal")
        assert r.status_code == 200
        assert "No scored stock yet" in r.text
    finally:
        p.positions, p.last_scan_picks = saved_pos, saved_picks
        p.scan_matrix = saved_matrix


def test_signal_renders_strings_from_scan_pick():
    p = state.portfolio()
    saved_picks = list(p.last_scan_picks or [])
    p.last_scan_picks = [{
        "ticker": "SIGTEST.NS", "score": 71.4,
        "components": {"momentum_1m": 82.0, "volume_expansion": 64.0,
                       "short_interest": 50.0, "analyst_revision": 25.0},
    }]
    try:
        r = _client().get("/signal?ticker=SIGTEST.NS")
        assert r.status_code == 200
        for needle in ("SIGTEST.NS", "signal-data", "momentum 1m",
                       "no data",              # short_interest stays silent
                       "cannot resonate"):
            assert needle in r.text, needle
        # analyst_revision is LIVE now — must NOT be marked stub
        assert '"name": "analyst_revision", "score": 25.0' \
            in r.text.replace("&#34;", '"') or "analyst revision" in r.text
    finally:
        p.last_scan_picks = saved_picks


def test_signal_position_uses_buy_journal_components():
    p = state.portfolio()
    p.positions["SIGPOS.NS"] = PaperPosition(
        ticker="SIGPOS.NS", qty=1, entry_price=100.0,
        entry_date=datetime(2026, 7, 1), sector="IT", peak_price=100.0,
        entry_score=77.0, reasoning="test",
    )
    p.decision_journal.append({
        "ts": "2026-07-01T09:25:00", "action": "BUY", "ticker": "SIGPOS.NS",
        "components": {"momentum": 70.0, "quality": 60.0},
    })
    try:
        r = _client().get("/signal?ticker=SIGPOS.NS")
        assert r.status_code == 200
        assert "SIGPOS.NS" in r.text
        assert "momentum" in r.text and "quality" in r.text
        assert "77.0" in r.text          # total from entry_score
    finally:
        p.positions.pop("SIGPOS.NS", None)


# ---- 2026-07-29 owner order: "signal window need to link with universe,
# it must pick stock from there not the fixed" ----

def test_signal_search_box_lists_the_full_universe():
    r = _client().get("/signal")
    assert r.status_code == 200
    assert 'list="universe-list"' in r.text          # search box wired
    assert r.text.count("<option") > 2000            # full universe, not the pool


def test_signal_computes_any_universe_stock_on_demand():
    """RELIANCE not held/picked -> computed live from stored history with
    the exact live-mode functions, labeled as computed-now."""
    from unittest.mock import patch
    p = state.portfolio()
    saved_pos, saved_picks = dict(p.positions), list(p.last_scan_picks or [])
    saved_matrix = p.scan_matrix
    p.positions, p.last_scan_picks, p.scan_matrix = {}, [], {}
    try:
        with patch("web.db.is_connected", return_value=False):   # zip path
            r = _client().get("/signal?ticker=RELIANCE")
        assert r.status_code == 200
        assert "RELIANCE.NS" in r.text
        assert "computed now from stored history" in r.text
        assert "momentum" in r.text and "quality" in r.text
    finally:
        p.positions, p.last_scan_picks = saved_pos, saved_picks
        p.scan_matrix = saved_matrix


def test_signal_unknown_stock_says_data_needed():
    from unittest.mock import patch
    with patch("web.db.is_connected", return_value=False):
        r = _client().get("/signal?ticker=NOSUCHSTOCK")
    assert r.status_code == 200
    assert "DATA_NEEDED" in r.text
    assert "signal-data" not in r.text               # no strings faked


def test_signal_pool_includes_scan_matrix_rows():
    """The whole scanned bucket is selectable, not only the top-50 picks."""
    p = state.portfolio()
    saved_matrix = p.scan_matrix
    p.scan_matrix = {"ts": "2026-07-29T09:20:00", "mode": "nifty200_momentum",
                     "rows": {"MATRIXONLY.NS": {
                         "c": {"momentum": 61.0, "quality": 55.0},
                         "score": 58.9}}}
    try:
        r = _client().get("/signal?ticker=MATRIXONLY.NS")
        assert r.status_code == 200
        assert "MATRIXONLY.NS" in r.text
        assert "58.9" in r.text                      # stored score, no recompute
        assert "computed now" not in r.text
    finally:
        p.scan_matrix = saved_matrix
