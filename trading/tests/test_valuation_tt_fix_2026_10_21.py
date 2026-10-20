"""The owner's Tata sheet faults TT01-TT03 corrected on their order
(2026-10-21, "correct all three"), plus the E101 printed formula (VAL-15).
Money impact NONE — display only.

  * TT01 E22: the extra depreciation + interest (E118) comes off PAT net of
    tax — PAT rises by exactly E118 × D25/100 against the old formula.
  * TT02 E35: borrowings rise by the debt-funded share of the new capex.
  * TT03 E134: a bridge total below −100% gives DATA_NEEDED for the closing
    P/E and the model price, never a negative number.
  * At the owner's own inputs (no extra capex, bridge −0.06%) nothing moves:
    EPS 18.28, model price 690.62 (the cell-for-cell workbook check lives in
    test_valuation_scenarios_2026_10_14.py).
  * E101's printed formula no longer names the sheet's 94.9.
"""
from __future__ import annotations

import json

import pytest

from trading.valuation import is_dn
from trading.valuation import scenarios as sc

# base year with an effective tax rate of exactly 25% (D25 = 200 / 800)
BASE = dict(sc.SHEET_BASE, pat=600.0, tax=200.0)


def _run(drivers=None, assumptions=None, base=None):
    d = dict(sc.SHEET_DRIVERS)
    d.update(drivers or {})
    return sc.fy_scenario(base or BASE, d, sc.SHEET_MARKET, assumptions)


def _capex_case():
    # 100 Cr extra capex, life 5 years, 50% debt at 9%
    return _run({"other_capex": 100.0},
                {"capex_useful_life_years": 5.0, "capex_debt_funded_pct": 50.0,
                 "debt_interest_rate_pct": 9.0})


# ------------------------------------------------------------------ TT01

def test_tt01_extra_cost_comes_off_pat_net_of_tax():
    out = _capex_case()
    c = out["cells"]
    assert out["status"] == "OK", out.get("reason")
    assert c["D25"] == 25.0
    assert c["E117"] == 100.0
    assert c["E118"] == 24.5                       # 100/5 + 100 × 50% × 9%
    pre = c["E19"] * sc.SHEET_DRIVERS["pat_pct_of_ebitda"] / 100
    old_pat = sc.xround(pre - 24.5, 2)             # the sheet: no tax saving
    shield = 24.5 * 25.0 / 100                      # 6.125
    assert c["E22"] == sc.xround(pre - (24.5 - shield), 2)
    assert c["E22"] - old_pat == pytest.approx(shield, abs=0.005 + 1e-9)
    assert c["E22"] > old_pat


def test_tt01_with_a_tax_rate_change_keeps_the_sheet_adjustment():
    out = _run({"other_capex": 100.0, "tax_rate_change_pp": 2.0})
    c = out["cells"]
    pre = c["E19"] * sc.SHEET_DRIVERS["pat_pct_of_ebitda"] / 100
    want = sc.xround((pre - c["E118"] * (1 - 25.0 / 100)) * (1 - 2.0 / (100 - 25.0)), 2)
    assert c["E22"] == want


def test_tt01_no_extra_capex_means_no_change():
    out = _run()
    c = out["cells"]
    assert c["E118"] == 0.0
    pre = c["E19"] * sc.SHEET_DRIVERS["pat_pct_of_ebitda"] / 100
    assert c["E22"] == sc.xround(pre, 2)


# ------------------------------------------------------------------ TT02

def test_tt02_debt_funded_capex_adds_to_borrowings():
    out = _capex_case()
    c = out["cells"]
    assert c["E35"] == pytest.approx(BASE["borrowings"] + 100.0 * 50.0 / 100, abs=1e-9)
    assert c["E37"] == sc.xround(c["E35"] / c["E32"], 2)


def test_tt02_wfh_capex_counts_too_and_debt_share_scales_it():
    out = _run({"other_capex": 100.0, "wfh_shift_pp": 10.0},
               {"capex_debt_funded_pct": 30.0})
    c = out["cells"]
    assert c["E117"] == 115.0                       # 100 + 10/10 × 15
    assert c["E35"] == pytest.approx(BASE["borrowings"] + 115.0 * 0.30, abs=1e-9)


def test_tt02_no_capex_keeps_the_base_borrowings():
    out = _run()
    assert out["cells"]["E35"] == BASE["borrowings"]


# ------------------------------------------------------------------ TT03

