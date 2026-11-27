"""The two volume gates — owner decision 2026-11-26: "two volume gates shown
separately (5-vs-20-day trend; hard 1.5x spike, blocking Intraday only)".

Gate 1, the TREND (5-bar mean against the 20-bar mean), has always been half
of the timing check. It keeps blocking exactly as before, and gains its own
reading and switch. Gate 2, the SPIKE (the latest bar against the mean of the
20 bars before it), is new: it refuses a buy in Intraday only and is a
reading in every other segment.
"""
import ast
import pathlib

import numpy as np
import pandas as pd
import pytest

from trading import decision_evidence as de
from trading import intraday_bars as ib
from trading import timing as T
from trading.config import Config
from trading.tests.test_completed_intraday_2026_11_12 import (  # noqa: F401
    bar_frame, intraday_engine, neutral_context)

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _cfg(mode):
    cfg = Config(mode=mode)
    cfg.apply_mode_preset()
    return cfg


def _frame(volume, close=None):
    volume = [float(v) for v in volume]
    close = close if close is not None else [100.0] * len(volume)
    return pd.DataFrame({"Close": close, "Volume": volume})


# ------------------------------------------------------------ the readings

def test_the_spike_is_the_latest_bar_against_the_twenty_bars_before_it():
    cfg = _cfg("intraday")
    volume = pd.Series([100.0] * 20 + [150.0])
    assert T.passes_volume_spike(volume, cfg) == (True, 1.5)          # exactly the mark passes
    assert T.passes_volume_spike(pd.Series([100.0] * 20 + [149.0]), cfg) == (False, 1.49)
    # the latest bar is NOT part of its own average: with it included this
    # reading would be 300 / 109.5 = 2.74, and a spike would dilute itself
    ok, ratio = T.passes_volume_spike(pd.Series([100.0] * 20 + [300.0]), cfg)
    assert (ok, ratio) == (True, 3.0)
    # only the 20 bars before the latest count; older bars do not
    ok, ratio = T.passes_volume_spike(pd.Series([9_999.0] * 5 + [100.0] * 20 + [200.0]), cfg)
    assert (ok, ratio) == (True, 2.0)


@pytest.mark.parametrize("volume", [
    [100.0] * 20,                       # 20 bars: nothing before the latest 20
    [0.0] * 20 + [500.0],               # nothing traded before: no base
    [100.0] * 20 + [float("nan")],      # the latest bar is unreadable
    [float("nan")] * 20 + [500.0],      # the base is unreadable
    [],
])
def test_a_spike_that_cannot_be_read_is_unknown_never_a_pass(volume):
    assert T.passes_volume_spike(pd.Series(volume, dtype=float), _cfg("intraday")) == (None, None)


def test_the_owners_numbers_are_the_defaults():
    cfg = Config()
    assert cfg.volume_gate1_enabled is True and cfg.volume_gate2_enabled is True
    assert (cfg.volume_gate2_ratio, cfg.volume_gate2_lookback) == (1.5, 20)
    assert cfg.volume_gate2_block_modes == ("intraday",)


@pytest.mark.parametrize("mode,need1,applies,blocks2", [
    ("intraday", 1.5, True, True),
    ("short", 1.3, True, False),
    ("swing", 1.2, True, False),
    ("positional", 1.2, True, False),
    ("nifty200_momentum", 0.0, False, False),     # Momentum's timing is off
])
def test_each_segment_reads_both_gates_with_its_own_rule(mode, need1, applies, blocks2):
    cfg = _cfg(mode)
    frame = _frame([100.0] * 16 + [400.0] * 4 + [130.0])
    gates = T.volume_gates(frame, cfg)
    trend_ok, trend = T.passes_volume_confirm(frame["Volume"], cfg)
    assert gates["gate1"] == {"ok": trend_ok, "ratio": round(trend, 3), "need": need1,
                              "applies": applies, "blocks": applies}
    # 130 against the mean of the 20 bars before it: (16*100 + 4*400) / 20 = 160
    assert gates["gate2"] == {"ok": False, "ratio": 0.812, "need": 1.5, "blocks": blocks2}
    assert T.gate2_blocks(cfg) is blocks2


