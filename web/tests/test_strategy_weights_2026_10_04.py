"""Owner order 2026-10-04 ("use each category's weights"): the Strategy page
shows the weights the score really uses, and no longer calls the futures-OI
short-interest proxy a stub."""
from __future__ import annotations

from fastapi.testclient import TestClient

from web import auth as auth_mod
from web import state
from web.main import app


def _page(mode):
    cfg = state.cfg()
    saved = cfg.mode
    cfg.mode = mode
    try:
        c = TestClient(app)
        r = c.post("/login", data={"email": "owner@sourceborn.in",
                                   "password": auth_mod._SEEDED_PWD})
        assert r.status_code == 200
        r = c.get("/strategy")
        assert r.status_code == 200
        return r.text
    finally:
        cfg.mode = saved


def test_positional_shows_its_own_weights():
    html = _page("positional")
    assert "25.0%" in html                      # positional earnings_growth
    assert "stubbed @ 50" not in html
    assert "futures open-interest proxy" in html.lower()


def test_momentum_says_the_table_is_not_its_buy_rule():
    html = _page("nifty200_momentum")
    assert "momentum_composite_marker" in html
