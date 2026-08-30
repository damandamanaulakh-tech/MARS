"""Ocean crash guard (owner approval 2026-08-26: "Approve as specced" —
max 8 slots, four rails; parallel with Everest). Ticket resized to
Rs 1,68,750 on the owner's order of 2026-08-30.

Pinned: each rail blocks and passes on the exact conditions the owner
approved, with FAIL-SAFE unknowns (a dip-buyer proves safety, never
assumes it); survival reads the fundamentals STORE (the old yfinance
path could never pass on Render); an end-to-end scan opens an
ocean-tagged position when everything passes, is vetoed by a danger
fire, and is EXEMPT from the breadth gate (a dip engine exists for
fallen markets — gating it on breadth would mean it can never buy).
"""
from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from trading.config import Config
from trading.engine import TradingEngine
from trading.engines import ocean
from trading.paper import Portfolio


def _series(vals):
    return pd.Series(vals, index=pd.bdate_range(end="2026-08-25",
                                                periods=len(vals)))


def _fresh_damage(cluster=False):
    return {"asof": date.today().isoformat(), "value": 0.1, "sample": 12,
            "static_cluster": cluster, "q90_cluster": cluster}


def test_rail1_vix_knife_blocks_and_calm_passes():
    calm = _series([12.0] * 200)
    ok, why = ocean.crash_guard_market(calm, _fresh_damage())
    assert ok, why
    spiking = _series(list(np.linspace(12, 20, 190)) + list(np.linspace(21, 45, 10)))
    ok, why = ocean.crash_guard_market(spiking, _fresh_damage())
    assert not ok and "vix_knife" in why
    ok, why = ocean.crash_guard_market(None, _fresh_damage())
    assert not ok and why == "vix_unknown"
    ok, why = ocean.crash_guard_market(_series([12.0] * 50), _fresh_damage())
    assert not ok and why == "vix_unknown"   # too short to judge


def test_rail1_vix_crush_reopens_after_the_spike():
    """The one measured dip-entry rule: spike then crush to <=75% of peak."""
    crushed = _series(list(np.linspace(12, 15, 140)) + [45.0] * 10
                      + list(np.linspace(40, 20, 50)))
    # last=20 <= 75% of the 120d peak 45 -> vix_crush_ok True; and last(20)
    # sits below the window q90 anyway -> rail 1 passes either way
    ok, why = ocean.crash_guard_market(crushed, _fresh_damage())
    assert ok, why


def test_rail2_damage_store_gates():
    calm = _series([12.0] * 200)
    ok, why = ocean.crash_guard_market(calm, _fresh_damage(cluster=True))
    assert not ok and "damage_cluster" in why
    ok, why = ocean.crash_guard_market(calm, {})
    assert not ok and "damage" in why        # missing = blocked, never assumed
    ok, why = ocean.crash_guard_market(
        calm, {"asof": "2026-01-01", "static_cluster": False})
    assert not ok                            # stale = blocked


def _reclaim_df(fresh_low=False, reclaim=True):
    n = 60
    close = np.linspace(200, 120, n)
    low = close - 2
    high = close + 2
    if not fresh_low:
        low[-1] = low[-10]                   # today's low above the 20d min
        close[-1] = high[-2] + (1 if reclaim else -1)
    return pd.DataFrame({"Open": close, "High": high, "Low": low,
                         "Close": close, "Volume": np.full(n, 1e6)},
                        index=pd.bdate_range(end="2026-08-25", periods=n))


def test_rail3_reclaim_and_rail4_danger_veto():
    ok, why = ocean.crash_guard_stock(_reclaim_df(), [])
    assert ok, why
    ok, why = ocean.crash_guard_stock(_reclaim_df(fresh_low=True), [])
    assert not ok and why == "fresh_20d_low"
    ok, why = ocean.crash_guard_stock(_reclaim_df(reclaim=False), [])
    assert not ok and why == "no_reclaim_above_prior_high"
    ok, why = ocean.crash_guard_stock(_reclaim_df(), ["T12W_0089"])
    assert not ok and "danger_T12W_0089" in why


def test_survival_reads_the_store_not_yahoo(monkeypatch):
    from trading import data_refresh as dr
    ocean._fund_cache.clear()
    old = dr._fund_reader
    dr.set_fundamentals_reader(lambda: [
        {"symbol": "ZOCEAN", "field": "annualTotalDebt",
         "as_of": "2026-03-31", "value": 100.0},
        {"symbol": "ZOCEAN", "field": "annualStockholdersEquity",
         "as_of": "2026-03-31", "value": 1000.0},
        {"symbol": "ZOCEAN", "field": "annualFreeCashFlow",
         "as_of": "2026-03-31", "value": 50.0},
        {"symbol": "ZDEBT", "field": "annualTotalDebt",
         "as_of": "2026-03-31", "value": 900.0},
        {"symbol": "ZDEBT", "field": "annualStockholdersEquity",
         "as_of": "2026-03-31", "value": 1000.0},
        {"symbol": "ZDEBT", "field": "annualFreeCashFlow",
         "as_of": "2026-03-31", "value": 50.0},
    ])
    try:
        ok, reasons = ocean.survival_check("ZOCEAN.NS")
        assert ok, reasons                     # D/E 0.1, FCF positive
        ok, reasons = ocean.survival_check("ZDEBT.NS")
        assert not ok and "D/E" in reasons[0]  # 0.9 >= 0.5
        ok, reasons = ocean.survival_check("NOSUCH.NS")
        assert not ok                          # missing = fail, never assume
    finally:
        dr._fund_reader = old
        ocean._fund_cache.clear()


