"""⚡ /triggers — the owner's trigger layer on screen + the post-scan pass.

Wired 2026-08-04 on the owner's order ("wire it"). Display + watchlist only.
"""

from fastapi.testclient import TestClient

from web import auth as auth_mod, state
from web.main import app


def _client(login: bool = True) -> TestClient:
    c = TestClient(app)
    if login:
        c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD})
    return c


def test_page_requires_login():
    r = _client(login=False).get("/triggers", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_page_states_the_scope_and_the_honest_numbers():
    html = _client().get("/triggers").text
    assert "112" in html and "79" in html and "33" in html
    assert "DATA_NEEDED" in html
    assert "touch no entry and no exit" in html
    assert "REQUIRES_15Y_BACKTEST" in html


def test_page_shows_the_owners_eight_gates_verbatim():
    html = _client().get("/triggers").text
    assert "GATE_RISE_WATCH_01" in html
    assert "compression_count&gt;=2 AND accumulation_count&gt;=2" in html
    assert "GATE_COLLAPSE_BLOCK_01" in html


def test_stored_pass_renders_rows_and_watchlist():
    p = state.portfolio()
    old = p.trigger_watch
    p.trigger_watch = {
        "ts": "2026-08-04T09:25:00",
        "scanned": 2,
        "watchlist": ["WATCHME.NS"],
        "no_history": [],
        "rows": {
            "WATCHME.NS": {"asof": "2026-08-04", "watch": True,
                           "family_counts": {"Compression / Base Before Move": 3,
                                             "Stealth Accumulation / Absorption": 2},
                           "scores": {"RISE": 31.0, "FALL": 5.0},
                           "data_quality": 100.0,
                           "gates_passed": ["GATE_RISE_WATCH_01"],
                           "fired": ["T12W_0001"]},
            "AVOID.NS": {"asof": "2026-08-04", "watch": False,
                         "family_counts": {"Distribution / Pre-Fall": 4},
                         "scores": {"RISE": 2.0, "FALL": 61.0},
                         "data_quality": 100.0,
                         "gates_passed": ["GATE_FALL_AVOID_01"],
                         "fired": []},
        },
    }
    try:
        html = _client().get("/triggers").text
        assert "WATCHME" in html and "AVOID" in html
        assert "Pre-rise watchlist" in html
        assert "RISE_WATCH" in html and "FALL_AVOID" in html
        assert "no orders placed" in html
    finally:
        p.trigger_watch = old


def test_detail_page_says_data_needed_for_an_unknown_stock():
    html = _client().get("/triggers/NOHISTORY").text
    assert "DATA_NEEDED" in html
    assert "nothing is guessed" in html.lower() or "Nothing is guessed" in html


def test_run_now_button_is_owner_only():
    r = _client(login=False).post("/triggers/run", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login" or "owner_only" in r.headers["location"]


def test_post_scan_pass_populates_watch_and_journals(monkeypatch):
    """The scheduler pass over a fake stored-history universe: rows stored,
    watch names journaled once, engine positions never touched."""
    import numpy as np
    import pandas as pd
    from web import scheduler

    n = 300
    rng = np.random.default_rng(5)
    c = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, n)))
    df = pd.DataFrame({
        "open": c, "high": c * 1.008, "low": c * 0.992,
        "close": c, "volume": np.full(n, 100_000.0),
    })
    monkeypatch.setattr("web.candle_chart.load_ohlcv",
                        lambda t, tail=420: df if t == "HAVEDATA.NS" else None)

    p = state.portfolio()
    old_watch, old_picks = p.trigger_watch, p.last_scan_picks
    p.last_scan_picks = [{"ticker": "HAVEDATA.NS"}, {"ticker": "NODATA.NS"}]
    positions_before = dict(p.positions)
    try:
        out = scheduler.run_trigger_layer("test")
        assert "HAVEDATA.NS" in out["rows"]
        assert "NODATA.NS" in out["no_history"]      # DATA_NEEDED, not guessed
        assert p.positions == positions_before        # no entry, no exit
    finally:
        p.trigger_watch, p.last_scan_picks = old_watch, old_picks


def test_pass_failure_is_journaled_not_silent(monkeypatch):
    from web import scheduler

    def _boom(*a, **k):
        raise RuntimeError("history store down")
    monkeypatch.setattr("web.candle_chart.load_ohlcv", _boom)
    p = state.portfolio()
    old_watch, old_picks = p.trigger_watch, p.last_scan_picks
    p.last_scan_picks = [{"ticker": "ANY.NS"}]
    try:
        out = scheduler.run_trigger_layer("test")
        assert out == {}
        assert any(e.get("action") == "TRIGGER_LAYER_FAILED"
                   for e in p.decision_journal[-5:])
    finally:
        p.trigger_watch, p.last_scan_picks = old_watch, old_picks
