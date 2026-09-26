"""Owner 2026-09-26: "Still upstox not connecting / Diagnos the issue" ->
screenshot of api-v2.upstox.com printing UDAPI100016 "Invalid Credentials"
-> "Review and fix".

Two faults, both pinned here:

1. The engine rebuild (`state.reload_config`, run by the dashboard mode
   switch and every Settings save) started with NO broker, so the token the
   login had just handed the engine in memory was dropped and the next read
   healed from a stale Mongo document — red again minutes after green.
2. When Upstox rejects the app itself (client_id / redirect_uri do not match
   its developer page), the browser used to be sent straight into Upstox's
   raw JSON with nothing in the app able to read it. The login route now
   asks Upstox first and lands the owner on the dashboard with Upstox's own
   words and the exact redirect_uri the app sent.
"""
from __future__ import annotations

import inspect
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from trading.brokers import upstox as U
from web import state
from web.routes import upstox as upstox_routes


def _operative(src: str) -> str:
    return "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith("#"))


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("UPSTOX_API_KEY", "k-1234")
    monkeypatch.setenv("UPSTOX_API_SECRET", "s")
    monkeypatch.setenv("UPSTOX_REDIRECT_URI", "https://x/upstox/callback")


@pytest.fixture
def owner():
    from web.main import app
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": "sourceborn"}, follow_redirects=False)
    assert r.status_code == 303
    return c


@pytest.fixture
def quiet_quotes(monkeypatch):
    monkeypatch.setattr(U.UpstoxClient, "get_quote", lambda self, key: {})
    monkeypatch.setattr(U.UpstoxClient, "get_ltp", lambda self, keys: {})


def _fresh_client() -> U.UpstoxClient:
    c = U.UpstoxClient(api_key="k", api_secret="s", redirect_uri="r")
    c.access_token = "FRESH"
    c.user_name = "OWNER"
    c.connected_at = datetime.utcnow().isoformat()
    return c


# --------------------------------------------------------------------------
# 1. the engine rebuild keeps the owner's session
# --------------------------------------------------------------------------

def test_reload_config_keeps_the_in_memory_token():
    """Behavioural: login -> reload -> the router still says Upstox live.
    Reproduced 2026-09-26 before the fix: `ok` -> `no_upstox_token`."""
    eng = state.engine()
    prev = eng.broker
    try:
        state.attach_broker_client(_fresh_client())
        assert state.quote_source_status()["reason"] == "ok"
        state.reload_config()
        assert state.quote_source_status()["reason"] == "ok"
        assert state.engine().broker.access_token == "FRESH"
    finally:
        state.engine().attach_broker(prev)


def test_reload_config_with_no_broker_stays_without_one():
    """Carrying None across must not invent a client."""
    eng = state.engine()
    prev = eng.broker
    try:
        eng.attach_broker(None)
        state.reload_config()
        assert state.engine().broker is None or state.engine().broker.access_token != "FRESH"
    finally:
        state.engine().attach_broker(prev)


def test_mode_switch_route_keeps_the_token(owner, env):
    """The dashboard's own mode quick-switch is the reload_config caller the
    owner is most likely to tap right after logging in."""
    cfg = state.cfg()
    saved_mode = cfg.mode
    prev = state.engine().broker
    try:
        state.attach_broker_client(_fresh_client())
        r = owner.post("/settings/mode", data={"mode": saved_mode},
                       follow_redirects=False)
        assert r.status_code == 303
        assert state.quote_source_status()["reason"] == "ok"
    finally:
        cfg.mode = saved_mode
        cfg.apply_mode_preset()
        state.engine().attach_broker(prev)


# --------------------------------------------------------------------------
# 2. the pre-flight turns Upstox's raw JSON into a readable bar
# --------------------------------------------------------------------------

def _rejecting(monkeypatch, code="UDAPI100016", message="Invalid Credentials"):
    monkeypatch.delenv("SB_SKIP_UPSTOX_PREFLIGHT", raising=False)
    monkeypatch.setattr(
        U.UpstoxClient, "check_app_registration",
        lambda self, state=None, timeout=8.0: {
            "code": code, "message": message, "host": "api-v2.upstox.com",
            "http_status": 401})


def _accepting(monkeypatch):
    monkeypatch.delenv("SB_SKIP_UPSTOX_PREFLIGHT", raising=False)
    monkeypatch.setattr(U.UpstoxClient, "check_app_registration",
                        lambda self, state=None, timeout=8.0: None)


def test_a_rejected_app_lands_on_the_dashboard_not_on_upstox_json(
        owner, env, monkeypatch):
    _rejecting(monkeypatch)
    r = owner.get("/upstox/login", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/?upstox_error=app_rejected"
    rej = upstox_routes.LAST_APP_REJECTION
    assert rej and rej["code"] == "UDAPI100016"
    assert rej["redirect_uri"] == "https://x/upstox/callback"
    assert rej["client_id_tail"] == "1234"
    last = [e for e in state.portfolio().decision_journal[-6:]
            if e.get("action") == "UPSTOX_CONNECT_FAILED"]
    assert last and last[-1]["reason"] == "app_rejected"
    assert "UDAPI100016" in last[-1]["detail"]
    assert "https://x/upstox/callback" in last[-1]["detail"]


def test_the_bar_prints_upstox_words_and_the_redirect_uri_sent(
        owner, env, quiet_quotes, monkeypatch):
    _rejecting(monkeypatch)
    owner.get("/upstox/login", follow_redirects=False)
    page = owner.get("/?upstox_error=app_rejected").text
    assert 'data-upstox-notice="app_rejected"' in page
    assert "UDAPI100016" in page and "Invalid Credentials" in page
    assert "https://x/upstox/callback" in page
    assert "…1234" in page
    assert "password" in page          # says the owner's PIN is NOT involved


def test_an_accepted_app_goes_to_upstox_as_before(owner, env, monkeypatch):
    _accepting(monkeypatch)
    r = owner.get("/upstox/login", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith(U.AUTH_URL)
    assert "client_id=k-1234" in r.headers["location"]
    assert upstox_routes.LAST_APP_REJECTION is None


def test_the_test_switch_skips_the_preflight_entirely(owner, env, monkeypatch):
    """With SB_SKIP_UPSTOX_PREFLIGHT=1 (both conftests) no test ever GETs
    Upstox's dialog — the 2026-08-26 lesson about live endpoints in tests."""
    monkeypatch.setenv("SB_SKIP_UPSTOX_PREFLIGHT", "1")
    called = {"n": 0}

    def spy(self, state=None, timeout=8.0):
        called["n"] += 1
        return {"code": "X", "message": "must not be consulted"}
    monkeypatch.setattr(U.UpstoxClient, "check_app_registration", spy)
    r = owner.get("/upstox/login", follow_redirects=False)
    assert called["n"] == 0
    assert r.headers["location"].startswith(U.AUTH_URL)


def test_the_skip_switch_never_reaches_production():
    """render.yaml must not carry the test-only switch (the
    SB_IGNORE_MARKET_HOURS pattern)."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    assert "SB_SKIP_UPSTOX_PREFLIGHT" not in (root / "render.yaml").read_text()
    src = _operative(inspect.getsource(upstox_routes.login))
    assert 'os.environ.get("SB_SKIP_UPSTOX_PREFLIGHT") != "1"' in src