# ---------------------------------------------------------------- end to end

def _crash_frame(n=320):
    """Deep crash then a 2-day turn: ~40% under the 200DMA, weekly RSI
    low, climax volume 5 days ago, last bar reclaims the prior high
    without a fresh 20-day low."""
    steps = np.concatenate([
        np.full(200, 1.0006),                # drift up: builds the 200DMA
        np.full(112, 0.988),                 # the waterfall
        [1.02, 1.03, 1.01, 1.0, 1.04, 1.05, 1.06, 1.05],   # the turn
    ])[:n]
    close = 500 * np.cumprod(steps)
    high = close * 1.012
    low = close * 0.988
    vol = np.full(n, 1e6)
    vol[-5] = 6e6                            # climax day inside last 5
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    return pd.DataFrame({"Open": close, "High": high, "Low": low,
                         "Close": close, "Volume": vol}, index=idx)


@pytest.fixture()
def eng(tmp_path, monkeypatch):
    from trading import data_refresh as dr
    cfg = Config(state_dir=str(tmp_path), mode="nifty200_momentum")
    cfg.apply_mode_preset()
    names = tuple(f"N{i}.NS" for i in range(11)) + ("CRASH.NS",)
    cfg.custom_universe = names
    e = TradingEngine(cfg, Portfolio.fresh(cfg))
    rng = np.random.default_rng(3)
    frames = {}
    for i, t in enumerate(names[:-1]):
        stepsf = 1 + 0.4 / 320 + rng.normal(0, 0.004, 320)
        c = 100 * np.cumprod(stepsf)
        idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=320)
        frames[t] = pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99,
                                  "Close": c, "Volume": np.full(320, 5e6)},
                                 index=idx)
    frames["CRASH.NS"] = _crash_frame()
    frames[cfg.benchmark] = frames["N0.NS"].copy()
    frames["^INDIAVIX"] = pd.DataFrame(
        {"Open": 12.0, "High": 12.5, "Low": 11.5, "Close": 12.0,
         "Volume": 0.0}, index=frames["N0.NS"].index)

    def fake_fetch_daily(tickers, period="6mo"):
        if isinstance(tickers, str):
            tickers = [tickers]
        return {t: frames[t].copy() for t in tickers if t in frames}

    monkeypatch.setattr("trading.engine.fetch_daily", fake_fetch_daily)
    monkeypatch.setattr(
        "trading.engine.routed_fetch_quotes",
        lambda tickers, upstox_client=None: {
            t: float(frames[t]["Close"].iloc[-1])
            for t in tickers if t in frames})
    # deterministic: danger internals have their own tests
    monkeypatch.setattr("trading.danger.evaluate_stock", lambda df, ext=None: [])
    ocean._fund_cache.clear()
    old = dr._fund_reader
    dr.set_fundamentals_reader(lambda: [
        {"symbol": "CRASH", "field": "annualTotalDebt",
         "as_of": "2026-03-31", "value": 10.0},
        {"symbol": "CRASH", "field": "annualStockholdersEquity",
         "as_of": "2026-03-31", "value": 1000.0},
        {"symbol": "CRASH", "field": "annualFreeCashFlow",
         "as_of": "2026-03-31", "value": 99.0},
    ])
    yield e
    dr._fund_reader = old
    ocean._fund_cache.clear()


def test_scan_opens_an_ocean_sized_position(eng):
    eng.scan()
    p = eng.portfolio.positions.get("CRASH.NS")
    assert p is not None, [e for e in eng.portfolio.decision_journal[-6:]]
    assert "ocean" in p.tags
    notional = p.qty * p.entry_price
    # Rs 1,68,750 ticket (owner order 2026-08-30, "use more in of 35%"),
    # not the Rs 1.25L Everest ticket and not the superseded Rs 62.5k half.
    assert 1_40_000 <= notional <= 1_70_000
    buy = next(e for e in eng.portfolio.decision_journal
               if e.get("action") == "BUY" and e.get("ticker") == "CRASH.NS")
    assert buy["why"].startswith("OCEAN:")
    assert buy["sizing"]["mode"] == "ocean_fixed"


def test_ocean_is_exempt_from_the_breadth_gate(eng, monkeypatch):
    """The gate stays ON for momentum (owner choice) but must not starve
    the dip engine it exists in opposition to."""
    monkeypatch.setattr(TradingEngine, "_breadth_gate_blocks",
                        lambda self, *a, **k: True)
    eng.scan()
    assert "CRASH.NS" in eng.portfolio.positions
    momentum_buys = [e for e in eng.portfolio.decision_journal
                     if e.get("action") == "BUY"
                     and not e.get("why", "").startswith("OCEAN:")]
    assert momentum_buys == []                   # gate still rules Everest


def test_danger_fire_vetoes_the_ocean_buy(eng, monkeypatch):
    monkeypatch.setattr("trading.danger.evaluate_stock",
                        lambda df, ext=None: ["T12W_0027"])
    eng.scan()
    assert "CRASH.NS" not in eng.portfolio.positions
    blocked = [e for e in eng.portfolio.decision_journal
               if e.get("action") == "OCEAN_STOCK_BLOCKED"]
    assert blocked and "danger_T12W_0027" in blocked[0]["why"]
