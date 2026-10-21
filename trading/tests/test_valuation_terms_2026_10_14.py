"""The 52 finance terms of the task book (sheet "Finance terms",
2026-10-12) — owner order 2026-10-14 ("lets hit the formulas work
completion"). Money impact: NONE.

Pins trading/valuation/terms.py:
  * the registry holds EXACTLY the sheet's 52 names, each with a formula and
    a stage;
  * hand-checked numbers from the committed statements zip (TCS ROE, cash
    conversion, P/E, EV/EBITDA, Graham, net debt/EBITDA; HDFCBANK P/B and
    justified P/B; DLF and SAIL);
  * every term returns a float or DATA_NEEDED and never raises — on an empty
    snapshot, on None, on every real name;
  * the bank-only and lives-elsewhere terms are DATA_NEEDED with their stated
    reason; a bank's WACC and debt/equity are refused, not printed;
  * the plain formulas (CAPM, WACC, NPV, IRR, DCF, DDM, justified P/B,
    Graham, PEG) are the textbook identities;
  * the module is pure.
"""
from __future__ import annotations

import ast
import json
import math
import tokenize
import zipfile
from pathlib import Path

import pytest

from trading.valuation import is_dn
from trading.valuation import snapshot as S
from trading.valuation import terms as T

ROOT = Path(__file__).resolve().parents[2]
MOD = ROOT / "trading" / "valuation" / "terms.py"

SHEET_TERMS = [
    "Revenue growth", "EBITDA / margin", "PAT margin / conversion",
    "Effective tax rate", "Normalised PAT", "Basic EPS", "Diluted EPS",
    "Book value per share (BVPS)", "ROE", "ROA", "ROCE", "ROIC",
    "ROI (of a trade)", "Cost of equity (CAPM)", "WACC", "NPV", "IRR",
    "DCF value", "FCF / FCF yield", "Cash conversion", "Net debt / EBITDA",
    "Debt / equity", "Interest coverage", "Working-capital days",
    "Receivable days", "Asset turnover", "P/E (trailing, forward)", "PEG",
    "Earnings yield", "P/B", "P/S, EV/Sales", "EV", "EV/EBITDA",
    "Dividend yield / payout", "Dividend discount model", "Graham number",
    "Altman Z", "Piotroski F", "Beta", "Sharpe-like momentum",
    "NIM / CASA / GNPA / NNPA / PCR / CAR / credit cost",
    "AUM / spread / ALM", "Embedded value / VNB", "NAV per share",
    "Order book / book-to-bill", "ARPU / SSSG / occupancy / EBITDA per room",
    "Sector relative strength", "Institutional change (pp)", "Delivery %",
    "Promoter holding / pledge", "Impact cost / capacity",
    "Kelly fraction (measured)",
]

TCS_PX = 2156.0
HDFC_PX = 707.25


@pytest.fixture(scope="module")
def tcs():
    return S.snapshot("TCS", price=TCS_PX)


@pytest.fixture(scope="module")
def hdfc():
    return S.snapshot("HDFCBANK", price=HDFC_PX)


# ------------------------------------------------------------- registry
def test_registry_is_exactly_the_52_sheet_terms():
    assert len(T.TERMS) == 52
    assert list(T.TERMS) == SHEET_TERMS
    for name, e in T.TERMS.items():
        assert isinstance(e["formula"], str) and e["formula"].strip(), name
        assert isinstance(e["stage"], str) and e["stage"].strip(), name
        assert isinstance(e["note"], str), name
        assert e["fn"] is None or callable(e["fn"]), name


# --------------------------------------------------------- TCS by hand
def test_tcs_roe_is_pat_over_average_equity(tcs):
    want = 492_100_000_000 / ((1_072_400_000_000 + 947_560_000_000) / 2)
    assert T.compute("ROE", tcs) == pytest.approx(want)
    assert T.compute("ROE", tcs) == pytest.approx(0.4872, abs=1e-4)


def test_tcs_cash_conversion(tcs):
    assert T.compute("Cash conversion", tcs) == pytest.approx(520_940 / 492_100)
    d = T.detail("Cash conversion", tcs)
    assert d["parts"]["flag_below_0_7_three_years"] is False


def test_tcs_pe_on_reported_basic_eps_because_its_ttm_has_a_gap(tcs):
    d = T.detail("P/E (trailing, forward)", tcs, price=TCS_PX)
    assert d["value"] == pytest.approx(2156 / 136.01)
    assert round(d["value"], 2) == 15.85
    assert d["parts"]["eps_basis"] == "reported annual basic EPS"
    assert is_dn(d["parts"]["forward_pe"])
    d2 = T.detail("P/E (trailing, forward)", tcs, price=TCS_PX,
                  market={"forward_eps": 150.0})
    assert d2["parts"]["forward_pe"] == pytest.approx(2156 / 150.0)


