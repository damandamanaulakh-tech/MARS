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
        # run_trigger_layer PERSISTS the portfolio, so restoring the attribute
        # alone left the score-less pick in cfg.state_dir/portfolio.json for
        # every later test (and later run) to load — that is what made
        # test_parameter_page's /scan render blow up. Save after restoring.
        p.trigger_watch, p.last_scan_picks = old_watch, old_picks
        p.save()


def test_pass_failure_is_journaled_not_silent(monkeypatch):
    from web import scheduler

    def _boom(*a, **k):
        raise RuntimeError("history store down")
    monkeypatch.setattr("web.candle_chart.load_ohlcv", _boom)
    p = state.portfolio()
    old_watch, old_picks = p.trigger_watch, p.last_scan_picks
    # A pick with no score on purpose: the layer must not care.
    p.last_scan_picks = [{"ticker": "ANY.NS"}]
    try:
        out = scheduler.run_trigger_layer("test")
        assert out == {}
        assert any(e.get("action") == "TRIGGER_LAYER_FAILED"
                   for e in p.decision_journal[-5:])
    finally:
        # run_trigger_layer PERSISTS the portfolio, so restoring the attribute
        # alone left the score-less pick in cfg.state_dir/portfolio.json for
        # every later test (and later run) to load — that is what made
        # test_parameter_page's /scan render blow up. Save after restoring.
        p.trigger_watch, p.last_scan_picks = old_watch, old_picks
        p.save()


# ---------------------------------------------------------------------------
# News-fed triggers on the page (owner order 2026-08-14: "wire them")
# ---------------------------------------------------------------------------

_NEWS_ROW = {
    "ticker": "TATASTEEL.NS", "asof": "2026-08-14", "verdict": "TRIGGER",
    "risk_score": 2.5, "opp_score": 0.0, "tags": ["governance"],
    "sentiment": 0.0, "fetch_failed": False,
    "why": "risk events in the last 7d: governance",
    "headlines": [{"title": "Tata Steel CFO resigns", "published": "Thu, 14 Aug 2026",
                   "source": "ET", "tags": ["governance"], "side": "RISK",
                   "sentiment": 0}],
}


def _with_news(rows: dict):
    p = state.portfolio()
    old = p.news_watch
    p.news_watch = {"ts": "2026-08-14T04:00:00", "rows": rows,
                    "triggered": sorted(rows), "fetch_failed": [],
                    "scanned": len(rows), "lookback_days": 7,
                    "note": "display only"}
    p.save()
    return p, old


def test_stored_news_feeds_the_nine_triggers_and_says_when_it_was_read():
    """The verdict is TODAY's and the price bars are the last STORED ones.
    A page that let the reader assume they were simultaneous would be lying
    by omission."""
    p, old = _with_news({"TATASTEEL.NS": _NEWS_ROW})
    try:
        html = _client().get("/triggers/TATASTEEL.NS").text
        assert "news triggers" in html
        assert "STORED price bar" in html
        assert "never be calibrated" in html
    finally:
        p.news_watch = old
        p.save()


def test_a_stock_with_no_stored_reading_says_data_needed_not_quiet():
    """No reading is not the same as no news, and the page must not let the
    nine look like they checked and found nothing."""
    p, old = _with_news({"TATASTEEL.NS": _NEWS_ROW})
    try:
        html = _client().get("/triggers/RELIANCE.NS").text
        assert "DATA_NEEDED" in html
        assert "not a quiet" in html
    finally:
        p.news_watch = old
        p.save()


def test_page_never_fetches_news_while_rendering(monkeypatch):
    """Rendering must be offline: a page render that hit Google News would
    make every reload a network call and could hang the request."""
    from trading import news_feed

    def _boom(*a, **k):
        raise AssertionError("the page must not fetch news")
    monkeypatch.setattr(news_feed, "fetch_headlines", _boom)
    p, old = _with_news({"TATASTEEL.NS": _NEWS_ROW})
    try:
        assert _client().get("/triggers/TATASTEEL.NS").status_code == 200
    finally:
        p.news_watch = old
        p.save()


def test_news_runs_before_the_trigger_pass_so_the_nine_can_read_it():
    """Order matters: nine of the owner's triggers take a news flag as one of
    their two legs, so a news pass that ran afterwards would always leave
    them DATA_NEEDED."""
    import inspect

    from web import scheduler
    src = inspect.getsource(scheduler.run_trigger_layer)
    assert src.index("run_news_feed") < src.index("external_panels.prepare")
    assert "news=news_rows" in src
