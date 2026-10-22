"""Entry-path fix batch, builder A2 (owner cards 2026-10-22).

A2-1 D2  — the composite's earnings-growth and analyst legs read the app's
           STORED files first (Yahoo second, 50 last).
A2-2     — the Yahoo headline fetch has a time limit and a breaker.
A2-3     — XR-050: a dead regime feed raises (the engine's C7 path keeps the
           last regime and its cash reserve) instead of faking NEUTRAL.
A2-4     — XR-056 Ocean survival fails on missing values; XR-055 the FII
           "5 days" are trading days by date; XR-042 policy words match whole.
A2-5 D8  — XR-060: the danger layer counts NumPy booleans.
A2-6     — XR-078 backtest sizes like live; ROOT-ROUTE-01 a partial stored
           history no longer stops the repo zip from filling the window.

No test reaches the network: Yahoo, NSE and Mongo are stubbed.
"""
from __future__ import annotations

import importlib
import threading
import time
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from trading.config import Config


# ---------------------------------------------------------------- helpers
def _stored_feeds():
    """The new module, or None on code that predates it (so a test proving
    the fix fails on its assertion, not on an import)."""
    try:
        return importlib.import_module("trading.stored_feeds")
    except ImportError:
        return None


@pytest.fixture
def no_yahoo_no_mongo(monkeypatch):
    """Yahoo dead for both legs, no Mongo overlay, today pinned to the day
    the cards were answered (so the committed quarters stay fresh)."""
    import trading.scoring as S
    import trading.signals.analysts as A
    from trading import data_refresh as dr

    def dead(*a, **k):
        raise AssertionError("Yahoo must not be asked when the store answers")

    monkeypatch.setattr(S, "fetch_earnings_growth", lambda t: None)
    monkeypatch.setattr(A, "fetch_analyst_score", lambda t: None)
    monkeypatch.setattr(dr, "_fund_reader", None)
    sf = _stored_feeds()
    if sf is not None:
        monkeypatch.setattr(sf, "_ist_today", lambda: date(2026, 10, 22))
        sf.clear_cache()
    yield S
    if sf is not None:
        sf.clear_cache()


def _frame(n=320, drift=0.0006, seed=1):
    rng = np.random.default_rng(seed)
    c = 100 * np.cumprod(1 + drift + rng.normal(0, 0.01, n))
    idx = pd.bdate_range(end="2026-10-21", periods=n)
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99,
                         "Close": c, "Volume": np.full(n, 5e5)}, index=idx)


# =========================================================== A2-1 (D2)
def test_earnings_leg_reads_the_committed_store_with_yahoo_dead(no_yahoo_no_mongo):
    S = no_yahoo_no_mongo
    v = S.score_earnings_growth("TCS.NS")
    # TCS quarterly net income 2026-06-30 1.3349e11 vs 2025-06-30 1.2760e11
    # = +4.62% -> band 50 + 4.62 x 2.5
    assert float(v) == pytest.approx(50 + (1.3349e11 / 1.276e11 - 1) * 100 * 2.5,
                                     abs=0.05)
    assert "fundamentals store" in (getattr(v, "source", "") or "")


def test_analyst_leg_reads_the_ratings_file_with_yahoo_dead(no_yahoo_no_mongo):
    S = no_yahoo_no_mongo
    # TCS mean_rating 2.07317 (Yahoo recommendationMean) -> (5 - m) / 4 x 100
    v = S._analyst_revision_score("TCS.NS")
    assert float(v) == pytest.approx((5 - 2.07317) / 4 * 100, abs=0.05)
    assert "ratings file" in (getattr(v, "source", "") or "")


def test_a_row_without_a_mean_uses_the_broker_counts_labelled(no_yahoo_no_mongo):
    S = no_yahoo_no_mongo
    v = S._analyst_revision_score("ADANIENSOL.NS")     # mean blank, counts 2/4/3/0/0
    mean = (2 * 1 + 4 * 2 + 3 * 3) / 9
    assert float(v) == pytest.approx((5 - mean) / 4 * 100, abs=0.05)
    assert "(counts)" in v.source


