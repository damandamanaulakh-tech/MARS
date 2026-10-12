"""The rebuilt Dashboard — owner order 2026-10-12 (items 30 a-m, 29, 12, 32, 43).

"There only dashboard, remove master": one page called Dashboard, the fund
summary on top (capital / allocated / reserved / used / cash / P&L per
segment and in total), the index strip under it with an honest
international chip, full picks + watchlist, this month's equity curve on a
1..31 axis, the whole book in the left column, one Status card, breadth and
the calendar. The sector strip and scan coverage moved to /scan; evidence and
the ATR panel moved to the MARS Den.

Every render here uses a MIXED book — a scan-opened position, an owner pick
with entry_score 0 and an Ocean-tagged position — because the /positions 500
of 2026-08-21 only appeared when scan buys and owner buys sat in one book.
State touched on the shared portfolio is restored in `finally`.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from unittest.mock import patch

from fastapi.testclient import TestClient

from trading import category_budget as cb
from trading.config import Config
from trading.paper import PaperPosition, Portfolio
from web import auth as auth_mod, market_panels as mp, state
from web.clock import IST
from web.main import app


# ---------------------------------------------------------------- fixtures

def _client(email="owner@sourceborn.in", pwd=None):
    c = TestClient(app)
    r = c.post("/login", data={"email": email,
                               "password": pwd or auth_mod._SEEDED_PWD})
    assert r.status_code == 200          # login lands on the page it redirects to
    return c


def _pos(ticker, qty, entry, *, mode="", tags=None, score=70.0):
    return PaperPosition(
        ticker=ticker, qty=qty, entry_price=entry,
        entry_date=datetime(2026, 10, 1, 4, 0), sector="IT",
        peak_price=entry, entry_score=score, reasoning="fixture",
        mode=mode, tags=list(tags or []))


def _mixed_book() -> dict:
    return {
        # scan-opened, its own category
        "SWNGX.NS": _pos("SWNGX.NS", 100, 1000.0, mode="swing", score=64.5),
        # a hand-tapped BUY: entry_score 0 is a REAL zero, counts as momentum
        "OWNPK.NS": _pos("OWNPK.NS", 50, 2000.0, mode="", tags=["owner_pick"],
                         score=0.0),
        # Ocean: outside the five shares, ticket x slots
        "OCNX.NS": _pos("OCNX.NS", 10, 500.0, mode="nifty200_momentum",
                        tags=["ocean"], score=100.0),
    }


class _Book:
    """Swap the shared portfolio's book for the mixed one, restore after."""

    def __enter__(self):
        self.p = state.portfolio()
        self.saved = dict(self.p.positions)
        self.p.positions = _mixed_book()
        return self.p

    def __exit__(self, *exc):
        self.p.positions = self.saved
        return False


def _render(path="/", client=None, marks=({}, False)):
    """GET a page with the book's quotes pinned (entry marks unless given)."""
    with patch("web.db.is_connected", return_value=False), \
         patch.object(state, "marks", return_value=marks):
        r = (client or _client()).get(path)
    assert r.status_code == 200, r.text[:400]
    return r.text


# ------------------------------------------------------------ item 30a: title

def test_the_page_is_called_dashboard_and_master_is_gone():
    with _Book():
        page = _render()
    assert "<title>Dashboard — Sourceborn MARS" in page
    assert "Master" not in page
    assert "MASTER / OWNER VIEW" not in page


# ------------------------------------------------------- item 30b: fund block

def test_fund_block_renders_every_segment_and_a_total():
    with _Book():
        page = _render()
    assert "fund_summary_marker" in page
    for seg in list(cb.SHARE_FIELDS) + [cb.OCEAN, "total"]:
        assert f'data-fund-segment="{seg}"' in page, seg
    # the one-line summary, kept ONCE and enlarged inside the block
    assert page.count('data-fund-summary-line="1"') == 1
    assert "3 open positions" in page
    # no live quote in this render -> the block says so, never "live"
    assert "entry marks — connect Upstox for live P&amp;L" in page


