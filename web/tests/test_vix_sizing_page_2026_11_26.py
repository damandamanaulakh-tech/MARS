"""VIX size multiplier on the screens: the Den states the ladder, whether it
is on, and its test result read from the committed run; Settings carries the
switch, default off. Owner decision 2026-11-26."""
from __future__ import annotations

import re

from fastapi.testclient import TestClient

from web import auth, state
from web.main import app
from web.routes import den_routes
from web.tests.test_volume_gates_page_2026_11_26 import clean_settings


def _text(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _client():
    client = TestClient(app)
    assert client.post("/login", data={"email": "owner@sourceborn.in",
                                       "password": auth._SEEDED_PWD}).status_code == 200
    return client


def test_the_den_states_the_ladder_that_it_is_off_and_the_test_result():
    client = _client()
    with clean_settings() as cfg:
        page = client.get("/den")
        assert page.status_code == 200 and "den_vix_sizing_marker" in page.text
        line = _text(page.text.split("den_vix_sizing_marker", 1)[1].split("ILLUSTRATION", 1)[0])
        assert "VIX size multiplier (owner decision 26 Nov 2026): OFF" in line
        assert ("India VIX under 12 ×1.15 · India VIX 12 to under 16 ×1.00 · India VIX 16 to under 20 ×0.80 · "
                "India VIX 20 to under 25 ×0.65 · India VIX 25 and above ×0.50") in line
        assert "not Ocean, not your own BUY" in line
        assert "Tested before building, on 11,055 Momentum picks: not supported ." in line
        assert "Per rupee deployed +3.42% with it against +3.43% without." in line
        assert "25 and above +7.07%" in line and "12 to under 16 +2.46%" in line
        cfg.vix_sizing_enabled = True
        line = _text(client.get("/den").text.split("den_vix_sizing_marker", 1)[1].split("ILLUSTRATION", 1)[0])
        assert "VIX size multiplier (owner decision 26 Nov 2026): ON" in line


def test_the_result_is_read_from_the_committed_run_never_retyped(monkeypatch):
    block = den_routes._vix_sizing_block(state.cfg())
    assert block["test"]["supported"] is False and block["test"]["buys"] == 11055
    assert [b["multiplier"] for b in block["bands"]] == [1.15, 1.0, 0.8, 0.65, 0.5]
    import json
    monkeypatch.setattr(json, "loads", lambda *a, **k: 1 / 0)       # the run cannot be read
    missing = den_routes._vix_sizing_block(state.cfg())
    assert missing["test"] is None and len(missing["bands"]) == 5    # the ladder still prints


def _algo_form(client):
    html = client.get("/den").text
    html = html.split("settings_algo_form_marker", 1)[1].split("</form>", 1)[0]
    fields = dict(re.findall(r'name="([a-z_0-9]+)"[^>]*value="([^"]*)"', html))
    assert fields, "the settings form rendered no fields"
    return fields


def test_the_switch_is_off_in_settings_and_a_save_keeps_it_off_until_set():
    client = _client()
    with clean_settings():
        fields = _algo_form(client)
        assert fields["vix_sizing_enabled"] == "0"
        assert client.post("/settings", data=fields, follow_redirects=False).status_code == 303
        assert state.cfg().vix_sizing_enabled is False
        fields["vix_sizing_enabled"] = "1"
        assert client.post("/settings", data=fields, follow_redirects=False).status_code == 303
        assert state.cfg().vix_sizing_enabled is True
        assert state.engine().mode_cfg("swing").vix_sizing_enabled is True     # reaches the scan's own settings
