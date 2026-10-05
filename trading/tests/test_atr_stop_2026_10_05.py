"""Owner order 2026-10-05 ("Go for A"): the ATR-scaled stop.

A new MOMENTUM buy's stop is the wider of the category stop (-5%) and
2 x its own 14-day ATR, capped at -15%. Measured first
(research/stop_cooldown_study/summary.md: train +2.82 vs +0.88, holdout
+21.14 vs +19.33), then wired. Other categories keep their fixed stops;
held positions keep the stop they were opened with.

These tests pin: the rule itself, the engine setting it on a real BUY
(journal included), the boot retag not overwriting it, the heat gate
counting each held name at its own stop, the danger leash honouring the
entry width, the backtest using the same rule, and the switch.
"""
from __future__ import annotations

import ast
import inspect
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from trading import targets as T
from trading.config import Config
from trading.paper import PaperPosition, Portfolio
from trading.risk import RiskState, can_open

MOM = "nifty200_momentum"


def _bars(atr_pct: float, n: int = 80, price: float = 100.0) -> pd.DataFrame:
    """Flat closes with a daily high-low range of `atr_pct` of price, so
    ATR14 / price == atr_pct exactly."""
    idx = pd.bdate_range(end="2026-10-01", periods=n)
    c = np.full(n, price)
    half = price * atr_pct / 2.0
    return pd.DataFrame({"Open": c, "High": c + half, "Low": c - half,
                         "Close": c, "Volume": np.full(n, 1e6)}, index=idx)


# ------------------------------------------------------------- the rule

def test_the_measured_constants_are_the_studys():
    assert T.ATR_STOP_MODES == (MOM,)
    assert T.ATR_STOP_K == 2.0 and T.ATR_STOP_CAP == 0.15
    assert "stop_cooldown_study" in T.ATR_STOP_SOURCE
    assert Config().atr_stop_enabled is True


def test_a_quiet_name_stays_at_the_floor():
    pct, d = T.stop_pct_for(MOM, 100.0, _bars(0.01), -0.05)
    assert pct == pytest.approx(-0.05) and d["source"] == "atr_floor"
    assert d["raw_pct"] == pytest.approx(2.0, abs=0.01)


def test_a_jumpy_name_widens_to_two_atr():
    pct, d = T.stop_pct_for(MOM, 100.0, _bars(0.04), -0.05)
    assert pct == pytest.approx(-0.08) and d["source"] == "atr"
    assert d["k"] == 2.0 and d["atr"] == pytest.approx(4.0)


def test_the_cap_holds_at_fifteen_percent():
    pct, d = T.stop_pct_for(MOM, 100.0, _bars(0.10), -0.05)
    assert pct == pytest.approx(-0.15) and d["source"] == "atr_cap"


def test_other_categories_keep_their_fixed_stop():
    for mode, floor in (("swing", -0.05), ("positional", -0.10),
                        ("short", -0.03), ("intraday", -0.015)):
        pct, d = T.stop_pct_for(mode, 100.0, _bars(0.10), floor)
        assert pct == pytest.approx(floor) and d["reason"] == "mode_not_atr", mode


def test_no_history_and_the_switch_off_mean_the_fixed_stop():
    pct, d = T.stop_pct_for(MOM, 100.0, None, -0.05)
    assert pct == -0.05 and d["reason"] == "no_atr"
    pct, d = T.stop_pct_for(MOM, 100.0, _bars(0.10), -0.05, enabled=False)
    assert pct == -0.05 and d["reason"] == "disabled"
    price, _ = T.stop_price_for(MOM, 100.0, _bars(0.10), -0.05, enabled=False)
    assert price == 95.0


def test_the_floor_can_never_be_loosened_by_sign():
    """A positive floor typed by mistake still reads as a stop below entry."""
    pct, _ = T.stop_pct_for(MOM, 100.0, _bars(0.01), 0.05)
    assert pct == pytest.approx(-0.05)


# ----------------------------------------------------------- the engine

def _engine(tmp_path, mode=MOM):
    from trading.engine import TradingEngine
    cfg = Config(state_dir=str(tmp_path), mode=mode)
    cfg.apply_mode_preset()
    p = Portfolio.fresh(cfg)
    return TradingEngine(cfg, p)


def _pick(ticker, df):
    return {"ticker": ticker, "score": 70.0, "reasoning": "fixture",
            "history": df, "components": {}}


def test_a_momentum_buy_gets_the_widened_stop_and_the_journal_says_so(tmp_path):
    e = _engine(tmp_path)
    e._open(_pick("JUMPY.NS", _bars(0.04)), 100, 100.0,
            datetime(2026, 10, 6, 4, 0))
    pos = e.portfolio.positions["JUMPY.NS"]
    assert pos.stop_price == pytest.approx(92.0)
    buy = [x for x in e.portfolio.decision_journal if x.get("action") == "BUY"][-1]
    assert buy["stop"]["price"] == pytest.approx(92.0)
    assert buy["stop"]["source"] == "atr" and buy["stop"]["k"] == 2.0
    assert buy["stop"]["atr"] == pytest.approx(4.0)


