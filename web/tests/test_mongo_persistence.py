"""Tests for Mongo-backed Upstox token + portfolio persistence (mocked Mongo)."""

from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from web import auth as auth_mod
from web import db
from web.main import app


def _logged_in() -> TestClient:
    c = TestClient(app)
    c.post(
        "/login",
        data={"email": "owner@sourceborn.in", "password": auth_mod._SEEDED_PWD},
    )
    return c


# ---------- token persistence ----------

def test_save_upstox_token_noop_when_disconnected():
    # MONGO_URL not set in tests -> connection is False
    db.save_upstox_token({"access_token": "T"})
    assert db.load_upstox_token() is None


def test_save_and_load_upstox_token_with_mocked_mongo():
    fake_collection = MagicMock()
    fake_collection.find_one.return_value = {
        "_id": "default",
        "access_token": "TOKEN_FROM_MONGO",
        "user_id": "U99",
        "user_name": "PersistedUser",
        "connected_at": "2026-05-29T08:30:00",
    }
    fake_db = MagicMock()
    fake_db.upstox_token = fake_collection
    with patch.object(db, "_DB", fake_db), patch.object(db, "_CONNECTED", True):
        db.save_upstox_token({"access_token": "TOKEN_FROM_MONGO"})
        fake_collection.replace_one.assert_called_once()
        loaded = db.load_upstox_token()
    assert loaded["access_token"] == "TOKEN_FROM_MONGO"
    assert loaded["user_name"] == "PersistedUser"
    assert "_id" not in loaded


def test_clear_upstox_token_calls_delete():
    fake_collection = MagicMock()
    fake_db = MagicMock()
    fake_db.upstox_token = fake_collection
    with patch.object(db, "_DB", fake_db), patch.object(db, "_CONNECTED", True):
        db.clear_upstox_token()
    fake_collection.delete_one.assert_called_once_with({"_id": "default"})


# ---------- token survives "restart" (session reset) ----------

def test_session_falls_back_to_mongo_on_cold_start(monkeypatch):
    """Simulate Render restart: session empty, but Mongo has the token."""
    monkeypatch.setenv("UPSTOX_API_KEY", "K")
    monkeypatch.setenv("UPSTOX_API_SECRET", "S")
    monkeypatch.setenv("UPSTOX_REDIRECT_URI", "https://x/upstox/callback")

    fake_token_doc = {
        "_id": "default",
        "access_token": "PERSISTED_TOK",
        "user_id": "U7",
        "user_name": "Daman",
        "connected_at": "2026-05-29T08:30:00",
    }
    fake_db = MagicMock()
    fake_db.upstox_token.find_one.return_value = fake_token_doc

    with patch.object(db, "_DB", fake_db), patch.object(db, "_CONNECTED", True):
        c = _logged_in()
        # No /upstox/login or /callback was hit, session has no token
        body = c.get("/upstox/status").json()
        assert body["connected"] is True
        assert body["user_name"] == "Daman"


def test_status_remains_false_when_mongo_empty(monkeypatch):
    monkeypatch.setenv("UPSTOX_API_KEY", "K")
    monkeypatch.setenv("UPSTOX_API_SECRET", "S")
    monkeypatch.setenv("UPSTOX_REDIRECT_URI", "https://x/upstox/callback")

    fake_db = MagicMock()
    fake_db.upstox_token.find_one.return_value = None
    with patch.object(db, "_DB", fake_db), patch.object(db, "_CONNECTED", True):
        c = _logged_in()
        body = c.get("/upstox/status").json()
        assert body["connected"] is False


# ---------- portfolio persistence ----------

def test_save_portfolio_noop_when_disconnected():
    db.save_portfolio({"cash": 999.0})
    assert db.load_portfolio() is None


def test_save_and_load_portfolio_with_mocked_mongo():
    fake_collection = MagicMock()
    fake_collection.find_one.return_value = {
        "_id": "default",
        "cash": 2_400_000.0,
        "positions": {},
        "trades": [],
        "equity_history": [],
        "last_scan": None,
        "last_scan_picks": [],
        "last_save": None,
    }
    fake_db = MagicMock()
    fake_db.portfolio = fake_collection
    with patch.object(db, "_DB", fake_db), patch.object(db, "_CONNECTED", True):
        db.save_portfolio({"cash": 2_400_000.0})
        fake_collection.replace_one.assert_called_once()
        loaded = db.load_portfolio()
    assert loaded["cash"] == 2_400_000.0
    assert "_id" not in loaded