def test_fund_arithmetic_is_the_books_own(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.positions = _mixed_book()
    prices = {"SWNGX.NS": 1100.0, "OWNPK.NS": 1800.0, "OCNX.NS": 450.0}
    f = mp.fund_summary(p, cfg, prices, live_count=3)
    rows = {r["category"]: r for r in f["rows"]}
    # owner pick (blank mode) counts as momentum — category_budget's convention
    assert rows[cb.MOMENTUM]["allocated"] == 100_000.0
    assert rows[cb.MOMENTUM]["pnl"] == -10_000.0        # red on the page
    assert rows["swing"]["allocated"] == 100_000.0
    assert rows["swing"]["pnl"] == 10_000.0             # green on the page
    assert rows[cb.OCEAN]["allocated"] == 5_000.0
    assert rows[cb.OCEAN]["limit"] == (cfg.ocean_position_value
                                       * cfg.ocean_max_positions)
    assert rows["intraday"]["open"] == 0 and rows["intraday"]["pnl"] == 0.0
    # totals
    assert f["allocated"] == 205_000.0
    assert f["now"] == 110_000.0 + 90_000.0 + 4_500.0
    assert f["pnl"] == -500.0
    assert f["capital"] == cfg.starting_capital
    equity = p.equity(prices)
    assert f["equity"] == equity
    assert f["reserved"] == cfg.cash_reserve_pct * equity
    # every segment share is category_budget.limit — the number can_open uses
    for cat in cb.SHARE_FIELDS:
        assert rows[cat]["limit"] == cb.limit(cfg, cat, equity)
    assert f["marks"] == "live marks" and f["live"] is True
    assert mp.fund_summary(p, cfg, prices, 0)["marks"].startswith("entry marks")
    assert mp.fund_summary(p, cfg, prices, 1)["marks"].startswith("1 of 3 names live")


def test_a_loss_renders_red_and_a_gain_green():
    marks = ({"SWNGX.NS": 1100.0, "OWNPK.NS": 1800.0, "OCNX.NS": 450.0}, True)
    with _Book():
        page = _render(marks=marks)
    def _row(seg):
        m = re.search(rf'<tr[^>]*data-fund-segment="{seg}".*?</tr>', page, re.S)
        assert m, seg
        return m.group(0)
    assert "text-red-700" in _row(cb.MOMENTUM)
    assert "text-emerald-700" in _row("swing")
    assert "live marks" in page


def test_the_owner_picks_zero_score_prints_as_zero_not_a_dash():
    """A hand-tapped BUY stores entry_score 0 — a real 0 (the /positions
    500 lesson: `x if x else y` treated it as missing)."""
    with _Book():
        page = _render()
    row = re.search(r'<tr[^>]*>\s*<td[^>]*>OWNPK\b.*?</tr>', page, re.S)
    assert row, "owner pick row missing from the open-positions column"
    assert ">0.0<" in row.group(0)


def test_market_state_is_labelled_as_a_reading_not_a_forecast():
    with _Book():
        page = _render()
    assert "Market state" in page
    assert 'title="state reading, not a forecast"' in page


# ----------------------------------------------- item 30b/43: index strip

def test_index_strip_sits_below_the_fund_block_with_the_international_chip():
    with _Book():
        page = _render()
    assert "NIFTY 50" in page
    assert page.index("fund_summary_marker") < page.index("NIFTY 50")
    assert "international_chip_marker" in page
    assert "International: DATA_NEEDED — no reachable free feed yet" in page


# --------------------------------------------- item 30c/30e: lists and book

def test_picks_and_watchlist_are_full_lists():
    p = state.portfolio()
    saved = list(p.last_scan_picks or [])
    p.last_scan_picks = [{"ticker": f"PK{i:02d}.NS", "score": 90 - i,
                          "last_close": 100.0 + i} for i in range(12)]
    try:
        with _Book():
            page = _render()
        # all 12, past the old [:6] / [:8] cuts
        for i in range(12):
            assert f"PK{i:02d}" in page, i
        assert "Today's Picks" in page and "Watchlist" in page
    finally:
        p.last_scan_picks = saved


def test_the_whole_book_is_in_the_open_positions_column():
    with _Book():
        page = _render()
    assert "open_positions_marker" in page
    for t in ("SWNGX", "OWNPK", "OCNX"):
        assert t in page
    assert "Capital Allocation" not in page


# ---------------------------------------------------- item 30d: equity curve

def test_equity_month_series_buckets_by_ist_day_and_keeps_the_last_mark():
    now = datetime(2026, 10, 12, 10, 0, tzinfo=IST)
    history = [                                  # chronological, naive UTC
        ("2026-08-15T05:00:00", 50.0),           # two months back: ignored
        ("2026-09-03T05:00:00", 100.0),          # Sep 3 IST -> previous month
        ("2026-09-30T20:00:00", 150.0),          # = Oct 1 01:30 IST -> current
        ("2026-10-05T04:00:00", 200.0),          # Oct 5 09:30 IST
        ("2026-10-05T10:00:00", 210.0),          # Oct 5 15:30 IST: last mark wins
    ]
    s = mp.equity_month_series(history, now_ist=now)
    assert s["labels"] == list(range(1, 32))
    assert len(s["current"]) == 31 and len(s["previous"]) == 31
    assert s["current"][0] == 150.0
    assert s["current"][4] == 210.0
    assert s["current"][1:4] == [None, None, None]     # gaps, never a guess
    assert s["previous"][2] == 100.0
    assert 50.0 not in s["current"] + s["previous"]
    assert (s["n_current"], s["n_previous"]) == (2, 1)
    assert (s["first"], s["last"]) == (150.0, 210.0)
    assert s["month"] == "Oct 2026" and s["prev_month"] == "Sep 2026"


def test_the_equity_script_carries_day_labels_1_to_31():
    p = state.portfolio()
    saved = list(p.equity_history or [])
    p.equity_history = saved + [(datetime.utcnow().isoformat(), 50_000_000.0)]
    try:
        with _Book():
            page = _render()
    finally:
        p.equity_history = saved
    assert "equity_curve_marker" in page
    assert 'id="equityChart"' in page
    assert json.dumps(list(range(1, 32))) in page


# --------------------------------------------- item 30f: panels that left

def test_the_removed_panels_are_gone_from_the_rendered_dashboard():
    with _Book():
        page = _render()
    for gone in ("Engine Triggers Today", "Walk-Forward Evidence",
                 "Volatility and the stop rule", "What Sourceborn AM07 can do",
                 "Daily Candles", "Capital Allocation"):
        assert gone not in page, gone


# ---------------------------------------------------- item 30g: Status card

def test_status_card_merges_quotes_storage_and_horizons():
    with _Book():
        page = _render()
    card = page[page.index("status_card_marker"):]
    card = card[:card.index("Market Breadth") if "Market Breadth" in card
                else card.index("s Coming")]
    assert "Quotes:" in card and "Storage:" in card and "Trade Horizons" in card


def test_writes_refused_still_turns_the_status_chip_red_and_raises_the_banner():
    bad = {"connected": True, "db": "sourceborn", "write_error": "quota exceeded",
           "write_error_at": "2026-10-12T04:00:00"}
    with _Book(), patch.object(state, "db_status", return_value=bad):
        page = _render()
    assert 'data-db-write-error="1"' in page
    assert "WRITES REFUSED, book not being saved" in page
    assert "quota exceeded" in page


# ---------------------------------------------- item 30h: breadth + calendar

def test_status_card_sits_above_the_calendar():
    with _Book():
        page = _render()
    assert page.index("status_card_marker") < page.index("s Coming")


# ------------------------------------------------- item 30i: moved to /scan

def test_sector_strip_and_coverage_live_on_scan_not_the_dashboard():
    sectors = {"rows": [{"sector": "IT", "chg_1d": -2.17, "chg_5d": -5.12}],
               "n": 1, "asof": "2026-10-09"}
    with _Book(), patch.object(mp, "sector_panel", return_value=sectors):
        dash = _render("/")
        scan = _render("/scan")
    assert "sector_strip_marker" not in dash
    assert "coverage_marker" not in dash
    assert "sector_strip_marker" in scan
    assert "-2.17%" in scan
    # the existing /scan cards were kept, not replaced
    assert "multi_formula_card_marker" in scan


def test_a_broken_sector_panel_cannot_take_scan_down():
    with patch.object(mp, "sector_panel", side_effect=RuntimeError("boom")), \
         patch.object(mp, "coverage_panel", side_effect=RuntimeError("boom")):
        scan = _render("/scan")
    assert "sector_strip_marker" not in scan
    assert "Scan &amp; Watchlist" in scan


# --------------------------------------------------- item 30j: purge button

def test_owner_sees_the_purge_universe_button():
    with _Book():
        page = _render()
    assert 'data-purge-universe="1"' in page
    assert 'action="/positions/purge-universe"' in page
    assert ("Clear all cached universe data and re-pull a fresh list from "
            "Upstox?") in page
    assert "REFRESH PRICES" in page


def test_a_viewer_does_not_get_the_purge_button(monkeypatch):
    monkeypatch.setitem(auth_mod.SEEDED_USERS, "viewer@sourceborn.in",
                        {"role": "viewer", "password": "viewer-test-pw",
                         "name": "Viewer"})
    c = TestClient(app)
    r = c.post("/login", data={"email": "viewer@sourceborn.in",
                               "password": "viewer-test-pw"})
    assert r.status_code == 200
    with _Book(), patch("web.db.is_connected", return_value=False), \
         patch.object(state, "marks", return_value=({}, False)):
        r = c.get("/")
    # web/main.py's viewer guard lets a viewer see the dashboard — so this
    # is a real render, not a 403 that would pass any "not in" check.
    assert r.status_code == 200
    assert "fund_summary_marker" in r.text
    assert 'data-purge-universe="1"' not in r.text


# ------------------------------------------------------------ item 30k: IST

def test_header_clock_and_page_date_are_ist():
    with _Book():
        page = _render()
    clock = re.search(r'data-ist-clock="1">([^<]+)<', page)
    assert clock and clock.group(1).strip().endswith("IST")
    date = re.search(r'data-ist-date="1">([^<]+)<', page)
    assert date and date.group(1).strip().endswith("IST")


def test_the_dashboard_route_never_reads_utcnow_for_a_screen_stamp():
    # Walk the AST, not the text: the module's docstrings QUOTE the old
    # `datetime.utcnow()` to explain the fix (the source-pin-trips-on-its-
    # own-comment fault class — pin the call, never the words).
    import ast
    from pathlib import Path
    tree = ast.parse(Path("web/routes/dashboard.py").read_text())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Attribute) and n.attr == "utcnow"]
    assert calls == []


