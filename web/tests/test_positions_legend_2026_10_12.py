"""Owner item 38 (2026-10-12): "it shows orange triangle - add meaning of
that like star, and add one row which says P&L also, with number of days
for held".

The orange triangle was the ⚠ DECAY tag. /positions now carries a LEGEND
row above the table (every marker the page can print, each derived from a
stored field), a DAYS HELD cell per row (today in IST minus the entry day),
P&L ₹ and % on ONE line, and a totals row with total P&L, the position
count and the average days held.

The fixture is the MIXED book that killed the page on 2026-08-21: a
scan-opened momentum position, an owner pick with entry_score 0, a Buy Desk
pick, an Ocean pick, a hand-set-levels pick, a danger-tightened pick and a
decaying one — all on one page.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient

from trading.calendars import _ist_now
from trading.paper import PaperPosition
from web import auth as auth_mod, state
from web.main import app
from web.routes import positions as positions_mod


def _client() -> TestClient:
    # follow_redirects=False: the login 303 lands on the dashboard, which is
    # not under test here and must not be able to fail this page's tests.
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD},
               follow_redirects=False)
    assert r.status_code in (200, 303)
    return c


def _pos(ticker: str, **kw) -> PaperPosition:
    base = dict(qty=10, entry_price=100.0, entry_date=datetime(2026, 8, 21),
                sector="Test", peak_price=100.0, entry_score=70.0,
                reasoning="fixture")
    base.update(kw)
    return PaperPosition(ticker=ticker, **base)


_TODAY = _ist_now().date()


def _days_ago(n: int) -> datetime:
    d = _TODAY - timedelta(days=n)
    return datetime(d.year, d.month, d.day, 9, 30)


def _mixed_book():
    return [
        _pos("SCAN.NS", mode="nifty200_momentum", entry_date=_days_ago(7),
             entry_score=71.3),
        _pos("OWNER.NS", entry_score=0.0, tags=["owner_pick"],
             entry_date=_days_ago(3), reasoning="owner buy"),
        _pos("DESK.NS", tags=["owner_pick", "buy_desk"], entry_date=_days_ago(2)),
        _pos("OCEAN.NS", tags=["ocean"], entry_date=_days_ago(10)),
        _pos("HAND.NS", stop_price=92.0, target_price=130.0, custom_levels=True,
             tags=["owner_pick"], entry_date=_days_ago(1)),
        _pos("DANGER.NS", stop_price=97.5, custom_levels=True,
             mode="nifty200_momentum", entry_date=_days_ago(5)),
        _pos("DECAY.NS", mode="nifty200_momentum", entry_date=_days_ago(0)),
        # a bare `date` (no .date()) — the 2026-08-21 real-book shape
        _pos("OLDDATE.NS", entry_date=date(2026, 8, 5)),
    ]


class _Book:
    """Swap the live book for the fixture, with the journal lines the page
    reads (a HOLD score for the decay watch, a DANGER_TIGHTEN line), and
    put it back afterwards whatever happens."""

    def __init__(self, monkeypatch, prices: dict | None = None):
        self.p = state.portfolio()
        self.old_pos = dict(self.p.positions)
        self.old_journal = list(self.p.decision_journal or [])
        self.monkeypatch = monkeypatch
        self.prices = prices or {}

    def __enter__(self):
        self.p.positions = {x.ticker: x for x in _mixed_book()}
        # the DECAY.NS row's OWN category floor — what the page flags against
        floor = positions_mod._decay_floor_for(
            self.p.positions["DECAY.NS"], state.cfg(), {})
        self.p.decision_journal = self.old_journal + [
            {"ts": "2026-10-12T04:00:00", "action": "HOLD", "ticker": "DECAY.NS",
             "current_score": floor + 2},
            {"ts": "2026-10-12T04:05:00", "action": "DANGER_TIGHTEN",
             "ticker": "DANGER.NS", "new_stop": 97.5},
        ]
        self.monkeypatch.setattr(state, "marks",
                                 lambda tickers, max_age=0: (dict(self.prices),
                                                             bool(self.prices)))
        return self.p

    def __exit__(self, *a):
        self.p.positions = self.old_pos
        self.p.decision_journal = self.old_journal


def _attr(html: str, name: str) -> list[str]:
    return re.findall(rf'{name}="([^"]*)"', html)


# ----------------------------------------------------------- the legend

def test_legend_explains_every_marker_the_code_can_print(monkeypatch):
    with _Book(monkeypatch):
        html = _client().get("/positions").text
    assert "marker_legend_marker" in html
    legend_keys = set(_attr(html, "data-legend"))
    assert legend_keys == {m["key"] for m in positions_mod.MARKERS}
    # the orange triangle finally has its meaning next to it
    assert "⚠ DECAY" in html
    assert "score-decay exit" in html
    for label in ("OWNER PICK", "BUY DESK", "OCEAN", "DANGER-TIGHTENED",
                  "HAND-SET LEVELS"):
        assert label in html


def test_row_markers_come_from_stored_fields_and_never_outrun_the_legend(monkeypatch):
    with _Book(monkeypatch):
        html = _client().get("/positions").text
    markers = _attr(html, "data-marker")
    legend = set(_attr(html, "data-legend"))
    assert set(markers) <= legend            # no marker without a legend line
    # each fixture position carries exactly the markers its fields imply
    rows = {}
    for chunk in html.split('<tr class="border-b')[1:]:
        t = re.search(r"([A-Z]+\.NS)", chunk)
        if t:
            rows[t.group(1)] = set(_attr(chunk, "data-marker"))
    assert rows["SCAN.NS"] == set()
    assert rows["OWNER.NS"] == {"owner_pick"}
    assert rows["DESK.NS"] == {"owner_pick", "buy_desk"}
    assert rows["OCEAN.NS"] == {"ocean"}
    assert rows["HAND.NS"] == {"owner_pick", "custom"}
    assert rows["DANGER.NS"] == {"danger"}      # custom_levels + DANGER_TIGHTEN line
    assert rows["DECAY.NS"] == {"decay"}
    # legend counts are the row counts
    counts = dict(zip(_attr(html, "data-legend"),
                      re.findall(r"(\d+) now</span>", html)))
    assert counts["owner_pick"] == "3" and counts["danger"] == "1"
    assert counts["decay"] == "1" and counts["ocean"] == "1"


def test_row_markers_helper_reads_the_real_fields():
    hand = _pos("H.NS", custom_levels=True)
    assert [m["key"] for m in positions_mod._row_markers(hand, False, False)] == ["custom"]
    assert [m["key"] for m in positions_mod._row_markers(hand, False, True)] == ["danger"]
    desk = _pos("D.NS", tags=["owner_pick", "buy_desk", "ocean"])
    assert [m["key"] for m in positions_mod._row_markers(desk, True, False)] == \
        ["decay", "owner_pick", "buy_desk", "ocean"]
    assert positions_mod._row_markers(_pos("P.NS"), False, False) == []


# ----------------------------------------------------------- days held

def test_days_held_is_today_ist_minus_the_entry_day(monkeypatch):
    with _Book(monkeypatch):
        html = _client().get("/positions").text
    got = {}
    for chunk in html.split('<tr class="border-b')[1:]:
        t = re.search(r"([A-Z]+\.NS)", chunk)
        d = _attr(chunk, "data-days-held")
        if t and d:
            got[t.group(1)] = d[0]
    assert got["SCAN.NS"] == "7"
    assert got["OWNER.NS"] == "3"
    assert got["DECAY.NS"] == "0"
    assert got["OCEAN.NS"] == "10"
    # a bare `date` entry still counts (and the page did not die on it)
    assert got["OLDDATE.NS"] == str((_TODAY - date(2026, 8, 5)).days)
    assert "7d" in html


def test_entry_day_helper_accepts_datetime_date_and_garbage():
    assert positions_mod._entry_day(datetime(2026, 10, 1, 9, 30)) == date(2026, 10, 1)
    assert positions_mod._entry_day(date(2026, 10, 1)) == date(2026, 10, 1)
    assert positions_mod._entry_day(None) is None
    assert positions_mod._entry_day("2026-10-01") is None


def test_a_future_dated_entry_reads_zero_days_not_negative(monkeypatch):
    p = state.portfolio()
    old = dict(p.positions)
    p.positions = {"FUT.NS": _pos("FUT.NS", entry_date=_days_ago(-3))}
    monkeypatch.setattr(state, "marks", lambda tickers, max_age=0: ({}, False))
    try:
        html = _client().get("/positions").text
    finally:
        p.positions = old
    assert 'data-days-held="0"' in html


# ----------------------------------------------------------- P&L and totals

def test_pnl_rupees_and_percent_sit_on_one_line_with_real_marks(monkeypatch):
    prices = {"SCAN.NS": 110.0, "OWNER.NS": 95.0}
    with _Book(monkeypatch, prices):
        html = _client().get("/positions").text
    # SCAN: 10 x (110-100) = +100, +10.00%; OWNER: 10 x (95-100) = -50, -5.00%
    assert "+₹100 <span" in html and "(+10.00%)" in html
    assert "-₹50 <span" in html and "(-5.00%)" in html
    assert 'data-pnl-pct="10.00"' in html and 'data-pnl-pct="-5.00"' in html
    # ONE line: the percentage is no longer a block <div> under the rupees
    pnl_cells = re.findall(r'data-pnl-pct="[^"]*">\s*(.*?)</td>', html, re.S)
    assert pnl_cells and all("<div" not in c for c in pnl_cells)


def test_totals_row_carries_pnl_count_and_average_days_held(monkeypatch):
    prices = {"SCAN.NS": 110.0, "OWNER.NS": 95.0}
    with _Book(monkeypatch, prices):
        html = _client().get("/positions").text
    assert "positions_totals_marker" in html
    assert 'data-total-count="8"' in html and "8 positions" in html
    # invested 8 x 1,000 = 8,000; value 8,000 + 100 - 50 = 8,050 -> +0.63%
    assert 'data-total-pnl-pct="0.63"' in html
    assert "+₹50 <span" in html and "(+0.63%)" in html
    avg = (7 + 3 + 2 + 10 + 1 + 5 + 0 + (_TODAY - date(2026, 8, 5)).days) / 8
    assert f'data-avg-days-held="{avg:.1f}"' in html
    assert f"avg {avg:.1f}d" in html


def test_header_and_totals_column_counts_agree():
    """A new column must widen the details colspan and the totals row with
    it, or the table skews (the 2026-09-11 colspan lesson)."""
    src = open("web/templates/positions.html").read()
    head = src.split("<thead")[1].split("</thead>")[0]
    n_th = head.count("<th")
    # the Held column is in the header (derived count below — a future column
    # must not surface as a meaningless number mismatch)
    assert any(">Held<" in h for h in head.split("<th")[1:])
    assert f'colspan="{n_th}"' in src            # the details row spans all
    foot = src.split("<tfoot")[1].split("</tfoot>")[0]
    spans = [int(x) for x in re.findall(r'colspan="(\d+)"', foot)]
    n_td = foot.count("<td")
    assert n_td - len(spans) + sum(spans) == n_th


def test_decay_floor_is_the_positions_own_category_floor():
    """The exit pass judges each position on its OWN mode's floor
    (engine._exit_cfg); the page must flag against the same number, not the
    live config's — momentum/positional 40, intraday/short/swing 45."""
    from trading import modes
    cfg = state.cfg()
    cache: dict = {}
    for mode in ("nifty200_momentum", "swing", "short", "positional", "intraday"):
        want = modes.get_preset(mode).score_decay_exit
        overrides = ((getattr(cfg, "preset_overrides", None) or {}).get(mode) or {})
        if "score_decay_exit" in overrides:
            want = overrides["score_decay_exit"]
        got = positions_mod._decay_floor_for(_pos("X.NS", mode=mode), cfg, cache)
        assert got == float(want), mode
    # blank legacy mode = momentum (the retag convention)
    assert positions_mod._decay_floor_for(_pos("Y.NS", mode=""), cfg, cache) == \
        cache["nifty200_momentum"]
    # the shared config is never mutated by the per-mode copy
    before = (cfg.mode, cfg.score_decay_exit)
    positions_mod._decay_floor_for(_pos("Z.NS", mode="intraday"), cfg, {})
    assert (cfg.mode, cfg.score_decay_exit) == before


