"""The two volume gates on the screens — owner decision 2026-11-26: "two
volume gates shown separately". Each gate has its own reading on the Scan
page, its own piano lane and tap page, its own line on the stock page and in
the Den, and its own switch in Settings.
"""
from __future__ import annotations

import copy
import re
from datetime import date
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import Portfolio
from web import auth, state, stock_card
from web.main import app
from web.routes import scan_routes as sr


def _text(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _client():
    client = TestClient(app)
    assert client.post("/login", data={"email": "owner@sourceborn.in",
                                       "password": auth._SEEDED_PWD}).status_code == 200
    return client


def _gates(trend_ok, trend, spike_ok, spike, need1=1.3, blocks2=False, applies=True):
    return {"gate1": {"ok": trend_ok, "ratio": trend, "need": need1, "applies": applies, "blocks": applies},
            "gate2": {"ok": spike_ok, "ratio": spike, "need": 1.5, "blocks": blocks2}}


def _matrix(mode="short", blocks2=False, need1=1.3, applies=True, stopped=0):
    return {
        "ts": "2026-11-26T09:01:00", "mode": mode, "score_floor": 70.0, "universe_size": 4,
        "volume_gates": {"of": 4,
                         "gate1": {"need": need1, "blocks": applies, "known": 3, "passed": 1, "applies": applies},
                         "gate2": {"need": 1.5, "blocks": blocks2, "known": 3, "passed": 2, "stopped": stopped}},
        "rows": {
            "BOTH.NS": {"score": 80.0, "timing": True, "abs_mom": True, "outcome": "opened",
                        "vol": {"tk": True, "tr": 1.62, "sk": True, "sr": 2.4}},
            "SPIKE.NS": {"score": 76.0, "timing": False, "abs_mom": True, "outcome": "",
                         "vol": {"tk": False, "tr": 0.91, "sk": True, "sr": 1.75}},
            "QUIET.NS": {"score": 74.0, "timing": False, "abs_mom": True, "outcome": "",
                         "vol": {"tk": False, "tr": 1.05, "sk": False, "sr": 0.8}},
            "THIN.NS": {"score": 71.0, "timing": False, "abs_mom": True, "outcome": "",
                        "vol": {"tk": None, "tr": None, "sk": None, "sr": None}},
        },
    }


PICKS = [
    {"ticker": "BOTH.NS", "score": 80.0, "last_close": 100.0, "timing_pass": True,
     "volume_gates": _gates(True, 1.62, True, 2.4)},
    {"ticker": "QUIET.NS", "score": 74.0, "last_close": 200.0, "timing_pass": False,
     "volume_gates": _gates(False, 1.05, False, 0.8)},
    {"ticker": "THIN.NS", "score": 71.0, "last_close": 300.0, "timing_pass": False,
     "volume_gates": _gates(None, None, None, None)},
    {"ticker": "OLD.NS", "score": 70.0, "last_close": 400.0, "timing_pass": True},      # stored before the gates
]


def _scan_page(matrix, picks=PICKS, status=None):
    p = state.portfolio()
    saved = (list(p.last_scan_picks or []), copy.deepcopy(p.scan_matrix),
             copy.deepcopy(getattr(p, "intraday_data_status", None)))
    p.last_scan_picks, p.scan_matrix = copy.deepcopy(picks), copy.deepcopy(matrix)
    if status is not None:
        p.intraday_data_status = status
    try:
        page = _client().get("/scan")
        assert page.status_code == 200, page.text[:300]
        return page.text
    finally:
        p.last_scan_picks, p.scan_matrix, p.intraday_data_status = saved


# ------------------------------------------------------------------ cells

def test_a_miss_is_red_only_where_the_gate_can_refuse():
    reading = {"tk": False, "tr": 1.05, "sk": False, "sr": 0.8}
    short = _matrix()["volume_gates"]
    intraday = _matrix("intraday", blocks2=True, need1=1.5)["volume_gates"]
    assert sr._volume_cell(reading, "t", short) == ("blocked", "1.05× (needs 1.3×)")     # Gate 1 blocks
    assert sr._volume_cell(reading, "s", short) == ("weak", "0.80× (needs 1.5×)")        # a reading
    assert sr._volume_cell(reading, "s", intraday) == ("blocked", "0.80× (needs 1.5×)")  # refuses here
    assert sr._volume_cell({"tk": True, "tr": 1.62, "sk": True, "sr": 2.4}, "s", short)[0] == "hit"


def test_off_and_unreadable_are_grey_never_a_pass_or_a_fail():
    momentum = _matrix("nifty200_momentum", need1=0.0, applies=False)["volume_gates"]
    assert sr._volume_cell({"tk": True, "tr": 1.9, "sk": True, "sr": 2.0}, "t", momentum) == (
        "waiting", "off in this segment")
    short = _matrix()["volume_gates"]
    for reading in ({"tk": None, "tr": None, "sk": None, "sr": None}, None, "junk"):
        assert sr._volume_cell(reading, "t", short) == ("waiting", "could not be read")
        assert sr._volume_cell(reading, "s", short) == ("waiting", "could not be read")


# ------------------------------------------------------------------ piano

def test_each_gate_has_its_own_piano_lane_next_to_timing():
    piano = sr._build_piano(_matrix())
    keys = [s["key"] for s in piano["sections"]]
    assert keys[keys.index("timing") + 1:keys.index("timing") + 3] == ["vol_trend", "vol_spike"]
    lanes = {s["key"]: s for s in piano["sections"]}
    assert (lanes["vol_trend"]["hit"], lanes["vol_trend"]["total"]) == (1, 4)
    assert (lanes["vol_spike"]["hit"], lanes["vol_spike"]["total"]) == (2, 4)
    assert [c["cls"] for c in lanes["vol_spike"]["cells"]] == ["hit", "hit", "weak", "waiting"]
    assert "QUIET.NS · volume spike (gate 2): 0.80× (needs 1.5×)" in [
        c["tip"] for c in lanes["vol_spike"]["cells"]]


def test_a_scan_record_stored_before_the_gates_gets_no_lane_and_no_block():
    old = {"ts": "2026-11-25T09:01:00", "mode": "short", "score_floor": 70.0,
           "rows": {"A.NS": {"score": 80.0, "timing": True, "abs_mom": True, "outcome": ""}}}
    keys = [s["key"] for s in sr._build_piano(old)["sections"]]
    assert "vol_trend" not in keys and "vol_spike" not in keys
    assert sr._volume_gates_view(old, state.cfg()) is None
    html = _scan_page(old)
    assert "volume_gates_marker" not in html and "data-volume-readings" in html   # picks still read


@pytest.mark.parametrize("key,title", [("vol_trend", "Volume Gate 1 — volume trend"),
                                       ("vol_spike", "Volume Gate 2 — volume spike")])
def test_each_lane_opens_its_own_page_with_the_rule_and_every_stock(key, title):
    p = state.portfolio()
    saved = copy.deepcopy(p.scan_matrix)
    p.scan_matrix = _matrix()
    try:
        page = _client().get(f"/scan/parameter/{key}")
    finally:
        p.scan_matrix = saved
    assert page.status_code == 200
    text = _text(page.text)
    assert title in text
    for ticker in ("BOTH", "SPIKE", "QUIET", "THIN"):
        assert ticker in text
    if key == "vol_spike":
        assert "refuses a buy only in Intraday" in text and "has not been tested" in text
        assert "0.80× (needs 1.5×)" in text and "could not be read" in text
    else:
        assert "Off in Momentum" in text and "1.05× (needs 1.3×)" in text


# -------------------------------------------------------------- scan page

def test_the_scan_page_prints_both_gates_with_their_own_counts():
    html = _scan_page(_matrix())
    assert "volume_gates_marker" in html
    text = _text(html)
    assert "Volume gates — last Short pass" in text
    assert "Two separate readings on the daily bars that pass scored (4 stocks)" in text
    gate1 = _text(html.split('data-volume-gate="1"', 1)[1].split('data-volume-gate="2"', 1)[0])
    gate2 = _text(html.split('data-volume-gate="2"', 1)[1].split("</div>\n</div>", 1)[0])
    assert "Needs 1.3× in Short. 1 of 3 stocks pass (1 could not be read)." in gate1
    assert "Part of the timing check: a stock below the mark is not bought." in gate1
    assert "Needs 1.5× . 2 of 3 stocks pass (1 could not be read)." in gate2
    assert "In Short it is a reading only; it stopped nothing." in gate2
    assert "It refuses a buy in: Intraday." in gate2
    assert "Not tested against returns in any segment." in gate2


def test_in_intraday_the_page_says_it_refuses_and_how_many_it_stopped():
    html = _scan_page(_matrix("intraday", blocks2=True, need1=1.5, stopped=1))
    text = _text(html)
    assert "Volume gates — last Intraday pass" in text and "on the 5-minute bars" in text
    assert "It refuses a buy in this segment: 1 stock that cleared score and timing was stopped by it" in text


def test_momentum_reads_gate_one_as_off_not_as_passed():
    text = _text(_scan_page(_matrix("nifty200_momentum", need1=0.0, applies=False)))
    assert "Off in Momentum: this segment's timing check is off." in text
    assert "Needs 0" not in text


def test_each_pick_shows_both_readings_under_its_timing_verdict():
    html = _scan_page(_matrix())
    rows = {}
    for row in html.split("<tr")[1:]:
        ticker = re.search(r'href="/chart/(\w+)\.NS"', row)
        if ticker:
            block = re.search(r'data-volume-readings>(.*?)</div>', row, re.S)
            rows[ticker.group(1)] = _text(block.group(1)) if block else None
    assert rows["BOTH"] == "Trend 1.62× ✓ · Spike 2.40× ✓"
    assert rows["QUIET"] == "Trend 1.05× ✗ · Spike 0.80× ✗"
    assert rows["THIN"] == "Trend — · Spike —"          # unreadable: a dash, never a cross
    assert rows["OLD"] is None                           # stored before the gates: nothing invented


def test_the_last_five_minute_pass_line_counts_what_the_spike_gate_stopped():
    from web.tests.test_intraday_data_2026_11_12 import coverage
    status = coverage()                                   # the stored status shape the page reads
    status["last_pass"] = {"at": "2026-11-26T05:00:00", "asked": 100, "ready": 96, "scored": 96,
                           "cleared_score": 3, "opened": [],
                           "not_bought": [{"reason": "score", "stocks": 90},
                                          {"reason": "volume_spike", "stocks": 3}],
                           "volume_gates": {"of": 96, "gate1": {"need": 1.5, "blocks": True},
                                            "gate2": {"need": 1.5, "blocks": True, "known": 96,
                                                      "passed": 11, "stopped": 3}}}
    html = _scan_page(_matrix(), status=status)
    line = _text(html.split("data-intraday-last-pass", 1)[1].split("</p>", 1)[0])
    assert "volume spike: 3" in line
    assert "Volume spike gate (1.5×): 3 stock(s) that cleared score and timing were stopped by it." in line
    # a pass stored before the gates, or with the gate only a reading, adds no sentence
    for gates in (None, {"gate2": {"need": 1.5, "blocks": False, "stopped": 0}}):
        older = copy.deepcopy(status)
        if gates is None:
            older["last_pass"].pop("volume_gates")
        else:
            older["last_pass"]["volume_gates"] = gates
        assert "data-intraday-spike-gate" not in _scan_page(_matrix(), status=older)


# ------------------------------------------------------------- stock page

def _bars(volume):
    n = len(volume)
    idx = pd.bdate_range("2025-08-01", periods=n)
    close = 500.0 * np.cumprod(np.full(n, 1.001))
    return pd.DataFrame({"Open": close * 0.995, "High": close * 1.012, "Low": close * 0.988,
                         "Close": close, "Volume": np.asarray(volume, dtype=float)}, index=idx)


def _card(tmp_path, df):
    cfg = Config(state_dir=str(tmp_path), mode="nifty200_momentum")
    cfg.apply_mode_preset()
    e = TradingEngine(cfg, Portfolio.fresh(cfg))
    return stock_card.build("ABC.NS", df, e.portfolio, cfg, e.mode_cfg, today=date(2026, 11, 26)), e, cfg


def test_the_stock_card_reads_both_gates_on_the_stored_daily_bars(tmp_path):
    # last 5 days average 1.25x the last 20; the latest day is 1.6x the 20 before it
    volume = [400_000.0] * 300 + [400_000.0] * 15 + [500_000.0, 500_000.0, 500_000.0, 500_000.0, 664_000.0]
    card, _, _ = _card(tmp_path, _bars(volume))
    gates = card["volume_gates"]
    assert gates["trend_ratio"] == pytest.approx(1.23, abs=0.02)
    assert (gates["spike_ok"], gates["spike_need"]) == (True, 1.5)
    assert gates["spike_ratio"] == pytest.approx(664_000 / 420_000, abs=0.001)
    by = {g["label"]: g for g in gates["segments"]}
    assert set(by) == {"Momentum", "Short", "Swing", "Positional"}        # Intraday is not daily
    assert by["Momentum"]["applies"] is False
    assert (by["Short"]["need"], by["Short"]["ok"]) == (1.3, False)
    assert (by["Swing"]["need"], by["Swing"]["ok"]) == (1.2, True)


def test_the_stock_page_prints_the_readings_and_says_what_they_are(tmp_path, monkeypatch):
    volume = [400_000.0] * 315 + [500_000.0] * 4 + [664_000.0]
    df = _bars(volume)
    cfg = Config(state_dir=str(tmp_path), mode="nifty200_momentum")
    cfg.apply_mode_preset()
    e = TradingEngine(cfg, Portfolio.fresh(cfg))
    monkeypatch.setattr(state, "cfg", lambda: cfg)
    monkeypatch.setattr(state, "portfolio", lambda: e.portfolio)
    monkeypatch.setattr(state, "engine", lambda: e)
    monkeypatch.setattr(auth, "current_user", lambda r: {"role": "owner", "email": "fixture"})
    with patch("web.routes.chart_routes.load_ohlcv", return_value=df):
        page = TestClient(app).get("/chart/ABC.NS")
    assert page.status_code == 200
    line = _text(page.text.split('data-stock-card-volume="1"', 1)[1].split("</div>", 1)[0])
    assert "Gate 1 · trend (last 5 days ÷ last 20) 1.2" in line
    assert "Momentum off" in line and "Short needs 1.3× ✗" in line and "Swing needs 1.2× ✓" in line
    assert "Gate 2 · spike (latest day ÷ the 20 before it) 1.58× , needs 1.5× ✓" in line
    assert "a reading only in these four segments" in line
    assert "Gate 2 refuses a buy there" in line


def test_bars_without_volume_print_no_volume_line(tmp_path):
    df = _bars([400_000.0] * 320).drop(columns=["Volume"])
    card, _, _ = _card(tmp_path, df)
    assert card["volume_gates"] is None
    short = _bars([400_000.0] * 10)
    assert _card(tmp_path, short)[0]["volume_gates"] is None      # too short to read either gate


# ---------------------------------------------------------- den + settings

def test_the_den_states_both_gates_from_the_live_settings():
    client = _client()
    cfg = state.cfg()
    saved = (cfg.volume_gate1_enabled, cfg.volume_gate2_enabled)
    try:
        page = client.get("/den")
        assert page.status_code == 200 and "den_volume_gates_marker" in page.text
        line = _text(page.text.split("den_volume_gates_marker", 1)[1].split("</li>", 1)[0])
        assert "Momentum off · Intraday 1.5× · Short 1.3× · Swing 1.2× · Positional 1.2×" in line
        assert "part of the timing check, a stock below its mark is not bought" in line
        assert "latest bar ÷ average of the 20 bars before it ≥ 1.5×" in line
        assert "refuses a buy in Intraday only, a reading in the other segments" in line
        assert "Gate 2 has not been tested against returns." in line
        cfg.volume_gate1_enabled, cfg.volume_gate2_enabled = False, False
        line = _text(client.get("/den").text.split("den_volume_gates_marker", 1)[1].split("</li>", 1)[0])
        assert "switched OFF — measured and shown, not required" in line
        assert "a reading in every segment" in line
    finally:
        cfg.volume_gate1_enabled, cfg.volume_gate2_enabled = saved


def _algo_form(client):
    html = client.get("/den").text
    html = html.split("settings_algo_form_marker", 1)[1].split("</form>", 1)[0]
    fields = dict(re.findall(r'name="([a-z_0-9]+)"[^>]*value="([^"]*)"', html))
    assert fields, "the settings form rendered no fields"
    return fields


def test_each_gate_has_its_own_switch_and_a_save_keeps_them_on():
    client = _client()
    before = (state.cfg().volume_gate1_enabled, state.cfg().volume_gate2_enabled)
    try:
        fields = _algo_form(client)
        # a bool must render as 1 / 0 — "True" in a number box posts empty = OFF
        assert (fields["volume_gate1_enabled"], fields["volume_gate2_enabled"]) == ("1", "1")
        assert client.post("/settings", data=fields, follow_redirects=False).status_code == 303
        assert (state.cfg().volume_gate1_enabled, state.cfg().volume_gate2_enabled) == (True, True)
        fields["volume_gate2_enabled"] = "0"
        assert client.post("/settings", data=fields, follow_redirects=False).status_code == 303
        assert state.cfg().volume_gate2_enabled is False and state.cfg().volume_gate1_enabled is True
        from trading import timing as T
        intraday = state.engine().mode_cfg("intraday")
        assert T.gate2_blocks(intraday) is False          # the switch reaches the scan's own test
    finally:
        cfg = state.cfg()
        cfg.volume_gate1_enabled, cfg.volume_gate2_enabled = before