def test_tcs_ev_and_ev_to_ebitda(tcs):
    mcap = TCS_PX * 3_618_087_518
    # ONE EV rule (VAL-12a, owner 2026-10-21; reviewer defect 2 found the
    # terms still on the old rule): short-term investments come off net
    # debt — TCS's 'Other Short Term Investments' line is ₹34,968 cr
    sti = tcs["extras"]["short_term_investments"]
    assert sti == pytest.approx(349_680_000_000)
    ev = mcap + 112_830_000_000 - 64_050_000_000 - sti + 12_380_000_000
    assert T.compute("EV", tcs, price=TCS_PX) == pytest.approx(ev)
    assert T.detail("EV", tcs, price=TCS_PX)["parts"]["short_term_investments"] == sti
    v = T.compute("EV/EBITDA", tcs, price=TCS_PX)
    assert v == pytest.approx(ev / 722_740_000_000)
    assert round(v, 3) == 10.394                       # was 10.878 on the old rule
    assert T.detail("EV/EBITDA", tcs, price=TCS_PX)["parts"]["ebitda_source"] \
        == "EBITDA line"


def test_tcs_graham_number_and_bvps(tcs):
    bvps = 1_072_400_000_000 / 3_618_087_518
    assert T.compute("Book value per share (BVPS)", tcs) == pytest.approx(bvps)
    g = T.compute("Graham number", tcs)
    assert g == pytest.approx(math.sqrt(22.5 * 136.01 * bvps))
    assert round(g, 1) == 952.4


def test_tcs_net_debt_to_ebitda(tcs):
    v = T.compute("Net debt / EBITDA", tcs)
    assert v == pytest.approx((112_830_000_000 - 64_050_000_000) / 722_740_000_000)
    assert T.detail("Net debt / EBITDA", tcs)["parts"]["flag_above_3"] is False


def test_tcs_basic_eps_computed_beside_reported(tcs):
    d = T.detail("Basic EPS", tcs)
    assert d["value"] == pytest.approx(492_100_000_000 / 3_618_087_518)
    assert d["parts"]["reported_basic_eps"] == pytest.approx(136.01)
    assert abs(d["parts"]["gap_vs_reported"]) < 1e-4


def test_tcs_altman_z_uses_the_retained_earnings_line(tcs):
    d = T.detail("Altman Z", tcs, price=TCS_PX)
    ta = 1_823_720_000_000
    a = 747_910_000_000 / ta
    b = 998_830_000_000 / ta
    c = 667_140_000_000 / ta
    dd = TCS_PX * 3_618_087_518 / (ta - 1_072_400_000_000)
    e = 2_670_210_000_000 / ta
    assert d["value"] == pytest.approx(1.2 * a + 1.4 * b + 3.3 * c + 0.6 * dd + 1.0 * e)
    assert d["parts"]["B_source"] == "Retained Earnings line"


def test_tcs_piotroski_counts_nine_legs(tcs):
    d = T.detail("Piotroski F", tcs)
    assert len(d["parts"]["legs"]) == 9
    assert d["value"] == float(sum(d["parts"]["legs"].values()))
    assert d["value"] == 5.0


def test_ebitda_falls_back_to_operating_income_plus_d_and_a(tcs):
    snap = json.loads(json.dumps(tcs))
    snap["fields"]["ebitda"] = {"status": "DATA_NEEDED", "reason": "x"}
    d = T.detail("EBITDA / margin", snap)
    assert d["parts"]["ebitda_source"] == "operating_income + d_and_a"
    assert d["value"] == pytest.approx((670_220_000_000 + 55_600_000_000)
                                       / 2_670_210_000_000)


# ---------------------------------------------------------- HDFCBANK
def test_hdfcbank_pb_and_justified_pb(hdfc):
    bvps = 8_167_395_600_000 / 15_393_260_762
    d = T.detail("P/B", hdfc, price=HDFC_PX,
                 market={"roe_pct": 15, "growth_pct": 8, "coe_pct": 12})
    assert d["value"] == pytest.approx(HDFC_PX / bvps)
    assert round(d["value"], 3) == 1.333
    assert d["parts"]["justified_pb"] == pytest.approx(1.75)
    assert T.justified_pb(0.15, 0.08, 0.12) == pytest.approx(1.75)


