"""Tests for /upstox/login + /upstox/callback + /upstox/status + sidebar pill."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from web import auth as auth_mod
from web.main import app


@pytest.fixture
def env_set(monkeypatch):
    monkeypatch.setenv("UPSTOX_API_KEY", "test_key")
    monkeypatch.setenv("UPSTOX_API_SECRET", "test_secret")
    monkeypatch.setenv(
        "UPSTOX_REDIRECT_URI", "http://testserver/upstox/callback",
    )


@pytest.fixture
def env_missing(monkeypatch):
    for k in ("UPSTOX_API_KEY", "UPSTOX_API_SECRET", "UPSTOX_REDIRECT_URI"):
        monkeypatch.delenv(k, raising=False)


def _logged_in(monkeypatch_env=None) -> TestClient:
    c = TestClient(app)
    c.post(
        "/login",
        data={"email": "owner@sourceborn.in", "password": auth_mod._SEEDED_PWD},
    )
    return c


def test_login_redirects_to_upstox_when_env_set(env_set):
    c = _logged_in()
    r = c.get("/upstox/login", follow_redirects=False)
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("https://api.upstox.com/v2/login/authorization/dialog?")
    assert "client_id=test_key" in loc
    assert "response_type=code" in loc


def test_login_redirects_to_error_when_env_missing(env_missing):
    c = _logged_in()
    r = c.get("/upstox/login", follow_redirects=False)
    assert r.status_code == 303
    assert "upstox_error=config_missing" in r.headers["location"]


def test_login_blocks_when_not_authenticated(env_set):
    c = TestClient(app)
    r = c.get("/upstox/login", follow_redirects=False)
    assert r.status_code == 303
    assert "/login" in r.headers["location"]


def test_callback_no_code_redirects_with_error(env_set):
    c = _logged_in()
    r = c.get("/upstox/callback", follow_redirects=False)
    assert r.status_code == 303
    assert "upstox_error=no_code" in r.headers["location"]


def test_callback_with_error_param(env_set):
    c = _logged_in()
    r = c.get("/upstox/callback?error=access_denied", follow_redirects=False)
    assert r.status_code == 303
    assert "upstox_error=access_denied" in r.headers["location"]


def test_callback_success_stores_token_in_session(env_set):
    c = _logged_in()
    # First hit /upstox/login so the session has the state cookie set
    c.get("/upstox/login", follow_redirects=False)

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "access_token": "ACCESS_XYZ",
        "user_id": "U123",
        "user_name": "Daman",
    }
    with patch("trading.brokers.upstox.httpx.post", return_value=fake_response):
        r = c.get("/upstox/callback?code=AUTH_CODE", follow_redirects=False)
    assert r.status_code == 303
    assert "upstox_connected=1" in r.headers["location"]

    # status endpoint should now report connected
    s = c.get("/upstox/status").json()
    assert s["connected"] is True
    assert s["user_name"] == "Daman"


def test_callback_state_mismatch_blocks(env_set):
    c = _logged_in()
    # Seed session with a state by calling login
    c.get("/upstox/login", follow_redirects=False)
    # Then send a callback with the wrong state
    r = c.get(
        "/upstox/callback?code=ABC&state=wrong_state",
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "state_mismatch" in r.headers["location"]


def test_callback_token_exchange_failure(env_set):
    c = _logged_in()
    c.get("/upstox/login", follow_redirects=False)
    bad = MagicMock()
    bad.status_code = 401
    bad.text = "invalid_grant"
    with patch("trading.brokers.upstox.httpx.post", return_value=bad):
        r = c.get("/upstox/callback?code=BAD", follow_redirects=False)
    assert r.status_code == 303
    assert "token_exchange_failed" in r.headers["location"]


def test_disconnect_clears_session(env_set):
    c = _logged_in()
    c.get("/upstox/login", follow_redirects=False)
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {
        "access_token": "T", "user_id": "U", "user_name": "X",
    }
    with patch("trading.brokers.upstox.httpx.post", return_value=fake):
        c.get("/upstox/callback?code=A", follow_redirects=False)
    assert c.get("/upstox/status").json()["connected"] is True

    r = c.post("/upstox/disconnect", follow_redirects=False)
    assert r.status_code == 303
    assert "upstox_disconnected=1" in r.headers["location"]
    assert c.get("/upstox/status").json()["connected"] is False


def test_status_when_not_logged_in(env_set):
    c = TestClient(app)
    body = c.get("/upstox/status").json()
    assert body["connected"] is False
    assert body["reason"] == "not_logged_in"


def test_status_env_ok_flag(env_set):
    c = _logged_in()
    body = c.get("/upstox/status").json()
    assert body["connected"] is False
    assert body["env_ok"] is True


def test_status_env_missing(env_missing):
    c = _logged_in()
    body = c.get("/upstox/status").json()
    assert body["connected"] is False
    assert body["env_ok"] is False


def test_sidebar_shows_connect_button_when_not_connected(env_set):
    c = _logged_in()
    r = c.get("/")
    assert r.status_code == 200
    assert "Connect Upstox" in r.text


def test_sidebar_shows_user_name_when_connected(env_set):
    c = _logged_in()
    c.get("/upstox/login", follow_redirects=False)
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {
        "access_token": "T", "user_id": "U", "user_name": "Daman",
    }
    with patch("trading.brokers.upstox.httpx.post", return_value=fake):
        c.get("/upstox/callback?code=A", follow_redirects=False)
    r = c.get("/")
    assert "Upstox: Daman" in r.text
