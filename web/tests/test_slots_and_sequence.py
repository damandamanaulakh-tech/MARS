"""Slot funnel on /scan + selection-sequence ladder on /triggers.

Owner orders 2026-08-15: "wire the slot sizing too" and "stock selection
sequence is still pending". Both display only — these tests pin the screens,
the honest wording, and that old stored state (rows persisted before the
stage existed) still renders.
"""
from fastapi.testclient import TestClient

from web import auth as auth_mod, state
from web.main import app


def _client() -> TestClient:
    c = TestClient(app)
    c.post("/login", data={"email": "owner@sourceborn.in",
                           "password": auth_mod._SEEDED_PWD})
    return c


# ---------------------------------------------------------------------------
# /scan slot funnel
# ---------------------------------------------------------------------------

def _with_picks(n: int, cash: float):
    p = state.portfolio()
    old = (p.last_scan_picks, p.cash)
    p.last_scan_picks = [
        {"ticker": f"PK{i:02d}.NS", "score": 90 - i, "last_close": 100.0,
         "timing_pass": True, "reasoning": "fixture"}
        for i in range(n)]
    p.cash = cash
    p.save()
    return p, old


def test_scan_shows_the_funnel_and_divides_at_the_fundable_line():
    """3 slots of cash, 6 picks: the card says 3, the divider names the 3
    beyond cash, and nothing is hidden."""
    # Sized on the live ladder (audit F-14): the fixture picks have no stored
    # history, so each costs the slab — three slabs plus change is 3 slots.
    slab = state.cfg().position_slab_value
    # Pins the CASH arithmetic: on this Rs 4.5 lakh fixture book the category
    # cash share (owner order 2026-10-04) would bind first — that rule is
    # pinned in test_category_budgets_page_2026_10_04.py / the trading tests.
    cfg = state.cfg()
    budgets_were = cfg.category_budgets_enabled
    cfg.category_budgets_enabled = False
    p, old = _with_picks(6, cash=3 * slab + 500.0)
    try:
        html = _client().get("/scan").text
        assert "slot_funnel_marker" in html
        assert "3 slots fundable today" in html
        assert "slot_divider_marker" in html
        assert "shown, not hidden" in html
        # the authority line: display only, the gate stays in charge
        assert "risk.can_open" in html
        for i in range(6):                      # every pick still on the page
            assert f"PK{i:02d}.NS" in html
    finally:
        cfg.category_budgets_enabled = budgets_were
        p.last_scan_picks, p.cash = old
        p.save()


def test_no_divider_when_cash_covers_every_pick():
    p, old = _with_picks(3, cash=5_000_000.0)
    try:
        html = _client().get("/scan").text
        assert "slot_funnel_marker" in html
        assert "slot_divider_marker" not in html
    finally:
        p.last_scan_picks, p.cash = old
        p.save()


def test_zero_cash_says_zero_and_marks_every_pick_beyond():
    p, old = _with_picks(2, cash=40_000.0)      # less than one trade
    try:
        html = _client().get("/scan").text
        assert "0 slots fundable today" in html
        assert "slot_divider_marker" in html
        assert "the 2 picks below" in html
    finally:
        p.last_scan_picks, p.cash = old
        p.save()


# ---------------------------------------------------------------------------
# /triggers selection-sequence ladder
# ---------------------------------------------------------------------------

_ROW = {
    "asof": "2026-08-14",
    "family_counts": {"Compression / Base Before Move": 2,
                      "Stealth Accumulation / Absorption": 2},
    "scores": {"RISE": 30.0, "FALL": 10.0, "COLLAPSE": 0.0},
    "data_quality": 90,
    "gates_passed": ["GATE_RISE_WATCH_01"],
    "fired": [],
    "watch": True,
}


def _with_watch(rows: dict):
    p = state.portfolio()
    old = p.trigger_watch
    p.trigger_watch = {"ts": "2026-08-14T04:00:00", "rows": rows,
                       "watchlist": [t for t, r in rows.items()
                                     if r.get("watch")],
                       "no_history": [], "scanned": len(rows)}
    p.save()
    return p, old


def test_ladder_renders_with_every_stock_on_its_rung():
    quiet = dict(_ROW, family_counts={}, gates_passed=[], watch=False)
    p, old = _with_watch({"ENTW.NS": dict(_ROW), "QUIE.NS": quiet})
    try:
        html = _client().get("/triggers").text
        assert "sequence_ladder_marker" in html
        assert "Entry window" in html and "Quiet" in html
        # honesty line: a rung is a reading order, never an entry signal
        assert "never an entry signal" in html
    finally:
        p.trigger_watch = old
        p.save()


def test_rows_stored_before_the_stage_existed_still_render():
    """The daily pass stores `stage` since 2026-08-15; state persisted before
    that has none. The page derives it instead of rendering blank or 500."""
    legacy = dict(_ROW)                        # no "stage" key on purpose
    assert "stage" not in legacy
    p, old = _with_watch({"OLDR.NS": legacy})
    try:
        r = _client().get("/triggers")
        assert r.status_code == 200
        assert "Entry window" in r.text
    finally:
        p.trigger_watch = old
        p.save()


def test_daily_pass_stores_the_stage_on_every_row(monkeypatch):
    """run_trigger_layer computes the stage once and persists it with the
    row, so the page and any later reader agree."""
    import numpy as np
    import pandas as pd

    from web import scheduler

    def fake_load(t, tail=420):
        n = 120
        rng = np.random.default_rng(5)
        c = 100 + np.cumsum(rng.normal(0, 1, n))
        return pd.DataFrame(
            {"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c,
             "Volume": rng.integers(1e6, 2e6, n).astype(float)},
            index=pd.bdate_range("2025-01-01", periods=n))
    monkeypatch.setattr("web.candle_chart.load_ohlcv", fake_load)
    monkeypatch.setattr(scheduler, "run_news_feed", lambda *a, **k: {})
    p = state.portfolio()
    old_watch, old_picks = p.trigger_watch, p.last_scan_picks
    p.last_scan_picks = [{"ticker": "SEQ.NS", "score": 70.0}]
    try:
        out = scheduler.run_trigger_layer("test")
        row = out["rows"]["SEQ.NS"]
        assert "stage" in row
        assert row["stage"]["stage"] in (
            "QUIET", "COMPRESSING", "ACCUMULATING", "RECLAIM",
            "ENTRY_WINDOW")
        assert isinstance(row["stage"]["rung"], int)
    finally:
        p.trigger_watch, p.last_scan_picks = old_watch, old_picks
        p.save()


def test_detail_page_shows_the_stage_chip():
    html = _client().get("/triggers/TATASTEEL.NS").text
    assert "sequence_stage_marker" in html
    assert "reading order only, never an entry signal" in html
