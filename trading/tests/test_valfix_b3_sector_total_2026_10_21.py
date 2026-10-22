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


# ------------------------------------------------- batch-3 review fix (B3)

def _period_row(t, periods, pat=100.0, shares=100):
    fy = {p: {"shares": shares, "pat": pat} for p in periods}
    return {"ticker": t, "shares_outstanding": shares, "pat": pat, "fy": fy}


MARCH = [f"{y}-03-31" for y in (2023, 2024, 2025, 2026)]                 # FY23-FY26
SEP = [f"{y}-09-30" for y in (2023, 2024, 2025, 2026)]                   # FY24-FY27


def _group(sep_periods):
    rows = [_period_row(f"M{i}.NS", MARCH) for i in range(9)]
    rows.append(_period_row("SEPYR.NS", sep_periods))
    closes = {r["ticker"]: {p: 10.0 for p in r["fy"]} for r in rows}
    return rows, closes


def test_an_off_cycle_name_cannot_shrink_the_four_year_window_to_itself():
    # 9 March-year names hold FY23-FY26; one Sep-year name (SIEMENS / ENRIN
    # shape) holds FY24-FY27 — its FY27 arrives before any March-year FY27.
    # The old window took the newest four labels ANYONE held (FY24-FY27)
    # and the panel came out as the Sep name alone.
    rows, closes = _group(SEP)
    h = ST.fy_history(rows, closes, {})
    assert h["labels"] == ["FY23", "FY24", "FY25", "FY26"]
    assert h["panel"] == [f"M{i}.NS" for i in range(9)]
    assert "SEPYR.NS" in h["why"] and "1 of 10" in h["why"]
    assert h["avg"]["pe"] == pytest.approx(10.0)               # 10 × 100 ÷ 100
    assert h["off_cycle"] == ["SEPYR.NS"]
    assert h["quorum"] == 5 and h["n_names"] == 10
    # the Sep name joins when it holds all four of the group's labels
    rows2, closes2 = _group(["2022-09-30"] + SEP)              # FY23-FY27
    h2 = ST.fy_history(rows2, closes2, {})
    assert h2["labels"] == ["FY23", "FY24", "FY25", "FY26"]
    assert len(h2["panel"]) == 10 and h2["off_cycle"] == [] and h2["why"] == ""


def test_the_quorum_is_half_the_group_at_least_two():
    # two names: a label one of them alone holds is not in the window
    rows = [_period_row("A.NS", MARCH), _period_row("B.NS", SEP)]
    closes = {r["ticker"]: {p: 10.0 for p in r["fy"]} for r in rows}
    h = ST.fy_history(rows, closes, {})
    assert h["labels"] == ["FY24", "FY25", "FY26"] and "needs 4" in h["why"]
    assert h["panel"] == [] and "at least 2 of its 2" in h["why"]
    assert [ST._quorum(n) for n in (0, 1, 2, 3, 4, 9, 10, 11)] == [1, 1, 2, 2, 2, 5, 5, 6]


def test_the_block_reads_the_average_when_names_were_left_out_and_names_them():
    # the left-out note is a note, not a failure: the average still prints.
    # The stored FY dict is written out here (the shape fy_history now
    # stores), so this pins block() itself — the old block() read any
    # `why` as "no average"
    rows, _closes = _group(SEP)
    panel = [f"M{i}.NS" for i in range(9)]
    labels = ["FY23", "FY24", "FY25", "FY26"]
    fy = {"labels": labels, "panel": panel, "off_cycle": ["SEPYR.NS"],
          "why": ("1 of 10 priced name(s) left out of the constant panel — not every one of "
                  "FY23/FY24/FY25/FY26 priced (a year-end off the March cycle, or fewer "
                  "years filed): SEPYR.NS"),
          "by_fy": {lab: {"pe": 10.0, "ev_ebitda": None, "pb": None} for lab in labels},
          "avg": {"pe": 10.0, "ev_ebitda": None, "pb": None}}
    prices = {r["ticker"]: 12.0 for r in rows}
    blk = ST.block(rows, prices, "earnings", {"fy": fy, "high": None})
    assert blk["avg"] == pytest.approx(10.0) and blk["panel_now"] == pytest.approx(12.0)
    assert blk["vs_avg_pct"] == pytest.approx(20.0) and blk["panel_n"] == 9
    assert "SEPYR.NS" in blk["fy_why"]
    assert blk.get("off_cycle") == ["SEPYR.NS"]
    # a panel with a loss year gives no average — the reason comes first
    loss = dict(fy, avg={"pe": None, "ev_ebitda": None, "pb": None})
    b2 = ST.block(rows, prices, "earnings", {"fy": loss, "high": None})
    assert b2["avg"] is None and b2["fy_why"].startswith("a year on the 9-name panel has no "
                                                         "positive P/E")