def test_the_switches_change_only_what_each_gate_may_refuse():
    cfg = _cfg("intraday")
    frame = _frame([100.0] * 21)
    cfg.volume_gate2_enabled = False
    assert T.gate2_blocks(cfg) is False and T.volume_gates(frame, cfg)["gate2"]["blocks"] is False
    cfg.volume_gate2_enabled, cfg.volume_gate2_block_modes = True, ()
    assert T.gate2_blocks(cfg) is False
    cfg.volume_gate1_enabled = False
    gate1 = T.volume_gates(frame, cfg)["gate1"]
    assert gate1["blocks"] is False and gate1["ok"] is False and gate1["ratio"] == 1.0


def test_too_little_history_reads_unknown_on_both():
    gates = T.volume_gates(_frame([100.0] * 10), _cfg("swing"))
    assert gates["gate1"]["ok"] is None and gates["gate1"]["ratio"] is None
    assert gates["gate2"]["ok"] is None and gates["gate2"]["ratio"] is None


# ------------------------------------------------- gate 1 inside the timing

def _breaking_out(volume):
    close = list(np.linspace(100.0, 110.0, len(volume)))       # latest close = the high
    return _frame(volume, close)


def test_gate_one_still_blocks_exactly_as_before_by_default():
    cfg = _cfg("swing")                                         # needs 1.2x
    quiet = _breaking_out([100.0] * 25)
    busy = _breaking_out([100.0] * 20 + [300.0] * 5)
    assert T.timing_ok(quiet, cfg)[0] is False                  # breakout yes, volume no
    assert T.timing_ok(busy, cfg)[0] is True
    decision = de.new("X.NS", "swing", "scan")
    with de.capture(decision):
        T.timing_ok(quiet, cfg)
    assert decision["gates"]["volume"]["state"] == "failed"
    assert decision["gates"]["volume"]["role"] == "gate"


def test_gate_one_switched_off_is_measured_and_shown_but_not_required():
    cfg = _cfg("swing")
    cfg.volume_gate1_enabled = False
    quiet = _breaking_out([100.0] * 25)
    decision = de.new("X.NS", "swing", "scan")
    with de.capture(decision):
        passed, diag = T.timing_ok(quiet, cfg)
    assert passed is True and diag["volume_ok"] is False and diag["volume_gate1_on"] is False
    volume = decision["gates"]["volume"]
    # the record must not read as a refusal: nothing was refused
    assert volume["state"] == "skipped" and volume["role"] == "advisory"
    assert volume["value"] == 1.0 and "switched off" in volume["reason"]
    # the breakout leg is still required
    falling = _frame([100.0] * 25, list(np.linspace(110.0, 100.0, 25)))
    assert T.timing_ok(falling, cfg)[0] is False


# --------------------------------------------- gate 2 in the Intraday scan

def _supply(monkeypatch, last_bar_volume):
    """UPP breaks out and its 5-bar volume trend passes; the LATEST bar's own
    volume is the argument. 16 quiet bars of 10,000 and 4 busy bars of
    100,000 come before it, so the mean it is read against is 28,000."""
    def fetch(tickers, cfg, *a, **k):
        when = ib.utc_now(a[1])
        frames, details = {}, {}
        for t in tickers:
            frame = bar_frame(now=when)
            close = frame.Close.iloc[-2] * 1.015
            frame.iloc[-1] = [close - .01, close + .06, close - .06, close, last_bar_volume]
            frame.loc[frame.index[-5:-1], "Volume"] = 100000.
            clean, d = ib.validate(frame, when, min_bars=ib.required_bars(cfg))
            details[t] = d
            if d["status"] == "ready":
                frames[t] = clean
        return frames, details
    monkeypatch.setattr("trading.engine.idata.fetch", fetch)


