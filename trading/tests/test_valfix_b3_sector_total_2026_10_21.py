"""VALFIX batch 3 — the sector's TOTAL valuation beside the target (owner
order 2026-10-21: "show beside the target"; study 2's V3). Display only —
it moves no target.

Pins trading/valuation/sector_total.py:
  * total market cap = Σ price × latest-FY shares; aggregate P/E = Σ mcap ÷
    Σ PAT over the names that HAVE a PAT (a missing PAT used to inflate it);
    EV/EBITDA for EV routes, P/B for book routes;
  * a name whose newest FY is older than its group's counts in the cap and
    in no multiple (study 2's NESTLEIND / NATIONALUM rule);
  * the 4-year FY-end average on a constant panel, "now" on the same panel;
  * % below the group's 52-week high on the members that cover the window;
  * the block takes no target and returns none.
"""
from __future__ import annotations

import inspect

import pandas as pd
import pytest

from trading.valuation import sector_total as ST

CR = 1e7


def _row(t, shares, pat, *, ebitda=None, debt=None, cash=None, equity=None, fy=None):
    return {"ticker": t, "shares_outstanding": shares, "pat": pat, "ebitda": ebitda,
            "total_debt": debt, "cash": cash, "equity": equity,
            "fy": fy if fy is not None else {"2026-03-31": {}}}


def test_totals_pair_each_multiple_only_with_names_that_carry_both_sides():
    rows = [_row("A.NS", 100, 1000.0, ebitda=2000.0, debt=500.0, cash=100.0, equity=4000.0),
            _row("B.NS", 50, 500.0, ebitda=None, debt=0.0, cash=0.0, equity=2000.0),
            _row("C.NS", 10, None)]                       # no PAT on file (currency DN)
    px = {"A.NS": 200.0, "B.NS": 100.0, "C.NS": 1000.0}
    t = ST.totals(rows, px)
    assert t["n_priced"] == 3
    assert t["mcap_cr"] == pytest.approx((20000 + 5000 + 10000) / CR)   # C in the cap
    assert t["pe"] == pytest.approx((20000 + 5000) / 1500.0)            # C not in the P/E
    assert t["no_pat"] == ["C.NS"]
    assert t["ev_ebitda"] == pytest.approx((20000 + 400) / 2000.0)      # A only
    assert t["pb"] == pytest.approx((20000 + 5000) / 6000.0)


def test_a_name_a_year_behind_counts_in_the_cap_not_the_multiples():
    rows = [_row("A.NS", 100, 1000.0, fy={"2025-03-31": {}, "2026-03-31": {}}),
            _row("B.NS", 100, 1000.0, fy={"2025-03-31": {}, "2026-03-31": {}}),
            _row("OLD.NS", 100, 10.0, fy={"2024-03-31": {}, "2025-03-31": {}})]
    t = ST.totals(rows, {"A.NS": 10.0, "B.NS": 10.0, "OLD.NS": 10.0})
    assert t["fy"] == "FY26" and t["stale_fy"] == ["OLD.NS"]
    assert t["mcap_cr"] == pytest.approx(3000 / CR)
    assert t["pe"] == pytest.approx(2000 / 2000.0)


def test_fy_labels_put_a_december_year_in_the_next_march_fy():
    assert ST.fy_label("2026-03-31") == "FY26"
    assert ST.fy_label("2025-12-31") == "FY26"
    assert ST.fy_label("2025-06-30") == "FY26"
    assert ST.fy_label("garbage") is None


def _hist_row(t, shares_now, pats):
    fy = {f"{y}-03-31": {"shares": shares_now, "pat": p} for y, p in pats.items()}
    return {"ticker": t, "shares_outstanding": shares_now, "pat": pats[max(pats)], "fy": fy}


