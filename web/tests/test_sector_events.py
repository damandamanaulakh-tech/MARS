"""Sector event alerts + one-tap segment square-off (owner pick C-narrow).

Owner ask 2026-08-29: "how will app algorithm decide to exit the effected
stock/segement like IT, metals, healthcare" when an FDA or sanctions
headline lands mid-week. They picked the narrow option: the app groups the
holdings a risk headline named and offers one button; the owner decides.

Pinned here: only TRIGGER verdicts count; a FAILED fetch is reported apart
and never becomes an alert OR an all-clear; the whole segment is shown, not
only the named names; the exit is owner-only, goes through the normal close
path, and leaves a name without a live quote OPEN; and the money path never
imports this module — no news rule can fire by itself.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from trading import sector_events
from trading.paper import PaperPosition
from web import auth as auth_mod, state
from web.main import app

SECTORS = {"TATASTEEL.NS": "Metal", "JSWSTEEL.NS": "Metal",
           "HINDALCO.NS": "Metal", "TCS.NS": "IT"}


def _rows(**verdicts) -> dict:
    out = {}
    for ticker, verdict in verdicts.items():
        out[ticker] = {
            "ticker": ticker, "verdict": verdict,
            "why": f"{verdict} on {ticker}",
            "tags": ["regulatory"] if verdict == "TRIGGER" else [],
            "fetch_failed": verdict == "UNKNOWN",
            "headlines": [{"title": f"US sanctions hit {ticker}",
                           "side": "RISK", "source": "ET",
                           "published": "2026-08-29"}],
        }
    return out


def test_only_trigger_verdicts_raise_a_sector_alert():
    held = {t: object() for t in SECTORS}
    rows = _rows(**{"TATASTEEL.NS": "TRIGGER", "JSWSTEEL.NS": "TRIGGER",
                    "HINDALCO.NS": "PASS", "TCS.NS": "NO_NEWS"})
    out = sector_events.sector_alerts(held, rows, SECTORS)
    assert [a["sector"] for a in out] == ["Metal"]
    metal = out[0]
    assert metal["n_flagged"] == 2 and metal["n_held"] == 3
    assert [f["ticker"] for f in metal["flagged"]] == ["JSWSTEEL.NS",
                                                       "TATASTEEL.NS"]
    # the whole segment is shown — the owner asked about exiting a SEGMENT
    assert metal["quiet"] == ["HINDALCO.NS"]
    assert metal["flagged"][0]["headline"].startswith("US sanctions")


def test_a_failed_fetch_is_neither_an_alert_nor_an_all_clear():
    held = {t: object() for t in SECTORS}
    rows = _rows(**{"TATASTEEL.NS": "UNKNOWN", "TCS.NS": "NO_NEWS"})
    assert sector_events.sector_alerts(held, rows, SECTORS) == []
    assert sector_events.unknown_feeds(held, rows) == ["TATASTEEL.NS"]


def test_worst_sector_comes_first():
    held = {t: object() for t in SECTORS}
    rows = _rows(**{"TATASTEEL.NS": "TRIGGER", "JSWSTEEL.NS": "TRIGGER",
                    "TCS.NS": "TRIGGER"})
    assert [a["sector"] for a in
            sector_events.sector_alerts(held, rows, SECTORS)] == ["Metal", "IT"]


def test_helper_is_quiet_and_safe_on_empty_or_malformed_input():
    assert sector_events.sector_alerts({}, {}, {}) == []
    assert sector_events.sector_alerts({"X.NS": object()}, {"X.NS": {}},
                                       {}) == []
    assert sector_events.unknown_feeds({}, {}) == []


def test_money_path_never_imports_sector_events():
    """No news-derived rule may fire by itself — the whole point of C-narrow."""
    root = Path(__file__).resolve().parents[2]
    for mod in ("engine", "risk", "orders", "danger"):
        src = (root / "trading" / f"{mod}.py").read_text()
        body = "\n".join(ln for ln in src.splitlines()
                         if not ln.lstrip().startswith("#"))
        assert "sector_events" not in body, mod


# --------------------------------------------------------------------------
# The page and the button
# --------------------------------------------------------------------------

def _seed_book():
    p = state.portfolio()
    p.positions.clear()
    for t in ("TATASTEEL.NS", "JSWSTEEL.NS", "HINDALCO.NS"):
        p.positions[t] = PaperPosition(
            ticker=t, qty=10, entry_price=100.0,
            entry_date=datetime(2026, 8, 1), mode="nifty200_momentum",
            sector=SECTORS[t], peak_price=100.0, entry_score=70.0,
            reasoning="test fixture")
    p.news_watch = {"ts": "2026-08-29T09:30:00", "rows": _rows(
        **{"TATASTEEL.NS": "TRIGGER", "JSWSTEEL.NS": "TRIGGER",
           "HINDALCO.NS": "NO_NEWS"})}
    cfg = state.cfg()
    cfg.sector_map = {**cfg.sector_map, **SECTORS}
    p.save()
    return p


def _client() -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD})
    assert r.status_code == 200
    return c


def test_positions_page_renders_the_alert(monkeypatch):
    _seed_book()
    # keep the render offline: /positions marks to market, and an unpatched
    # mark reaches for live quotes
    monkeypatch.setattr(state, "marks",
                        lambda ts: ({t: 120.0 for t in ts}, True))
    r = _client().get("/positions")
    assert r.status_code == 200
    body = r.text
    assert "sector_event_marker" in body
    assert "Metal" in body and "2 of 3" in body
    # the honesty line must be on screen, not only in a docstring
    assert "Nothing here sells by itself" in body
    assert "HINDALCO.NS" in body        # the unnamed rest of the segment


def test_sector_exit_closes_only_the_named_names(monkeypatch):
    p = _seed_book()
    monkeypatch.setattr(state, "marks",
                        lambda ts: ({t: 120.0 for t in ts}, True))
    r = _client().post("/positions/sector-exit",
                       data={"sector": "Metal", "scope": "flagged"},
                       follow_redirects=False)
    assert r.status_code == 303
    assert set(p.positions) == {"HINDALCO.NS"}
    entry = next(e for e in reversed(p.decision_journal)
                 if e.get("action") == "OWNER_SECTOR_EXIT")
    assert entry["scope"] == "flagged"
    assert sorted(entry["closed"]) == ["JSWSTEEL.NS", "TATASTEEL.NS"]


def test_sector_scope_closes_the_whole_segment(monkeypatch):
    p = _seed_book()
    monkeypatch.setattr(state, "marks",
                        lambda ts: ({t: 120.0 for t in ts}, True))
    _client().post("/positions/sector-exit",
                   data={"sector": "Metal", "scope": "sector"},
                   follow_redirects=False)
    assert p.positions == {}


def test_a_name_without_a_live_quote_is_left_open(monkeypatch):
    """Never close at an invented price — the 2026-08-04 rule."""
    p = _seed_book()
    monkeypatch.setattr(
        state, "marks",
        lambda ts: ({t: (0.0 if t == "TATASTEEL.NS" else 120.0) for t in ts},
                    True))
    _client().post("/positions/sector-exit",
                   data={"sector": "Metal", "scope": "flagged"},
                   follow_redirects=False)
    assert "TATASTEEL.NS" in p.positions
    entry = next(e for e in reversed(p.decision_journal)
                 if e.get("action") == "OWNER_SECTOR_EXIT")
    assert entry["skipped_no_live_price"] == ["TATASTEEL.NS"]


def test_sector_exit_is_owner_only():
    _seed_book()
    r = TestClient(app).post("/positions/sector-exit",
                             data={"sector": "Metal"},
                             follow_redirects=False)
    assert r.status_code == 303 and "owner_only" in r.headers["location"]


def test_an_unflagged_sector_cannot_be_squared_off(monkeypatch):
    """The button only acts on a segment news actually named today."""
    p = _seed_book()
    r = _client().post("/positions/sector-exit",
                       data={"sector": "IT", "scope": "sector"},
                       follow_redirects=False)
    assert "sector_not_flagged" in r.headers["location"]
    assert len(p.positions) == 3
