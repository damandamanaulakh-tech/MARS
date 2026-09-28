"""The three-formula filter inside the real engine.scan() (owner order
2026-09-11). Same seams-only fixture shape as test_engine_direct.py.

The two claims that matter for money: with the switch OFF (the default)
the engine buys exactly what it bought before this feature existed, and
with it ON every removed name is named on the page AND in the journal —
a gate that silently shrinks the buy list is the lying-screen class.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import Portfolio


def _frame(n=300, start=100.0, drift=0.15, seed=1, vol_scale=5e6):
    rng = np.random.default_rng(seed)
    steps = 1 + drift / n + rng.normal(0, 0.004, n)
    close = start * np.cumprod(steps)
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    return pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * 0.99,
        "Close": close,
        "Volume": rng.integers(vol_scale, vol_scale * 2, n).astype(float),
    }, index=idx)


def _engine(tmp_path, monkeypatch, gate: bool):
    cfg = Config(state_dir=str(tmp_path), mode="nifty200_momentum")
    cfg.apply_mode_preset()
    cfg.custom_universe = ("UPP.NS",)
    cfg.multi_formula_gate = gate
    # The synthetic series is deliberately smooth, so it never prints a
    # bullish reversal structure and the candle gate alone would hold every
    # entry (reason "candle_wait_no_pattern"). Off here so the thing under
    # test is the three-formula filter and nothing else.
    cfg.candle_confirm_enabled = False
    e = TradingEngine(cfg, Portfolio.fresh(cfg))
    frames = {
        "UPP.NS": _frame(drift=0.60, seed=2),
        cfg.benchmark: _frame(drift=0.05, seed=4),
        "^INDIAVIX": _frame(n=300, start=12.0, drift=0.0, seed=5),
    }

    def fake_fetch_daily(tickers, period="6mo"):
        if isinstance(tickers, str):
            tickers = [tickers]
        return {t: frames[t].copy() for t in tickers if t in frames}

    # A bullish regime, deterministically: without it the live C7 fail-safe
    # sees a dead feed (no network in tests) and brakes every entry, so
    # "the scan still buys" could never be proved.
    from trading.regime import RegimeReading
    from datetime import datetime as _dt

    def fake_regime(*a, **k):
        return RegimeReading(
            timestamp=_dt.utcnow(), nifty_close=100.0, nifty_200dma=90.0,
            pct_below_52w_high=2.0, vix=12.0, fii_5d_net=100.0,
            regime="bull_extension", everest_active=True, ocean_active=False,
            sizing_multiplier_everest=1.0, sizing_multiplier_ocean=0.0,
            reasoning="test fixture")
    monkeypatch.setattr("trading.regime.fetch_regime", fake_regime)
    # The v4 macro-event halt reads the REAL calendar (RBI MPC / Budget /
    # election within 3 days blocks every new entry). On 2026-09-28 CI ran
    # inside the 2026-10-01 RBI window and this file's "it still buys"
    # claims failed with reason halt_pre_rbi_mpc_2026-10-01 — a calendar
    # fact, not a code fault. The halt has its own tests in
    # test_calendars.py; here it is off so the claim under test is the
    # three-formula switch and nothing date-dependent.
    monkeypatch.setattr("trading.calendars.is_system_event_within",
                        lambda *a, **k: None)
    monkeypatch.setattr("trading.engine.fetch_daily", fake_fetch_daily)
    monkeypatch.setattr(
        "trading.engine.routed_fetch_quotes",
        lambda tickers, upstox_client=None: {
            t: float(frames[t]["Close"].iloc[-1]) for t in tickers if t in frames})
    return e


def test_every_scanned_row_carries_the_three_verdicts(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, gate=False)
    e.scan()
    rows = (e.portfolio.scan_matrix or {}).get("rows") or {}
    assert rows, "scan matrix empty"
    for t, r in rows.items():
        mfv = r.get("multi")
        assert mfv and "rows" in mfv, t
        assert set(mfv["rows"]) == {"am07", "ash08", "ashstocks"}, t
    # and the picks the page renders carry it too
    assert all("multi" in pk for pk in e.portfolio.last_scan_picks)


def test_the_verdicts_are_display_only_while_the_switch_is_off(tmp_path, monkeypatch):
    """UPP.NS is a strong up-trender: it clears our floor. With the filter
    off it must open regardless of what the outside formulas think."""
    e = _engine(tmp_path, monkeypatch, gate=False)
    e.scan()
    assert e.portfolio.last_scan_opened, "the strong name should have opened"
    assert not [x for x in e.portfolio.decision_journal
                if x.get("action") == "MULTI_FORMULA_BLOCKED"]


def test_the_switch_on_blocks_and_says_so_in_both_places(tmp_path, monkeypatch):
    """Force a refusal by putting the outside floors out of reach, then
    check the name is on the skipped list AND in the journal."""
    e = _engine(tmp_path, monkeypatch, gate=True)
    monkeypatch.setattr("trading.multi_formula.ASH08_FLOOR", 999.0)
    e.scan()
    assert e.portfolio.last_scan_opened == []
    skipped = {s["ticker"]: s["reason"] for s in e.portfolio.last_scan_skipped}
    assert "UPP.NS" in skipped and "multi-formula gate" in skipped["UPP.NS"]
    blocked = [x for x in e.portfolio.decision_journal
               if x.get("action") == "MULTI_FORMULA_BLOCKED"]
    assert len(blocked) == 1
    assert blocked[0]["ticker"] == "UPP.NS"
    assert "three-formula filter is ON" in blocked[0]["why"]


def test_a_name_the_outside_formulas_cannot_judge_is_not_blocked(tmp_path, monkeypatch):
    """Fail-open: DATA_NEEDED is not evidence against a stock."""
    e = _engine(tmp_path, monkeypatch, gate=True)
    monkeypatch.setattr("trading.multi_formula.ash08_score", lambda c: None)
    e.scan()
    assert not [x for x in e.portfolio.decision_journal
                if x.get("action") == "MULTI_FORMULA_BLOCKED"]


def test_a_broken_view_costs_the_columns_never_the_scan(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, gate=False)

    def boom(*a, **k):
        raise RuntimeError("formula exploded")
    monkeypatch.setattr("trading.multi_formula.evaluate", boom)
    e.scan()                                    # must not raise
    assert (e.portfolio.scan_matrix or {}).get("rows")
    assert e.portfolio.last_scan_opened, "the scan must still buy"