def test_bank_wacc_and_debt_equity_are_refused(hdfc):
    w = T.compute("WACC", hdfc, price=HDFC_PX, market={"coe_pct": 12})
    assert is_dn(w) and "financial-statement layout" in w["reason"]
    de = T.compute("Debt / equity", hdfc)
    assert is_dn(de) and "blank for banks" in de["reason"]
    # a non-bank keeps its debt/equity
    tcs = S.snapshot("TCS")
    assert T.compute("Debt / equity", tcs) == pytest.approx(
        112_830_000_000 / 1_072_400_000_000)


def test_bank_only_and_lives_elsewhere_terms_say_why(hdfc):
    want = {
        "NIM / CASA / GNPA / NNPA / PCR / CAR / credit cost":
            "bank ratios not in statements; results PDFs / NSE filings",
        "Delivery %": "frozen 2026-08-07, laid off",
        "Impact cost / capacity": "lives in trading.lens_v2",
        "Kelly fraction (measured)": "lives in trading.measured_kelly",
        "NAV per share": "project cash flows not held",
        "Order book / book-to-bill": "from results; news order_win only",
    }
    for term, reason in want.items():
        v = T.compute(term, hdfc, price=HDFC_PX)
        assert is_dn(v) and v["reason"] == reason, term
    for term in ("AUM / spread / ALM", "Embedded value / VNB",
                 "ARPU / SSSG / occupancy / EBITDA per room",
                 "Sharpe-like momentum"):
        assert is_dn(T.compute(term, hdfc)), term
    assert "trading.strategies" in T.compute("Sharpe-like momentum", hdfc)["reason"]


# ------------------------------------------------------------ DLF / SAIL
def test_dlf_roe_and_debt_equity():
    dlf = S.snapshot("DLF")
    assert T.compute("ROE", dlf) == pytest.approx(
        44_146_750_000 / ((454_733_699_000 + 425_502_201_000) / 2))
    assert T.compute("Debt / equity", dlf) == pytest.approx(
        3_058_932_000 / 454_733_699_000)
    # realty: inventory-heavy working capital
    assert T.compute("Working-capital days", dlf) == pytest.approx(
        (8_473_800_000 + 247_174_750_000 - 17_195_938_000) * 365 / 78_376_577_000)


def test_sail_interest_cover_and_net_debt_to_ebitda():
    sail = S.snapshot("SAIL")
    d = T.detail("Interest coverage", sail)
    assert d["value"] == pytest.approx(66_636_600_000 / 21_581_500_000)
    assert d["parts"]["flag_below_2"] is False
    nd = T.detail("Net debt / EBITDA", sail)
    assert nd["value"] == pytest.approx((319_277_100_000 - 286_600_000)
                                        / 126_513_400_000)
    assert nd["parts"]["flag_above_3"] is False


# ---------------------------------------------------------- never raises
@pytest.mark.parametrize("snap", [None, {}, {"fields": {}}, {"fields": {"revenue": 1.0}}])
def test_every_term_is_a_float_or_data_needed_on_an_empty_snapshot(snap):
    out = T.compute_all(snap)
    assert set(out) == set(SHEET_TERMS)
    for term, v in out.items():
        assert isinstance(v, float) or is_dn(v), term
        if is_dn(v):
            assert v["reason"] and "could not compute" not in v["reason"], term


def test_every_term_runs_clean_on_every_name_in_the_zip():
    with zipfile.ZipFile(ROOT / "data" / "fundamentals_yf_statements_2026-08-09.zip") as z:
        syms = sorted({n.split("/")[1].rsplit("_", 1)[0] for n in z.namelist()
                       if n.endswith("_is.csv")})
    assert len(syms) >= 150
    market = {"growth_pct": 8, "coe_pct": 12, "rf_pct": 7, "erp_pct": 5,
              "beta": 1.1, "rate_pct": 9, "cash_flows": [10, 10, 110],
              "outlay": 95, "fcf_forecast": [1e9, 1.1e9, 1.2e9]}
    for sym in syms:
        snap = S.snapshot(sym, price=500.0)
        out = T.detail_all(snap, price=500.0, market=market)
        json.dumps(out, allow_nan=False)
        for term, d in out.items():
            v = d["value"]
            assert isinstance(v, float) or is_dn(v), (sym, term)
            if is_dn(v):
                assert "could not compute" not in v["reason"], (sym, term, v)


def test_missing_inputs_are_named_with_the_snapshot_reason(hdfc):
    v = T.compute("Interest coverage", hdfc)
    assert is_dn(v)
    assert "ebit" in v["missing"]
    assert "not in statements for HDFCBANK" in v["reason"]
    assert is_dn(T.compute("P/E (trailing, forward)", hdfc))     # no price
    assert "price" in T.compute("P/E (trailing, forward)", hdfc)["reason"]