def test_a_quiet_momentum_buy_keeps_minus_five(tmp_path):
    e = _engine(tmp_path)
    e._open(_pick("CALM.NS", _bars(0.01)), 100, 100.0,
            datetime(2026, 10, 6, 4, 0))
    assert e.portfolio.positions["CALM.NS"].stop_price == pytest.approx(95.0)
    buy = [x for x in e.portfolio.decision_journal if x.get("action") == "BUY"][-1]
    assert buy["stop"]["source"] == "atr_floor"


def test_a_swing_buy_is_untouched(tmp_path):
    e = _engine(tmp_path, mode="swing")
    e._open(_pick("SW.NS", _bars(0.10)), 100, 100.0,
            datetime(2026, 10, 6, 4, 0))
    assert e.portfolio.positions["SW.NS"].stop_price == pytest.approx(95.0)
    buy = [x for x in e.portfolio.decision_journal if x.get("action") == "BUY"][-1]
    assert buy["stop"]["reason"] == "mode_not_atr"


def test_the_switch_restores_the_fixed_stop(tmp_path):
    e = _engine(tmp_path)
    e.cfg.atr_stop_enabled = False
    e._open(_pick("JUMPY.NS", _bars(0.04)), 100, 100.0,
            datetime(2026, 10, 6, 4, 0))
    assert e.portfolio.positions["JUMPY.NS"].stop_price == pytest.approx(95.0)