def test_tt03_bridge_below_minus_100_is_data_needed_never_negative():
    out = _run({"pe_bridge_extra_pct": -120.0})
    assert out["cells"]["E132"] < -100
    assert is_dn(out["closing_pe"]) and is_dn(out["cells"]["E134"])
    assert is_dn(out["model_price"]) and is_dn(out["cells"]["E135"])
    assert out["status"] == "DATA_NEEDED"
    assert "below −100%" in out["reason"] and "TT03" in out["reason"]
    # nothing anywhere in the cells is a negative price or multiple
    for cell in ("E134", "E135", "E39", "E41"):
        v = out["cells"][cell]
        assert not (isinstance(v, (int, float)) and v < 0), cell
    json.dumps(out)


def test_tt03_through_a_real_sheet_cell_too():
    out = _run({"sector_move_pct": -130.0})          # E72 → E121
    assert out["cells"]["E132"] < -100
    assert is_dn(out["cells"]["E134"]) and is_dn(out["cells"]["E135"])


def test_tt03_bridge_minus_50_is_still_a_number():
    out = _run({"pe_bridge_extra_pct": -50.0})
    c = out["cells"]
    assert out["status"] == "OK"
    assert c["E134"] == sc.xround(c["E133"] * (1 + c["E132"] / 100), 2)
    assert c["E134"] > 0 and c["E135"] > 0


def test_tt03_feeds_the_range_as_missing_not_as_a_price():
    cases = sc.three_cases(BASE, dict(sc.SHEET_DRIVERS, pe_bridge_extra_pct=-95.0),
                           sc.SHEET_MARKET)
    # downside adds −10 points → below −100%; base and upside stay numbers
    assert is_dn(cases["downside"]["closing_pe"])
    assert not is_dn(cases["base"]["closing_pe"])
    rng = sc.justified_range("earnings", cases, price=500.0)
    assert is_dn(rng["per_share"]["downside"])
    assert not is_dn(rng["per_share"]["base"]) and rng["per_share"]["base"] > 0
    assert rng["status"] == "DATA_NEEDED" and is_dn(rng["low"])
    for k in ("low", "base", "high"):
        v = rng[k]
        assert not (isinstance(v, (int, float)) and v < 0), k
    json.dumps(rng)


# ------------------------------------------------------ owner sheet unchanged

def test_owner_sheet_numbers_do_not_move():
    t = sc.fy_scenario(sc.SHEET_BASE, sc.SHEET_DRIVERS, sc.SHEET_MARKET)
    assert t["status"] == "OK"
    assert t["eps"] == 18.28 and t["cells"]["E29"] == 18.28
    assert t["model_price"] == 690.62 and t["cells"]["E135"] == 690.62
    assert t["closing_pe"] == 37.78
    assert t["cells"]["E35"] == sc.SHEET_BASE["borrowings"]


# ------------------------------------------------------- printed formulas

def test_corrected_cells_print_the_new_formula_and_the_order_note():
    cf = {c["cell"]: c["formula"] for c in sc.chain_formulas()}
    assert cf["E22"].startswith("ROUND((E19*E23/100-E118*(1-D25/100))*(1-E102/(100-D25)),2)")
    assert cf["E35"].startswith("ROUND(D35+E117*B154/100,2)")
    assert cf["E134"].startswith('IF(E132<-100,"DATA_NEEDED",ROUND(E133*(1+E132/100),2))')
    for cell, tag in (("E22", "TT01"), ("E35", "TT02"), ("E134", "TT03")):
        assert "corrected on the owner's order 2026-10-21" in cf[cell], cell
        assert tag in cf[cell], cell


def test_e101_text_names_the_base_year_end_rate_not_94_9():
    cf = {c["cell"]: c for c in sc.chain_formulas()}
    assert "94.9" not in cf["E101"]["formula"]
    assert "E78 / USD-INR at the base year-end date" in cf["E101"]["formula"]
    rows = {r["key"]: r for r in sc.fy_scenario(sc.SHEET_BASE, sc.SHEET_DRIVERS,
                                                  sc.SHEET_MARKET)["chain"]}
    assert "94.9" not in rows["rupee_depreciation_pct"]["formula"]
    assert "94.9" not in rows["usdinr_base"]["label"]


def test_e101_row_text_prints_the_rate_actually_used():
    txt = sc.e101_formula(93.4843, "2026-03-31")
    assert "93.48" in txt and "2026-03-31" in txt and "94.9" not in txt
    assert sc.e101_formula(None) == sc.E101_FORMULA
    # the number E101 computes is divided by the input, not by 94.9
    out = sc.fy_scenario(sc.SHEET_BASE, sc.SHEET_DRIVERS,
                         dict(sc.SHEET_MARKET, usdinr=96.74, usdinr_base=93.4843))
    assert out["cells"]["E101"] == sc.xround((96.74 / 93.4843 - 1) * 100, 2)
