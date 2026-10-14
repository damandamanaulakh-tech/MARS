"""Valuation chain stages 15-17 — scenarios → scenario EPS → justified range
(owner order 2026-10-14 "lets hit the formulas work completion"; money
impact NONE, display only).

Pins trading/valuation/scenarios.py:
  * REPRODUCTION — fy_scenario run on the owner's Tata Technologies sheet
    inputs matches the sheet's own cached FY27E cells (the committed xlsx
    research/valuation/Tata_Technologies_IPO_valuation_model_2026-10-09.xlsx);
  * with a neutral market and zero cost drivers, EPS is the plain
    growth × margin × conversion arithmetic;
  * three cases order downside ≤ base ≤ upside on EPS and price;
  * book / EV / NAV / unheld routes give the hand-worked answers or
    DATA_NEEDED with the reason;
  * the sensitivity grid is re-run cell by cell, centre = base price;
  * a missing input is DATA_NEEDED naming it, never a raise, never a 0;
  * every output survives a JSON round trip;
  * the module is pure (openpyxl lives only in this TEST).
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from trading import valuation_context as vc
from trading.valuation import is_dn
from trading.valuation import scenarios as sc

ROOT = Path(__file__).resolve().parents[2]
XLSX = ROOT / "research" / "valuation" / "Tata_Technologies_IPO_valuation_model_2026-10-09.xlsx"
MODULE = ROOT / "trading" / "valuation" / "scenarios.py"


def _sheet():
    openpyxl = pytest.importorskip("openpyxl")
    assert XLSX.exists(), "the owner's sheet must be committed as the fixture"
    return openpyxl.load_workbook(XLSX, data_only=True).active


def _inputs_from_workbook(ws):
    """Read every input through INPUT_CELLS; a None cell is a constant
    inside a formula (usdinr_base 94.9, the case points) → module value."""
    defaults = {"base": sc.SHEET_BASE, "drivers": sc.SHEET_DRIVERS,
                "market": sc.SHEET_MARKET,
                "assumptions": {k: v["value"] for k, v in sc.SHEET_ASSUMPTIONS.items()}}
    out = {}
    for grp, cells in sc.INPUT_CELLS.items():
        out[grp] = {k: (ws[c].value if c else defaults[grp][k]) for k, c in cells.items()}
    return out


@pytest.fixture(scope="module")
def tata():
    return sc.fy_scenario(sc.SHEET_BASE, sc.SHEET_DRIVERS, sc.SHEET_MARKET)


@pytest.fixture(scope="module")
def cases():
    return sc.three_cases(sc.SHEET_BASE, sc.SHEET_DRIVERS, sc.SHEET_MARKET)


# --------------------------------------------------------- (1) reproduction

PINNED = ("E17", "E19", "E22", "E29", "E116", "E132", "E134", "E135", "B61", "B70")
PRICES = ("E135", "B61")   # per-share price / price ratio: tolerance 0.05


def test_the_module_constants_are_the_owner_sheet_inputs():
    ws = _sheet()
    got = _inputs_from_workbook(ws)
    for k, v in got["base"].items():
        assert sc.SHEET_BASE[k] == v, k
    for k, v in got["drivers"].items():
        assert sc.SHEET_DRIVERS[k] == v, k
    for k, v in got["market"].items():
        assert sc.SHEET_MARKET[k] == v, k
    for k, v in got["assumptions"].items():
        assert sc.SHEET_ASSUMPTIONS[k]["value"] == v, k
    # all 29 sensitivities are typed "Assumption" in the sheet's column F
    assert len(sc.SHEET_ASSUMPTIONS) == 29
    for name, a in sc.SHEET_ASSUMPTIONS.items():
        row = int(a["cell"][1:])
        assert 140 <= row <= 168
        assert ws[f"F{row}"].value == "Assumption", name
        assert a["source"] == f"owner sheet 2026-10-09, row {row}"


def test_fy_scenario_reproduces_the_sheet_cached_values():
    ws = _sheet()
    if all(ws[c].value is None for c in PINNED):
        pytest.skip("sheet cache empty — the task-book pin below carries the check")
    got = _inputs_from_workbook(ws)
    out = sc.fy_scenario(got["base"], got["drivers"], got["market"], got["assumptions"])
    assert out["status"] == "OK", out.get("reason")
    for cell in PINNED:
        want = ws[cell].value
        tol = 0.05 if cell in PRICES else 0.01
        assert out["cells"][cell] == pytest.approx(want, abs=tol), cell


def test_every_computed_cell_matches_the_sheet_cache_exactly(tata):
    ws = _sheet()
    if ws["E135"].value is None:
        pytest.skip("sheet cache empty")
    compared = 0
    for cell, val in tata["cells"].items():
        want = ws[cell].value
        if isinstance(val, (int, float)) and isinstance(want, (int, float)):
            assert val == pytest.approx(want, abs=1e-9), cell
            compared += 1
        else:
            assert val == want, cell
    assert compared >= 150


def test_task_book_figures_eps_18_28_and_model_price_690_62_hold_without_the_cache(tata):
    # the figures recorded in the 2026-10-12 task book from the sheet — they
    # pin the chain even if the xlsx were ever saved without a recalculation
    assert tata["eps"] == 18.28 and tata["cells"]["E29"] == 18.28
    assert tata["model_price"] == 690.62 and tata["cells"]["E135"] == 690.62
    assert tata["closing_pe"] == 37.78 and tata["opening_pe"] == 37.8
    assert tata["forward_pe_at_latest"] == 39.2
    assert tata["model_vs_latest_pct"] == -3.5


def test_section_h_is_labelled_the_owners_additive_whatif_and_every_factor_shows(tata):
    assert tata["pe_bridge_additive_whatif"] is True
    pb = tata["pe_bridge"]
    assert pb["additive_whatif"] is True
    assert [f["cell"] for f in pb["factors"]] == [f"E{r}" for r in range(120, 132)]
    assert sum(f["pct"] for f in pb["factors"]) == pytest.approx(pb["sheet_sum_pct"], abs=0.01)
    assert pb["total_pct"] == -0.06 and pb["case_extra_pct"] == 0.0
    # readable names and sheet cells are both present, keys unique
    keys = [r["key"] for r in tata["chain"]]
    assert len(keys) == len(set(keys))
    assert tata["cells"]["E29"] == tata["eps"]


def test_xround_is_excel_half_away_from_zero():
    assert sc.xround(2.675, 2) == 2.68          # Python round() gives 2.67
    assert sc.xround(-2.675, 2) == -2.68
    assert sc.xround(0.125, 2) == 0.13
    assert sc.xround(37.75, 1) == 37.8
    assert sc.xround(28038.5, 0) == 28039.0


# ------------------------------------------------- (2) arithmetic baseline

def _neutral_market():
    m = dict(sc.SHEET_MARKET)
    m.update({"sensex_end": m["sensex_start"], "nifty_end": m["nifty_start"],
              "repo_end": m["repo_start"], "cpi": 4.0, "gdp": 6.5,
              "brent_end": m["brent_start"], "usdinr": m["usdinr_base"],
              "sentiment": 0.0, "inst_holding_end": m["inst_holding_start"],
              "delivery_pct": 45.0, "chart_score": 0.0, "event_pct": 0.0,
              "gap_close_pct": 0.0})
    return m


def test_zero_drivers_and_no_market_moves_reduce_eps_to_growth_margin_conversion():
    d = {k: 0.0 for k in sc.SHEET_DRIVERS}
    d.update({"revenue_growth_pct": 20.0, "base_ebitda_margin_pct": 16.1,
              "pat_pct_of_ebitda": 67.6, "payout_pct": 80.0, "net_cash": 880.0})
    out = sc.fy_scenario(sc.SHEET_BASE, d, _neutral_market())
    x = sc.xround
    rev = x(5505.57 * (1 + 20 / 100), 2)
    ebitda = x(rev * 16.1 / 100, 2)
    pat = x(ebitda * 67.6 / 100, 2)
    eps = x(pat / 40.6, 2)
    assert (rev, ebitda, pat, eps) == (6606.68, 1063.68, 719.05, 17.71)
    assert out["revenue"] == rev and out["ebitda"] == ebitda
    assert out["net_cost_impact"] == 0.0 and out["pat"] == pat
    assert out["eps"] == 17.71
    assert out["pe_total_impact_pct"] == 0.0
    assert out["closing_pe"] == out["opening_pe"] == 37.8
    assert out["model_price"] == 669.44          # 17.71 × 37.8


# ---------------------------------------------------------- (3) three cases

def test_three_cases_order_downside_base_upside(cases):
    assert set(cases) == {"downside", "base", "upside"}
    for key in ("eps", "model_price", "revenue", "ebitda", "closing_pe"):
        d, b, u = (cases[c][key] for c in ("downside", "base", "upside"))
        assert d <= b <= u, key
    # the base case IS the sheet's chain; the deltas are printed on each case
    assert cases["base"]["eps"] == 18.28 and cases["base"]["model_price"] == 690.62
    assert cases["downside"]["case_deltas"] == sc.CASE_DELTAS["downside"]
    assert cases["upside"]["case"] == "upside"
    assert (cases["downside"]["eps"], cases["downside"]["model_price"]) == (16.46, 559.64)
    assert (cases["upside"]["eps"], cases["upside"]["model_price"]) == (20.18, 838.68)
    assert cases["downside"]["pe_bridge"]["case_extra_pct"] == -10.0


def test_earnings_range_without_a_band_uses_the_case_pe_and_says_so(cases):
    r = sc.justified_range("earnings", cases, price=715.85)
    assert r["status"] == "OK"
    assert (r["low"], r["base"], r["high"]) == (559.64, 690.62, 838.68)
    assert r["multiple_band"]["source"].startswith("no peer/own band: case P/E only")
    assert r["per_share"]["base"] == cases["base"]["model_price"]
    assert r["vs_price_pct"]["base"] == pytest.approx((690.62 / 715.85 - 1) * 100, abs=0.01)
    assert r["bridge"]["pe_bridge_additive_whatif"] is True


def test_earnings_range_with_peer_and_own_history_band(cases):
    r = sc.justified_range("earnings", cases, price=715.85, peer_multiple=28.5,
                           own_multiple_history=[61.0, 40.5, 37.8, 52.2, 45.0])
    band = r["multiple_band"]
    assert band["low"] == 28.5 and band["high"] == 61.0
    assert band["mid"] == pytest.approx((28.5 + 45.0) / 2)
    assert r["low"] == sc.xround(16.46 * 28.5, 2)
    assert r["base"] == sc.xround(18.28 * band["mid"], 2)
    assert r["high"] == sc.xround(20.18 * 61.0, 2)
    assert r["low"] <= r["base"] <= r["high"]


def test_an_inverted_case_is_sorted_and_noted():
    r = sc.justified_range("earnings", {}, price=None,
                           per_share_overrides={"downside": 30.0, "base": 20.0, "upside": 10.0},
                           own_multiple_history=[10.0, 15.0, 20.0])
    assert r["status"] == "OK"
    assert r["low"] <= r["base"] <= r["high"]
    assert any("sorted" in n for n in r["notes"])


# --------------------------------------------------------- (4) book route

def test_book_route_justified_pb():
    r = sc.justified_range("book", {}, price=150.0, roe_pct=15.0, bvps=100.0,
                           rf_pct=7.0, beta=1.0, erp_pct=5.0, growth_pct=8.0)
    assert r["status"] == "OK"
    assert r["bridge"]["coe_pct"] == 12.0
    assert r["base"] == 175.0                      # (15−8)/(12−8) × 100
    assert (r["low"], r["high"]) == (125.0, 225.0)  # ROE ∓ 2pp
    assert r["multiple_band"]["mid"] == 1.75


def test_book_route_defaults_read_valuation_context_by_name():
    assert sc.ASSUMED["equity_risk_premium_pct"]["value"] == vc.ASSUMED["equity_risk_premium_pct"]
    assert sc.ASSUMED["long_run_growth_pct"]["value"] == vc.ASSUMED["long_run_growth_pct"]
    r = sc.justified_range("book", {}, price=None, roe_pct=15.0, bvps=100.0,
                           rf_pct=7.0, beta=1.0)      # ERP 5, g 8 by default
    assert r["base"] == 175.0


def test_book_route_missing_rf_or_beta_is_data_needed_naming_it():
    r = sc.justified_range("book", {}, price=None, roe_pct=15.0, bvps=100.0, beta=1.0)
    assert r["status"] == "DATA_NEEDED"
    assert is_dn(r["base"]) and "rf_pct" in r["reason"]
    r2 = sc.justified_range("book", {}, price=None, roe_pct=15.0, rf_pct=7.0, beta=1.0)
    # no BVPS anywhere (no cases either)
    assert r2["status"] == "DATA_NEEDED" and "bvps" in r2["reason"]


# ------------------------------------------------------------- (5) EV route

def test_ev_ebitda_bridge_hand_example():
    r = sc.justified_range("ev_ebitda", {}, price=400.0, peer_multiple=10.0,
                           own_multiple_history=[8.0, 12.0, 9.0],
                           per_share_overrides={"downside": 40.0, "base": 50.0, "upside": 60.0},
                           net_debt_per_share=20.0, minorities_per_share=5.0)
    assert r["status"] == "OK"
    band = r["multiple_band"]
    assert (band["low"], band["mid"], band["high"]) == (8.0, 9.5, 12.0)
    # EV − net debt − minorities
    assert (r["low"], r["base"], r["high"]) == (295.0, 450.0, 695.0)
    assert r["bridge"]["ev_per_share"]["base"] == 475.0
    assert any("MID-CYCLE" in n for n in r["notes"])


def test_ev_ebitda_without_a_band_or_minorities_is_data_needed():
    r = sc.justified_range("ev_ebitda", {}, price=None,
                           per_share_overrides={"downside": 40.0, "base": 50.0, "upside": 60.0},
                           net_debt_per_share=20.0, minorities_per_share=0.0)
    assert r["status"] == "DATA_NEEDED" and "no EV/EBITDA band" in r["reason"]
    r2 = sc.justified_range("ev_ebitda", {}, price=None, peer_multiple=10.0,
                            per_share_overrides={"downside": 40.0, "base": 50.0, "upside": 60.0},
                            net_debt_per_share=20.0)
    assert r2["status"] == "DATA_NEEDED" and "minorities_per_share" in r2["reason"]


def test_ev_routes_read_the_cases_and_the_case_net_cash(cases):
    r = sc.justified_range("ev_ebitda", cases, price=715.85, peer_multiple=24.0,
                           minorities_per_share=0.0)
    assert r["status"] == "OK"
    nd = -cases["base"]["net_cash"] / cases["base"]["shares"]
    assert r["bridge"]["net_debt_per_share"] == pytest.approx(nd)
    ebitda_ps = cases["base"]["ebitda"] / cases["base"]["shares"]
    assert r["base"] == sc.xround(ebitda_ps * 24.0 - nd, 2)
    assert "single multiple" in r["multiple_band"]["source"]
    s = sc.justified_range("ev_sales", cases, price=715.85, peer_multiple=4.0,
                           minorities_per_share=0.0)
    assert s["status"] == "OK" and s["low"] <= s["base"] <= s["high"]


# ------------------------------------------------- (6) NAV and unheld routes

def test_nav_is_data_needed_with_the_pb_fallback():
    r = sc.justified_range("nav", {}, price=150.0, roe_pct=15.0, bvps=100.0,
                           rf_pct=7.0, beta=1.0)
    assert r["status"] == "DATA_NEEDED"
    assert "project-level NAV not held; P/B shown as fallback" in r["reason"]
    fb = r["fallback"]
    assert fb["family"] == "book" and fb["status"] == "OK" and fb["base"] == 175.0
    r2 = sc.justified_range("nav", {}, price=None)
    assert r2["status"] == "DATA_NEEDED" and r2["fallback"] is None


@pytest.mark.parametrize("fam,word", [("embedded_value", "embedded value"),
                                      ("sotp", "sum-of-parts")])
def test_unheld_routes_say_why(fam, word):
    r = sc.justified_range(fam, {}, price=100.0)
    assert r["status"] == "DATA_NEEDED" and word in r["reason"]
    assert all(is_dn(r[k]) for k in ("low", "base", "high"))


def test_unknown_family_is_data_needed_not_a_raise():
    r = sc.justified_range("astrology", {}, price=100.0)
    assert r["status"] == "DATA_NEEDED" and "unknown valuation family" in r["reason"]


# ------------------------------------------------------ (7) sensitivity grid

def test_sensitivity_grid_is_rerun_cell_by_cell(cases):
    r = sc.justified_range("earnings", cases, price=715.85)
    g = r["sensitivity"]
    assert g["status"] == "OK"
    assert len(g["prices"]) == 3 and all(len(row) == 3 for row in g["prices"])
    assert g["centre"] == g["prices"][1][1] == r["base"] == cases["base"]["model_price"]
    assert [m["multiple_pct"] for m in g["multiple_row"]] == [-10.0, 0.0, 10.0]
    assert g["multiple_row"][1]["price"] == r["base"]
    # an off-centre cell IS a fresh fy_scenario run (growth +1pp, margin −1pp)
    d = dict(sc.SHEET_DRIVERS, revenue_growth_pct=21.0, base_ebitda_margin_pct=15.1)
    direct = sc.fy_scenario(sc.SHEET_BASE, d, sc.SHEET_MARKET)["model_price"]
    assert g["prices"][2][0] == direct
    # grid monotone in both directions
    for row in g["prices"]:
        assert row[0] < row[1] < row[2]
    assert g["prices"][0][1] < g["prices"][1][1] < g["prices"][2][1]
    lo, mid, hi = (m["price"] for m in g["multiple_row"])
    assert lo < mid < hi


def test_grid_without_case_inputs_is_data_needed():
    r = sc.justified_range("earnings", {}, price=None,
                           per_share_overrides={"downside": 9.0, "base": 10.0, "upside": 11.0},
                           own_multiple_history=[10.0, 20.0, 30.0])
    assert r["status"] == "OK"
    assert is_dn(r["sensitivity"])


# ------------------------------------------------ (8) missing input handling

def test_a_missing_driver_is_data_needed_naming_it_and_flows_downstream():
    d = dict(sc.SHEET_DRIVERS)
    del d["revenue_growth_pct"]
    out = sc.fy_scenario(sc.SHEET_BASE, d, sc.SHEET_MARKET)
    assert out["status"] == "DATA_NEEDED"
    assert out["data_needed"] == ["drivers.revenue_growth_pct"]
    for key in ("revenue", "ebitda", "pat", "eps", "model_price", "model_market_cap"):
        assert is_dn(out[key]) and "revenue_growth_pct" in out[key]["reason"], key
    assert is_dn(out["model_price"]) and is_dn(out["cells"]["E135"])
    # rows that do not depend on it still compute
    assert out["fy0_tax_rate_pct"] == 28.5 and out["fy0_eps"] == 13.46
    assert out["pe_market"] == 0.79 and out["closing_pe"] == 37.78
    assert "revenue_growth_pct" in out["reason"]


def test_a_missing_market_input_hits_only_the_bridge():
    m = dict(sc.SHEET_MARKET)
    m["cpi"] = None
    out = sc.fy_scenario(sc.SHEET_BASE, sc.SHEET_DRIVERS, m)
    assert out["eps"] == 18.28                      # earnings chain untouched
    assert is_dn(out["pe_inflation"]) and "market.cpi" in out["pe_inflation"]["reason"]
    assert is_dn(out["closing_pe"]) and is_dn(out["model_price"])
    assert out["data_needed"] == ["market.cpi"]


def test_optional_inputs_missing_do_not_fail_the_scenario():
    b = {k: v for k, v in sc.SHEET_BASE.items() if k not in ("prior_revenue", "ipo_price")}
    m = {k: v for k, v in sc.SHEET_MARKET.items() if k != "latest_price"}
    out = sc.fy_scenario(b, sc.SHEET_DRIVERS, m)
    assert out["status"] == "OK" and out["model_price"] == 690.62
    assert is_dn(out["return_vs_ipo_pct"]) and is_dn(out["forward_pe_at_latest"])
    assert "base.ipo_price" in out["optional_missing"]


def test_garbage_inputs_never_raise():
    for args in ((None, None, None), ({}, {}, {}), ({"revenue": "x"}, {"payout_pct": "nan"}, [])):
        out = sc.fy_scenario(*args)
        assert out["status"] == "DATA_NEEDED"
        assert is_dn(out["eps"])
    c = sc.three_cases(None, None, None)
    r = sc.justified_range("earnings", c, price=None)
    assert r["status"] == "DATA_NEEDED"
    zero = dict(sc.SHEET_BASE, shares=0.0)
    out = sc.fy_scenario(zero, sc.SHEET_DRIVERS, sc.SHEET_MARKET)
    assert is_dn(out["fy0_eps"]) and "division by zero" in out["fy0_eps"]["reason"]


# -------------------------------------------------------- (9) JSON round trip

def test_every_output_survives_a_json_round_trip(tata, cases):
    outs = [tata, cases,
            sc.justified_range("earnings", cases, price=715.85),
            sc.justified_range("book", cases, price=715.85, rf_pct=7.0, beta=1.0),
            sc.justified_range("ev_ebitda", cases, price=715.85, peer_multiple=24.0,
                               minorities_per_share=0.0),
            sc.justified_range("ev_sales", cases, price=715.85),
            sc.justified_range("nav", cases, price=715.85, rf_pct=7.0, beta=1.0),
            sc.justified_range("embedded_value", cases, price=715.85),
            sc.justified_range("sotp", cases, price=715.85),
            sc.ASSUMED, sc.SHEET_ASSUMPTIONS]
    for o in outs:
        text = json.dumps(o, allow_nan=False)
        assert json.loads(text) == o


# ------------------------------------------------------------------- purity

def _operative_source(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(getattr(body[0], "value", None), ast.Constant) and \
                    isinstance(body[0].value.value, str):
                spans.append((body[0].lineno, body[0].end_lineno or body[0].lineno))
    keep = []
    for i, line in enumerate(text.splitlines(), start=1):
        if any(a <= i <= b for a, b in spans) or line.strip().startswith("#"):
            continue
        keep.append(line.split("  #")[0])
    return "\n".join(keep)


def test_scenarios_module_is_pure():
    src = _operative_source(MODULE)
    assert not re.search(r"\bimport\s+(httpx|requests|urllib|yfinance|openpyxl)\b", src)
    assert not re.search(r"\bfrom\s+(httpx|requests|urllib|yfinance|openpyxl)\b", src)
    assert not re.search(r"^\s*(from|import)\s+web\b", src, re.M)
    assert not re.search(r"^\s*(from|import)\s+[\w.]*\b(engine|risk|orders|danger)\b",
                         src, re.M)
    assert not re.search(r"^\s*from\s+trading\s+import\s+.*\b(engine|risk|orders|danger)\b",
                         src, re.M)
