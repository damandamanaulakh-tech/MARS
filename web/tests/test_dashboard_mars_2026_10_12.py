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
        # a hand-tapped BUY: entry_score 0 is a REAL zero; its own "owner"
        # bucket outside the five shares (owner order 2026-10-21)
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

def test_fund_block_renders_every_segment_without_the_total_row():
    """Owner markup 2026-10-12 ("that green marked doesn't make sense, remove
    that"): the Total row, the shares footnote and the why-not block are gone
    from the fund card. The totals still live in the strip above the table."""
    with _Book():
        page = _render()
    assert "fund_summary_marker" in page
    for seg in list(cb.SHARE_FIELDS) + [cb.OCEAN]:
        assert f'data-fund-segment="{seg}"' in page, seg
    assert 'data-fund-segment="total"' not in page
    assert "Shares are of the deployable capital" not in page
    assert "Ocean counts its own ticket" not in page
    assert "why_not_today_marker" not in page
    assert "no block recorded today" not in page
    # the totals strip still carries the book's numbers once each
    for label in ("Capital", "Allocated", "Reserved", "Used (now)", "Cash",
                  "UNREALIZED P&amp;L"):
        assert label in page, label
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
    # owner pick: its own bucket outside the five shares (owner order
    # 2026-10-21; it counted as momentum before)
    assert rows[cb.OWNER]["allocated"] == 100_000.0
    assert rows[cb.OWNER]["pnl"] == -10_000.0           # red on the page
    assert rows[cb.OWNER]["limit"] is None               # no share applies
    assert rows[cb.MOMENTUM]["allocated"] == 0.0 and rows[cb.MOMENTUM]["open"] == 0
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
    # every segment share is category_budget.limit — the number can_open
    # uses. Repointed 2026-10-22 (owner card "shares from what is left after
    # owner + Ocean"): the gate and the card both pass the held book, so the
    # share is of the share base, not of the whole deployable amount.
    for cat in cb.SHARE_FIELDS:
        assert rows[cat]["limit"] == cb.limit(cfg, cat, equity,
                                              positions=p.positions)
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
    # the losing owner pick sits in the Owner segment (owner order
    # 2026-10-21); momentum holds nothing in this book, so it prints a dash
    assert "text-red-700" in _row(cb.OWNER)
    assert "text-red-700" not in _row(cb.MOMENTUM)
    assert "text-emerald-700" in _row("swing")
    assert "live marks" in page


# The zero-score-prints-as-zero pin that sat here read the dashboard's
# open-positions table; that table left the page (owner markup 2026-10-12).
# The same fact stays pinned where the book now renders:
# test_positions_owner_buy_500.py and test_positions_legend_2026_10_12.py.


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


def test_the_open_positions_table_left_the_dashboard_for_a_button():
    """Owner markup 2026-10-12: the open-positions table beside the picks
    column stretched into an empty box with a sideways scroll ("this gap is
    not good … just give a button only"). The book is on /positions; the
    dashboard keeps one button carrying the count, and the picks + watchlist
    take the full width."""
    with _Book():
        page = _render()
    assert "open_positions_marker" not in page
    assert "DETAILS ›" not in page
    btn = re.search(r'<a href="/positions"[^>]*data-open-positions-button="1"[^>]*>(.*?)</a>',
                    page, re.S)
    assert btn, "the OPEN POSITIONS button is missing"
    assert "OPEN POSITIONS (3)" in btn.group(1)
    # picks + watchlist are no longer a half-width column: the wrapper that
    # carries them is a plain stack, not a two-column grid
    assert "picks_watchlist_marker" in page
    wrapper = re.search(r'<div class="([^"]*)" data-picks-and-watchlist="picks_watchlist_marker">', page)
    assert wrapper and "grid-cols" not in wrapper.group(1), wrapper and wrapper.group(1)
    block = page[page.index("picks_watchlist_marker"):page.index("equity_curve_marker")]
    assert "Today's Picks" in block and "Watchlist" in block
    assert "Capital Allocation" not in page
    # the button also reaches a viewer (the book pages are theirs to read)
    assert page.count('data-open-positions-button="1"') == 1


def test_the_open_positions_button_count_follows_the_book():
    p = state.portfolio()
    saved = dict(p.positions)
    try:
        p.positions = {}
        page = _render()
        assert "OPEN POSITIONS (0)" in page
    finally:
        p.positions = saved


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


def test_the_why_not_block_left_the_dashboard():
    """Owner markup 2026-10-12 removed the block from the fund card. The
    journal still records every block line (Reports CSV) and the pure
    reader above stays tested, so one word brings it back."""
    p = state.portfolio()
    mine = {"ts": datetime.utcnow().isoformat(), "action": "CATEGORY_BUDGET_FULL",
            "category": "swing", "why": "fixture-why-2026-10-12"}
    p.decision_journal.append(mine)
    try:
        with _Book():
            page = _render()
    finally:
        p.decision_journal[:] = [e for e in p.decision_journal if e is not mine]
    assert "why_not_today_marker" not in page
    assert "fixture-why-2026-10-12" not in page
    assert "no block recorded today" not in page


# ------------------------------------------- Status card: horizon figures

def test_every_horizon_figure_keeps_its_label_and_number_together():
    """Owner markup 2026-10-12: "trail and its number going to next line" —
    the one-line sentence wrapped between the word and the value. Each figure
    is now one nowrap cell holding both, so a wrap can only fall BETWEEN
    figures."""
    with _Book():
        page = _render()
    card = page[page.index("status_card_marker"):]
    cells = re.findall(r'<div class="whitespace-nowrap"[^>]*data-horizon-trail="1"[^>]*>(.*?)</div>\s*</div>',
                       card, re.S)
    assert cells, "no trail cell on the status card"
    for cell in cells:
        assert "trail" in cell
        assert re.search(r"-?\d+(\.\d+)?%", cell), cell       # the number sits inside the same cell
    # the old sentence form is gone
    assert "· trail " not in card
    assert page.count('data-horizon-figures="1"') == len(cells)


# ------------------------------------------------------- phone-width layout

def test_every_dashboard_table_scrolls_inside_its_own_box():
    """16px gutters, no horizontal page scroll: a wide table scrolls in its
    own overflow box instead of pushing the page sideways on a phone."""
    with _Book():
        page = _render()
    assert page.count("<table") <= page.count("overflow-x-auto")
