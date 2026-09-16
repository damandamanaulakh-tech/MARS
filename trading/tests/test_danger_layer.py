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
from trading import external_panels as XP
from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import PaperPosition, Portfolio

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Evidence lock
# ---------------------------------------------------------------------------

def test_verified_list_comes_from_the_committed_calibration_csvs():
    """The money path's tripwire list must come from evidence FILES, never a
    hardcoded list. Two sources since the 2026-08-13 order.
    """
    import csv

    def verified(path):
        with open(path) as f:
            return {r["trigger_id"] for r in csv.DictReader(f)
                    if r["proof_tag"] == "VERIFIED"
                    and r["side"] in ("FALL", "COLLAPSE")}

    ohlcv = verified(ROOT / "research" / "bt_calibration" /
                     "run_2026-08-09_strict15" / "trigger_calibration.csv")
    ext = verified(ROOT / "research" / "bt_calibration_external" /
                   "run_2026-08-13" / "external_trigger_calibration.csv")
    assert len(ohlcv) == 10
    # every OHLCV verdict is admitted; the external side only by explicit order
    assert ohlcv <= set(danger.VERIFIED)
    assert set(danger.PANEL_FED) <= ext
    assert set(danger.VERIFIED) == ohlcv | set(danger.PANEL_FED)
    assert len(danger.VERIFIED) == 12


def test_a_future_external_run_cannot_self_promote_into_the_money_path():
    """Scoring VERIFIED is not enough — the id must be on the ordered list."""
    assert danger._EXTERNAL_ALLOWED == ("T12W_0095", "T12W_0094")
    admitted = danger._read_verified(
        danger._EXTERNAL_CSV, "main", needs_panels=True,
        allow=danger._EXTERNAL_ALLOWED)
    unrestricted = danger._read_verified(
        danger._EXTERNAL_CSV, "main", needs_panels=True, allow=None)
    assert set(admitted) <= set(unrestricted)
    assert set(admitted) == set(danger.PANEL_FED)


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
                        lambda df, ext=None: list(danger.VERIFIED)[:2])
    prices = {"CRASH.NS": 72.9}
    eng._danger_pass(prices, {"CRASH.NS": _crashing()}, datetime.utcnow())
    pos = eng.portfolio.positions["CRASH.NS"]
    assert pos.qty == 50                            # half sold
    assert len(eng.portfolio.trades) == 1           # real ClosedTrade
    assert eng.portfolio.trades[-1].qty == 50
    acts = [e["action"] for e in eng.portfolio.decision_journal]
    assert "DANGER_EXIT_HALF" in acts


def test_auto_mode_tighten_on_single_fire(tmp_path, monkeypatch):
    """UPDATED 2026-09-16 to the new truth (owner order): the leash is floored
    at the owner's own stop width.

    This used to pin 120 x 0.975 = 117 — danger.TIGHTEN_PCT, an ASSUMED -2.5%
    that no measurement in this repo backs, overriding the -5% the owner
    decided. Since the 30-minute in-session pass (2026-09-10) that leash is
    checked ~13 times a day instead of ~2, and a 2.5% leash checked that often
    in a falling market sells almost anything. The tighten still works — the
    stop goes 95 -> 114 on a winner at 120 — it just cannot be narrower than
    the risk they accepted.
    """
    eng = _engine(tmp_path, start_days_ago=10)
    one = list(danger.VERIFIED)[:1]
    monkeypatch.setattr(danger, "evaluate_stock", lambda df, ext=None: one)
    prices = {"CRASH.NS": 120.0}
    eng._danger_pass(prices, {"CRASH.NS": _calm()}, datetime.utcnow())
    pos = eng.portfolio.positions["CRASH.NS"]
    leash = max(abs(danger.TIGHTEN_PCT), abs(eng.cfg.stop_loss_pct))
    assert pos.stop_price == round(120.0 * (1 - leash), 2) == 114.0
    assert pos.stop_price > 95.0, "it must still TIGHTEN, just not past -5%"
    assert pos.custom_levels is True                # survives boot retag
    assert pos.qty == 100


def test_tighten_never_loosens_an_already_tighter_stop(tmp_path, monkeypatch):
    eng = _engine(tmp_path, start_days_ago=10)
    monkeypatch.setattr(danger, "evaluate_stock",
                        lambda df, ext=None: list(danger.VERIFIED)[:1])
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


# ---------------------------------------------------------------------------
# Panel-fed tripwires (owner order 2026-08-13 "wire 0095 and 0094 both")
# ---------------------------------------------------------------------------

