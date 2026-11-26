"""Index versus the typical stock — the owner's "bubble" measure.

Owner 2026-11-26: "market goes quite high but not much movement in stocks,
this bubble I want to define". Display only: a pure reading the Momentum scan
stores and the dashboard prints. No gate, size, stop or target reads it.
"""
import ast
import pathlib

import numpy as np
import pandas as pd
import pytest

from trading import market_gap as mg

ROOT = pathlib.Path(__file__).resolve().parents[2]
IDX = pd.bdate_range("2026-07-01", periods=90)


def _frame(total_return, bars=90, index=IDX):
    """A straight line ending `total_return` above its value 60 sessions ago
    (or above its first bar, when it has fewer than 61)."""
    close = np.full(bars, 100.0)
    ramp = min(bars, 61)
    close[-ramp:] = np.linspace(100.0, 100.0 * (1 + total_return), ramp)
    return pd.DataFrame({"Close": close}, index=index[-bars:])


def _market(index_return, stock_returns):
    history = {f"S{i:03d}.NS": _frame(r) for i, r in enumerate(stock_returns)}
    return history, _frame(index_return)


def test_the_owners_example_reads_as_a_narrow_rally():
    # index +7.2% over 60 sessions; a quarter of stocks ahead, the typical one down
    returns = [0.12] * 30 + [-0.029] * 90
    history, bench = _market(0.072, returns)
    w = mg.reading(history, bench)["windows"]["60"]
    assert w["index_pct"] == pytest.approx(7.2, abs=0.01)
    assert w["median_pct"] == pytest.approx(-2.9, abs=0.01)
    assert w["gap_points"] == pytest.approx(10.1, abs=0.02)
    assert w["beat_share"] == pytest.approx(0.25) and w["up_share"] == pytest.approx(0.25)
    assert w["sample"] == 120 and w["state"] == "narrow"


def test_a_broad_move_and_a_falling_index_are_named_differently():
    history, bench = _market(0.05, [0.06] * 80 + [0.02] * 40)
    assert mg.reading(history, bench)["windows"]["60"]["state"] == "broad"
    history, bench = _market(-0.04, [-0.10] * 120)
    w = mg.reading(history, bench)["windows"]["60"]
    assert w["state"] == "falling" and w["gap_points"] == pytest.approx(6.0, abs=0.02)


@pytest.mark.parametrize("gap,beat,index,state", [
    (10.1, 0.26, 0.072, "narrow"), (5.0, 0.39, 0.01, "narrow"),
    (4.9, 0.10, 0.05, "broad"), (12.0, 0.40, 0.05, "broad"),
    (12.0, 0.10, 0.0, "falling"), (None, 0.1, 0.05, ""), (5.0, None, 0.05, "")])
def test_the_two_assumed_cut_offs(gap, beat, index, state):
    assert mg.state_for(gap, beat, index) == state
    assert mg.ASSUMED["narrow_gap_points"] == 5.0 and mg.ASSUMED["narrow_beat_share"] == 0.40


def test_funds_stale_names_and_short_histories_do_not_vote():
    history, bench = _market(0.05, [0.01] * 110)
    history["LIQUIDFUND.NS"] = _frame(0.50)                                # a fund unit
    history["HALTED.NS"] = _frame(0.50, index=IDX - pd.Timedelta(days=30))  # stopped trading
    history["NEWLIST.NS"] = _frame(0.50, bars=30)                          # under a window of bars
    history["EMPTY.NS"] = pd.DataFrame({"Close": []})
    history["BROKEN.NS"] = None
    got = mg.reading(history, bench, exclude={"LIQUIDFUND.NS"})
    w60, w20 = got["windows"]["60"], got["windows"]["20"]
    assert w60["sample"] == 110 and w60["median_pct"] == pytest.approx(1.0, abs=0.01)
    assert w20["sample"] == 111                       # the new listing has 20 sessions, not 60
    assert got["asof"] == IDX[-1].strftime("%Y-%m-%d")


def test_a_thin_sample_or_a_missing_index_gives_no_reading():
    history, bench = _market(0.05, [0.01] * (mg.MIN_SAMPLE - 1))
    assert mg.reading(history, bench) is None
    history, _ = _market(0.05, [0.01] * 150)
    assert mg.reading(history, None) is None
    assert mg.reading(history, pd.DataFrame({"Close": []})) is None
    assert mg.reading({}, bench) is None


def _uses(path):
    """Every place a module touches `.market_gap` in CODE (comments and
    docstrings are not code — reading the syntax tree, not the text)."""
    tree = ast.parse(path.read_text())
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == "market_gap"]


def test_no_trading_rule_reads_the_gap():
    for name in ("risk", "orders", "danger", "sizing", "exits", "scoring", "timing", "targets",
                 "category_budget", "measured_kelly", "regime"):
        assert not _uses(ROOT / "trading" / f"{name}.py"), name
    # the engine only WRITES the reading; it never loads it back into a decision
    engine = _uses(ROOT / "trading" / "engine.py")
    assert engine and all(isinstance(node.ctx, ast.Store) for node in engine)


def test_only_the_momentum_pass_stores_it_and_a_bad_reading_keeps_the_old_one(tmp_path, monkeypatch):
    from trading.tests.test_audit_s4_s9_2026_10_02 import _scan_engine
    e = _scan_engine(tmp_path, monkeypatch)
    seen = []
    monkeypatch.setattr("trading.market_gap.reading",
                        lambda history, bench, exclude=(): seen.append(sorted(history)) or
                        {"asof": "2026-11-25", "windows": {"60": {"gap_points": 10.1}}})
    e.scan()
    assert len(seen) == 1 and e.portfolio.market_gap["windows"]["60"]["gap_points"] == 10.1
    assert e.portfolio.market_gap["benchmark"] == e.cfg.benchmark and e.portfolio.market_gap["computed"]
    kept = dict(e.portfolio.market_gap)
    monkeypatch.setattr("trading.market_gap.reading", lambda *a, **k: None)       # a thin day
    e.scan()
    assert e.portfolio.market_gap == kept
    monkeypatch.setattr("trading.market_gap.reading", lambda *a, **k: 1 / 0)      # a crash
    e.scan()
    assert e.portfolio.market_gap == kept
    e.cfg.mode = "swing"
    e.cfg.apply_mode_preset()
    monkeypatch.setattr("trading.market_gap.reading", lambda *a, **k: pytest.fail("swing pass read the gap"))
    e.scan()
