"""Danger layer (owner order 2026-08-11: "go with c then auto with B").

Mode C: alerts only for 7 days. Mode B (auto): 1 VERIFIED danger trigger on
a held name -> stop tightens to -2.5% off price; 2+ -> exit half. Sell side
only, evidence-locked to the strict calibration CSV."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trading import danger
from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import PaperPosition, Portfolio

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Evidence lock
# ---------------------------------------------------------------------------

def test_verified_list_comes_from_strict_calibration_csv():
    import csv
    with open(ROOT / "research" / "bt_calibration" / "run_2026-08-09_strict15"
              / "trigger_calibration.csv") as f:
        expect = {r["trigger_id"] for r in csv.DictReader(f)
                  if r["proof_tag"] == "VERIFIED"
                  and r["side"] in ("FALL", "COLLAPSE")}
    assert set(danger.VERIFIED) == expect
    assert len(danger.VERIFIED) == 10


def test_danger_side_only_never_entries():
    for meta in danger.VERIFIED.values():
        assert meta["side"] in ("FALL", "COLLAPSE")
    src = (ROOT / "trading" / "danger.py").read_text()
    assert "GATE_RISE" not in src and "evaluate_gates" not in src


# ---------------------------------------------------------------------------
# Mode clock
# ---------------------------------------------------------------------------

def test_alert_week_then_auto():
    assert danger.auto_mode_active("2026-08-11", "2026-08-12") is False
    assert danger.auto_mode_active("2026-08-11", "2026-08-17") is False
    assert danger.auto_mode_active("2026-08-11", "2026-08-18") is True
    assert danger.auto_mode_active("", "2026-08-18") is False


def test_plan_matrix():
    assert danger.plan("X", [], True).action == "NONE"
    two = list(danger.VERIFIED)[:2]
    assert danger.plan("X", two[:1], False).action == "ALERT"
    assert danger.plan("X", two[:1], True).action == "TIGHTEN"
    assert danger.plan("X", two, True).action == "EXIT_HALF"


# ---------------------------------------------------------------------------
# Engine integration on production-shaped frames
# ---------------------------------------------------------------------------

def _calm(n=300, seed=3):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0.0004, 0.004, n))
    return pd.DataFrame({
        "Open": close, "High": close * 1.004, "Low": close * 0.996,
        "Close": close, "Volume": np.full(n, 200_000.0)},
        index=pd.bdate_range(end="2026-08-11", periods=n))


def _crashing(n=300):
    """One massive final bar: -18% wide-range close at the low on 25x
    volume, breaking the 20d support — the tripwires' home ground."""
    df = _calm(n)
    c = df["Close"].to_numpy().copy()
    o = c.copy(); h = df["High"].to_numpy().copy(); l = df["Low"].to_numpy().copy()
    v = df["Volume"].to_numpy().copy()
    o[-1] = c[-2] * 0.995
    h[-1] = c[-2] * 0.998
    c[-1] = c[-2] * 0.82
    l[-1] = c[-1] * 0.998
    v[-1] = 5_000_000
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c,
                         "Volume": v}, index=df.index)


def test_crashing_frame_fires_danger_triggers():
    fired = danger.evaluate_stock(_crashing())
    assert len(fired) >= 1, f"expected a tripwire on a -18% break, got {fired}"
    assert danger.evaluate_stock(_calm()) == []


def _engine(tmp_path, start_days_ago: int) -> TradingEngine:
    cfg = Config(state_dir=str(tmp_path))
    eng = TradingEngine(cfg=cfg, portfolio=Portfolio.fresh(cfg))
    eng.portfolio.danger_alerts_start = (
        datetime.utcnow().date() - timedelta(days=start_days_ago)).isoformat()
    eng.portfolio.positions["CRASH.NS"] = PaperPosition(
        ticker="CRASH.NS", qty=100, entry_price=100.0,
        entry_date=datetime.utcnow() - timedelta(days=10),
        sector="Test", peak_price=100.0, entry_score=70.0,
        reasoning="test", stop_price=95.0, target_price=112.0)
    return eng