# ------------------------------------------------------- plain formulas
def test_plain_formulas_are_the_textbook_identities():
    assert T.capm(0.07, 1.2, 0.05) == pytest.approx(0.13)
    assert T.wacc(60.0, 40.0, 0.13, 0.09, 0.25) == pytest.approx(
        0.6 * 0.13 + 0.4 * 0.09 * 0.75)
    assert T.wacc(60.0, 0.0, 0.13, None, 0.25) == pytest.approx(0.13)
    assert T.npv(0.10, [10, 10, 110], 100) == pytest.approx(0.0, abs=1e-9)
    assert T.irr([10, 10, 110], 100) == pytest.approx(0.10, abs=1e-7)
    assert is_dn(T.irr([-10, -10], 100))                  # no sign change
    assert T.ddm(10.0, 0.05, 0.12) == pytest.approx(10.5 / 0.07)
    assert is_dn(T.ddm(10.0, 0.12, 0.12))
    assert T.trade_roi(100.0, 120.0, 1.0) == pytest.approx(0.19)
    assert T.peg(20.0, 10.0) == pytest.approx(2.0)
    assert is_dn(T.peg(20.0, -5.0))
    assert is_dn(T.graham_number(-1.0, 100.0))
    assert T.growth(110.0, 100.0) == pytest.approx(0.10)
    assert is_dn(T.growth(110.0, -100.0))
    assert is_dn(T.ratio(1.0, None))


def test_dcf_discounts_the_terminal_value_and_refuses_g_above_the_cap():
    fcf = [100.0, 110.0]
    w, g = 0.10, 0.05
    tv = 110.0 * 1.05 / 0.05
    want = 100 / 1.1 + 110 / 1.1 ** 2 + tv / 1.1 ** 2
    assert T.dcf_value(fcf, w, g) == pytest.approx(want)
    over = T.dcf_value(fcf, 0.15, 0.11)
    assert is_dn(over) and "nominal-GDP cap" in over["reason"]
    assert is_dn(T.dcf_value(fcf, 0.05, 0.06))
    assert is_dn(T.dcf_value([], 0.10, 0.05))
    assert T.ASSUMED["dcf_growth_cap_pct"] == 10.0
    assert "ASSUMED" in T.ASSUMED_SOURCE["dcf_growth_cap_pct"]


def test_market_terms_read_their_inputs_and_refuse_without_them(tcs):
    assert is_dn(T.compute("Beta", tcs))
    assert T.compute("Beta", tcs, market={"beta": 0.8}) == pytest.approx(0.8)
    assert T.compute("Cost of equity (CAPM)", tcs,
                     market={"rf_pct": 7, "beta": 1.0, "erp_pct": 5}) == pytest.approx(0.12)
    assert T.compute("ROI (of a trade)", tcs,
                     market={"trade": {"entry": 100, "exit": 110, "costs": 0.5}}) \
        == pytest.approx(0.095)
    assert is_dn(T.compute("ROI (of a trade)", tcs))
    assert T.compute("Institutional change (pp)", tcs,
                     market={"inst_pct_now": 30.5, "inst_pct_prev": 29.0}) \
        == pytest.approx(1.5)
    closes = [100.0] * 10 + [100.0 + i for i in range(22)]
    sector = [100.0] * 32
    rs = T.detail("Sector relative strength", tcs,
                  market={"stock_series": closes, "sector_series": sector})
    assert rs["value"] == pytest.approx(121.0 / 100.0 - 1.0)
    assert is_dn(rs["parts"]["stock_minus_market_1m"])


# ------------------------------------------------------------ purity
def _code_tokens(path: Path) -> str:
    out = []
    with open(path, "rb") as f:
        for tok in tokenize.tokenize(f.readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING, tokenize.ENCODING):
                continue
            out.append(tok.string)
    return " ".join(out)


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            names.add(mod)
            names.update(f"{mod}.{a.name}" for a in node.names)
    return names


@pytest.mark.parametrize("path", [MOD, ROOT / "trading" / "valuation" / "snapshot.py"])
def test_both_valuation_modules_are_pure(path):
    code = _code_tokens(path)
    for bad in ("httpx", "requests", "urllib", "yfinance", "web .",
                "__import__", "importlib"):
        assert bad not in code, (path.name, bad)
    for name in _imports(path):
        parts = set(name.split("."))
        assert not parts & {"httpx", "requests", "urllib", "yfinance", "web"}, name
        assert not parts & {"engine", "risk", "orders", "danger"}, name


def test_terms_imports_nothing_but_the_package_and_the_stdlib():
    stdlib = {"__future__", "logging", "math", "typing", "datetime"}
    names = _imports(MOD)
    assert "trading.valuation" in names            # the walk saw the imports
    for name in names:
        assert name.split(".")[0] in stdlib or name.startswith("trading.valuation"), name
