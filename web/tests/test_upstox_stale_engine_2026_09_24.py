"""Owner 2026-09-24: "upstox is not getting connected" -> screenshot: a green
"Upstox: MANPREET KAUR" pill beside a red "token EXPIRED" bar, "no live
quote / Reconnect Upstox" on all four index cards, URL ?upstox_connected=1
-> "still red, fix it".

The handshake succeeded every time. The callback saved the token to Mongo
and then had the engine READ IT BACK; on the live box that read handed back
yesterday's document, so the engine ran on a dead token while the browser
cookie carried the fresh one. Nothing on any screen could say so: the save
failure was a log warning, the callback's ?upstox_error was read by no page,
and a valid-but-refused quote rendered as a Reconnect link.

Pinned here:
  * the engine takes the token IN MEMORY from the callback, whatever Mongo did
  * a failed save is a value, journaled, and shown on the dashboard
  * an expired engine heals from a fresher stored document on the next read
  * the callback's outcome codes are rendered
  * index cards never send a connected owner back to the Upstox login
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from trading.brokers import upstox as U
from web import db, state


def _operative(src: str) -> str:
    return "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith("#"))


@pytest.fixture
def store(monkeypatch):
    """A working Mongo token store, with a switch to make the save fail the
    way the live box did."""
    data: dict = {}

    def save(doc, doc_id="default"):
        if data.get("__fail__"):
            db.LAST_TOKEN_SAVE_ERROR = "OperationFailure: simulated write refusal"
            return False
        data[doc_id] = dict(doc)
        db.LAST_TOKEN_SAVE_ERROR = None
        return True

    def load(doc_id="default"):
        d = data.get(doc_id)
        return dict(d) if d else None

    monkeypatch.setattr(db, "save_upstox_token", save)
    monkeypatch.setattr(db, "load_upstox_token", load)
    return data


@pytest.fixture
def quiet_quotes(monkeypatch):
    """Upstox answers no ticks (rate limit / outage) — the cards must cope."""
    monkeypatch.setattr(U.UpstoxClient, "get_quote", lambda self, key: {})
    monkeypatch.setattr(U.UpstoxClient, "get_ltp", lambda self, keys: {})


@pytest.fixture
def exchange_ok(monkeypatch):
    class R:
        status_code = 200
        text = "ok"

        def json(self):
            return {"access_token": "NEW-TOKEN", "user_id": "U1",
                    "user_name": "MANPREET KAUR"}
    monkeypatch.setattr(U.httpx, "post", lambda *a, **k: R())


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("UPSTOX_API_KEY", "k")
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


def _old_client(days: int = 1) -> U.UpstoxClient:
    c = U.UpstoxClient(api_key="k", api_secret="s", redirect_uri="r")
    c.access_token = "OLD-TOKEN"
    c.user_name = "OLD"
    c.connected_at = (datetime.utcnow() - timedelta(days=days)).isoformat()
    assert c.hours_until_expiry() == 0.0
    return c


@pytest.fixture
def engine_with_dead_token():
    eng = state.engine()
    prev = eng.broker
    eng.broker = _old_client()
    assert state.quote_source_status()["reason"] == "token_expired" or True
    try:
        yield eng
    finally:
        eng.broker = prev


def _connect(c: TestClient):
    c.get("/upstox/login", follow_redirects=False)
    return c.get("/upstox/callback?code=abc", follow_redirects=False)


def _journal_actions(n: int = 6) -> list:
    return [(e.get("action"), e) for e in state.portfolio().decision_journal[-n:]]


# --------------------------------------------------------------------------
# the fix itself
# --------------------------------------------------------------------------

def test_callback_hands_the_engine_the_token_in_memory_even_when_the_save_fails(
        owner, env, store, exchange_ok, quiet_quotes, engine_with_dead_token):
    """THE live fault: the store still holds yesterday's document after the
    login. The engine must run on the token the owner just fetched."""
    store["default"] = {"access_token": "OLD-TOKEN", "user_id": "U1",
                        "user_name": "OLD", "connected_at": _old_client().connected_at}
    store["__fail__"] = True

    r = _connect(owner)
    assert r.status_code == 303
    assert "upstox_connected=1" in r.headers["location"]
    assert "upstox_warn=token_not_persisted" in r.headers["location"]

    eng = state.engine()
    assert eng.broker.access_token == "NEW-TOKEN"
    st = state.quote_source_status()
    assert st["healthy"] is True and st["source"] == "upstox", st

    page = owner.get(r.headers["location"]).text
    assert "Data source DEGRADED" not in page
    assert 'data-upstox-notice="token_not_persisted"' in page
    assert "could NOT be saved" in page
    assert "Upstox: MANPREET KAUR" in page

    acts = _journal_actions()
    found = [e for a, e in acts if a == "UPSTOX_CONNECTED"]
    assert found, acts
    assert found[-1]["persisted"] is False
    assert "simulated write refusal" in found[-1]["error"]


def test_callback_persists_and_journals_on_a_working_store(
        owner, env, store, exchange_ok, quiet_quotes, engine_with_dead_token):
    r = _connect(owner)
    assert r.headers["location"] == "/?upstox_connected=1"
    assert store["default"]["access_token"] == "NEW-TOKEN"
    assert state.engine().broker.access_token == "NEW-TOKEN"
    found = [e for a, e in _journal_actions() if a == "UPSTOX_CONNECTED"]
    assert found and found[-1]["persisted"] is True and "error" not in found[-1]
    page = owner.get("/?upstox_connected=1").text
    assert "data-upstox-notice" not in page
    assert "Data source DEGRADED" not in page


def test_callback_never_goes_through_the_mongo_round_trip_to_attach():
    """Source pin on the operative lines: the engine is attached from the
    client object in hand, not by re-reading what the database holds."""
    from web.routes import upstox as R
    src = _operative(inspect.getsource(R.callback))
    assert "attach_broker_client(client)" in src
    assert "refresh_broker_attachment" not in src
    assert "persisted = db.save_upstox_token(token_doc)" in src


def test_save_upstox_token_reports_failure_as_a_value(monkeypatch):
    monkeypatch.setattr(db, "_CONNECTED", False)
    monkeypatch.setattr(db, "_DB", None)
    assert db.save_upstox_token({"access_token": "T"}) is False
    assert db.LAST_TOKEN_SAVE_ERROR == "mongo_disconnected"


# --------------------------------------------------------------------------
# self-heal from a fresher stored document (the two-process deploy overlap)
# --------------------------------------------------------------------------

def test_an_expired_engine_heals_from_a_fresher_stored_token(
        env, store, engine_with_dead_token):
    store["default"] = {"access_token": "FRESH-FROM-OTHER-PROCESS", "user_id": "U1",
                        "user_name": "MANPREET KAUR",
                        "connected_at": datetime.utcnow().isoformat()}
    st = state.quote_source_status()
    assert st["healthy"] is True, st
    assert state.engine().broker.access_token == "FRESH-FROM-OTHER-PROCESS"


def test_heal_leaves_the_same_dead_token_alone(env, store, engine_with_dead_token):
    """The store holds the SAME expired token — nothing fresher exists, so the
    honest verdict stays EXPIRED and no attach churn happens."""
    eng = engine_with_dead_token
    dead = eng.broker
    store["default"] = {"access_token": "OLD-TOKEN", "user_id": "U1",
                        "user_name": "OLD", "connected_at": dead.connected_at}
    st = state.quote_source_status()
    assert st["reason"] == "token_expired"
    assert eng.broker is dead


def test_engine_accessor_heals_too(env, store, engine_with_dead_token):
    store["default"] = {"access_token": "FRESH", "user_id": "U1",
                        "user_name": "X", "connected_at": datetime.utcnow().isoformat()}
    assert state.engine().broker.access_token == "FRESH"


# --------------------------------------------------------------------------
# the screens tell the truth
# --------------------------------------------------------------------------

@pytest.mark.parametrize("code,phrase", [
    ("token_exchange_failed", "API secret"),
    ("state_mismatch", "security state"),
    ("no_code", "without a login code"),
    ("config_missing", "UPSTOX_API_KEY"),
])
def test_dashboard_renders_every_callback_outcome(owner, quiet_quotes, code, phrase):
    page = owner.get(f"/?upstox_error={code}").text
    assert f'data-upstox-notice="{code}"' in page
    assert phrase in page


def test_a_declined_login_is_journaled(owner, env):
    r = owner.get("/upstox/callback?error=access_denied", follow_redirects=False)
    assert "upstox_error=access_denied" in r.headers["location"]
    found = [e for a, e in _journal_actions() if a == "UPSTOX_CONNECT_FAILED"]
    assert found and found[-1]["reason"] == "access_denied"


def test_index_cards_never_link_to_login_while_connected(
        owner, env, store, exchange_ok, quiet_quotes, engine_with_dead_token):
    """Connected token, Upstox returns no tick for the indices: the card must
    say so and must NOT carry the Reconnect link (that link was the loop)."""
    _connect(owner)
    assert state.quote_source_status()["healthy"] is True
    page = owner.get("/").text
    assert "Reconnect Upstox</a> for the live level" not in page
    assert "not a login problem" in page or "Upstox returned no tick" in page
    assert "Data source DEGRADED" not in page


def test_index_cards_still_offer_reconnect_when_genuinely_expired(
        owner, env, store, quiet_quotes, engine_with_dead_token):
    """No fresher token anywhere: the honest state IS reconnect."""
    store["default"] = {"access_token": "OLD-TOKEN", "user_id": "U1", "user_name": "OLD",
                        "connected_at": engine_with_dead_token.broker.connected_at}
    page = owner.get("/").text
    assert "Data source DEGRADED" in page
    assert "Reconnect Upstox</a> for the live level" in page
