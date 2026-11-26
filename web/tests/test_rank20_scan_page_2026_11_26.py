"""The Scan page shows the 20-session rank beside the three formulas, with
its measured record (owner decision 2026-11-26: a column, not a buy rule)."""
from __future__ import annotations

import copy
import re

from fastapi.testclient import TestClient

from web import auth, state
from web.main import app


def _view(mom20, rank, of, top):
    rows = {"am07": {"score": 70.0, "floor": 62.0, "pass": True, "label": "PASS"},
            "ash08": {"score": 80.0, "floor": 68.0, "pass": True, "label": "PASS"},
            "ashstocks": {"score": 70.0, "floor": 70.0, "pass": True, "label": "PASS"}}
    view = {"rows": rows, "mom20": mom20, "agree": 3, "judged": 3, "all_pass": True}
    if rank is not None:
        view["rank20"] = {"rank": rank, "of": of, "top": top}
    return view


PICKS = [{"ticker": "TOP1.NS", "score": 74.0, "last_close": 100.0, "multi": _view(0.171, 1, 812, True)},
         {"ticker": "MID1.NS", "score": 70.0, "last_close": 200.0, "multi": _view(-0.021, 57, 812, False)},
         {"ticker": "OLD1.NS", "score": 66.0, "last_close": 300.0, "multi": _view(0.03, None, None, None)},
         {"ticker": "NOVW.NS", "score": 64.0, "last_close": 400.0, "multi": None}]


def _scan_page():
    p = state.portfolio()
    saved = (list(p.last_scan_picks or []), copy.deepcopy(p.scan_matrix))
    p.last_scan_picks = copy.deepcopy(PICKS)
    p.scan_matrix = {"mode": "nifty200_momentum", "score_floor": 62.0, "rows": {}}
    try:
        client = TestClient(app)
        assert client.post("/login", data={"email": "owner@sourceborn.in",
                                           "password": auth._SEEDED_PWD}).status_code == 200
        page = client.get("/scan")
        assert page.status_code == 200, page.text[:300]
        return page.text
    finally:
        p.last_scan_picks, p.scan_matrix = saved


def test_the_rank_is_printed_per_stock_and_the_top_ten_carry_a_star():
    html = _scan_page()
    rows = {}
    for row in html.split("<tr")[1:]:
        ticker = re.search(r'href="/chart/(\w+)\.NS"', row)
        if ticker:
            rank = re.search(r'data-rank20="(\d+)"[^>]*>\s*(★)?\s*20d #(\d+)', row)
            rows[ticker.group(1)] = (rank.group(1), bool(rank.group(2))) if rank else None
    assert rows["TOP1"] == ("1", True) and rows["MID1"] == ("57", False)
    # a view stored before the rank existed, and a stock with no view, print nothing
    assert rows["OLD1"] is None and rows["NOVW"] is None
    assert "20-session return +17.1%: rank 1 of the 812 stocks" in html


def test_the_measured_record_is_printed_beside_the_column():
    text = " ".join(re.sub(r"<[^>]+>", " ", _scan_page()).split())
    assert "rank20_record_marker" in _scan_page()
    assert "A column, not a buy rule" in text
    assert "−0.77 (train) and −0.45 (test)" in text