def test_alert_mode_touches_nothing(tmp_path):
    eng = _engine(tmp_path, start_days_ago=1)      # inside the alert week
    prices = {"CRASH.NS": 72.9}
    eng._danger_pass(prices, {"CRASH.NS": _crashing()}, datetime.utcnow())
    pos = eng.portfolio.positions["CRASH.NS"]
    assert pos.qty == 100 and pos.stop_price == 95.0
    acts = [e["action"] for e in eng.portfolio.decision_journal]
    assert "DANGER_ALERT" in acts
    assert "DANGER_EXIT_HALF" not in acts


def test_auto_mode_exits_half_on_two_fires(tmp_path, monkeypatch):
    eng = _engine(tmp_path, start_days_ago=10)     # auto mode
    monkeypatch.setattr(danger, "evaluate_stock",
                        lambda df: list(danger.VERIFIED)[:2])
    prices = {"CRASH.NS": 72.9}
    eng._danger_pass(prices, {"CRASH.NS": _crashing()}, datetime.utcnow())
    pos = eng.portfolio.positions["CRASH.NS"]
    assert pos.qty == 50                            # half sold
    assert len(eng.portfolio.trades) == 1           # real ClosedTrade
    assert eng.portfolio.trades[-1].qty == 50
    acts = [e["action"] for e in eng.portfolio.decision_journal]
    assert "DANGER_EXIT_HALF" in acts


def test_auto_mode_tighten_on_single_fire(tmp_path, monkeypatch):
    eng = _engine(tmp_path, start_days_ago=10)
    one = list(danger.VERIFIED)[:1]
    monkeypatch.setattr(danger, "evaluate_stock", lambda df: one)
    # winner at 120 with the entry stop still at 95: tighten pulls it to 117
    prices = {"CRASH.NS": 120.0}
    eng._danger_pass(prices, {"CRASH.NS": _calm()}, datetime.utcnow())
    pos = eng.portfolio.positions["CRASH.NS"]
    assert pos.stop_price == round(120.0 * 0.975, 2)
    assert pos.custom_levels is True                # survives boot retag
    assert pos.qty == 100


def test_tighten_never_loosens_an_already_tighter_stop(tmp_path, monkeypatch):
    eng = _engine(tmp_path, start_days_ago=10)
    monkeypatch.setattr(danger, "evaluate_stock",
                        lambda df: list(danger.VERIFIED)[:1])
    # underwater at 90 with stop 95: -2.5% off price (87.75) would LOOSEN
    # the stop - the layer must keep 95 and only alert
    eng._danger_pass({"CRASH.NS": 90.0}, {"CRASH.NS": _calm()},
                     datetime.utcnow())
    assert eng.portfolio.positions["CRASH.NS"].stop_price == 95.0


def test_calm_stock_untouched_and_quiet(tmp_path):
    eng = _engine(tmp_path, start_days_ago=10)
    eng._danger_pass({"CRASH.NS": 100.0}, {"CRASH.NS": _calm()},
                     datetime.utcnow())
    assert eng.portfolio.positions["CRASH.NS"].qty == 100
    assert not [e for e in eng.portfolio.decision_journal
                if str(e.get("action", "")).startswith("DANGER_")
                and e.get("action") != "DANGER_LAYER_ARMED"]


def test_disabled_flag_makes_layer_inert(tmp_path):
    eng = _engine(tmp_path, start_days_ago=10)
    eng.cfg.danger_layer_enabled = False
    eng._danger_pass({"CRASH.NS": 72.9}, {"CRASH.NS": _crashing()},
                     datetime.utcnow())
    assert eng.portfolio.positions["CRASH.NS"].qty == 100
    assert not [e for e in eng.portfolio.decision_journal
                if str(e.get("action", "")).startswith("DANGER_")]


def test_once_per_ticker_per_day(tmp_path):
    eng = _engine(tmp_path, start_days_ago=1)
    now = datetime.utcnow()
    eng._danger_pass({"CRASH.NS": 72.9}, {"CRASH.NS": _crashing()}, now)
    eng._danger_pass({"CRASH.NS": 72.9}, {"CRASH.NS": _crashing()}, now)
    alerts = [e for e in eng.portfolio.decision_journal
              if e.get("action") == "DANGER_ALERT"]
    assert len(alerts) == 1


def test_start_date_persists_round_trip(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.danger_alerts_start = "2026-08-11"
    p.save()
    p2 = Portfolio.load_or_fresh(cfg)
    assert p2.danger_alerts_start == "2026-08-11"