def test_a_name_outside_the_files_stays_neutral(no_yahoo_no_mongo):
    S = no_yahoo_no_mongo
    assert float(S.score_earnings_growth("ZZNOTREAL.NS")) == 50.0
    assert float(S._analyst_revision_score("ZZNOTREAL.NS")) == 50.0


def test_the_yahoo_path_still_works_when_the_store_has_nothing(monkeypatch):
    import trading.scoring as S
    import trading.signals.analysts as A
    monkeypatch.setattr(S, "fetch_earnings_growth", lambda t: 10.0)
    monkeypatch.setattr(A, "fetch_analyst_score",
                        lambda t: A.AnalystScore(25.0, 5, 4.0))
    assert float(S.score_earnings_growth("ZZNOTREAL.NS")) == 75.0
    assert float(S._analyst_revision_score("ZZNOTREAL.NS")) == 25.0


def test_one_mapping_for_both_analyst_sources():
    import trading.signals.analysts as A
    assert A.score_from_mean(1.0) == 100.0
    assert A.score_from_mean(5.0) == 0.0
    assert A.score_from_mean(4.0) == 25.0


def test_the_monthly_mongo_overlay_reaches_the_scorer(no_yahoo_no_mongo, monkeypatch):
    S = no_yahoo_no_mongo
    from trading import data_refresh as dr
    monkeypatch.setattr(dr, "_fund_reader", lambda: [
        {"symbol": "TCS", "field": "quarterlyNetIncome",
         "as_of": "2026-09-30", "value": 1.45e11},
        {"symbol": "TCS", "field": "quarterlyNetIncome",
         "as_of": "2025-09-30", "value": 1.25e11},
    ])
    sf = _stored_feeds()
    if sf is not None:
        sf.clear_cache()
    v = S.score_earnings_growth("TCS.NS")
    assert float(v) == pytest.approx(50 + (1.45 / 1.25 - 1) * 100 * 2.5, abs=0.05)
    assert "2026-09-30" in v.source


def test_a_currency_mixup_is_never_read_as_growth(no_yahoo_no_mongo, monkeypatch):
    """The store carries ADR names in US dollars; a rupee row laid over a
    dollar row (~90x) must not read as +8,900%."""
    S = no_yahoo_no_mongo
    from trading import data_refresh as dr
    monkeypatch.setattr(dr, "_fund_reader", lambda: [
        {"symbol": "ZZADR", "field": "quarterlyNetIncome",
         "as_of": "2025-06-30", "value": 0.8e9},
        {"symbol": "ZZADR", "field": "quarterlyNetIncome",
         "as_of": "2026-06-30", "value": 72e9},
    ])
    sf = _stored_feeds()
    if sf is not None:
        sf.clear_cache()
    assert float(S.score_earnings_growth("ZZADR.NS")) == 50.0


def test_backtests_never_read_todays_files(no_yahoo_no_mongo):
    S = no_yahoo_no_mongo
    from trading import data as D
    D.set_backtest_mode(True)
    try:
        assert float(S.score_earnings_growth("TCS.NS")) == 50.0
        assert float(S._analyst_revision_score("TCS.NS")) == 50.0
    finally:
        D.set_backtest_mode(False)


def test_the_reasoning_text_names_where_the_numbers_came_from(no_yahoo_no_mongo,
                                                              monkeypatch):
    S = no_yahoo_no_mongo
    import trading.signals.news as N
    monkeypatch.setattr(N, "fetch_sentiment",
                        lambda t: type("x", (), {"score": 50.0})())
    monkeypatch.setattr(S, "_short_interest_score", lambda t, c: 50.0)
    cfg = Config(mode="positional")
    total, comps = S.compute_score("TCS.NS", _frame(), None, {}, cfg,
                                   mode="positional")
    txt = S.reasoning_text(comps, total, S.weights_for("positional"))
    assert "fundamentals store" in txt and "ratings file" in txt
    # a category that does not weigh a leg does not mention it
    txt_i = S.reasoning_text(comps, total, S.weights_for("intraday"))
    assert "fundamentals store" not in txt_i


