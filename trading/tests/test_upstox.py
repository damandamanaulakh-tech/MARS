"""Unit tests for trading/brokers/upstox.py — fully mocked, no network."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from trading.brokers.upstox import (
    UpstoxAuthError,
    UpstoxClient,
    UpstoxConfigError,
)


# ---- env / config ----

def test_from_env_raises_when_keys_missing(monkeypatch):
    for k in ("UPSTOX_API_KEY", "UPSTOX_API_SECRET", "UPSTOX_REDIRECT_URI"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(UpstoxConfigError):
        UpstoxClient.from_env()


def test_from_env_builds_when_keys_present(monkeypatch):
    monkeypatch.setenv("UPSTOX_API_KEY", "key123")
    monkeypatch.setenv("UPSTOX_API_SECRET", "secret456")
    monkeypatch.setenv("UPSTOX_REDIRECT_URI", "https://example.com/upstox/callback")
    c = UpstoxClient.from_env()
    assert c.api_key == "key123"
    assert c.api_secret == "secret456"
    assert c.redirect_uri == "https://example.com/upstox/callback"
    assert c.access_token is None


# ---- authorization URL ----

def test_authorization_url_includes_required_params():
    c = UpstoxClient(
        api_key="K", api_secret="S",
        redirect_uri="https://app.example.com/upstox/callback",
    )
    url = c.authorization_url()
    assert url.startswith("https://api.upstox.com/v2/login/authorization/dialog?")
    assert "response_type=code" in url
    assert "client_id=K" in url
    # The redirect_uri ends up URL-encoded
    assert "redirect_uri=https" in url


def test_authorization_url_state_param_included():
    c = UpstoxClient(api_key="K", api_secret="S", redirect_uri="https://x/")
    url = c.authorization_url(state="abc123")
    assert "state=abc123" in url


# ---- code -> token exchange ----

def test_exchange_code_success_sets_token():
    c = UpstoxClient(api_key="K", api_secret="S", redirect_uri="https://x/")
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "access_token": "ACCESS_XYZ",
        "user_id": "U123",
        "user_name": "Daman",
        "broker": "UPSTOX",
    }
    with patch("trading.brokers.upstox.httpx.post", return_value=fake_response):
        body = c.exchange_code("AUTH_CODE")
    assert body["access_token"] == "ACCESS_XYZ"
    assert c.access_token == "ACCESS_XYZ"
    assert c.user_id == "U123"
    assert c.user_name == "Daman"
    assert c.connected_at is not None
    assert c.is_connected()


def test_exchange_code_http_error_raises_auth_error():
    c = UpstoxClient(api_key="K", api_secret="S", redirect_uri="https://x/")
    bad_response = MagicMock()
    bad_response.status_code = 401
    bad_response.text = '{"error":"invalid_grant"}'
    with patch("trading.brokers.upstox.httpx.post", return_value=bad_response):
        with pytest.raises(UpstoxAuthError):
            c.exchange_code("BAD_CODE")
    assert c.access_token is None


def test_exchange_code_missing_token_in_response():
    c = UpstoxClient(api_key="K", api_secret="S", redirect_uri="https://x/")
    weird_response = MagicMock()
    weird_response.status_code = 200
    weird_response.json.return_value = {"foo": "bar"}  # no access_token
    with patch("trading.brokers.upstox.httpx.post", return_value=weird_response):
        with pytest.raises(UpstoxAuthError):
            c.exchange_code("CODE")


def test_exchange_code_network_failure():
    import httpx as _httpx
    c = UpstoxClient(api_key="K", api_secret="S", redirect_uri="https://x/")
    with patch(
        "trading.brokers.upstox.httpx.post",
        side_effect=_httpx.ConnectError("boom"),
    ):
        with pytest.raises(UpstoxAuthError):
            c.exchange_code("CODE")


# ---- token expiry math (03:30 IST = 22:00 UTC previous day) ----

def test_token_expires_at_ist_returns_today_2200_utc_if_connected_morning():
    # Connected at 06:00 UTC (11:30 IST) -> expires today 22:00 UTC (03:30 IST tomorrow)
    connected = "2026-05-29T06:00:00"
    expiry = UpstoxClient.token_expires_at_ist(connected)
    assert expiry is not None
    assert expiry.hour == 22 and expiry.minute == 0
    assert expiry.date() == datetime(2026, 5, 29).date()


def test_token_expires_at_ist_returns_tomorrow_when_connected_late():
    # Connected at 23:00 UTC (already past 22:00 UTC today) -> expires tomorrow 22:00 UTC
    connected = "2026-05-29T23:00:00"
    expiry = UpstoxClient.token_expires_at_ist(connected)
    assert expiry is not None
    assert expiry.date() == datetime(2026, 5, 30).date()


def test_token_expires_at_ist_none_for_empty_input():
    assert UpstoxClient.token_expires_at_ist(None) is None
    assert UpstoxClient.token_expires_at_ist("not-a-date") is None


def test_hours_until_expiry_positive_when_connected_now():
    c = UpstoxClient(api_key="K", api_secret="S", redirect_uri="https://x/")
    c.connected_at = datetime.utcnow().isoformat()
    hrs = c.hours_until_expiry()
    assert hrs is not None
    assert 0 <= hrs <= 24


# ---- authed REST helpers ----

def test_auth_headers_raises_when_no_token():
    c = UpstoxClient(api_key="K", api_secret="S", redirect_uri="https://x/")
    with pytest.raises(UpstoxAuthError):
        c._auth_headers()


def test_auth_headers_includes_bearer():
    c = UpstoxClient(api_key="K", api_secret="S", redirect_uri="https://x/")
    c.access_token = "T"
    h = c._auth_headers()
    assert h["Authorization"] == "Bearer T"
    assert h["accept"] == "application/json"


def test_get_quote_returns_data_on_success():
    c = UpstoxClient(api_key="K", api_secret="S", redirect_uri="https://x/")
    c.access_token = "T"
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {
        "data": {
            "NSE_EQ:RELIANCE": {"last_price": 2450.5, "ohlc": {"close": 2440.0}},
        },
    }
    fake.raise_for_status = MagicMock()
    with patch("trading.brokers.upstox.httpx.get", return_value=fake):
        q = c.get_quote("NSE_EQ|INE002A01018")
    assert q["last_price"] == 2450.5


def test_get_quote_returns_none_on_http_error():
    import httpx as _httpx
    c = UpstoxClient(api_key="K", api_secret="S", redirect_uri="https://x/")
    c.access_token = "T"
    with patch(
        "trading.brokers.upstox.httpx.get",
        side_effect=_httpx.ConnectError("offline"),
    ):
        assert c.get_quote("NSE_EQ|INE002A01018") is None


def test_get_profile_returns_data():
    c = UpstoxClient(api_key="K", api_secret="S", redirect_uri="https://x/")
    c.access_token = "T"
    fake = MagicMock()
    fake.json.return_value = {"data": {"email": "you@example.com", "broker": "UPSTOX"}}
    fake.raise_for_status = MagicMock()
    with patch("trading.brokers.upstox.httpx.get", return_value=fake):
        d = c.get_profile()
    assert d["email"] == "you@example.com"
