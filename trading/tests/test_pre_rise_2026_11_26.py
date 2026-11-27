"""Pre-rise as ONE signal — owner decision 2026-11-26: compression
(T12W_0005) plus volume ignition (T12W_0057), the ignition within 5 trading
sessions of the compression.

Display only, and shown with its measured record: on 12 years of daily bars
the pair did no better than chance (scripts/pre_rise_study.py).
"""
import ast
import importlib.util
import json
import pathlib

import numpy as np
import pandas as pd
import pytest

from trading import pre_rise as PR
from trading import triggers as TR

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _frame(n=140, inside_end=None, ignite_at=None):
    """A rising series with no inside bars and flat volume. `inside_end` =
    index of the LAST of five consecutive inside bars; `ignite_at` = index of
    a bar that trades 5x after five quiet bars."""
    high = 100.0 + 0.5 * np.arange(n)
    low = high - 2.0
    volume = np.full(n, 1000.0)
    if inside_end is not None:
        for i in range(inside_end - 4, inside_end + 1):
            high[i] = high[i - 1] - 0.1
            low[i] = low[i - 1] + 0.1
        for i in range(inside_end + 1, n):               # resume rising, each bar outside the last
            high[i] = high[i - 1] + 0.5
            low[i] = low[i - 1] - 0.05
    if ignite_at is not None:
        volume[ignite_at - 5:ignite_at] = 300.0
        volume[ignite_at] = 5000.0
    close = (high + low) / 2
    idx = pd.bdate_range("2026-01-01", periods=n)
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close, "Volume": volume}, index=idx)


def _production(df, trigger, ago=0):
    return bool(TR._FNS[trigger](TR._Ctx(df.iloc[:len(df) - ago].tail(PR.TAIL))))


def test_the_fixture_fires_the_owners_two_triggers_as_intended():
    df = _frame(inside_end=135, ignite_at=139)
    assert _production(df, "T12W_0005", ago=4) is True       # the cluster's last bar
    assert _production(df, "T12W_0057", ago=0) is True       # the ignition bar
    assert _production(df, "T12W_0057", ago=1) is False
    assert _production(_frame(), "T12W_0005") is False and _production(_frame(), "T12W_0057") is False


def test_ignition_within_five_sessions_of_compression_is_the_signal():
    df = _frame(inside_end=135, ignite_at=139)
    reading = PR.reading(df)
    assert reading["on"] is True and reading["signal_ago"] == 0
    assert reading["ignition_ago"] == 0 and reading["compression_ago"] is not None
    assert reading["asof"] == df.index[-1].strftime("%Y-%m-%d")     # the bars it was read on


def test_either_half_alone_is_not_the_signal():
    only_volume = PR.reading(_frame(ignite_at=139))
    assert only_volume["on"] is False and only_volume["signal_ago"] is None
    assert only_volume["ignition_ago"] == 0 and only_volume["compression_ago"] is None
    only_base = PR.reading(_frame(inside_end=138))
    assert only_base["on"] is False and only_base["ignition_ago"] is None
    assert only_base["compression_ago"] == 0
    quiet = PR.reading(_frame())
    assert quiet == {"asof": _frame().index[-1].strftime("%Y-%m-%d"), "compression_ago": None,
                     "ignition_ago": None, "signal_ago": None, "on": False}


def test_compression_more_than_five_sessions_before_the_ignition_does_not_count():
    # the 5-of-10 count is last true 5 bars after the cluster's final bar;
    # an ignition 12 bars after it is 7 sessions past the last compression bar
    df = _frame(inside_end=127, ignite_at=139)
    last_compression = next(ago for ago in range(20) if _production(df, "T12W_0005", ago))
    assert last_compression > PR.WITHIN_SESSIONS
    reading = PR.reading(df)
    assert reading["on"] is False and reading["ignition_ago"] == 0
    # one session inside the limit, it counts
    df = _frame(inside_end=129, ignite_at=139)
    assert next(ago for ago in range(20) if _production(df, "T12W_0005", ago)) == PR.WITHIN_SESSIONS
    assert PR.reading(df)["on"] is True


def test_the_label_is_shown_for_five_sessions_from_the_day_it_fires():
    assert PR.SHOWN_FOR_SESSIONS == 5 and PR.WITHIN_SESSIONS == 5
    four_ago = PR.reading(_frame(inside_end=131, ignite_at=135))
    assert four_ago["on"] is True and four_ago["signal_ago"] == 4 and four_ago["ignition_ago"] == 4
    five_ago = PR.reading(_frame(inside_end=130, ignite_at=134))
    assert five_ago["on"] is False and five_ago["signal_ago"] is None
    assert five_ago["ignition_ago"] == 5                      # still reported, no longer labelled