# ============================================================== A2-2
def test_a_hung_headline_fetch_is_cut_off_and_trips_the_breaker(monkeypatch):
    import yfinance
    import trading.signals.news as N
    release = threading.Event()
    calls = {"n": 0}

    class _Hung:
        def __init__(self, t):
            calls["n"] += 1

        @property
        def news(self):
            release.wait(30)
            return []

    monkeypatch.setattr(yfinance, "Ticker", _Hung)
    monkeypatch.setattr(N, "NEWS_TIMEOUT_S", 0.2, raising=False)
    monkeypatch.setattr(N, "_NEWS_FAILS", 0, raising=False)
    monkeypatch.setattr(N, "_NEWS_BREAKER_UNTIL", 0.0, raising=False)
    out: list = []
    try:
        th = threading.Thread(
            target=lambda: out.append([N._get_headlines_yfinance("X.NS")
                                       for _ in range(4)]), daemon=True)
        th.start()
        th.join(5.0)
        assert out, "a hung Yahoo headline call still holds the scan"
        assert out[0] == [[], [], [], []]
        assert calls["n"] == 3, "the breaker did not stop the 4th call"
    finally:
        release.set()


# ============================================================== A2-3
def _correction_frames(n=300):
    idx = pd.bdate_range(end="2026-10-21", periods=n)
    c = np.concatenate([np.linspace(20000, 26000, n - 60),
                        np.linspace(26000, 22500, 60)])
    nifty = pd.DataFrame({"Open": c, "High": c * 1.005, "Low": c * 0.995,
                          "Close": c, "Volume": 0.0}, index=idx)
    vix = pd.DataFrame({"Open": 15.0, "High": 15.5, "Low": 14.5,
                        "Close": np.full(n, 15.0), "Volume": 0.0}, index=idx)
    return {"^NSEI": nifty, "^INDIAVIX": vix}


def test_a_dead_regime_feed_raises_instead_of_reading_neutral(monkeypatch):
    import trading.data as D
    from trading.regime import fetch_regime
    monkeypatch.setattr(D, "fetch_daily", lambda *a, **k: {})
    with pytest.raises(Exception) as exc:
        reading = fetch_regime()
        pytest.fail(f"dead feed read as {reading.regime}: {reading.reasoning}")
    assert getattr(exc.value, "status", None) == "FEED_DEAD"

    def boom(*a, **k):
        raise ConnectionError("upstox down")
    monkeypatch.setattr(D, "fetch_daily", boom)
    with pytest.raises(Exception) as exc2:
        fetch_regime()
    assert getattr(exc2.value, "status", None) == "FEED_DEAD"


def test_a_dead_feed_keeps_the_correction_reserve(monkeypatch, tmp_path):
    import trading.data as D
    import trading.signals.flows as F
    from trading.engine import TradingEngine
    from trading.paper import Portfolio
    cfg = Config(state_dir=str(tmp_path), mode="nifty200_momentum")
    cfg.apply_mode_preset()
    cfg.custom_universe = ("ZZA.NS",)
    p = Portfolio.fresh(cfg)
    eng = TradingEngine(cfg, p)
    monkeypatch.setattr("trading.engine.fetch_daily", lambda *a, **k: {})
    monkeypatch.setattr("trading.engine.routed_fetch_quotes",
                        lambda *a, **k: {})
    monkeypatch.setattr(F, "get_flows", lambda *a, **k: None)
    live = _correction_frames()
    monkeypatch.setattr(D, "fetch_daily", lambda *a, **k: live)
    eng.scan()
    assert p.last_regime == "correction"
    assert cfg.cash_reserve_pct == pytest.approx(0.20)
    n_hist = len(p.regime_history)
    # the feed dies
    monkeypatch.setattr(D, "fetch_daily", lambda *a, **k: {})
    eng.scan()
    assert cfg.cash_reserve_pct == pytest.approx(0.20), \
        "a dead feed dropped the correction reserve to the neutral 0%"
    assert p.last_regime == "correction"
    assert len(p.regime_history) == n_hist, "a regime change that never happened"
    assert any(e.get("action") == "REGIME_FEED_DOWN" for e in p.decision_journal)