def _flat_frame(n: int = 200):
    idx = pd.bdate_range("2025-01-01", periods=n)
    c = np.full(n, 100.0)
    return pd.DataFrame({"Open": c * 0.999, "High": c * 1.005,
                         "Low": c * 0.995, "Close": c,
                         "Volume": np.full(n, 1e6)}, index=idx)


def _broken_support_frame(n: int = 200):
    """Closes below the prior 20 sessions' lowest low — a real breakdown."""
    d = _flat_frame(n)
    d.iloc[-1, d.columns.get_loc("Low")] = 93.0
    d.iloc[-1, d.columns.get_loc("Close")] = float(d["Low"][:-1].min()) * 0.98
    return d


def test_both_ordered_tripwires_are_admitted_with_their_evidence():
    assert set(danger.PANEL_FED) == {"T12W_0094", "T12W_0095"}
    for tid, expect in (("T12W_0095", 2.28), ("T12W_0094", 1.546)):
        v = danger.VERIFIED[tid]
        assert v["side"] == "COLLAPSE"
        assert abs(v["lift"] - expect) < 0.01
        assert v["needs_panels"] is True


def test_the_two_evidence_bars_stay_distinguishable():
    """The 10 OHLCV tripwires cleared strict +/-15%; the two panel-fed ones
    cleared main +/-10%. A future session must not mistake one for the other."""
    labels = {tid: v["labels"] for tid, v in danger.VERIFIED.items()}
    assert labels["T12W_0095"] == "main +/-10%"
    assert labels["T12W_0094"] == "main +/-10%"
    ohlcv = [t for t in danger.VERIFIED if t not in danger.PANEL_FED]
    assert ohlcv and all(labels[t] == "strict +/-15%" for t in ohlcv)


def test_index_overlay_fires_on_a_breakdown_under_a_weak_index():
    weak = XP.StockExternals(ticker="X.NS",
                             index_close=np.linspace(100, 80, 60))
    assert "T12W_0095" in danger.evaluate_stock(_broken_support_frame(), ext=weak)
    strong = XP.StockExternals(ticker="X.NS",
                               index_close=np.linspace(80, 100, 60))
    assert "T12W_0095" not in danger.evaluate_stock(_broken_support_frame(),
                                                    ext=strong)


def test_contagion_fires_when_peers_break_first_and_target_holds():
    lead = XP.StockExternals(ticker="X.NS", peers=10, peer_break_20d_low=4)
    assert "T12W_0094" in danger.evaluate_stock(_flat_frame(), ext=lead)
    quiet = XP.StockExternals(ticker="X.NS", peers=10, peer_break_20d_low=1)
    assert "T12W_0094" not in danger.evaluate_stock(_flat_frame(), ext=quiet)


def test_the_panel_fed_pair_cannot_alone_reach_exit_half():
    """0095 needs the target's support BROKEN, 0094 needs it INTACT — they are
    mutually exclusive, so the pair can only ever tighten a stop on its own.
    Documented because 2+ fires is what escalates to selling half."""
    ext = XP.StockExternals(ticker="X.NS", peers=10, peer_break_20d_low=9,
                            index_close=np.linspace(100, 80, 60))
    for df in (_flat_frame(), _broken_support_frame()):
        fired = [t for t in danger.evaluate_stock(df, ext=ext)
                 if t in danger.PANEL_FED]
        assert len(fired) <= 1, fired


def test_no_panels_means_abstain_not_a_guessed_fire():
    """Panels unavailable -> the layer degrades to the OHLCV tripwires."""
    fired = danger.evaluate_stock(_broken_support_frame(), ext=None)
    assert not any(t in danger.PANEL_FED for t in fired)


def test_externals_for_never_loads_the_heavy_feeds():
    """This runs inside engine.refresh; streaming the 3.5M-row delivery file
    there would be unacceptable."""
    got = danger.externals_for(["TCS.NS"])
    if got is None:
        pytest.skip("panels unavailable in this checkout")
    x = got["TCS.NS"]
    assert x.delivery_ratio is None and x.fut_oi is None
    assert x.index_close is not None


def test_engine_reaches_the_panels_only_through_this_module():
    from pathlib import Path
    root = Path(danger.__file__).resolve().parent
    for name in ("engine.py", "risk.py", "orders.py"):
        assert "external_panels" not in (root / name).read_text(), (
            f"{name} must reach the panels only via trading.danger")
