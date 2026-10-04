"""Owner order 2026-10-02 ("go for next"): audit items S-4 and S-9.

S-4  Rotation ran BEFORE the entry gates, so it could sell a near-target
     position to fund a pick that a later gate (macro-event halt, loss halt,
     liquidity, candle, ...) then refused.
S-9  The book's equity — drawdown governor, kill switch, equity curve, sizing
     — marked a held name with no live quote at its ENTRY price (refresh) or
     at yesterday's scan close (scan), not at the freshest price available.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import PaperPosition, Portfolio


def _frame(n=300, start=100.0, drift=0.15, seed=1, end=None):
    rng = np.random.default_rng(seed)
    close = start * np.cumprod(1 + drift / n + rng.normal(0, 0.004, n))
    idx = pd.bdate_range(end=end or pd.Timestamp.today().normalize(), periods=n)
    return pd.DataFrame({"Open": close, "High": close * 1.01,
                         "Low": close * 0.99, "Close": close,
                         "Volume": rng.integers(5e6, 1e7, n).astype(float)},
                        index=idx)


def _near_target(ticker="OLD.NS"):
    return PaperPosition(
        ticker=ticker, qty=100, entry_price=100.0,
        entry_date=datetime(2026, 8, 1), sector="IT", peak_price=119.0,
        entry_score=70.0, reasoning="fixture", mode="nifty200_momentum",
        stop_price=95.0, target_price=120.0)           # 95% of the way there


def _scan_engine(tmp_path, monkeypatch, event=None):
    """The engine with one strong pick, one near-target holding and almost
    no cash — the exact case rotation exists for."""
    cfg = Config(state_dir=str(tmp_path), mode="nifty200_momentum")
    cfg.apply_mode_preset()
    cfg.custom_universe = ("UPP.NS",)
    cfg.candle_confirm_enabled = False
    p = Portfolio.fresh(cfg)
    p.cash = 1_000.0
    p.positions["OLD.NS"] = _near_target()
    e = TradingEngine(cfg, p)
    frames = {"UPP.NS": _frame(drift=0.60, seed=2),
              cfg.benchmark: _frame(drift=0.05, seed=4),
              "^INDIAVIX": _frame(start=12.0, drift=0.0, seed=5)}

    def fake_fetch_daily(tickers, period="6mo"):
        if isinstance(tickers, str):
            tickers = [tickers]
        return {t: frames[t].copy() for t in tickers if t in frames}

    from trading.regime import RegimeReading

    def fake_regime(*a, **k):
        return RegimeReading(
            timestamp=datetime.utcnow(), nifty_close=100.0, nifty_200dma=90.0,
            pct_below_52w_high=2.0, vix=12.0, fii_5d_net=100.0,
            regime="bull_extension", everest_active=True, ocean_active=False,
            sizing_multiplier_everest=1.0, sizing_multiplier_ocean=0.0,
            reasoning="test fixture")
    monkeypatch.setattr("trading.regime.fetch_regime", fake_regime)
    monkeypatch.setattr("trading.calendars.is_system_event_within",
                        lambda *a, **k: event)
    monkeypatch.setattr("trading.engine.fetch_daily", fake_fetch_daily)
    quotes = {t: float(f["Close"].iloc[-1]) for t, f in frames.items()}
    quotes["OLD.NS"] = 119.0
    monkeypatch.setattr("trading.engine.routed_fetch_quotes",
                        lambda tickers, upstox_client=None:
                        {t: quotes[t] for t in tickers if t in quotes})
    monkeypatch.setattr(e, "_trading_allowed", lambda now=None: True)
    monkeypatch.setattr(e, "_breadth_gate_blocks", lambda *a, **k: False)
    return e


class _Event:
    event_type = "budget"
    event_date = "2026-10-05"


# --------------------------------------------------------------------- S-4

def test_a_macro_halt_means_nothing_is_rotated_out(tmp_path, monkeypatch):
    """Every pick is refused by the Budget halt, so no position may be sold
    to fund one. The old order sold OLD.NS first and refused the pick after."""
    e = _scan_engine(tmp_path, monkeypatch, event=_Event())
    e.scan()
    assert "OLD.NS" in e.portfolio.positions, "sold to fund a refused pick"
    assert not [t for t in e.portfolio.trades if t.reason == "ROTATION_EXIT"]
    reasons = {s["ticker"]: s["reason"] for s in e.portfolio.last_scan_skipped}
    assert reasons.get("UPP.NS", "").startswith("halt_pre_budget")


def test_a_pick_that_clears_every_gate_still_rotates(tmp_path, monkeypatch):
    """The same book with no halt: rotation still does its job.

    Pins the CASH rule: on this Rs 11,000 fixture book the 40% momentum share
    (owner order 2026-10-04) binds first, and the share rule has its own
    tests in test_category_budgets_2026_10_04.py."""
    e = _scan_engine(tmp_path, monkeypatch, event=None)
    e.cfg.category_budgets_enabled = False
    picks_seen = []
    real = e._rotation_exits

    def spy(picks, now, **k):
        picks_seen.append([p["ticker"] for p in picks])
        return real(picks, now, **k)
    monkeypatch.setattr(e, "_rotation_exits", spy)
    e.scan()
    assert picks_seen == [["UPP.NS"]]
    assert [t.ticker for t in e.portfolio.trades
            if t.reason == "ROTATION_EXIT"] == ["OLD.NS"]


# --------------------------------------------------------------------- S-9

def _held_engine(tmp_path, monkeypatch, *, b_entry=datetime(2026, 8, 1)):
    cfg = Config(state_dir=str(tmp_path), mode="nifty200_momentum")
    cfg.apply_mode_preset()
    p = Portfolio.fresh(cfg)
    p.cash = 10_000.0
    for t, when in (("AAA.NS", datetime(2026, 8, 1)), ("BBB.NS", b_entry)):
        p.positions[t] = PaperPosition(
            ticker=t, qty=10, entry_price=100.0, entry_date=when, sector="IT",
            peak_price=100.0, entry_score=70.0, reasoning="fixture",
            mode="nifty200_momentum", stop_price=50.0, target_price=500.0)
    e = TradingEngine(cfg, p)
    yday = pd.Timestamp.today().normalize() - pd.offsets.BDay(1)
    frames = {"AAA.NS": _frame(start=100.0, drift=0.0, seed=1, end=yday),
              "BBB.NS": _frame(start=150.0, drift=0.0, seed=3, end=yday),
              cfg.benchmark: _frame(seed=4)}

    def fake_fetch_daily(tickers, period="6mo"):
        if isinstance(tickers, str):
            tickers = [tickers]
        return {t: frames[t].copy() for t in tickers if t in frames}
    monkeypatch.setattr("trading.engine.fetch_daily", fake_fetch_daily)
    # AAA has a live quote, BBB has none
    monkeypatch.setattr("trading.engine.routed_fetch_quotes",
                        lambda tickers, upstox_client=None:
                        {t: 105.0 for t in tickers if t == "AAA.NS"})
    monkeypatch.setattr(e, "_trading_allowed", lambda now=None: True)
    monkeypatch.setattr(e, "_danger_pass", lambda *a, **k: None)
    return e, float(frames["BBB.NS"]["Close"].iloc[-1])


def test_refresh_marks_an_unquoted_name_at_its_stored_close(tmp_path, monkeypatch):
    e, b_close = _held_engine(tmp_path, monkeypatch)
    e.refresh()
    _, eq = e.portfolio.equity_history[-1]
    assert abs(b_close - 100.0) > 10, "fixture must separate close from entry"
    assert eq == pytest.approx(10_000.0 + 10 * 105.0 + 10 * b_close)
    # and the drawdown governor saw the same number
    assert e.drawdown_gov.last_equity == pytest.approx(eq)
    # the unquoted name is still never exited on that mark
    assert "BBB.NS" in e.portfolio.positions


def test_a_close_older_than_the_entry_does_not_replace_the_fill(tmp_path,
                                                              monkeypatch):
    """BBB was bought TODAY; yesterday's stored close is older than the fill,
    so the entry price stays the mark."""
    e, _ = _held_engine(tmp_path, monkeypatch, b_entry=datetime.utcnow())
    marks = e._freshest_marks({"AAA.NS": 105.0},
                              {"BBB.NS": _frame(start=150.0, drift=0.0, seed=3,
                                                end=pd.Timestamp.today().normalize()
                                                - pd.offsets.BDay(1))},
                              fallback={"BBB.NS": 150.0})
    assert marks == {"AAA.NS": 105.0}


def test_live_beats_stored_beats_fallback(tmp_path, monkeypatch):
    e, b_close = _held_engine(tmp_path, monkeypatch)
    hist = {"BBB.NS": _frame(start=150.0, drift=0.0, seed=3)}
    m = e._freshest_marks({}, hist, fallback={"AAA.NS": 99.0, "BBB.NS": 1.0})
    assert m["AAA.NS"] == 99.0
    assert m["BBB.NS"] == pytest.approx(float(hist["BBB.NS"]["Close"].iloc[-1]))
    m = e._freshest_marks({"BBB.NS": 160.0}, hist)
    assert m["BBB.NS"] == 160.0
    # names not held are never marked
    assert "ZZZ.NS" not in e._freshest_marks({"ZZZ.NS": 5.0}, {})


def test_the_scan_marks_held_names_at_the_live_quote(tmp_path, monkeypatch):
    """The scan fetched live quotes for the held book at its very top and then
    valued the book at yesterday's closes. It records the live value now."""
    e = _scan_engine(tmp_path, monkeypatch, event=_Event())
    e.scan()
    _, eq = e.portfolio.equity_history[-1]
    assert eq == pytest.approx(1_000.0 + 100 * 119.0)
