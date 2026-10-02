"""Owner order 2026-10-02 ("go for next"): audit items F-13, S-5, S-8,
S-10 and S-15 from docs/AUDIT_2026-10-02.md. (F-13 is pinned in
test_internals.py, beside the formula it changes.)

S-5   A look-only scan before 09:15 stamped the day as done, so the catch-up
      skipped and no scan that could buy ran when the app slept through 09:20.
S-8   The day's breadth reading was cached only when the momentum pass had
      picks; on a no-pick day the other four passes read no breadth at all.
S-10  New names were pulled with one year of bars (~248); momentum needs 252.
S-15  Yahoo `.info` calls in the exit pass had no timeout.
"""
from __future__ import annotations

import time
from datetime import date

import numpy as np
import pandas as pd
import pytest

from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import Portfolio


def _frame(n=300, start=100.0, drift=-0.40, seed=1):
    rng = np.random.default_rng(seed)
    close = start * np.cumprod(1 + drift / n + rng.normal(0, 0.004, n))
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    return pd.DataFrame({"Open": close, "High": close * 1.01,
                         "Low": close * 0.99, "Close": close,
                         "Volume": np.full(n, 5e6)}, index=idx)


@pytest.fixture()
def eng(tmp_path, monkeypatch):
    cfg = Config(state_dir=str(tmp_path), mode="nifty200_momentum")
    cfg.apply_mode_preset()
    cfg.custom_universe = ("DOWN1.NS", "DOWN2.NS", "DOWN3.NS")
    e = TradingEngine(cfg, Portfolio.fresh(cfg))
    frames = {t: _frame(seed=i) for i, t in enumerate(cfg.custom_universe)}
    frames[cfg.benchmark] = _frame(drift=0.05, seed=9)
    frames["^INDIAVIX"] = _frame(start=12.0, drift=0.0, seed=8)

    def fake_fetch_daily(tickers, period="6mo"):
        if isinstance(tickers, str):
            tickers = [tickers]
        return {t: frames[t].copy() for t in tickers if t in frames}
    monkeypatch.setattr("trading.engine.fetch_daily", fake_fetch_daily)
    monkeypatch.setattr("trading.engine.routed_fetch_quotes",
                        lambda t, upstox_client=None: {})
    return e


# --------------------------------------------------------------------- S-8

def test_a_no_pick_momentum_scan_still_caches_breadth(eng):
    """Every name is falling, so momentum picks nothing — the gate is never
    consulted. Before S-8 nothing was cached and the later passes fell open."""
    eng.scan()
    assert not eng.portfolio.last_scan_opened
    cached = eng.portfolio.breadth_today or {}
    assert cached.get("asof") == date.today().isoformat()
    assert cached.get("sample", 0) >= 3


# --------------------------------------------------------------------- S-5

def test_only_an_in_hours_scan_stamps_the_live_mark(eng, monkeypatch):
    monkeypatch.setattr(eng, "_trading_allowed", lambda now=None: False)
    eng.scan()
    assert eng.portfolio.last_scan and not eng.portfolio.last_scan_live
    monkeypatch.setattr(eng, "_trading_allowed", lambda now=None: True)
    eng.scan()
    assert eng.portfolio.last_scan_live


def test_the_live_mark_survives_a_save_and_reload(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.last_scan_live = "2026-10-05T04:00:00"
    p.save()
    assert Portfolio.load_or_fresh(cfg).last_scan_live == "2026-10-05T04:00:00"


# -------------------------------------------------------------------- S-10

def test_a_new_name_gets_two_years_and_an_old_one_one():
    from trading import history_store as hs
    asked = {}

    def fake_dl(broker, key, years, today):
        asked[key] = years
        idx = pd.bdate_range(end=pd.Timestamp(today), periods=5)
        return pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0,
                             "Close": 1.0, "Volume": 1.0}, index=idx)

    long_doc = {"ticker": "OLD.NS", "end": "2026-01-01",
                "candles": [["d", 1, 1, 1, 1, 1]] * 400}
    short_doc = {"ticker": "SHORT.NS", "end": "2026-01-01",
                 "candles": [["d", 1, 1, 1, 1, 1]] * 250}
    docs = {"OLD.NS": long_doc, "SHORT.NS": short_doc}
    orig = hs.download_ticker_history
    hs.download_ticker_history = fake_dl
    try:
        hs.top_up_history(object(), {"NEW.NS": "k_new", "OLD.NS": "k_old",
                                     "SHORT.NS": "k_short"},
                          load_doc=docs.get, save_doc=lambda d: None,
                          today=date(2026, 10, 5))
    finally:
        hs.download_ticker_history = orig
    assert asked == {"k_new": 2, "k_old": 1, "k_short": 2}


# -------------------------------------------------------------------- S-15

def test_a_hung_yahoo_call_times_out():
    from trading.data import run_with_timeout
    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        run_with_timeout(lambda: time.sleep(5), timeout=0.2)
    assert time.monotonic() - t0 < 2.0


def test_earnings_growth_falls_back_and_the_breaker_opens(monkeypatch):
    from trading import data as D
    calls = []

    class Slow:
        def __init__(self, t):
            calls.append(t)

        @property
        def info(self):
            time.sleep(5)
            return {}
    monkeypatch.setattr(D.yf, "Ticker", Slow)
    monkeypatch.setattr(D, "YAHOO_INFO_TIMEOUT_S", 0.1)
    monkeypatch.setattr(D, "_EG_CACHE", {})
    monkeypatch.setattr(D, "_EG_FAILS", 0)
    monkeypatch.setattr(D, "_EG_BREAKER_UNTIL", 0.0)
    orig = D.run_with_timeout
    monkeypatch.setattr(D, "run_with_timeout",
                        lambda fn, timeout=0.1: orig(fn, timeout=0.1))
    t0 = time.monotonic()
    for i in range(D._EG_BREAKER_AFTER + 2):
        assert D.fetch_earnings_growth(f"X{i}.NS") is None
    assert D._EG_BREAKER_UNTIL > 0
    # after the breaker opens, no new call reaches Yahoo
    assert len(calls) == D._EG_BREAKER_AFTER
    assert time.monotonic() - t0 < 3.0


def test_earnings_growth_is_cached():
    from trading import data as D
    D._EG_CACHE["CACHED.NS"] = (time.time(), 12.5)
    assert D.fetch_earnings_growth("CACHED.NS") == 12.5


def test_the_analyst_fetch_is_bounded():
    import inspect
    from trading.signals import analysts
    src = inspect.getsource(analysts.fetch_analyst_score)
    code = "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith("#"))
    assert "run_with_timeout(" in code