def test_a_swing_row_flags_decay_on_the_swing_floor(monkeypatch):
    from trading import modes
    swing_floor = modes.get_preset("swing").score_decay_exit           # 45
    mom_floor = modes.get_preset("nifty200_momentum").score_decay_exit  # 40
    assert swing_floor > mom_floor          # the case the old code got wrong
    score = swing_floor + 3                 # inside swing's 5-pt band, outside momentum's
    p = state.portfolio()
    old_pos, old_j = dict(p.positions), list(p.decision_journal or [])
    p.positions = {"SW.NS": _pos("SW.NS", mode="swing", entry_date=_days_ago(2)),
                   "MO.NS": _pos("MO.NS", mode="nifty200_momentum",
                                 entry_date=_days_ago(2))}
    p.decision_journal = old_j + [
        {"ts": "2026-10-12T04:00:00", "action": "HOLD", "ticker": t,
         "current_score": score} for t in ("SW.NS", "MO.NS")]
    monkeypatch.setattr(state, "marks", lambda tickers, max_age=0: ({}, False))
    try:
        html = _client().get("/positions").text
    finally:
        p.positions, p.decision_journal = old_pos, old_j
    rows = {}
    for chunk in html.split('<tr class="border-b')[1:]:
        t = re.search(r"([A-Z]+\.NS)", chunk)
        if t:
            rows[t.group(1)] = set(_attr(chunk, "data-marker"))
    assert "decay" in rows["SW.NS"]
    assert "decay" not in rows["MO.NS"]


