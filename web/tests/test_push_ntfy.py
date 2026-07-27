"""Phone alerts via ntfy.sh (owner-approved 2026-07-27)."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from web import auth as auth_mod, push_ntfy, state
from web.main import app


class _FakeDB:
    """In-memory stand-in for the Mongo settings doc."""
    def __init__(self):
        self.doc = {}
        self.connected = True


def _wire(monkeypatch, fake):
    monkeypatch.setattr(push_ntfy, "_load", lambda: dict(fake.doc))
    monkeypatch.setattr(push_ntfy, "_save", lambda d: fake.doc.update(d))
    monkeypatch.setattr(push_ntfy.db, "is_connected", lambda: fake.connected)
    monkeypatch.delenv("NTFY_TOPIC", raising=False)


def test_unavailable_without_mongo(monkeypatch):
    fake = _FakeDB(); fake.connected = False
    _wire(monkeypatch, fake)
    st = push_ntfy.get_state()
    assert st["available"] is False
    assert push_ntfy.send("x", "y") is False       # never sends into the void


def test_enable_creates_a_private_topic_once(monkeypatch):
    fake = _FakeDB(); _wire(monkeypatch, fake)
    st = push_ntfy.enable()
    assert st["enabled"] and st["topic"].startswith("am07-")
    assert len(st["topic"]) == len("am07-") + 24   # 12 hex bytes
    first = st["topic"]
    push_ntfy.disable()
    assert push_ntfy.enable()["topic"] == first     # stable across toggles


def test_send_posts_to_the_topic(monkeypatch):
    fake = _FakeDB(); _wire(monkeypatch, fake)
    push_ntfy.enable()
    with patch("web.push_ntfy.httpx.post") as post:
        post.return_value.status_code = 200
        assert push_ntfy.send("T", "hello") is True
        url = post.call_args[0][0]
        assert url == f"https://ntfy.sh/{fake.doc['topic']}"
        assert post.call_args[1]["headers"]["Title"] == "T"


def test_send_swallows_transport_errors(monkeypatch):
    fake = _FakeDB(); _wire(monkeypatch, fake)
    push_ntfy.enable()
    with patch("web.push_ntfy.httpx.post", side_effect=OSError("net down")):
        assert push_ntfy.send("T", "hello") is False   # no raise = scan survives


def test_journal_push_filters_batches_and_advances_cursor(monkeypatch):
    fake = _FakeDB(); _wire(monkeypatch, fake)
    push_ntfy.enable()
    p = state.portfolio()
    saved = list(p.decision_journal)
    p.decision_journal = [
        {"ts": "2026-07-27T04:00:00", "action": "BUY", "ticker": "TCS.NS"},
        {"ts": "2026-07-27T04:01:00", "action": "HOLD", "ticker": "X.NS"},        # noise
        {"ts": "2026-07-27T04:02:00", "action": "OCEAN_BLOCKED", "why": "parked"}, # noise
        {"ts": "2026-07-27T04:03:00", "action": "SELL", "ticker": "INFY.NS",
         "reason": "STOP_LOSS"},
        {"ts": "2026-07-27T04:04:00", "action": "SCAN_FAILED", "why": "token"},
    ]
    try:
        with patch("web.push_ntfy.httpx.post") as post:
            post.return_value.status_code = 200
            n = push_ntfy.push_journal_events()
            assert n == 3                              # BUY, SELL, SCAN_FAILED only
            body = post.call_args[1]["content"].decode()
            assert "TCS.NS" in body and "INFY.NS" in body and "STOP_LOSS" in body
            assert "HOLD" not in body and "OCEAN_BLOCKED" not in body
            assert post.call_args[1]["headers"]["Priority"] == "high"  # SCAN_FAILED
            # cursor advanced -> second call pushes nothing
            assert push_ntfy.push_journal_events() == 0
            assert fake.doc["last_push_ts"] == "2026-07-27T04:04:00"
    finally:
        p.decision_journal = saved


def test_disabled_means_no_network_call(monkeypatch):
    fake = _FakeDB(); _wire(monkeypatch, fake)
    with patch("web.push_ntfy.httpx.post") as post:
        assert push_ntfy.push_journal_events() == 0
        post.assert_not_called()


def test_settings_routes_are_owner_gated(monkeypatch):
    fake = _FakeDB(); _wire(monkeypatch, fake)
    anon = TestClient(app)
    r = anon.post("/settings/push/enable", follow_redirects=False)
    assert r.status_code == 303 and "denied" in r.headers["location"]
    assert not fake.doc.get("enabled")

    owner = TestClient(app)
    owner.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD})
    owner.post("/settings/push/enable", follow_redirects=False)
    assert fake.doc.get("enabled") is True
    page = owner.get("/settings").text
    assert "Phone alerts" in page