@pytest.mark.parametrize("build", [
    lambda: None,
    lambda: pd.DataFrame(),
    lambda: _frame(n=PR.MIN_BARS - 1),
    lambda: _frame().drop(columns=["Volume"]),
])
def test_what_cannot_be_read_is_unknown_never_no_signal(build):
    assert PR.reading(build()) is None


def test_both_halves_come_from_the_production_trigger_functions(monkeypatch):
    df = _frame(inside_end=135, ignite_at=139)
    assert PR.reading(df)["on"] is True
    monkeypatch.setitem(TR._FNS, "T12W_0057", lambda x: False)
    assert PR.reading(df)["on"] is False and PR.reading(df)["ignition_ago"] is None
    monkeypatch.setitem(TR._FNS, "T12W_0057", lambda x: 1 / 0)
    assert PR.reading(df) is None                              # a broken trigger reads unknown


def test_the_screens_print_the_committed_measurement_and_nothing_else():
    run = json.loads((ROOT / "research/pre_rise_study/run_2026-11-26/pre_rise_calibration.json").read_text())
    cell = run["cells"]["signal|main_10pct|all"]
    assert (cell["fires"], cell["hit_pct"], cell["chance_pct"], cell["lift"]) == (373, 29.49, 32.11, 0.919)
    assert run["cells"]["signal|main_10pct|train"]["lift"] == 0.996
    assert run["cells"]["signal|main_10pct|test"]["lift"] == 0.799
    assert run["within_sessions"] == PR.WITHIN_SESSIONS and run["panel"] == 191
    assert PR.RECORD["main"] == {"fires": 373, "hit_pct": 29.49, "chance_pct": 32.11, "lift": 0.919}
    text = PR.record_text()
    assert "it fired 373 times in 469,947 stock-days" in text
    assert "a 10% rise followed 29.49% of the time against 32.11% for any day" in text
    assert "no better than chance (lift 0.919; 0.996 before July 2020, 0.799 after)" in text


def test_a_missing_or_mismatched_run_says_not_measured(monkeypatch):
    monkeypatch.setattr(PR, "RECORD", None)
    assert PR.record_text().startswith("Not measured")
    monkeypatch.setattr(PR, "RECORD", {"within_sessions": 3})
    assert PR.record_text().startswith("Not measured")       # a run of a different rule is not this rule's record
    monkeypatch.setattr(PR, "_RUN", ROOT / "research" / "nope.json")
    assert PR._load_record() is None


def test_the_study_measures_the_same_two_conditions_as_production():
    spec = importlib.util.spec_from_file_location("pre_rise_study", ROOT / "scripts" / "pre_rise_study.py")
    study = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(study)
    assert study.WITHIN == PR.WITHIN_SESSIONS
    df = _frame(n=420, inside_end=400, ignite_at=404)
    comp, ign = study.compression(df), study.ignition(df)
    for i in range(380, 420):
        x = TR._Ctx(df.iloc[:i + 1].tail(400))
        assert bool(TR._t0005(x)) == bool(comp[i]) and bool(TR._t0057(x)) == bool(ign[i]), i
    assert ign[404] and comp[400]


def test_no_buy_sell_size_or_exit_rule_can_read_the_signal():
    for name in ("engine", "risk", "orders", "danger", "sizing", "exits", "scoring", "timing",
                 "targets", "backtest", "rotation", "intraday_scoring"):
        tree = ast.parse((ROOT / "trading" / f"{name}.py").read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported |= {node.module or ""} | {a.name for a in node.names}
            elif isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
        assert not any("pre_rise" in name_ for name_ in imported), f"trading/{name}.py imports pre_rise"
    # and the module itself trades, stores and fetches nothing
    source = ast.parse((ROOT / "trading" / "pre_rise.py").read_text())
    modules = {(n.module or "") for n in ast.walk(source) if isinstance(n, ast.ImportFrom)} | {
        a.name for n in ast.walk(source) if isinstance(n, ast.Import) for a in n.names}
    assert modules <= {"__future__", "json", "pathlib", "typing", ""}, modules   # "" = `from . import triggers`


def test_the_reading_is_said_in_the_same_words_on_every_screen():
    assert PR.describe(None) == "cannot be read from the stored bars (too little history)"
    on = PR.describe({"on": True, "signal_ago": 0, "ignition_ago": 0, "compression_ago": 2})
    assert on == ("ON, fired on the latest stored bar (compression 2 sessions ago; "
                  "volume ignition on the latest stored bar)")
    off = PR.describe({"on": False, "signal_ago": None, "ignition_ago": None, "compression_ago": 1})
    assert off == "not on (compression 1 session ago; volume ignition not in the last 10 sessions)"
    assert "Display only" in PR.RULE_TEXT and "5 sessions before it" in PR.RULE_TEXT
