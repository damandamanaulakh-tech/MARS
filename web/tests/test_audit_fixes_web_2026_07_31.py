"""Web half of the 2026-07-31 "check and fix all" batch.

1. /scan/parameter/{key} was reachable WITHOUT logging in — the whole scan
   (every ticker, score and gate outcome) was public on a public URL.
2. Settings advertised a trailing stop that cannot fire, and let the owner
   edit eight fields that apply_mode_preset() silently reverts on next boot.
"""

from fastapi.testclient import TestClient

from web import auth as auth_mod, state
from web.main import app
from web.routes.settings_routes import PRESET_OWNED_FIELDS

MATRIX = {
    "ts": "2026-07-31T09:20:00", "mode": "nifty200_momentum",
    "score_floor": 62.0, "universe_size": 2393,
    "rows": {
        "TCS.NS": {"c": {"momentum": 78.0, "quality": 62.0},
                   "score": 71.0, "timing": True, "abs_mom": True,
                   "candle": "breakout", "outcome": "opened"},
    },
}


def _client(login: bool = True) -> TestClient:
    c = TestClient(app)
    if login:
        c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD})
    return c


# ------------------------------------------------- 1. parameter page is private

def test_parameter_page_redirects_an_anonymous_visitor():
    state.portfolio().scan_matrix = MATRIX
    r = _client(login=False).get("/scan/parameter/momentum", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_no_scan_data_leaks_in_the_anonymous_response():
    state.portfolio().scan_matrix = MATRIX
    body = _client(login=False).get("/scan/parameter/momentum",
                                    follow_redirects=False).text
    assert "TCS" not in body and "71" not in body


def test_the_parameter_page_still_works_once_logged_in():
    state.portfolio().scan_matrix = MATRIX
    html = _client().get("/scan/parameter/momentum").text
    assert "TCS" in html


# --------------------------------------------- 2. honest Settings page warnings

def test_settings_warns_that_the_trailing_stop_cannot_fire():
    """Live bracket is stop -5% / target +12% / trail -15%: the trail needs a
    peak above +11.76% but +12% closes first. The page must say so."""
    cfg = state.cfg()
    cfg.stop_loss_pct, cfg.take_profit_pct = -0.05, 0.12
    cfg.trailing_stop_pct = -0.15
    html = _client().get("/settings").text
    assert "trailing stop cannot fire" in html
    assert "11.76" in html          # the real peak it would need
    assert "Nothing was changed" in html   # exits are the owner's decision


def test_no_trail_warning_when_the_trail_can_actually_fire():
    cfg = state.cfg()
    cfg.stop_loss_pct, cfg.take_profit_pct = -0.12, 0.12
    cfg.trailing_stop_pct = -0.15
    try:
        html = _client().get("/settings").text
        assert "trailing stop cannot fire" not in html
    finally:
        cfg.stop_loss_pct, cfg.take_profit_pct = -0.05, 0.12


def test_settings_names_the_fields_the_mode_preset_overwrites():
    html = _client().get("/settings").text
    assert "re-applied on every restart" in html
    for field in PRESET_OWNED_FIELDS:
        assert field in html, f"{field} is silently reverted but not disclosed"


def test_the_disclosed_list_matches_what_apply_mode_preset_really_sets():
    """If apply_mode_preset() starts writing another field, this fails — the
    warning must never drift out of date with the code."""
    from pathlib import Path
    src = Path("trading/config.py").read_text()
    body = src.split("def apply_mode_preset")[1].split("\n    def ")[0]
    written = {line.split("=")[0].strip().removeprefix("self.")
               for line in body.splitlines()
               if line.strip().startswith("self.") and "=" in line}
    written = {f for f in written if f and "." not in f and f != "mode"}
    assert written <= set(PRESET_OWNED_FIELDS), (
        f"undisclosed preset-owned fields: {written - set(PRESET_OWNED_FIELDS)}")