def test_decay_tooltip_says_exempt_on_an_owner_pick(monkeypatch):
    """The decay exit never sells an owner pick or an Ocean position
    (exits.check_exit), so their ⚠ must not say 'may be sold'."""
    p = state.portfolio()
    old_pos, old_j = dict(p.positions), list(p.decision_journal or [])
    p.positions = {"OP.NS": _pos("OP.NS", tags=["owner_pick"], mode="nifty200_momentum",
                                 entry_date=_days_ago(1)),
                   "PL.NS": _pos("PL.NS", mode="nifty200_momentum",
                                 entry_date=_days_ago(1))}
    p.decision_journal = old_j + [
        {"ts": "2026-10-12T04:00:00", "action": "HOLD", "ticker": t,
         "current_score": 41.0} for t in ("OP.NS", "PL.NS")]
    monkeypatch.setattr(state, "marks", lambda tickers, max_age=0: ({}, False))
    try:
        html = _client().get("/positions").text
    finally:
        p.positions, p.decision_journal = old_pos, old_j
    titles = {}
    for chunk in html.split('<tr class="border-b')[1:]:
        t = re.search(r"([A-Z]+\.NS)", chunk)
        m = re.search(r'data-marker="decay" title="([^"]*)"', chunk)
        if t and m:
            titles[t.group(1)] = m.group(1)
    assert "never sold on decay" in titles["OP.NS"]
    assert "may be sold on decay" in titles["PL.NS"]
    # and the legend line itself names the exemption
    assert "never an owner pick or an Ocean position" in html


def test_sell_box_and_sector_card_survive(monkeypatch):
    with _Book(monkeypatch):
        html = _client().get("/positions").text
    assert "/positions/SCAN.NS/sell" in html
    assert 'name="price"' in html
    assert "CLOSE ALL POSITIONS" in html
    src = open("web/templates/positions.html").read()
    assert "sector_event_marker" in src and "/positions/sector-exit" in src