# ============================================================== A2-4
def test_ocean_survival_fails_on_missing_values(monkeypatch):
    from trading import data_refresh as dr
    from trading.engines import ocean
    ocean._fund_cache.clear()
    monkeypatch.setattr(dr, "_fund_reader", lambda: [
        {"symbol": "ZNANEQ", "field": "annualTotalDebt",
         "as_of": "2026-03-31", "value": 100.0},
        {"symbol": "ZNANEQ", "field": "annualStockholdersEquity",
         "as_of": "2026-03-31", "value": float("nan")},
        {"symbol": "ZNANEQ", "field": "annualFreeCashFlow",
         "as_of": "2026-03-31", "value": 50.0},
        {"symbol": "ZNANFCF", "field": "annualTotalDebt",
         "as_of": "2026-03-31", "value": 100.0},
        {"symbol": "ZNANFCF", "field": "annualStockholdersEquity",
         "as_of": "2026-03-31", "value": 1000.0},
        {"symbol": "ZNANFCF", "field": "annualFreeCashFlow",
         "as_of": "2026-03-31", "value": float("nan")},
        {"symbol": "ZGOOD", "field": "annualTotalDebt",
         "as_of": "2026-03-31", "value": 100.0},
        {"symbol": "ZGOOD", "field": "annualStockholdersEquity",
         "as_of": "2026-03-31", "value": 1000.0},
        {"symbol": "ZGOOD", "field": "annualFreeCashFlow",
         "as_of": "2026-03-31", "value": 50.0},
    ])
    try:
        ok, why = ocean.survival_check("ZNANEQ.NS")
        assert not ok, f"unknown equity passed survival: {why}"
        ok, why = ocean.survival_check("ZNANFCF.NS")
        assert not ok, f"unknown free cash flow passed survival: {why}"
        ok, why = ocean.survival_check("ZGOOD.NS")
        assert ok, why
    finally:
        ocean._fund_cache.clear()


def test_fii_five_days_are_trading_days_not_rows():
    from trading.signals.flows import FlowReading, fii_net_last_n_days
    def r(d, net):
        return FlowReading(d, 0.0, 0.0, net, 0.0, 0.0, 0.0)
    gappy = [r(date(2026, 7, 24), 1000.0)] + [
        r(date(2026, 7, d), 5000.0) for d in (10, 9, 8, 7)]
    # only 07-24 sits inside the 5 trading days 07-20..07-24
    assert fii_net_last_n_days(gappy, n=5) == 1000.0
    full = [r(date(2026, 7, d), 1000.0) for d in (24, 23, 22, 21, 20, 17)]
    assert fii_net_last_n_days(full, n=5) == 5000.0


def test_policy_words_match_whole_words_only():
    from trading.signals.policy import _keyword_match_sectors as k
    assert k("Cabinet reviews the development of capital markets") == []
    assert k("Every level of review") == []
    assert "Auto" in k("New EV charging policy")
    assert "Pharma" in k("PLI push for API makers")
    assert "Defence" in k("Push for indigenisation of naval craft")


# ========================================================= A2-5 (D8)
def _crash_bar_frame(n=320):
    i = np.arange(n, dtype=float)
    c = 100 * np.exp(0.0004 * i)
    o, h, lo = c.copy(), c * 1.01, c * 0.99
    v = np.full(n, 2e5)
    c[-1] = c[-2] * 0.88                       # a 12% crash bar, closing at its low
    o[-1], h[-1], lo[-1], v[-1] = c[-2], c[-2] * 1.001, c[-1] * 0.999, 9e5
    return pd.DataFrame({"Open": o, "High": h, "Low": lo, "Close": c,
                         "Volume": v},
                        index=pd.bdate_range(end="2026-10-21", periods=n))


def test_numpy_true_tripwires_are_counted():
    from trading import danger
    fired = danger.evaluate_stock(_crash_bar_frame())
    # ATR shock + close at low (lift 4.1x) returns a NumPy bool
    assert "T12W_0091" in fired, fired
    assert len(fired) >= 2
    assert danger.plan("X.NS", fired, auto=True).action == "EXIT_HALF"


def test_an_abstaining_or_missing_input_never_counts(monkeypatch):
    from trading import danger
    monkeypatch.setattr(danger, "_FNS", {
        "T_NP": lambda x: np.True_, "T_NONE": lambda x: None,
        "T_NAN": lambda x: float("nan"), "T_NPF": lambda x: np.False_})
    monkeypatch.setattr(danger, "_EXT_FNS", {})
    assert danger.evaluate_stock(_crash_bar_frame()) == ["T_NP"]