def test_the_four_year_average_runs_on_a_constant_panel():
    rows = [_hist_row("A.NS", 100, {2023: 100.0, 2024: 100.0, 2025: 100.0, 2026: 100.0}),
            _hist_row("B.NS", 100, {2023: 100.0, 2024: 100.0, 2025: 100.0, 2026: 100.0}),
            _hist_row("NEW.NS", 100, {2025: 50.0, 2026: 50.0})]       # too young
    closes = {"A.NS": {"2023-03-31": 10.0, "2024-03-31": 20.0, "2025-03-31": 30.0,
                       "2026-03-31": 40.0},
              "B.NS": {"2023-03-31": 10.0, "2024-03-31": 20.0, "2025-03-31": 30.0,
                       "2026-03-31": 40.0},
              "NEW.NS": {"2025-03-31": 99.0, "2026-03-31": 99.0}}
    h = ST.fy_history(rows, closes, {"A.NS": 50.0, "B.NS": 50.0, "NEW.NS": 99.0})
    assert h["labels"] == ["FY23", "FY24", "FY25", "FY26"] and h["panel"] == ["A.NS", "B.NS"]
    assert [h["by_fy"][x]["pe"] for x in h["labels"]] == [10.0, 20.0, 30.0, 40.0]
    assert h["avg"]["pe"] == pytest.approx(25.0)
    assert h["now"]["pe"] == pytest.approx(50.0)                     # same panel, today
    short = ST.fy_history(rows[2:], closes, {})
    assert short["why"] and "needs 4" in short["why"]


def test_the_52_week_high_and_the_block_never_take_a_target():
    idx = pd.bdate_range("2025-09-01", periods=300)
    a = pd.Series(100.0, index=idx)
    a.iloc[150] = 150.0                                  # the group's high day
    late = pd.Series(10.0, index=idx[200:])              # listed inside the window
    hi = ST.high_52w({"A.NS": a, "L.NS": late}, {"A.NS": 1e5, "L.NS": 1e5})
    assert hi["high_cr"] == pytest.approx(150.0 * 1e5 / CR)
    assert hi["high_date"] == idx[150].date().isoformat()
    assert hi["n_in"] == 1 and hi["n_out"] == 1 and hi["members"] == ["A.NS"]
    rows = [_row("A.NS", 1e5, 1e6), _row("L.NS", 1e5, 1e5)]
    blk = ST.block(rows, {"A.NS": 120.0, "L.NS": 10.0}, "earnings",
                   {"fy": None, "high": hi}, "IT services / ER&D")
    assert blk["below_high_pct"] == pytest.approx((120 / 150 - 1) * 100)
    assert blk["multiple"] == "pe" and blk["multiple_label"] == "P/E"
    assert "not built yet" in blk["fy_why"]
    assert "target" not in inspect.signature(ST.block).parameters
    assert not any("target" in k for k in blk)
    assert ST.multiple_key("ev_ebitda") == "ev_ebitda" and ST.multiple_key("book") == "pb"


def test_every_constant_is_sourced():
    for k, v in ST.ASSUMED.items():
        assert v.get("source"), k


def test_a_year_with_no_pat_on_file_stays_out_of_that_years_pe():
    rows = [_hist_row("A.NS", 100, {2023: 100.0, 2024: 100.0, 2025: 100.0, 2026: 100.0}),
            _hist_row("B.NS", 100, {2023: 100.0, 2024: 100.0, 2025: 100.0, 2026: 100.0})]
    rows[1]["fy"]["2024-03-31"]["pat"] = None          # B filed no PAT for FY24
    closes = {t: {f"{y}-03-31": 10.0 for y in (2023, 2024, 2025, 2026)} for t in ("A.NS", "B.NS")}
    h = ST.fy_history(rows, closes, {"A.NS": 10.0, "B.NS": 10.0})
    assert h["panel"] == ["A.NS", "B.NS"]
    assert h["by_fy"]["FY24"]["pe"] == pytest.approx(10.0)   # A alone: 1000 ÷ 100 (old: 20)
    assert h["by_fy"]["FY23"]["pe"] == pytest.approx(10.0)