def test_a_broken_atr_falls_back_to_the_fixed_stop(tmp_path, monkeypatch):
    e = _engine(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("atr broke")
    monkeypatch.setattr(T, "stop_price_for", boom)
    e._open(_pick("X.NS", _bars(0.04)), 100, 100.0, datetime(2026, 10, 6, 4, 0))
    assert e.portfolio.positions["X.NS"].stop_price == pytest.approx(95.0)
    buy = [x for x in e.portfolio.decision_journal if x.get("action") == "BUY"][-1]
    assert buy["stop"]["reason"] == "atr_failed"


# ------------------------------------------------------------ boot retag

def _held(ticker, stop, mode=MOM, entry_date=datetime(2026, 9, 1)):
    return PaperPosition(
        ticker=ticker, qty=100, entry_price=100.0, entry_date=entry_date,
        sector="IT", peak_price=100.0, entry_score=70.0, reasoning="fixture",
        mode=mode, stop_price=stop, target_price=120.0)


def test_the_boot_retag_keeps_a_widened_stop(tmp_path):
    """The retag runs on every boot; it used to re-snapshot the flat -5%."""
    from trading.exits import retag_positions_per_mode
    cfg = Config(state_dir=str(tmp_path), mode=MOM)
    cfg.apply_mode_preset()
    p = Portfolio.fresh(cfg)
    p.positions["W.NS"] = _held("W.NS", 88.0)
    changed = retag_positions_per_mode(p, now=datetime(2026, 10, 6))
    assert changed == 0
    assert p.positions["W.NS"].stop_price == 88.0


def test_the_boot_retag_still_resets_a_blank_tag_and_a_missing_stop(tmp_path):
    from trading.exits import retag_positions_per_mode
    cfg = Config(state_dir=str(tmp_path), mode=MOM)
    cfg.apply_mode_preset()
    p = Portfolio.fresh(cfg)
    p.positions["B.NS"] = _held("B.NS", 88.0, mode="")
    p.positions["Z.NS"] = _held("Z.NS", 0.0)
    retag_positions_per_mode(p, now=datetime(2026, 10, 6))
    assert p.positions["B.NS"].stop_price == pytest.approx(95.0)
    assert p.positions["Z.NS"].stop_price == pytest.approx(95.0)


# -------------------------------------------------------------- heat gate

def _heat_gate(stop):
    cfg = Config(state_dir="/tmp/sb_atr_heat", mode=MOM)
    cfg.apply_mode_preset()
    cfg.cash_reserve_pct = 0.0
    cfg.category_budgets_enabled = False
    cfg.max_portfolio_heat_pct = 0.06
    cfg.min_position_value = 0
    equity = 10_00_000.0
    held = {"H.NS": PaperPosition(
        ticker="H.NS", qty=500, entry_price=1000.0,
        entry_date=datetime(2026, 9, 1), sector="IT", peak_price=1000.0,
        entry_score=70.0, reasoning="fixture", mode=MOM,
        stop_price=stop, target_price=1200.0)}
    risk = RiskState(cfg=cfg, starting_capital=equity)
    return can_open(risk, 5_00_000.0, equity, held, "NEW.NS", 100.0, cfg,
                    notional_override=1_00_000.0)


def test_the_heat_gate_counts_a_held_name_at_its_own_stop():
    """Rs 5L held with a 15% stop = Rs 75k at risk = 7.5% of a Rs 10L book,
    already past a 6% cap — the flat 5% reading said 2.5%."""
    ok, why, _ = _heat_gate(stop=850.0)
    assert not ok and why == "portfolio_heat_cap"
    ok, why, _ = _heat_gate(stop=950.0)
    assert ok, why


# ---------------------------------------------------------- danger leash

def _danger_engine(tmp_path, monkeypatch, pos):
    from trading import danger
    from trading.engine import TradingEngine
    cfg = Config(state_dir=str(tmp_path), mode=MOM)
    cfg.apply_mode_preset()
    p = Portfolio.fresh(cfg)
    p.positions = {pos.ticker: pos}
    p.danger_alerts_start = "2026-01-01"
    e = TradingEngine(cfg, p)
    tid = next(iter(danger.VERIFIED))
    monkeypatch.setattr(danger, "evaluate_stock", lambda df, ext=None: [tid])
    monkeypatch.setattr(danger, "externals_for", lambda held: {})
    return e


def test_the_danger_leash_is_floored_at_the_entry_width(tmp_path, monkeypatch):
    """A momentum buy opened with a 12% ATR stop: one danger fire at 120
    tightens to 120 x 0.88 = 105.6 (not 114 = the -5% leash); at 95 the
    leash would sit at 83.6, below the 88 stop, so nothing moves."""
    pos = _held("W.NS", 88.0)
    e = _danger_engine(tmp_path, monkeypatch, pos)
    e._danger_pass({"W.NS": 120.0}, {"W.NS": _bars(0.06)},
                   datetime(2026, 10, 6, 6, 0))
    assert e.portfolio.positions["W.NS"].stop_price == pytest.approx(105.6)
    line = [x for x in e.portfolio.decision_journal
            if str(x.get("action", "")).startswith("DANGER_")][-1]
    assert line["leash_pct"] == pytest.approx(12.0)

    pos2 = _held("Y.NS", 88.0)
    e2 = _danger_engine(tmp_path, monkeypatch, pos2)
    e2._danger_pass({"Y.NS": 95.0}, {"Y.NS": _bars(0.06)},
                    datetime(2026, 10, 6, 6, 0))
    assert e2.portfolio.positions["Y.NS"].stop_price == 88.0
    line = [x for x in e2.portfolio.decision_journal
            if str(x.get("action", "")).startswith("DANGER_")][-1]
    assert line["action"] == "DANGER_ALERT"


# -------------------------------------------------------------- backtest

def test_the_backtest_sets_the_same_stop(monkeypatch):
    import trading.backtest as bt
    cfg = Config(state_dir="/tmp/sb_atr_bt", mode=MOM)
    cfg.apply_mode_preset()
    cfg.candle_confirm_enabled = False
    cfg.nifty_min_adx = 0
    p = Portfolio.fresh(cfg)
    p.cash = 5_00_00_000.0
    risk = RiskState(cfg=cfg, starting_capital=p.cash)
    df = _bars(0.04, n=300)
    close = np.linspace(60.0, 100.0, 300)          # rising: passes abs-mom
    df["Close"] = close
    df["Open"] = close
    df["High"] = close + 2.0
    df["Low"] = close - 2.0
    next_date = df.index[-1] + pd.offsets.BDay(1)
    full = pd.concat([df, pd.DataFrame(
        {"Open": [100.0], "High": [102.0], "Low": [98.0], "Close": [100.0],
         "Volume": [1e6]}, index=[next_date])])
    monkeypatch.setattr(bt, "_score_for_mode", lambda *a, **k: (80.0, {}))
    monkeypatch.setattr(bt, "timing_ok", lambda h, c: (True, "ok"))
    monkeypatch.setattr(bt, "slippage_price", lambda px, side, c: px)
    monkeypatch.setattr(bt, "can_open", lambda *a, **k: (True, "ok", 100))
    monkeypatch.setattr("trading.calendars.is_system_event_within",
                        lambda *a, **k: None)
    bt._try_open_positions(cfg, p, risk, {"J.NS": df}, None,
                           {"J.NS": full}, next_date)
    pos = p.positions["J.NS"]
    want = T.stop_price_for(MOM, 100.0, df, -0.05)[0]
    assert want < 95.0, "fixture must be jumpy enough to widen"
    assert pos.stop_price == pytest.approx(want)


# ---------------------------------------------------------------- purity

def test_targets_stays_pure():
    """No network, no config mutation, no engine/risk/orders import — the
    same contract as internals.py."""
    src = inspect.getsource(T)
    tree = ast.parse(src)
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            mods.add(n.module or "")
    for bad in ("httpx", "requests", "yfinance", "trading.engine",
                "trading.risk", "trading.orders", "trading.danger"):
        assert bad not in mods and not any(m.endswith(bad.split(".")[-1])
                                           for m in mods if m), bad
