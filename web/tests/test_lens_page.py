"""/lens — the Universe seen through v1 and v2 at once (owner ask 2026-07-30:
"in Universe too u should focus on same"). Read-only by construction."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from web import auth as auth_mod, state
from web.main import app


def _owner() -> TestClient:
    c = TestClient(app)
    c.post("/login", data={"email": "owner@sourceborn.in",
                           "password": auth_mod._SEEDED_PWD})
    return c


def test_anon_is_redirected_to_login():
    r = TestClient(app).get("/lens", follow_redirects=False)
    assert r.status_code == 303 and "/login" in r.headers["location"]


def test_lens_shows_both_books_and_its_assumptions():
    with patch("web.db.is_connected", return_value=False):
        page = _owner().get("/lens?ticker=RELIANCE").text
    # both lenses labelled, and which one is actually running
    assert "v1 · live engine" in page and "v2 · growth lens" in page
    assert "running" in page and "view only" in page
    # the maths columns
    for needle in ("Kelly", "payoff R", "growth", "µ est."):
        assert needle in page, needle
    # assumptions are ON SCREEN, not buried
    assert "an assumption, not a measurement" in page
    assert "proxies from daily bars" in page
    # and the honest reason v2 is not simply better
    assert "toward certainty" in page


def test_off_scan_ticker_is_computed_on_demand():
    """A universe name the engine never scored still gets a v2 read from
    stored history — labelled off-scan, with v1 showing no size."""
    p = state.portfolio()
    saved_picks, saved_matrix = list(p.last_scan_picks or []), p.scan_matrix
    p.last_scan_picks, p.scan_matrix = [], {}
    try:
        with patch("web.db.is_connected", return_value=False):
            page = _owner().get("/lens?ticker=TCS").text
        assert "TCS" in page and "off-scan" in page
    finally:
        p.last_scan_picks, p.scan_matrix = saved_picks, saved_matrix


def test_unknown_ticker_is_reported_as_data_needed():
    p = state.portfolio()
    saved_picks, saved_matrix = list(p.last_scan_picks or []), p.scan_matrix
    p.last_scan_picks, p.scan_matrix = [], {}
    try:
        with patch("web.db.is_connected", return_value=False):
            page = _owner().get("/lens?ticker=NOSUCHSTOCK").text
        assert "DATA_NEEDED" in page          # never a guessed row
        assert "NOSUCHSTOCK" in page
    finally:
        p.last_scan_picks, p.scan_matrix = saved_picks, saved_matrix


def test_lens_never_opens_a_position_or_changes_config():
    p = state.portfolio()
    cfg = state.cfg()
    before = (len(p.positions), p.cash, cfg.position_size_value,
              cfg.max_positions, cfg.mode)
    with patch("web.db.is_connected", return_value=False):
        assert _owner().get("/lens?ticker=RELIANCE").status_code == 200
    assert (len(p.positions), p.cash, cfg.position_size_value,
            cfg.max_positions, cfg.mode) == before


def test_lens_is_in_the_sidebar():
    with patch("web.db.is_connected", return_value=False):
        page = _owner().get("/").text
    assert "/lens" in page and "Universe Lens" in page
