"""The pre-rise label on the screens — owner decision 2026-11-26:
compression + volume ignition shown as ONE signal.

It is read once a day by the trigger pass from stored bars, stored with the
stock's trigger row, and shown with its measured record (no better than
chance). No page view computes a scan or fetches anything for it.
"""
from __future__ import annotations

import copy
import re
from datetime import date, datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

from trading import pre_rise as PR
from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import Portfolio
from trading.tests.test_pre_rise_2026_11_26 import _frame
from web import auth, scheduler, state, stock_card
from web.main import app
from web.routes import scan_routes as sr


def _text(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _client():
    client = TestClient(app)
    assert client.post("/login", data={"email": "owner@sourceborn.in",
                                       "password": auth._SEEDED_PWD}).status_code == 200
    return client


SIGNAL = lambda: _frame(inside_end=135, ignite_at=139)          # noqa: E731  fires on the latest bar
QUIET = lambda: _frame()                                         # noqa: E731


def _row(pre_rise="absent", **extra):
    row = {"asof": "2026-11-25", "watch": False, "family_counts": {}, "scores": {"RISE": 10.0, "FALL": 5.0},
           "data_quality": 100.0, "gates_passed": [], "fired": []}
    if pre_rise != "absent":
        row["pre_rise"] = pre_rise
    row.update(extra)
    return row


def _reading(on, asof, signal_ago=0, ignition_ago=0, compression_ago=2):
    return {"asof": asof, "on": on, "signal_ago": signal_ago if on else None,
            "ignition_ago": ignition_ago, "compression_ago": compression_ago}


# ------------------------------------------------------- the trigger pass

def test_the_daily_trigger_pass_stores_the_reading_with_each_stock(monkeypatch):
    frames = {"FIRE.NS": SIGNAL(), "CALM.NS": QUIET()}
    monkeypatch.setattr("web.candle_chart.load_ohlcv", lambda t, tail=420: frames.get(t))
    p = state.portfolio()
    saved = (p.trigger_watch, p.last_scan_picks)
    p.last_scan_picks = [{"ticker": "FIRE.NS"}, {"ticker": "CALM.NS"}, {"ticker": "NONE.NS"}]
    positions = dict(p.positions)
    try:
        out = scheduler.run_trigger_layer("test")
        fire, calm = out["rows"]["FIRE.NS"]["pre_rise"], out["rows"]["CALM.NS"]["pre_rise"]
        assert fire["on"] is True and fire["signal_ago"] == 0
        assert fire["asof"] == frames["FIRE.NS"].index[-1].strftime("%Y-%m-%d")
        assert calm == {"asof": fire["asof"], "compression_ago": None, "ignition_ago": None,
                        "signal_ago": None, "on": False}
        assert "NONE.NS" in out["no_history"] and "NONE.NS" not in out["rows"]
        assert p.positions == positions                      # a label: nothing bought or sold
        # the stored book keeps it
        again = Portfolio.load_or_fresh(state.cfg())
        assert again.trigger_watch["rows"]["FIRE.NS"]["pre_rise"]["on"] is True
    finally:
        p.trigger_watch, p.last_scan_picks = saved
        p.save()


def test_a_failed_reading_costs_the_label_never_the_trigger_pass(monkeypatch):
    monkeypatch.setattr("web.candle_chart.load_ohlcv", lambda t, tail=420: SIGNAL())
    monkeypatch.setattr(PR, "reading", lambda df: 1 / 0)
    p = state.portfolio()
    saved = (p.trigger_watch, p.last_scan_picks)
    p.last_scan_picks = [{"ticker": "FIRE.NS"}]
    try:
        out = scheduler.run_trigger_layer("test")
        row = out["rows"]["FIRE.NS"]
        assert row["pre_rise"] is None and "stage" in row and "family_counts" in row
    finally:
        p.trigger_watch, p.last_scan_picks = saved
        p.save()


# ---------------------------------------------------------- trigger pages

def _triggers_page(rows):
    p = state.portfolio()
    saved = p.trigger_watch
    p.trigger_watch = {"ts": "2026-11-26T04:00:00", "scanned": len(rows), "watchlist": [],
                       "no_history": [], "rows": rows}
    try:
        page = _client().get("/triggers")
        assert page.status_code == 200, page.text[:300]
        return page.text
    finally:
        p.trigger_watch = saved


def test_the_trigger_table_has_one_pre_rise_column_and_states_the_record():
    html = _triggers_page({
        "FIRE.NS": _row(_reading(True, "2026-11-25", signal_ago=2, ignition_ago=2)),
        "HALF.NS": _row(_reading(False, "2026-11-25", ignition_ago=None, compression_ago=1)),
        "CALM.NS": _row(_reading(False, "2026-11-25", ignition_ago=None, compression_ago=None)),
        "OLD.NS": _row(),                                     # a row stored before the label
        "THIN.NS": _row(None),                                # too little history to read
    })
    cells = {}
    for row in html.split("<tr")[1:]:
        ticker = re.search(r'href="/triggers/(\w+)\.NS"', row)
        cell = re.search(r'data-pre-rise-cell>(.*?)</td>', row, re.S)
        if ticker and cell:
            cells[ticker.group(1)] = _text(cell.group(1))
    assert cells == {"FIRE": "PRE-RISE · 2d", "HALF": "compr", "CALM": "—", "OLD": "n/a", "THIN": "n/a"}
    note = _text(html.split("pre_rise_marker", 1)[1].split("</div>\n</div>", 1)[0])
    assert "PRE-RISE = a volume ignition bar" in note and "Display only" in note
    assert "it fired 373 times in 469,947 stock-days" in note
    assert "no better than chance (lift 0.919; 0.996 before July 2020, 0.799 after)" in note


def test_the_stock_trigger_page_reads_the_label_from_the_stored_bars():
    with patch("web.candle_chart.load_ohlcv", return_value=SIGNAL()):
        page = _client().get("/triggers/FIRE.NS")
    assert page.status_code == 200
    block = _text(page.text.split("pre_rise_detail_marker", 1)[1].split("sequence_stage_marker", 1)[0])
    assert block.startswith("--> Pre-rise PRE-RISE ON, fired on the latest stored bar")
    assert "volume ignition on the latest stored bar" in block
    assert "no better than chance" in block
    with patch("web.candle_chart.load_ohlcv", return_value=QUIET()):
        quiet = _client().get("/triggers/CALM.NS").text
    block = _text(quiet.split("pre_rise_detail_marker", 1)[1].split("sequence_stage_marker", 1)[0])
    assert "not on (compression not in the last 10 sessions; volume ignition not in the last 10 sessions)" in block
    assert "bg-violet-100 text-violet-800\">PRE-RISE</span>" not in quiet.split(
        "pre_rise_detail_marker", 1)[1].split("sequence_stage_marker", 1)[0]


# -------------------------------------------------------------- scan page

def test_only_a_current_stored_label_is_shown_on_the_scan_page():
    today = date(2026, 11, 26)
    p = Portfolio.fresh(Config())
    p.trigger_watch = {"rows": {
        "FIRE.NS": _row(_reading(True, "2026-11-25")),
        "STALE.NS": _row(_reading(True, "2026-11-10")),       # the trigger pass stopped 16 days ago
        "FUTURE.NS": _row(_reading(True, "2026-12-05")),
        "OFF.NS": _row(_reading(False, "2026-11-25")),
        "OLD.NS": _row(), "THIN.NS": _row(None), "JUNK.NS": "not a row",
        "NODATE.NS": _row({"on": True, "asof": "299"}),
    }}
    assert list(sr._pre_rise_on(p, today)) == ["FIRE.NS"]
    assert sr.PRE_RISE_MAX_AGE_DAYS == 7
    p.trigger_watch = None
    assert sr._pre_rise_on(p, today) == {}


def test_the_scan_page_prints_the_chip_beside_the_stock():
    p = state.portfolio()
    saved = (list(p.last_scan_picks or []), copy.deepcopy(p.scan_matrix), p.trigger_watch)
    asof = (datetime.utcnow().date() - timedelta(days=1)).isoformat()
    p.last_scan_picks = [{"ticker": "FIRE.NS", "score": 80.0, "last_close": 100.0, "timing_pass": True},
                         {"ticker": "CALM.NS", "score": 75.0, "last_close": 100.0, "timing_pass": True}]
    p.scan_matrix = {"mode": "swing", "score_floor": 60.0, "rows": {}}
    p.trigger_watch = {"ts": "x", "rows": {"FIRE.NS": _row(_reading(True, asof)),
                                           "CALM.NS": _row(_reading(False, asof))}}
    try:
        html = _client().get("/scan").text
    finally:
        p.last_scan_picks, p.scan_matrix, p.trigger_watch = saved
    chips = {}
    for row in html.split("<tr")[1:]:
        ticker = re.search(r'href="/chart/(\w+)\.NS"', row)
        if ticker:
            chips[ticker.group(1)] = "data-pre-rise-chip" in row
    assert chips == {"FIRE": True, "CALM": False}
    assert "Measured no better than chance; a label only, nothing is bought on it." in html


# ------------------------------------------------------------- stock page

def _card(tmp_path, df):
    cfg = Config(state_dir=str(tmp_path), mode="nifty200_momentum")
    cfg.apply_mode_preset()
    e = TradingEngine(cfg, Portfolio.fresh(cfg))
    return stock_card.build("ABC.NS", df, e.portfolio, cfg, e.mode_cfg, today=date(2026, 11, 26)), e, cfg


def test_the_stock_card_carries_the_label_and_its_record(tmp_path):
    on, _, _ = _card(tmp_path, SIGNAL())
    assert on["pre_rise"]["on"] is True
    assert on["pre_rise"]["text"].startswith("ON, fired on the latest stored bar")
    assert on["pre_rise"]["record"] == PR.record_text()
    off, _, _ = _card(tmp_path, QUIET())
    assert off["pre_rise"]["on"] is False and off["pre_rise"]["text"].startswith("not on")
    none, _, _ = _card(tmp_path, None)
    assert none["pre_rise"] is None                          # no stored bars: no line at all


def test_the_stock_page_prints_it_as_a_label_only(tmp_path, monkeypatch):
    cfg = Config(state_dir=str(tmp_path), mode="nifty200_momentum")
    cfg.apply_mode_preset()
    e = TradingEngine(cfg, Portfolio.fresh(cfg))
    monkeypatch.setattr(state, "cfg", lambda: cfg)
    monkeypatch.setattr(state, "portfolio", lambda: e.portfolio)
    monkeypatch.setattr(state, "engine", lambda: e)
    monkeypatch.setattr(auth, "current_user", lambda r: {"role": "owner", "email": "fixture"})
    with patch("web.routes.chart_routes.load_ohlcv", return_value=SIGNAL()):
        page = TestClient(app).get("/chart/ABC.NS")
    assert page.status_code == 200
    assert 'data-stock-card-pre-rise="on"' in page.text
    line = _text(page.text.split('data-stock-card-pre-rise="on"', 1)[1].split("</div>", 1)[0])
    assert "PRE-RISE ON, fired on the latest stored bar" in line
    assert "no better than chance" in line and "A label only; nothing is bought on it." in line


# -------------------------------------------------------------------- den

def test_the_den_states_the_rule_and_the_record():
    page = _client().get("/den")
    assert page.status_code == 200 and "den_pre_rise_marker" in page.text
    line = _text(page.text.split("den_pre_rise_marker", 1)[1].split("</div>", 1)[0])
    assert "Pre-rise label (owner decision 26 Nov 2026)" in line
    assert "Display only: no buy, size or exit rule reads it." in line
    assert "it fired 373 times" in line and "no better than chance" in line