# --------------------------------------------- item 32: why not today

def test_why_not_today_reads_todays_journal_by_ist_date():
    journal = [
        # 01:30 IST on the 12th, still the 11th in UTC: counts as TODAY
        {"ts": "2026-10-11T20:00:00", "action": "CATEGORY_BUDGET_FULL",
         "category": "swing", "why": "Swing has spent its 15% share"},
        # 15:30 IST on the 11th: yesterday, never shown
        {"ts": "2026-10-11T10:00:00", "action": "BREADTH_BLOCKED",
         "mode": "nifty200_momentum", "why": "yesterday's block"},
        {"ts": "2026-10-12T04:00:00", "action": "BREADTH_BLOCKED",
         "mode": "nifty200_momentum", "why": "Breadth gate: 52.0% in the band"},
        {"ts": "2026-10-12T04:05:00", "action": "SCAN_FAILED", "mode": "intraday",
         "err": "RuntimeError: boom",
         "why": "09:20 scan pass failed — check Upstox login/data."},
        {"ts": "2026-10-12T04:10:00", "action": "KILL_SWITCH_SCAN",
         "why": "Daily loss past 2.0%"},
        {"ts": "2026-10-12T04:11:00", "action": "BUY", "ticker": "X.NS"},
    ]
    rows = {r["category"]: r for r in mp.why_not_today(journal, "2026-10-12")}
    assert set(rows) == set(cb.SHARE_FIELDS) | {"all"}
    assert rows["swing"]["action"] == "CATEGORY_BUDGET_FULL"
    assert rows[cb.MOMENTUM]["why"] == "Breadth gate: 52.0% in the band"
    assert "boom" in rows["intraday"]["why"]           # the real error, shown
    assert rows["all"]["action"] == "KILL_SWITCH_SCAN"
    assert rows["short"]["action"] is None
    assert rows["positional"]["action"] is None


def test_why_not_today_renders_with_the_no_block_line():
    p = state.portfolio()
    mine = {"ts": datetime.utcnow().isoformat(), "action": "CATEGORY_BUDGET_FULL",
            "category": "swing", "why": "fixture-why-2026-10-12"}
    p.decision_journal.append(mine)
    try:
        with _Book():
            page = _render()
    finally:
        p.decision_journal[:] = [e for e in p.decision_journal if e is not mine]
    assert "why_not_today_marker" in page
    row = re.search(r'data-why-not="swing".*?</div>', page, re.S)
    assert row and "fixture-why-2026-10-12" in row.group(0)
    assert "no block recorded today" in page


# ------------------------------------------------------- phone-width layout

def test_every_dashboard_table_scrolls_inside_its_own_box():
    """16px gutters, no horizontal page scroll: a wide table scrolls in its
    own overflow box instead of pushing the page sideways on a phone."""
    with _Book():
        page = _render()
    assert page.count("<table") <= page.count("overflow-x-auto")
