"""Valuation context stage — owner order 2026-10-12 ("when we are
evaluating the price with connection of the market as well as the stock
sector … inflation rate at that time … dip in sensation … to verify and to
provide a target" → "both, indian sector only, discount rate - go").

Pins the arithmetic of trading/valuation_context.py:
  * β is the regression slope and refuses to guess on too few pairs;
  * the sector leg prints BOTH columns (1:1 and measured);
  * the rates leg goes through the discount rate: at the numbers quoted to
    the owner (10y 7.2 + ERP 5 − growth 8) one extra point is −19%;
  * the sentiment margin only WIDENS the buy discount, and only on gauges
    that are ON — an unknown gauge is counted, never assumed;
  * the verify split flags a re-run beyond the band;
  * every missing input is DATA_NEEDED with a reason, never a number;
  * every mapped sector index exists in the live feed's key table;
  * the module is pure and no money-path module imports it.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trading import valuation_context as vc

ROOT = Path(__file__).resolve().parents[2]


def _pair(n: int = 400, beta: float = 1.5, seed: int = 1):
    idx = pd.bdate_range("2024-01-01", periods=n)
    rng = np.random.default_rng(seed)
    b = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.01, n)), index=idx)
    rb = b.pct_change().fillna(0).to_numpy()
    s = pd.Series(100 * np.cumprod(1 + beta * rb + rng.normal(0, 0.002, n)), index=idx)
    return s, b


def test_beta_is_the_regression_slope_over_252_sessions():
    s, b = _pair(beta=1.5)
    r = vc.beta(s, b)
    assert r["n"] == 252
    assert abs(r["beta"] - 1.5) < 0.1
    assert r["window_start"] < r["window_end"]


def test_beta_refuses_to_guess_on_too_few_pairs():
    s, b = _pair(n=80)
    r = vc.beta(s, b)
    assert r["beta"] is None and r["n"] == 79 and "needs 120" in r["why"]


def test_sector_leg_prints_both_columns():
    out = vc.sector_leg(1000.0, -2.0, 1.5)
    assert out["one_to_one"] == pytest.approx(980.0)     # the owner's 1:1 rule
    assert out["measured"] == pytest.approx(970.0)       # the stock's own β
    # no β = no measured column, the 1:1 column still prints
    out = vc.sector_leg(1000.0, -2.0, None)
    assert out["one_to_one"] == pytest.approx(980.0) and out["measured"] is None
    # no sector move = nothing invented
    assert vc.sector_leg(1000.0, None, 1.5) == {"one_to_one": None, "measured": None}


def test_rates_leg_one_extra_point_is_minus_19pct_at_the_quoted_numbers():
    # 10y 7.2 + ERP 5 − growth 8 = r − g of 4.2 points; one more point on
    # the rate makes it 5.2 → the multiplier is 4.2/5.2 = −19.2%.
    r = vc.rates_leg(100.0, 7.2, 8.2, erp_pct=5.0, growth_pct=8.0)
    assert r["status"] == "OK"
    assert r["multiplier"] == pytest.approx(4.2 / 5.2)
    assert r["adjusted"] == pytest.approx(100.0 * 4.2 / 5.2)
    # the sensitivity printed FROM the then-rate is the −19% figure
    r0 = vc.rates_leg(100.0, 7.2, 7.2, erp_pct=5.0, growth_pct=8.0)
    assert r0["multiplier"] == pytest.approx(1.0)
    assert r0["per_pp_pct"] == pytest.approx(-19.23, abs=0.01)
    # inflation enters as points on today's rate, not as a flat cut
    r1 = vc.rates_leg(100.0, 7.2, 7.2, erp_pct=5.0, growth_pct=8.0,
                      inflation_shift_pp=1.0)
    assert r1["multiplier"] == pytest.approx(4.2 / 5.2)


def test_rates_leg_has_no_answer_when_growth_meets_the_discount_rate():
    r = vc.rates_leg(100.0, 7.2, 7.2, erp_pct=5.0, growth_pct=12.5)
    assert r["status"] == "DATA_NEEDED" and r["multiplier"] is None
    assert "no answer" in r["why"]
    r = vc.rates_leg(100.0, None, 7.2)
    assert r["status"] == "DATA_NEEDED"


def test_sentiment_margin_widens_only_on_gauges_that_are_on():
    idx = pd.bdate_range("2025-01-01", periods=200)
    # a calm VIX that wanders 11-14 and closes mid-range: no fear
    calm = pd.Series(12.5 + 1.2 * np.sin(np.arange(200) / 9.0), index=idx)
    quiet = vc.sentiment_margin(calm, breadth_pct=55.0, fii_5d_net_cr=1000.0,
                                damage_q90_on=False)
    assert quiet["n_on"] == 0 and quiet["discount_pct"] == pytest.approx(10.0)
    assert quiet["gauges"][0]["state"] == "off"
    spike = pd.concat([calm, pd.Series([30.0], index=[idx[-1] + pd.Timedelta(days=1)])])
    fear = vc.sentiment_margin(spike, breadth_pct=35.0, fii_5d_net_cr=-6000.0,
                               damage_q90_on=True)
    assert fear["n_on"] == 4 and fear["discount_pct"] == pytest.approx(20.0)
    # an unknown gauge is COUNTED as unknown, never read as fear or calm
    unk = vc.sentiment_margin(None, breadth_pct=None, fii_5d_net_cr=None,
                              damage_q90_on=None)
    assert unk["n_on"] == 0 and unk["n_unknown"] == 4
    assert unk["discount_pct"] == pytest.approx(10.0)
    assert all(g["state"] == "DATA_NEEDED" for g in unk["gauges"])


def test_verify_split_flags_a_rerun_beyond_the_band():
    d = vc.decompose(-20.0, -4.0, 1.5)
    assert d["sector_part_pct"] == pytest.approx(-6.0)
    assert d["residual_pct"] == pytest.approx(-14.0) and d["rerun"] is True
    d = vc.decompose(-7.0, -4.0, 1.5)
    assert d["residual_pct"] == pytest.approx(-1.0) and d["rerun"] is False
    # no β → the 1:1 split, and the page is told it was assumed
    d = vc.decompose(-20.0, -4.0, None)
    assert d["beta_used"] == 1.0 and d["beta_assumed_one"] is True
    assert d["residual_pct"] == pytest.approx(-16.0)
    assert vc.decompose(None, -4.0, 1.0)["status"] == "DATA_NEEDED"


def test_context_dates_every_input_and_never_invents_a_missing_one():
    s, b = _pair()
    bond = pd.Series([7.0] * len(s), index=s.index)
    r = vc.context("X.NS", 1000.0, "2025-01-15", s, b, "Nifty IT", bond=bond,
                   rate_now_pct=7.5, news_verdict="NO_NEWS")
    assert r["status"] == "OK"
    assert r["price_then_date"] == "2025-01-15" and r["price_now_date"] == "2025-07-11"
    sl = r["legs"]["sector"]
    assert sl["status"] == "OK" and sl["one_to_one"] and sl["measured"]
    assert sl["beta_n"] == 252
    rl = r["legs"]["rates"]
    assert rl["status"] == "OK" and rl["rate_now_source"] == "typed"
    assert rl["rate_then_date"] == "2025-01-15"
    # context = sector column × rates multiplier
    assert r["context_one_to_one"] == pytest.approx(sl["one_to_one"] * rl["multiplier"])
    assert r["context_measured"] == pytest.approx(sl["measured"] * rl["multiplier"])
    sm = r["legs"]["sentiment"]
    assert sm["buy_below_one_to_one"] == pytest.approx(
        r["context_one_to_one"] * (1 - sm["discount_pct"] / 100))
    assert r["legs"]["verify"]["status"] == "OK"
    assert r["legs"]["verify"]["news_verdict"] == "NO_NEWS"

    # valuation dated before the stored closes = DATA_NEEDED, no number
    r = vc.context("X.NS", 1000.0, "2023-01-01", s, b, "Nifty IT", bond=bond)
    assert r["status"] == "DATA_NEEDED" and "after the valuation date" in r["why"]
    assert "context_one_to_one" not in r

    # no sector series: the sector leg says why, the rates leg still works
    r = vc.context("X.NS", 1000.0, "2025-01-15", s, None, "Nifty IT", bond=bond,
                   bench_why="no stored rows for Nifty IT yet")
    assert r["legs"]["sector"]["status"] == "DATA_NEEDED"
    assert r["legs"]["sector"]["why"] == "no stored rows for Nifty IT yet"
    assert r["legs"]["sector"]["one_to_one"] is None
    assert r["legs"]["rates"]["status"] == "OK"
    assert r["context_rates_only"] == pytest.approx(1000.0 * r["legs"]["rates"]["multiplier"])
    assert r["legs"]["verify"]["status"] == "DATA_NEEDED"

    # no bond file and no typed rate: the rates leg is DATA_NEEDED and NOT
    # applied as × 1.0 (VAL-13, owner order 2026-10-21): the context target
    # is the sector leg only, labelled; the target alone is never printed
    # as a "rates leg only" context target
    r = vc.context("X.NS", 1000.0, "2025-01-15", s, b, "Nifty IT", bond=None)
    assert r["legs"]["rates"]["status"] == "DATA_NEEDED"
    assert r["context_one_to_one"] == pytest.approx(r["legs"]["sector"]["one_to_one"])
    assert r["rates_applied"] is False
    assert r["rates_note"] == "no rates adjustment — 10-year yield DATA_NEEDED"
    assert r["context_rates_only"] is None

    # a target of zero or an unreadable date is refused, not computed
    assert vc.context("X.NS", 0.0, "2025-01-15", s, b, "x")["status"] == "DATA_NEEDED"
    assert vc.context("X.NS", 10.0, "not-a-date", s, b, "x")["status"] == "DATA_NEEDED"


def test_every_mapped_sector_index_exists_in_the_live_feed():
    from trading.config import Config
    from trading.data_refresh import SECTOR_INDEX_KEYS
    for sector, idx in vc.SECTOR_INDEX_FOR.items():
        assert idx in SECTOR_INDEX_KEYS, (sector, idx)
    for ticker, idx in vc.TICKER_INDEX_OVERRIDE.items():
        assert idx in SECTOR_INDEX_KEYS, (ticker, idx)
        assert ticker.endswith(".NS")
    vocab = set(Config().sector_map.values())
    assert set(vc.SECTOR_INDEX_FOR) <= vocab, set(vc.SECTOR_INDEX_FOR) - vocab
    # Indian only (owner's pick): no key names a foreign index
    assert not any("global" in v.lower() or "us" == v.lower()
                   for v in vc.SECTOR_INDEX_FOR.values())


def _code_only(src: str) -> str:
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    return code.split('"""')[0] + '"""'.join(code.split('"""')[2:])


def test_the_module_stays_pure_and_no_money_path_module_imports_it():
    code = _code_only(inspect.getsource(vc))
    for banned in ("from .engine", "from .risk", "from .orders", "from .danger",
                   "import requests", "import httpx", "yfinance", "web.db",
                   "from web", "import web"):
        assert banned not in code, f"valuation_context must not reference {banned}"
    for name in ("engine.py", "risk.py", "orders.py", "danger.py"):
        src = (ROOT / "trading" / name).read_text()
        assert not re.search(r"valuation_context", _code_only(src)), name
