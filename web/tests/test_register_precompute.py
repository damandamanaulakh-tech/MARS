"""Node 10 (owner order 2026-08-24: "continue with node 10") — the register
market view precomputed over the FULL store.

What these tests pin: the job reduces streamed close-tails to stored
readings and journals; a thin store stores NOTHING (a 60-name "full panel"
wearing the wide label is the lie F5's disclosure existed to prevent); the
page prefers a fresh precompute and names it, degrades to the old capped
live view when stale or absent; the reading survives a save/load round
trip.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient

from web import auth as auth_mod, state
from web.main import app


def _client() -> TestClient:
    c = TestClient(app)
    c.post("/login", data={"email": "owner@sourceborn.in",
                           "password": auth_mod._SEEDED_PWD})
    return c


def _fake_tails(n_names: int, days: int = 340, end: str = ""):
    end = end or date.today().isoformat()
    dates = [d.date().isoformat() for d in
             __import__("pandas").bdate_range(end=end, periods=days)]
    rng = np.random.default_rng(9)
    docs = []
    for i in range(n_names):
        base = 50.0 + i
        closes = base * np.cumprod(1 + rng.normal(0.0003, 0.01, days))
        docs.append({
            "ticker": f"N{i:04d}.NS", "end": dates[-1],
            "candles": [[d, c, c, c, float(c), 0.0]
                        for d, c in zip(dates, closes)],
        })
    return docs


def test_precompute_stores_readings_and_journals(monkeypatch):
    from web import scheduler
    docs = _fake_tails(200)
    monkeypatch.setattr("web.db.history_tails", lambda rows: iter(docs))
    p = state.portfolio()
    old = p.register_market
    n0 = len(p.decision_journal)
    try:
        out = scheduler.run_register_precompute("test")
        assert out and out["sample"] == 200
        assert out["rows"]                       # PX/FII/C-03 readings present
        # THE PANEL ITEMS must compute — my first cut fed close-only frames,
        # the normalizer rejected every member, and PX read NA while this
        # test still passed on the panel-independent FII/VIX rows. A weak
        # assertion hid a real fault; this one cannot.
        px = out["rows"].get("PX-01") or {}
        assert px.get("state") not in (None, "NA", "DATA_NEEDED"), px
        assert any(e.get("action") == "REGISTER_PRECOMPUTE"
                   for e in p.decision_journal[n0:])
        assert state.portfolio().register_market["sample"] == 200
    finally:
        p.register_market = old
        p.save()


def test_thin_store_stores_nothing(monkeypatch):
    """A 60-name reading must not wear the full-store label — the page keeps
    its honest capped fallback instead."""
    from web import scheduler
    monkeypatch.setattr("web.db.history_tails",
                        lambda rows: iter(_fake_tails(60)))
    p = state.portfolio()
    old = p.register_market
    p.register_market = {}
    try:
        assert scheduler.run_register_precompute("test") == {}
        assert p.register_market == {}
    finally:
        p.register_market = old
        p.save()


def test_page_prefers_a_fresh_precompute_and_names_it():
    p = state.portfolio()
    old = p.register_market
    p.register_market = {
        "asof": date.today().isoformat(),
        "ts": datetime.utcnow().isoformat(),
        "sample": 2641,
        "rows": {"PX-01": {"state": "VALUE", "value": 2.62,
                           "note": "dbi (precomputed)"},
                 "C-03": {"state": "VALUE", "value": 20.0,
                          "note": "industry breadth (precomputed)"}},
    }
    p.save()
    try:
        html = _client().get("/register").text
        assert "register_precompute_marker" in html
        assert "2641" in html
        assert "register_fallback_marker" not in html
    finally:
        p.register_market = old
        p.save()


def test_stale_precompute_falls_back_to_the_capped_live_view():
    """Degrade-to-before: old readings must not wear today's confidence."""
    p = state.portfolio()
    old = p.register_market
    p.register_market = {
        "asof": (date.today() - timedelta(days=30)).isoformat(),
        "ts": "2026-07-01T00:00:00", "sample": 2641,
        "rows": {"PX-01": {"state": "VALUE", "value": 9.9, "note": "old"}},
    }
    p.save()
    try:
        html = _client().get("/register").text
        assert "register_fallback_marker" in html
        assert "register_precompute_marker" not in html
    finally:
        p.register_market = old
        p.save()


def test_reading_survives_a_save_load_round_trip(tmp_path):
    from trading.config import Config
    from trading.paper import Portfolio
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.register_market = {"asof": "2026-08-24", "sample": 2600,
                         "rows": {"PX-01": {"state": "VALUE", "value": 1.0,
                                            "note": "x"}}}
    p.save()
    p2 = Portfolio.load_or_fresh(cfg)
    assert p2.register_market == p.register_market