def test_a_cleared_intraday_pick_without_a_volume_spike_is_not_bought(
        tmp_path, monkeypatch, neutral_context):
    e, _, _ = intraday_engine(tmp_path, monkeypatch)
    _supply(monkeypatch, 30000.)                 # 30,000 / 28,000 = 1.07x: no spike

    result = e.scan()

    row = result.ranked[0]
    assert row["ticker"] == "UPP.NS" and row["score"] >= e.cfg.min_score_to_enter
    assert row["timing_pass"] is True            # score and timing are both cleared
    assert row["volume_gates"]["gate1"]["ok"] is True
    assert row["volume_gates"]["gate2"] == {"ok": False, "ratio": 1.071, "need": 1.5, "blocks": True}
    assert result.opened == [] and "UPP.NS" not in e.portfolio.positions
    assert {"ticker": "UPP.NS", "reason": "volume_spike"} in result.skipped
    gate = row["decision"]["gates"]["volume_spike"]
    assert gate["state"] == "failed" and gate["value"] == 1.071 and gate["threshold"] == 1.5
    matrix = e.portfolio.scan_matrix
    assert matrix["rows"]["UPP.NS"]["outcome"] == "volume_spike"
    assert matrix["rows"]["UPP.NS"]["vol"] == {"tk": True, "tr": row["volume_gates"]["gate1"]["ratio"],
                                               "sk": False, "sr": 1.071}
    assert matrix["volume_gates"]["gate2"] == {"need": 1.5, "blocks": True, "known": 1,
                                               "passed": 0, "stopped": 1}


def test_the_same_pick_with_a_spike_is_bought(tmp_path, monkeypatch, neutral_context):
    e, _, _ = intraday_engine(tmp_path, monkeypatch)
    _supply(monkeypatch, 42000.)                 # 42,000 / 28,000 = 1.5x: exactly the mark
    result = e.scan()
    assert result.opened == ["UPP.NS"], result.skipped
    gate = result.ranked[0]["decision"]["gates"]["volume_spike"]
    assert gate["state"] == "passed" and gate["value"] == 1.5
    assert e.portfolio.scan_matrix["volume_gates"]["gate2"]["stopped"] == 0


@pytest.mark.parametrize("change", ["switch_off", "segment_removed"])
def test_with_the_gate_off_the_pick_without_a_spike_is_bought(
        tmp_path, monkeypatch, neutral_context, change):
    e, _, _ = intraday_engine(tmp_path, monkeypatch)
    _supply(monkeypatch, 30000.)
    if change == "switch_off":
        e.cfg.volume_gate2_enabled = False
    else:
        e.cfg.volume_gate2_block_modes = ()
    result = e.scan()
    assert result.opened == ["UPP.NS"], result.skipped
    # the reading is still taken and shown; no refusal is recorded
    assert result.ranked[0]["volume_gates"]["gate2"]["ok"] is False
    assert "volume_spike" not in result.ranked[0]["decision"]["gates"]


def test_the_five_minute_pass_counts_what_the_spike_gate_stopped(
        tmp_path, monkeypatch, neutral_context):
    e, _, _ = intraday_engine(tmp_path, monkeypatch)
    _supply(monkeypatch, 30000.)
    result = e.scan(side_pass=True)
    assert result.opened == []
    last = e.portfolio.intraday_data_status["last_pass"]
    assert last["volume_gates"]["gate2"] == {"need": 1.5, "blocks": True, "known": 1,
                                             "passed": 0, "stopped": 1}
    assert {"reason": "volume_spike", "stocks": 1} in last["not_bought"]
    line = [r for r in e.portfolio.decision_journal if r.get("action") == "INTRADAY_PASS"][-1]
    assert "Volume spike gate (1.5x): 1 stock(s)" in line["why"]
    # the stock that cleared its score and was refused has ONE full record
    refused = [r for r in e.portfolio.decision_journal
               if r.get("action") == "SCAN_DECISION" and r.get("ticker") == "UPP.NS"]
    assert [r["why"] for r in refused] == ["volume_spike"]


