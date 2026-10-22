"""VALFIX batch 3 — market legs on the valuation target, "split legs, no
double count" (owner choices 2026-10-21; study S2). Display only.

Pins trading/valuation/market_legs.py:
  * market-adjusted = own × (1 + β·Sensex) × (1 + c·(sector − Sensex)) ×
    rates × currency, in two columns: typed β = 1 / 1:1 and measured;
  * a name with no NSE sector index counts the sector-vs-market leg 0 (the
    market again is not a sector); a missing sector or rates leg is not
    applied and is named; a missing Sensex = no market-adjusted target;
  * the measured β and coefficient come from a regression that recovers
    known slopes; an index with fewer than 252 sessions reads none;
  * the US indices are printed with δ × Sensex inside and counted 0; an
    Indian date pairs with the US close BEFORE it;
  * the verdict scales the RANGE by the legs — the price is never
    multiplied (FX through EPS, never the price);
  * the selection date: typed → the §11 date → publication; a §11 date
    older than the statements is not used; the bridge reads the market to
    the selection date, never before the period end.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from trading.valuation import market_legs as ML

OK = lambda pct: {"status": "OK", "pct": pct}      # noqa: E731
RATES = {"status": "OK", "multiplier": 0.95}


def test_both_columns_multiply_the_legs_on_the_own_target():
    reg = {"beta_mkt": 0.8, "rel_coef": 1.2}
    out = ML.market_adjust(1000.0, sensex_move=OK(-2.0), sector_move=OK(10.0),
                           sector_is_market=False, reg=reg, rates=RATES, fx_factor=0.99)
    assert out["status"] == "OK" and out["rel_pct"] == pytest.approx(12.0)
    t = out["typed"]
    assert t["target"] == pytest.approx(1000 * 0.98 * 1.12 * 0.95 * 0.99)
    m = out["measured"]
    assert m["target"] == pytest.approx(1000 * (1 - 0.016) * (1 + 0.144) * 0.95 * 0.99)
    assert m["beta"] == 0.8 and m["coef"] == 1.2


def test_no_sector_index_counts_the_sector_leg_zero_and_says_so():
    out = ML.market_adjust(1000.0, sensex_move=OK(5.0), sector_move={"status": "MARKET"},
                           sector_is_market=True, reg={"beta_mkt": 1.1, "rel_coef": None},
                           rates=RATES)
    assert out["rel_pct"] == 0.0
    assert out["typed"]["target"] == pytest.approx(1000 * 1.05 * 0.95)
    assert out["measured"]["target"] == pytest.approx(1000 * 1.055 * 0.95)
    assert any("counted 0" in n for n in out["notes"])


def test_missing_legs_are_named_never_silently_one():
    out = ML.market_adjust(1000.0, sensex_move=OK(1.0),
                           sector_move={"status": "DATA_NEEDED", "why": "no rows"},
                           sector_is_market=False, reg={"beta_mkt": None, "beta_why": "short"},
                           rates={"status": "DATA_NEEDED", "why": "no yield"})
    assert out["typed"]["sector_factor"] is None and out["typed"]["rates_factor"] is None
    assert out["typed"]["target"] == pytest.approx(1010.0)
    assert any("sector leg not applied — no rows" in n for n in out["notes"])
    assert any("rates leg not applied — no yield" in n for n in out["notes"])
    assert out["measured"] is None and "β on the Sensex DATA_NEEDED" in out["measured_why"]
    no_mkt = ML.market_adjust(1000.0, sensex_move={"status": "DATA_NEEDED", "why": "x"},
                              sector_move=OK(1.0), sector_is_market=False, reg={},
                              rates=RATES)
    assert no_mkt["status"] == "DATA_NEEDED" and no_mkt["typed"] is None
    same = ML.market_adjust(1000.0, sensex_move=OK(1.0), sector_move=OK(1.0),
                            sector_is_market=False, reg={}, rates=RATES, dated_today=True)
    assert same["status"] == "SAME_DAY" and same["target_now"] == 1000.0
    assert ML.market_adjust(None, sensex_move=OK(1.0), sector_move=OK(1.0),
                            sector_is_market=False, reg={}, rates=RATES)["status"] == "DATA_NEEDED"


def test_a_currency_factor_of_zero_is_used_not_replaced():
    out = ML.market_adjust(1000.0, sensex_move=OK(0.0), sector_move=OK(0.0),
                           sector_is_market=False, reg={}, rates=RATES, fx_factor=0.0)
    assert out["typed"]["target"] == 0.0


def _series(vals, start="2025-01-01"):
    return pd.Series(vals, index=pd.bdate_range(start, periods=len(vals)))


def test_the_regression_recovers_known_slopes_and_needs_252_index_sessions():
    rng = np.random.default_rng(7)
    n = 300
    m = rng.normal(0, 0.01, n)
    x = m + rng.normal(0, 0.008, n)
    s = 0.9 * m + 1.3 * (x - m) + rng.normal(0, 0.0005, n)
    lv = lambda r: 100 * np.cumprod(1 + r)              # noqa: E731
    stock, sensex, sector = _series(lv(s)), _series(lv(m)), _series(lv(x))
    reg = ML.regress(stock, sensex, sector)
    assert reg["rel_coef"] == pytest.approx(1.3, abs=0.03)
    assert reg["beta_n"] == 252 and reg["rel_n"] == 252
    # β on the Sensex alone carries the sector's correlated part as well
    expect = np.polyfit(m[-252:], s[-252:], 1)[0]
    assert reg["beta_mkt"] == pytest.approx(expect, abs=0.02)
    short = ML.regress(stock, sensex, sector.tail(150))
    assert short["rel_coef"] is None and "needs 252" in short["rel_why"]
    assert ML.regress(stock, sensex, None)["rel_why"] == "no sector index series"


def test_us_legs_pair_with_the_close_before_and_are_counted_zero():
    us = pd.Series([100.0, 101.0, 102.0, 110.0],
                   index=pd.to_datetime(["2026-05-13", "2026-05-14", "2026-05-15", "2026-10-16"]))
    mv = ML.us_move(us, "2026-05-15", "2026-10-19")
    assert mv["start_date"] == "2026-05-14" and mv["end_date"] == "2026-10-16"
    assert mv["pct"] == pytest.approx((110 / 101 - 1) * 100)
    lines = ML.global_lines({"sp500": OK(2.11), "nasdaq": OK(-1.86), "dow": OK(3.87)},
                            OK(-1.84), is_it=True)
    assert [x["key"] for x in lines] == ["sp500", "nasdaq", "dow"]
    sp = lines[0]
    assert sp["counted"] == 0.0 and sp["delta"] == 0.286
    assert sp["inside_pct"] == pytest.approx(0.286 * -1.84)
    assert sp["residual_pct"] == pytest.approx(2.11 - 0.286 * -1.84)
    assert lines[1]["delta"] == 0.332
    assert lines[2]["delta"] is None and lines[2]["delta_why"]
    assert [x["key"] for x in ML.global_lines({}, OK(1.0), is_it=False)] == ["sp500", "dow"]


def test_the_verdict_scales_the_range_and_never_the_price():
    rng = {"low": 900.0, "base": 1000.0, "high": 1100.0, "family": "earnings"}
    legs = {"typed": {"total_factor": 1.10}, "measured": {"total_factor": 0.80}}
    v = ML.verdicts(1000.0, rng, legs)
    assert v["now_column"] == "measured"
    assert v["measured"]["base"] == pytest.approx(800.0)
    assert v["typed"]["base"] == pytest.approx(1100.0)
    # the price stays 1000 in both: the 800 base is 20% under it, the 1100 base 10% over
    assert v["measured"]["upside_to_base_pct"] == pytest.approx(-20.0)
    assert v["typed"]["upside_to_base_pct"] == pytest.approx(10.0)
    only = ML.verdicts(1000.0, rng, {"typed": {"total_factor": 1.0}, "measured": None})
    assert only["now_column"] == "typed"


# ------------------------------------------------------------ the selection date

SNAP = {"period_end": "2026-03-31", "publication_date": "2026-05-15",
        "publication_assumed": True}
TODAY = date(2026, 10, 21)


def test_publication_date_is_the_default_with_its_lag_printed():
    s = ML.selection_date(SNAP, today=TODAY)
    assert s["date"] == "2026-05-15" and "period end + 45 days (ASSUMED)" in s["source"]
    assert s["dated_today"] is False and s["notes"] == []
    assert ML.selection_date({}, today=TODAY)["date"] is None


def test_typed_wins_then_the_section_11_date():
    s = ML.selection_date(SNAP, "2026-08-01", today=TODAY,
                          pushed={"date": "2026-07-01", "basis": "computed …"})
    assert s["date"] == "2026-08-01" and s["source"] == "typed in the inputs table"
    p = ML.selection_date(SNAP, "", today=TODAY,
                          pushed={"date": "2026-07-01", "basis": "computed earnings base case"})
    assert p["date"] == "2026-07-01" and "pushed from §12" in p["source"]
    t11 = ML.selection_date(SNAP, None, today=TODAY, pushed={"date": "2026-07-01", "basis": "typed"})
    assert "typed in §11" in t11["source"]
    today = ML.selection_date(SNAP, None, today=TODAY, pushed={"date": "2026-10-21", "basis": ""})
    assert today["dated_today"] is True


def test_a_section_11_date_older_than_the_statements_is_not_used():
    s = ML.selection_date(SNAP, None, today=TODAY, pushed={"date": "2025-12-01", "basis": "x"})
    assert s["date"] == "2026-05-15"
    assert any("predates these statements" in n for n in s["notes"])


def test_a_typed_date_before_publication_is_kept_and_flagged():
    s = ML.selection_date(SNAP, "2026-04-10", today=TODAY)
    assert s["date"] == "2026-04-10"
    assert any("not yet public" in n for n in s["notes"])


def test_the_bridge_reads_the_market_to_the_selection_date_never_before_the_period_end():
    assert ML.bridge_end(ML.selection_date(SNAP, today=TODAY)) == "2026-05-15"
    early = ML.selection_date(SNAP, "2026-01-15", today=TODAY)
    assert ML.bridge_end(early) == "2026-03-31"           # bridge row 0, the leg carries it
    assert ML.bridge_end({}) is None


def test_every_constant_is_sourced():
    for d in (ML.ASSUMED, ML.MEASURED):
        for k, v in d.items():
            assert v.get("source"), k
    assert ML.MEASURED["global_delta"]["value"] == {"nasdaq": 0.332, "sp500": 0.286}


# ------------------------------------------------- batch-3 review fix (B4)

def _mv(pct, start, end):
    # the exact keys valuation_context.move_between puts on an OK move
    return {"status": "OK", "pct": pct, "start_date": start, "start": 100.0,
            "end_date": end, "end": 100.0 * (1 + pct / 100.0)}


def test_the_mover_dict_carries_the_dates_windows_match_reads():
    from trading import valuation_context as vc
    s = pd.Series([100.0, 101.0, 102.0],
                  index=pd.to_datetime(["2026-05-14", "2026-07-01", "2026-10-16"]))
    m = vc.move_between(s, "2026-05-15", "2026-10-16")
    assert (m["start_date"], m["end_date"]) == ("2026-05-14", "2026-10-16")


def test_the_sector_leg_needs_the_same_window_as_the_sensex():
    sensex = _mv(-2.0, "2026-05-14", "2026-10-16")
    reg = {"beta_mkt": 0.9, "rel_coef": 1.1}
    kw = dict(sector_is_market=False, reg=reg, rates=RATES)
    # the sector store 10 days behind (a failed refresh) → NOT applied, both windows named
    behind = ML.market_adjust(1000.0, sensex_move=sensex,
                              sector_move=_mv(5.0, "2026-05-14", "2026-10-06"), **kw)
    assert behind["rel_pct"] is None
    assert behind["typed"]["sector_factor"] is None and behind["measured"]["sector_factor"] is None
    assert behind["typed"]["target"] == pytest.approx(1000 * 0.98 * 0.95)
    assert any(n.startswith("sector leg not applied — Sensex 2026-05-14→2026-10-16, the sector "
                            "index 2026-05-14→2026-10-06") for n in behind["notes"])
    named = ML.market_adjust(1000.0, sensex_move=sensex,
                             sector_move=_mv(5.0, "2026-05-14", "2026-10-06"),
                             sector_name="Nifty IT", **kw)
    assert ("sector leg not applied — Sensex 2026-05-14→2026-10-16, Nifty IT "
            "2026-05-14→2026-10-06: the windows differ by 10 day(s) (more than 7)") in named["notes"]
    # identical windows → applied
    same = ML.market_adjust(1000.0, sensex_move=sensex,
                            sector_move=_mv(5.0, "2026-05-14", "2026-10-16"), **kw)
    assert same["rel_pct"] == pytest.approx(7.0)
    assert same["typed"]["sector_factor"] == pytest.approx(1.07)
    assert same["sector_window"] == {"ok": True, "why": ""}
    # 3 days behind → inside the tolerance, applied (the gap is recorded)
    near = ML.market_adjust(1000.0, sensex_move=sensex,
                            sector_move=_mv(5.0, "2026-05-14", "2026-10-13"), **kw)
    assert near["rel_pct"] == pytest.approx(7.0)
    assert near["sector_window"]["ok"] is True and "3 day(s)" in near["sector_window"]["why"]
    assert not any("sector leg not applied" in n for n in near["notes"])
    # a start 8 days apart is a different window too
    start = ML.market_adjust(1000.0, sensex_move=sensex,
                             sector_move=_mv(5.0, "2026-05-22", "2026-10-16"), **kw)
    assert start["rel_pct"] is None
    assert any("the sector index 2026-05-22→2026-10-16" in n and "8 day(s)" in n
               for n in start["notes"])
    assert ML.ASSUMED["sector_window_tol_days"]["value"] == 7


def test_windows_match_compares_both_ends_and_says_when_it_cannot():
    s = _mv(1.0, "2026-05-14", "2026-10-16")
    assert ML.windows_match(s, _mv(1.0, "2026-05-14", "2026-10-09")) == (
        True, "Sensex 2026-05-14→2026-10-16, the sector index 2026-05-14→2026-10-09: the "
              "windows differ by 7 day(s) (within 7)")
    ok, why = ML.windows_match(s, _mv(1.0, "2026-05-14", "2026-10-08"), sector_name="Nifty Bank")
    assert ok is False and why.endswith("8 day(s) (more than 7)") and "Nifty Bank" in why
    assert ML.windows_match(s, _mv(1.0, "2026-05-14", "2026-10-13"), tol_days=2)[0] is False
    assert ML.windows_match(s, {"status": "OK", "pct": 1.0}) == (
        True, "window dates not on the moves — not compared")
