"""🎹 Tap-a-parameter view — /scan/parameter/{key} (user ask 2026-07-21:
'click piano note → parameter definition + which stocks passed/removed')."""

from fastapi.testclient import TestClient

from web import auth as auth_mod, state
from web.main import app

MATRIX = {
    "ts": "2026-07-21T09:20:00", "mode": "nifty200_momentum",
    "score_floor": 55.0, "universe_size": 2393,
    "rows": {
        "TCS.NS": {"c": {"momentum": 78.0, "quality": 62.0,
                         "short_interest": 50.0},
                   "score": 71.0, "timing": True, "abs_mom": True,
                   "candle": "breakout", "outcome": "opened"},
        "WEAKCO.NS": {"c": {"momentum": 45.0, "quality": 30.0,
                            "short_interest": 50.0},
                      "score": 41.0, "timing": False, "abs_mom": False,
                      "candle": "no_pattern", "outcome": "regime_halt"},
    },
}


def _client() -> TestClient:
    c = TestClient(app)
    c.post("/login", data={"email": "owner@sourceborn.in",
                           "password": auth_mod._SEEDED_PWD})
    return c


def _seed():
    state.portfolio().scan_matrix = MATRIX


def test_factor_page_groups_pass_and_fail():
    _seed()
    html = _client().get("/scan/parameter/momentum").text
    assert "volatility-adjusted" in html            # definition present
    assert "TCS" in html and "WEAKCO" in html
    assert "Where to change it in code" in html
    assert "nifty200_momentum.py" in html           # the knob location


def test_score_page_shows_live_floor():
    _seed()
    html = _client().get("/scan/parameter/score").text
    assert "≥ 55" in html


def test_short_interest_page_is_honest_about_being_a_proxy():
    """Node 7 (2026-08-23): the stub went live as a futures-OI PROXY — the
    page must say proxy, name the neutral rules, and never claim true
    borrowed-share data exists."""
    _seed()
    html = _client().get("/scan/parameter/short_interest").text
    assert "PROXY" in html
    assert "futures" in html.lower()
    assert "neutral" in html
    assert "STUB" not in html


def test_outcome_and_gates_pages_render():
    _seed()
    c = _client()
    for key in ("timing", "abs_mom", "candle", "outcome"):
        assert c.get(f"/scan/parameter/{key}").status_code == 200
    assert "regime_halt" in c.get("/scan/parameter/outcome").text


def test_unknown_key_redirects_to_scan():
    _seed()
    r = _client().get("/scan/parameter/nonsense_key", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/scan"


def test_piano_cells_link_to_parameter_pages():
    _seed()
    html = _client().get("/scan").text
    assert '/scan/parameter/momentum' in html
    assert '/scan/parameter/outcome' in html