# ------------------------------------- a reading only in the daily segments

def test_in_a_daily_segment_the_spike_is_read_and_never_refuses(tmp_path, monkeypatch):
    from trading.tests.test_audit_s4_s9_2026_10_02 import _scan_engine
    e = _scan_engine(tmp_path, monkeypatch)                      # Momentum, one strong pick
    result = e.scan()
    row = result.ranked[0]
    assert row["ticker"] == "UPP.NS"
    # the pick went on to the cash gates (this fixture's book has no cash):
    # nothing about volume stopped it on the way
    assert [s["reason"] for s in result.skipped] == ["category_budget_full"]
    gates = row["volume_gates"]
    assert gates["gate2"]["blocks"] is False and gates["gate2"]["ratio"] is not None
    assert gates["gate1"]["applies"] is False                    # Momentum's timing is off
    assert "volume_spike" not in row["decision"]["gates"]
    assert not [s for s in result.skipped if s.get("reason") == "volume_spike"]
    summary = e.portfolio.scan_matrix["volume_gates"]
    assert summary["of"] == 1 and summary["gate2"]["blocks"] is False
    assert summary["gate2"]["stopped"] == 0 and summary["gate1"]["applies"] is False
    assert e.portfolio.last_scan_picks[0]["volume_gates"] == gates
    # and the readings survive the saved book
    from trading.paper import Portfolio
    again = Portfolio.load_or_fresh(e.cfg)
    assert again.scan_matrix["volume_gates"] == summary
    assert again.scan_matrix["rows"]["UPP.NS"]["vol"]["sr"] == gates["gate2"]["ratio"]


def test_a_failed_reading_costs_the_columns_never_the_daily_scan(tmp_path, monkeypatch):
    from trading.tests.test_audit_s4_s9_2026_10_02 import _scan_engine
    e = _scan_engine(tmp_path, monkeypatch)
    monkeypatch.setattr("trading.engine.volume_gates", lambda *a, **k: 1 / 0)
    result = e.scan()
    assert result.ranked[0]["volume_gates"] is None
    assert [s["reason"] for s in result.skipped] == ["category_budget_full"]   # as without the fault
    assert e.portfolio.scan_matrix["rows"]["UPP.NS"]["vol"] is None
    assert e.portfolio.scan_matrix["volume_gates"]["gate2"] == {
        "need": 1.5, "blocks": False, "known": 0, "passed": 0, "stopped": 0}


def test_a_failed_reading_in_intraday_refuses_rather_than_assumes_a_spike(
        tmp_path, monkeypatch, neutral_context):
    e, _, _ = intraday_engine(tmp_path, monkeypatch)
    _supply(monkeypatch, 42000.)
    monkeypatch.setattr("trading.engine.volume_gates", lambda *a, **k: 1 / 0)
    result = e.scan()
    assert result.opened == []
    assert result.ranked[0]["decision"]["gates"]["volume_spike"]["state"] == "unavailable"


# ------------------------------------------------------------- boundaries

def test_only_the_scan_and_the_timing_module_read_the_spike_gate():
    """No size, stop, target, exit or owner-order rule reads the new gate."""
    names = {"volume_gate2_enabled", "volume_gate2_ratio", "volume_gate2_lookback",
             "volume_gate2_block_modes", "volume_gates", "gate2_blocks", "passes_volume_spike"}
    for name in ("risk", "orders", "danger", "sizing", "exits", "scoring", "targets",
                 "backtest", "intraday_scoring", "rotation"):
        tree = ast.parse((ROOT / "trading" / f"{name}.py").read_text())
        used = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {
            n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
            n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        assert not (used & names), f"trading/{name}.py reads the volume spike gate"
