"""Where the weekly batch details live (owner ask 2026-09-04: "where is
details for new batch every monday").

The Friday job now records the whole story on the portfolio —
persisted JSON and Mongo both ways, the risk_day lesson — and /scan shows
it on the screen the owner buys from: count vs target, SHORT BY when the
store cannot yet fill a 500, what came in, what went out, and when the
next rebuild is.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from trading.config import Config
from trading.paper import Portfolio
from web import auth as auth_mod, state
from web.main import app

BATCH = {"ts": "2026-09-04T11:05:00", "where": "weekly", "count": 3,
         "target": 500, "sample": 4, "judged": 3, "short_by": 497,
         "skipped_thin": 0, "skipped_illiquid": 1,
         "added": ["BIG.NS", "MID.NS"], "dropped": ["OLD.NS"],
         "held_added": ["HELD.NS"]}


def test_batch_survives_a_json_save_and_load(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.universe_batch = dict(BATCH)
    p.save()
    p2 = Portfolio.load_or_fresh(cfg)
    assert p2.universe_batch == BATCH


def test_batch_is_in_the_mongo_mirror_both_ways():
    """A field persisted one way only dies on the next Render restart."""
    from pathlib import Path
    src = Path(state.__file__).read_text()
    assert 'p.universe_batch = dict(doc.get("universe_batch"' in src
    assert '"universe_batch": self.universe_batch' in src


def _client():
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD})
    assert r.status_code == 200
    return c


def test_scan_page_shows_the_batch_and_says_how_short_it_is(monkeypatch):
    p = state.portfolio()
    p.universe_batch = dict(BATCH)
    p.save()
    r = _client().get("/scan")
    assert r.status_code == 200
    body = r.text
    assert "universe_batch_marker" in body
    assert "SHORT BY 497" in body
    assert "BIG.NS, MID.NS" in body and "OLD.NS" in body
    assert "never sells it" in body            # dropping != selling
    assert "next rebuild" in body


def test_card_hides_when_no_batch_has_run(monkeypatch):
    p = state.portfolio()
    p.universe_batch = {}
    p.save()
    body = _client().get("/scan").text
    assert "universe_batch_marker" not in body
