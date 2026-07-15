"""Web-layer tests for db fallback + mode switching from Settings page."""

from fastapi.testclient import TestClient

from web import auth as auth_mod
from web import db, state
from web.main import app


def _logged_in_client() -> TestClient:
    c = TestClient(app)
    c.post(
        "/login",
        data={"email": "owner@sourceborn.in", "password": auth_mod._SEEDED_PWD},
    )
    return c


def test_db_disconnected_by_default_in_tests():
    # Without MONGO_URL the module reports JSON fallback
    assert db.is_connected() is False
    s = db.status()
    assert s["connected"] is False
    assert s["url_set"] is False


def test_db_save_load_noop_when_disconnected():
    # Should not raise even though Mongo is unreachable
    db.save_portfolio({"a": 1})
    assert db.load_portfolio() is None
    db.save_settings({"mode": "intraday"})
    assert db.load_settings() == {}


def test_settings_page_shows_mode_picker():
    c = _logged_in_client()
    r = c.get("/settings")
    assert r.status_code == 200
    assert "Trading Mode" in r.text
    assert "Intraday" in r.text
    assert "Swing" in r.text
    assert "Positional" in r.text


def test_settings_page_shows_db_status_badge():
    c = _logged_in_client()
    r = c.get("/settings")
    assert "Storage" in r.text
    assert "Mongo offline" in r.text


def test_setting_mode_to_intraday_applies_preset():
    c = _logged_in_client()
    r = c.post(
        "/settings",
        data={"mode": "intraday"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    cfg = state.cfg()
    assert cfg.mode == "intraday"
    assert cfg.max_hold_days == 1
    assert cfg.stop_loss_pct == -0.015
    # Restore
    cfg.mode = "swing"
    cfg.apply_mode_preset()


def test_healthz_includes_db_status():
    c = TestClient(app)
    r = c.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "db" in body
    assert body["db"]["connected"] is False
