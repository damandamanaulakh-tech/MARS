"""WATCH label — owner order 2026-11-26: "Watch Floor - 55 and above".

Display only. A scored stock from 55 up to its segment's buy mark reads
WATCH; at or above the buy mark it reads CLEARED (the score no longer holds
it back). Nothing in the engine reads the watch floor.
"""
from __future__ import annotations

import copy
import pathlib
import re

import pytest

from trading.config import Config
from web import market_panels as mp, state
from web.tests.test_dashboard_mars_2026_10_12 import _Book, _render

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _text(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


@pytest.mark.parametrize("score,label", [
    (54.99, ""), (55.0, "watch"), (61.9, "watch"), (62.0, "cleared"), (90.0, "cleared"),
    (None, ""), ("abc", ""), (float("nan"), ""), ("58.5", "watch")])
def test_the_label_against_a_62_buy_mark(score, label):
    assert mp.watch_label(score, 62.0, 55.0) == label


def test_each_segment_is_judged_against_its_own_buy_mark():
    # 65 clears Momentum (62) and Swing (60) but is only a WATCH for
    # Positional (68), Short (70) and Intraday (72)
    marks = {"nifty200_momentum": 62, "swing": 60, "positional": 68, "short": 70, "intraday": 72}
    got = {m: mp.watch_label(65.0, floor, 55.0) for m, floor in marks.items()}
    assert got == {"nifty200_momentum": "cleared", "swing": "cleared",
                   "positional": "watch", "short": "watch", "intraday": "watch"}
    assert mp.watch_label(65.0, None, 55.0) == "" and mp.watch_label(65.0, 62.0, "x") == ""


def test_the_watch_floor_is_55_and_no_engine_code_reads_it():
    assert Config().watch_floor == 55.0
    for name in ("engine", "risk", "orders", "danger", "sizing", "exits", "scoring", "timing"):
        source = (ROOT / "trading" / f"{name}.py").read_text()
        assert "watch_floor" not in source, f"trading/{name}.py reads the display-only watch floor"


def _with_scan(picks, floor, mode):
    p = state.portfolio()
    saved = (list(p.last_scan_picks or []), copy.deepcopy(p.scan_matrix), list(p.last_scan_skipped or []))
    p.last_scan_picks = picks
    p.scan_matrix = {"mode": mode, "score_floor": floor, "rows": {}}
    p.last_scan_skipped = [{"ticker": "CLR2.NS", "reason": "category_budget_full"}]
    return p, saved


def _restore(p, saved):
    p.last_scan_picks, p.scan_matrix, p.last_scan_skipped = saved


PICKS = [{"ticker": "CLR1.NS", "score": 74.0, "last_close": 100.0},
         {"ticker": "CLR2.NS", "score": 68.0, "last_close": 200.0},
         {"ticker": "WAT1.NS", "score": 61.5, "last_close": 300.0},
         {"ticker": "WAT2.NS", "score": 55.0, "last_close": 400.0},
         {"ticker": "LOW1.NS", "score": 48.0, "last_close": 500.0},
         {"ticker": "NOSC.NS", "last_close": 600.0}]


def test_the_dashboard_watchlist_is_55_and_above_with_a_label_on_every_row():
    p, saved = _with_scan(copy.deepcopy(PICKS), 68.0, "positional")
    try:
        with _Book():
            html = _render()
    finally:
        _restore(p, saved)
    block = html[html.index("watch_rule_marker"):html.index("equity_curve_marker")]
    text = _text(block)
    assert "score 55 and above" in text and "WATCH = 55 up to the Positional buy mark 68" in text
    assert "2 more scored below 55 or carry no score" in text
    labels = {}
    for row in block.split("<tr")[1:]:
        ticker = re.search(r'href="/chart/(\w+)\.NS"', row)
        label = re.search(r'data-watch-label="(\w*)"', row)
        if ticker and label:
            labels[ticker.group(1)] = label.group(1)
    assert labels == {"CLR1": "cleared", "CLR2": "cleared", "WAT1": "watch", "WAT2": "watch"}
    assert "LOW1" not in block and "NOSC" not in block
    # a cleared name that was not bought keeps its real reason; a watch name says why
    assert "category_budget_fu" in text and "below the buy mark" in text


def test_the_scan_page_labels_watch_rows_without_touching_the_stored_picks():
    from fastapi.testclient import TestClient
    from web import auth
    from web.main import app
    picks = copy.deepcopy(PICKS)
    p, saved = _with_scan(picks, 62.0, "nifty200_momentum")
    before = copy.deepcopy(picks)
    try:
        client = TestClient(app)
        assert client.post("/login", data={"email": "owner@sourceborn.in",
                                           "password": auth._SEEDED_PWD}).status_code == 200
        page = client.get("/scan")
        assert page.status_code == 200, page.text[:300]
        assert p.last_scan_picks == before, "a page view wrote onto the stored picks"
    finally:
        _restore(p, saved)
    html = page.text
    assert html.count('data-watch-label="watch"') == 2          # 61.5 and 55.0 against 62
    watched = set()
    for row in html.split("<tr")[1:]:
        ticker = re.search(r'href="/chart/(\w+)\.NS"', row)
        if ticker and 'data-watch-label="watch"' in row:
            watched.add(ticker.group(1))
    assert watched == {"WAT1", "WAT2"}
    assert "up to the Momentum buy mark 62" in html


def test_an_empty_scan_still_renders_both_pages():
    p, saved = _with_scan([], 62.0, "nifty200_momentum")
    try:
        with _Book():
            html = _render()
        assert "Empty — every scored pick at 55 or above is already in the book" in _text(html)
    finally:
        _restore(p, saved)
