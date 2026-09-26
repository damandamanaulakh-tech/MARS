"""Owner screenshot 2026-09-26 08:01 IST: the browser on api-v2.upstox.com
showing raw JSON — UDAPI100016 "Invalid Credentials" — before the app's
callback was ever reached. Measured the same day from the sandbox: an
unknown client_id or an unregistered redirect_uri answers 401 JSON
UDAPI100068 on both Upstox hosts; a registered app answers a login page.

Pinned here: env values are whitespace-stripped (a pasted trailing space in
Render is the cheapest way to make client_id/redirect_uri stop matching
the developer app), and the server-side pre-flight reads Upstox's answer
honestly — an error document is a rejection, a login page is a pass, and
network trouble is NOT a rejection.
"""
from __future__ import annotations

import httpx
import pytest

from trading.brokers import upstox as U


def test_from_env_strips_pasted_whitespace(monkeypatch):
    monkeypatch.setenv("UPSTOX_API_KEY", "  abc-123 \n")
    monkeypatch.setenv("UPSTOX_API_SECRET", "\tsecret\n")
    monkeypatch.setenv("UPSTOX_REDIRECT_URI",
                       " https://sourceborn-stocks.onrender.com/upstox/callback\n")
    c = U.UpstoxClient.from_env()
    assert c.api_key == "abc-123"
    assert c.api_secret == "secret"
    assert c.redirect_uri == "https://sourceborn-stocks.onrender.com/upstox/callback"
    assert "%20" not in c.authorization_url() and "%0A" not in c.authorization_url()


def test_from_env_whitespace_only_is_still_missing(monkeypatch):
    monkeypatch.setenv("UPSTOX_API_KEY", "   ")
    monkeypatch.setenv("UPSTOX_API_SECRET", "s")
    monkeypatch.setenv("UPSTOX_REDIRECT_URI", "https://x/cb")
    with pytest.raises(U.UpstoxConfigError):
        U.UpstoxClient.from_env()


class _Resp:
    def __init__(self, text, status=200, host="api-v2.upstox.com"):
        self.text = text
        self.status_code = status
        self.url = httpx.URL(f"https://{host}/login/authorization/dialog")

    def json(self):
        import json
        return json.loads(self.text)


def _client() -> U.UpstoxClient:
    return U.UpstoxClient(api_key="k", api_secret="s",
                          redirect_uri="https://x/upstox/callback")


def test_preflight_reads_an_upstox_error_document(monkeypatch):
    body = ('{"status":"error","errors":[{"errorCode":"UDAPI100016",'
            '"message":"Invalid Credentials","propertyPath":null}]}')
    monkeypatch.setattr(U.httpx, "get", lambda *a, **k: _Resp(body, 401))
    rej = _client().check_app_registration(state="abc")
    assert rej and rej["code"] == "UDAPI100016"
    assert rej["message"] == "Invalid Credentials"
    assert rej["host"] == "api-v2.upstox.com"


def test_preflight_passes_a_login_page(monkeypatch):
    monkeypatch.setattr(U.httpx, "get",
                        lambda *a, **k: _Resp("<!doctype html><html>login</html>"))
    assert _client().check_app_registration() is None


def test_preflight_never_blocks_on_network_trouble(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("proxy down")
    monkeypatch.setattr(U.httpx, "get", boom)
    assert _client().check_app_registration() is None


def test_preflight_sends_the_same_url_the_browser_would_get(monkeypatch):
    seen = {}

    def fake_get(url, **k):
        seen["url"] = url
        seen["follow"] = k.get("follow_redirects")
        return _Resp("<html/>")
    monkeypatch.setattr(U.httpx, "get", fake_get)
    c = _client()
    c.check_app_registration(state="S1")
    assert seen["url"] == c.authorization_url(state="S1")
    assert seen["follow"] is True         # the rejection lives on the v2 host
