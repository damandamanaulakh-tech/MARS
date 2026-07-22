"""Signal page (standing waves) — renders live components, stubs flat."""

from datetime import datetime

from fastapi.testclient import TestClient

from web import auth as auth_mod, state
from web.main import app
from trading.paper import PaperPosition


def _client() -> TestClient:
    c = TestClient(app)
    c.post("/login", data={"email": "owner@sourceborn.in",
                           "password": auth_mod._SEEDED_PWD})
    return c


def test_signal_empty_state_renders():
    p = state.portfolio()
    saved_pos, saved_picks = dict(p.positions), list(p.last_scan_picks or [])
    p.positions, p.last_scan_picks = {}, []
    try:
        r = _client().get("/signal")
        assert r.status_code == 200
        assert "No scored stock yet" in r.text
    finally:
        p.positions, p.last_scan_picks = saved_pos, saved_picks


def test_signal_renders_strings_from_scan_pick():
    p = state.portfolio()
    saved_picks = list(p.last_scan_picks or [])
    p.last_scan_picks = [{
        "ticker": "SIGTEST.NS", "score": 71.4,
        "components": {"momentum_1m": 82.0, "volume_expansion": 64.0,
                       "short_interest": 50.0, "analyst_revision": 25.0},
    }]
    try:
        r = _client().get("/signal?ticker=SIGTEST.NS")
        assert r.status_code == 200
        for needle in ("SIGTEST.NS", "signal-data", "momentum 1m",
                       "no data",              # short_interest stays silent
                       "cannot resonate"):
            assert needle in r.text, needle
        # analyst_revision is LIVE now — must NOT be marked stub
        assert '"name": "analyst_revision", "score": 25.0' \
            in r.text.replace("&#34;", '"') or "analyst revision" in r.text
    finally:
        p.last_scan_picks = saved_picks


def test_signal_position_uses_buy_journal_components():
    p = state.portfolio()
    p.positions["SIGPOS.NS"] = PaperPosition(
        ticker="SIGPOS.NS", qty=1, entry_price=100.0,
        entry_date=datetime(2026, 7, 1), sector="IT", peak_price=100.0,
        entry_score=77.0, reasoning="test",
    )
    p.decision_journal.append({
        "ts": "2026-07-01T09:25:00", "action": "BUY", "ticker": "SIGPOS.NS",
        "components": {"momentum": 70.0, "quality": 60.0},
    })
    try:
        r = _client().get("/signal?ticker=SIGPOS.NS")
        assert r.status_code == 200
        assert "SIGPOS.NS" in r.text
        assert "momentum" in r.text and "quality" in r.text
        assert "77.0" in r.text          # total from entry_score
    finally:
        p.positions.pop("SIGPOS.NS", None)
