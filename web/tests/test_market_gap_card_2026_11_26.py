"""The "Market vs stocks" dashboard card and its storage (owner 2026-11-26)."""
from __future__ import annotations

import copy
import re
from copy import deepcopy

import pytest

from trading.config import Config
from trading.paper import Portfolio
from web import db, market_panels as mp, state
from web.tests.test_dashboard_mars_2026_10_12 import _Book, _render

READING = {"asof": "2026-11-25", "computed": "2026-11-26T03:55:00", "benchmark": "^NSEI",
           "windows": {"20": {"index_pct": 13.5, "median_pct": 7.2, "gap_points": 6.3, "beat_share": 0.31,
                              "up_share": 0.78, "sample": 812, "state": "narrow"},
                       "60": {"index_pct": 7.2, "median_pct": -2.9, "gap_points": 10.1, "beat_share": 0.26,
                              "up_share": 0.41, "sample": 806, "state": "narrow"}}}


def _text(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def test_the_reading_survives_a_restart_through_json_and_mongo(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "closed_history_policy", lambda: {})
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.market_gap = deepcopy(READING)
    state._patch_portfolio_save_to_mirror_mongo()
    monkeypatch.setattr(db, "is_connected", lambda: True)
    stored = {}
    monkeypatch.setattr(db, "save_portfolio", lambda d: stored.update(deepcopy(d)) or True)
    assert p.save()
    assert Portfolio.load_or_fresh(cfg).market_gap == READING
    restored = Portfolio.fresh(cfg)
    state._hydrate_portfolio_from_mongo(restored, stored)
    assert restored.market_gap == READING
    state._hydrate_portfolio_from_mongo(restored, {})        # a book saved before the field existed
    assert restored.market_gap == {}


def test_the_panel_reads_the_longest_window_as_the_headline():
    panel = mp.market_gap_panel(type("P", (), {"market_gap": READING})(), today_iso="2026-11-26")
    assert [r["sessions"] for r in panel["rows"]] == [20, 60]
    assert panel["headline"]["sessions"] == 60 and panel["headline"]["gap_points"] == 10.1
    assert panel["headline"]["beat_pct"] == pytest.approx(26.0)
    assert panel["stale"] is False and panel["age_days"] == 1
    assert "narrow rally" in panel["words"]
    old = mp.market_gap_panel(type("P", (), {"market_gap": READING})(), today_iso="2026-12-10")
    assert old["stale"] is True and old["age_days"] == 15


@pytest.mark.parametrize("stored", [None, {}, {"windows": {}}, {"windows": "x"}, "text",
                                    {"asof": "2026-11-25", "windows": {"60": {"index_pct": "n/a"}}}])
def test_nothing_stored_or_a_damaged_reading_hides_the_card(stored):
    assert mp.market_gap_panel(type("P", (), {"market_gap": stored})()) == {}


def test_the_dashboard_prints_the_card_and_says_what_it_is():
    p = state.portfolio()
    saved = copy.deepcopy(getattr(p, "market_gap", {}))
    p.market_gap = deepcopy(READING)
    p.market_gap["asof"] = __import__("web.clock", fromlist=["x"]).ist_today_iso()
    try:
        with _Book():
            html = _render()
    finally:
        p.market_gap = saved
    assert "market_gap_marker" in html and 'data-market-gap-state="narrow"' in html
    text = _text(html)
    assert "Over 60 sessions the Nifty is +7.2% and the typical stock is -2.9%" in text
    assert "a gap of +10.1 points" in text and "26% of stocks are ahead of the index" in text
    assert "a narrow rally" in text and "describes what happened, not a forecast" in text
    assert "these two cut-offs are assumptions" in text and "No buy, sell or size rule reads this card" in text
    assert html.count("data-market-gap-window=") == 2


def test_no_reading_no_card_and_an_old_one_is_flagged():
    p = state.portfolio()
    saved = copy.deepcopy(getattr(p, "market_gap", {}))
    try:
        p.market_gap = {}
        with _Book():
            assert "market_gap_marker" not in _render()
        p.market_gap = deepcopy(READING)
        p.market_gap["asof"] = "2026-01-05"
        with _Book():
            text = _text(_render())
        assert "days old, read with care" in text
    finally:
        p.market_gap = saved