def test_a_history_shorter_than_the_calibrated_lookback_abstains():
    from trading import danger
    assert danger.evaluate_stock(_crash_bar_frame().tail(50)) == []


def test_the_live_tripwires_read_countable_on_the_den():
    """The Den's XR-060 warning reads the TYPE each live tripwire returns."""
    from trading import danger
    x = danger._Ctx(_crash_bar_frame())
    for tid, fn in danger._FNS.items():
        r = fn(x)
        assert not isinstance(r, np.bool_), f"{tid} still answers a NumPy bool"


# ========================================================= A2-6 XR-078
def test_the_backtest_sizes_an_entry_like_live(monkeypatch, tmp_path):
    import trading.backtest as B
    from trading.paper import Portfolio
    from trading.risk import RiskState
    from trading.sizing import position_notional_for
    cfg = Config(state_dir=str(tmp_path), mode="nifty200_momentum")
    cfg.apply_mode_preset()
    hist_full = _frame(n=320, drift=0.002, seed=4)
    next_date = hist_full.index[-1]
    hist = hist_full.iloc[:-1]
    monkeypatch.setattr(B, "_score_for_mode",
                        lambda *a, **k: (85.0, {"momentum": 85.0}))
    monkeypatch.setattr(B, "timing_ok", lambda h, c: (True, {}))
    monkeypatch.setattr("trading.calendars.is_system_event_within",
                        lambda *a, **k: None)
    p = Portfolio.fresh(cfg)
    B._try_open_positions(cfg, p, RiskState(cfg, cfg.starting_capital),
                          {"ZZB.NS": hist}, None,
                          {"ZZB.NS": hist_full}, next_date)
    pos = p.positions.get("ZZB.NS")
    assert pos is not None
    spent = pos.qty * pos.entry_price
    want, diag = position_notional_for(cfg, cfg.starting_capital, score=85.0,
                                       close=hist["Close"].dropna())
    assert diag["mode"] == "slab" and want > 2 * 125_000
    assert spent > 125_000 * 1.01, "still the old fixed Rs 1,25,000 per trade"
    assert spent <= want * 1.001


# ===================================================== A2-6 ROOT-ROUTE-01
def _store_doc(start, end):
    idx = pd.bdate_range(start, end)
    return {"ticker": "TCS.NS", "end": end,
            "candles": [[d.strftime("%Y-%m-%d"), 1.0, 1.0, 1.0, 999.0, 1.0]
                        for d in idx]}


def test_a_partial_store_history_is_filled_from_the_repo_zip(monkeypatch):
    from web import db
    from web.routes import backtest_routes as BR
    monkeypatch.setattr(db, "is_connected", lambda: True)
    monkeypatch.setattr(db, "load_history_doc",
                        lambda t: _store_doc("2020-06-01", "2026-10-20"))
    monkeypatch.setattr("trading.data.fetch_daily_range", lambda *a, **k: {})
    out = BR._preloaded_history(["TCS.NS"], "^NSEI", "2020-01-01", "2020-12-31")
    df = out["TCS.NS"]
    # window start 2020-01-01 minus the 420-day scoring look-back
    assert df.index.min() <= pd.Timestamp("2018-11-15"), \
        "the zip did not fill the years the store lacks"
    # the store wins on dates both have, the zip fills the rest
    assert float(df.loc["2020-06-01", "Close"]) == 999.0
    assert float(df.loc["2019-06-03", "Close"]) != 999.0


def test_a_store_history_entirely_after_the_window_does_not_empty_it(monkeypatch):
    from web import db
    from web.routes import backtest_routes as BR
    monkeypatch.setattr(db, "is_connected", lambda: True)
    monkeypatch.setattr(db, "load_history_doc",
                        lambda t: _store_doc("2025-01-01", "2026-10-20"))
    monkeypatch.setattr("trading.data.fetch_daily_range", lambda *a, **k: {})
    out = BR._preloaded_history(["TCS.NS"], "^NSEI", "2020-01-01", "2020-12-31")
    assert len(out["TCS.NS"]) > 400
