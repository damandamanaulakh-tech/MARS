"""Stages 11-12 — normalised earnings + earnings quality / balance sheet
(trading/valuation/normalise.py). Owner order 2026-10-14 "lets hit the
formulas work completion"; money impact NONE (display only).

Pins: the TCS FY2026 reproduction of Yahoo's own Normalized Income (exact
tax-effect path to the rupee, estimated-at-effective-rate path within 2%);
a positive one-off lowers normalised PAT; TCS quality (cash conversion ≈
1.059, net cash → no stress flag); SAIL computes leverage + cover; a lender
gets DATA_NEEDED for the ratios that mean nothing for a bank, and so does
its score; an empty snapshot raises nothing and returns DATA_NEEDED; every
result round-trips as JSON; the module is pure.
"""
from __future__ import annotations

import ast
import copy
import json
import math
import re
from pathlib import Path

import pytest

from trading.valuation import dn, is_dn, num
from trading.valuation import normalise as N

ROOT = Path(__file__).resolve().parents[2]

TCS_YAHOO_NORMALIZED = 519_741_640_000.0


# --------------------------------------------------------------------------
# A snapshot built from the committed statements zip (no sibling module)
# --------------------------------------------------------------------------
_MAP = {
    "net_income_common": [("is", "Net Income Common Stockholders")],
    "unusual_items": [("is", "Total Unusual Items")],
    "special_income_charges": [("is", "Special Income Charges")],
    "normalized_income_yahoo": [("is", "Normalized Income")],
    "basic_shares_avg": [("is", "Basic Average Shares")],
    "diluted_shares_avg": [("is", "Diluted Average Shares")],
    "shares_outstanding": [("bs", "Ordinary Shares Number")],
    "ocf": [("cf", "Operating Cash Flow")],
    "fcf": [("cf", "Free Cash Flow")],
    "revenue": [("is", "Total Revenue")],
    "ebitda": [("is", "EBITDA")],
    "ebit": [("is", "EBIT")],
    "total_debt": [("bs", "Total Debt")],
    "cash": [("bs", "Cash And Cash Equivalents"), ("cf", "End Cash Position")],
    "equity": [("bs", "Stockholders Equity")],
    "receivables": [("bs", "Accounts Receivable"), ("bs", "Receivables")],
    "inventory": [("bs", "Inventory")],
    "payables": [("bs", "Accounts Payable"), ("bs", "Payables")],
    "interest_expense": [("is", "Interest Expense")],
    "tax": [("is", "Tax Provision")],
    "pretax_income": [("is", "Pretax Income")],
    "total_assets": [("bs", "Total Assets")],
    "buybacks": [("cf", "Repurchase Of Capital Stock")],
}
_EXTRA = {"tax_effect_unusual": ("is", "Tax Effect Of Unusual Items"),
          "restructuring": ("is", "Restructuring And Mergern Acquisition"),
          "write_off": ("is", "Write Off"),
          "short_term_investments": ("bs", "Other Short Term Investments")}


def _ser(st, kind, line):
    df = st.get(kind)
    if df is None or line not in df.index:
        return {}
    out = {}
    for c, v in df.loc[line].items():
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if not math.isnan(f):
            out[str(c)[:10]] = f
    return out


def _snap_from_zip(ticker):
    from trading.register_v4 import load_statements
    st = load_statements(ticker)
    if not st or "is" not in st:
        return {"ticker": ticker, "period_end": None, "currency": "INR",
                "fields": {}, "history": {}, "data_needed": {}}
    period = max(str(c)[:10] for c in st["is"].columns)
    fields, hist, need = {}, {}, {}
    for name, srcs in _MAP.items():
        for kind, line in srcs:
            s = _ser(st, kind, line)
            if s:
                hist[name] = s
                fields[name] = s.get(period, dn(f"{line} has no {period} value"))
                break
        else:
            fields[name] = need[name] = dn("not in statements zip")
    extra = {name: _ser(st, kind, line) for name, (kind, line) in _EXTRA.items()
             if _ser(st, kind, line)}
    return {"ticker": ticker, "period_end": period, "currency": "INR",
            "fields": fields, "history": hist, "data_needed": need, "extra": extra}


@pytest.fixture(scope="module")
def tcs():
    return _snap_from_zip("TCS")


# --------------------------------------------------------------------------
# Stage 11 — normalised earnings
# --------------------------------------------------------------------------
def test_tcs_fy2026_lines_are_the_ones_the_sign_check_names(tcs):
    f = tcs["fields"]
    assert tcs["period_end"] == "2026-03-31"
    assert f["net_income_common"] == 492_100_000_000
    assert f["unusual_items"] == -36_660_000_000
    assert tcs["extra"]["tax_effect_unusual"]["2026-03-31"] == pytest.approx(-9_018_360_000)
    assert f["normalized_income_yahoo"] == pytest.approx(TCS_YAHOO_NORMALIZED)


def test_tcs_reproduces_yahoo_normalized_income_exact_tax_path(tcs):
    n = N.normalised_earnings(tcs)
    assert n["status"] == "computed"
    assert n["reported_pat"] == 492_100_000_000
    assert n["normalised_pat"] == pytest.approx(TCS_YAHOO_NORMALIZED, rel=0.005)
    assert n["normalised_pat"] == pytest.approx(TCS_YAHOO_NORMALIZED, abs=1.0)
    assert n["matches_yahoo"] is True
    assert "exact" in n["tax_effect_basis"]
    applied = [a for a in n["adjustments"] if a["applied"]]
    assert [a["amount"] for a in applied] == pytest.approx([-36_660_000_000, -9_018_360_000])
    shown = [a for a in n["adjustments"] if not a["applied"]]
    assert shown and all("NOT subtracted again" in a["reason"] for a in shown)
    assert n["normalised_eps"] == pytest.approx(TCS_YAHOO_NORMALIZED / 3_618_087_518)
    assert n["eps_shares_basis"] == "diluted average shares"
    # history uses each year's own exact tax effect: FY25 Yahoo = 484,215.28 mn
    assert n["history"]["2025-03-31"] == pytest.approx(484_215_280_000, rel=1e-6)


def test_tcs_estimated_tax_path_lands_within_two_percent(tcs):
    snap = copy.deepcopy(tcs)
    snap["extra"].pop("tax_effect_unusual")
    n = N.normalised_earnings(snap)
    assert n["normalised_pat"] == pytest.approx(TCS_YAHOO_NORMALIZED, rel=0.02)
    assert "estimated at the effective tax rate" in n["tax_effect_basis"]
    assert "ASSUMED" in n["tax_effect_basis"]


def test_a_positive_one_off_lowers_normalised_pat():
    snap = {"ticker": "SYN", "period_end": "2026-03-31",
            "fields": {"net_income_common": 1000.0, "unusual_items": 100.0,
                       "tax": 250.0, "pretax_income": 1000.0,
                       "diluted_shares_avg": 10.0},
            "history": {}}
    n = N.normalised_earnings(snap)
    assert n["normalised_pat"] == pytest.approx(1000 - 100 + 25)
    assert n["normalised_pat"] < n["reported_pat"]
    assert n["normalised_eps"] == pytest.approx(92.5)
    assert is_dn(n["matches_yahoo"])


def test_no_tax_rate_and_no_exact_line_is_data_needed_not_a_guess():
    snap = {"fields": {"net_income_common": 1000.0, "unusual_items": 100.0},
            "history": {}}
    n = N.normalised_earnings(snap)
    assert is_dn(n["normalised_pat"]) and is_dn(n["status"])


def test_hdfcbank_has_no_unusual_line_and_reads_reported_as_normalised():
    n = N.normalised_earnings(_snap_from_zip("HDFCBANK"))
    assert n["status"] == "reported_as_normalised"
    assert n["normalised_pat"] == n["reported_pat"]
    assert n["matches_yahoo"] is True


def test_sail_and_dlf_match_yahoo():
    for t in ("SAIL", "DLF"):
        n = N.normalised_earnings(_snap_from_zip(t))
        assert n["matches_yahoo"] is True, t


# --------------------------------------------------------------------------
# Stage 12 — quality
# --------------------------------------------------------------------------
def test_tcs_quality_net_cash_and_no_stress_flag(tcs):
    q = N.quality(tcs, "earnings")
    assert q["status"] == "computed"
    assert q["cash_conversion"] == pytest.approx(1.059, abs=0.001)
    assert [x["period_end"] for x in q["cash_conversion_3y"]] == \
        ["2026-03-31", "2025-03-31", "2024-03-31"]
    assert num(q["net_debt"]) < 0
    assert q["net_debt_ebitda"] <= 0
    assert q["flags"] == []
    assert num(q["interest_cover"]) > 2 and num(q["score"]) > 80
    assert set(q["score_weights"]) == set(N.ASSUMED["score_weights"])


def test_without_short_term_investments_tcs_reads_small_net_debt_and_says_why(tcs):
    snap = copy.deepcopy(tcs)
    snap["extra"].pop("short_term_investments")
    q = N.quality(snap, "earnings")
    assert 0 < q["net_debt_ebitda"] < N.ASSUMED["thresholds"]["net_debt_ebitda_max"]
    assert "not carried" in q["net_debt_basis"]
    assert not any("net debt" in f for f in q["flags"])


def test_sail_quality_computes_leverage_and_cover():
    q = N.quality(_snap_from_zip("SAIL"), "ev_ebitda")
    assert q["status"] == "computed"
    assert q["interest_cover"] == pytest.approx(66_636.6 / 21_581.5, rel=1e-3)
    assert 0 < q["net_debt_ebitda"] < 3
    assert q["debt_equity"] == pytest.approx(0.529, abs=0.001)
    assert num(q["working_capital_days"]) is not None and num(q["score"]) is not None


def test_hdfcbank_as_a_lender_gets_data_needed_for_leverage_and_score():
    q = N.quality(_snap_from_zip("HDFCBANK"), "book")
    for k in ("net_debt_ebitda", "interest_cover", "debt_equity",
              "working_capital_days", "receivable_days", "score"):
        assert is_dn(q[k]), k
        assert "lender" in q[k]["reason"] and "NIM" in q[k]["reason"]
    assert q["flags"] == []


def test_weak_cash_conversion_three_years_running_is_flagged():
    hist_pat = {"2024-03-31": 100.0, "2025-03-31": 100.0, "2026-03-31": 100.0}
    hist_ocf = {"2024-03-31": 50.0, "2025-03-31": 60.0, "2026-03-31": 65.0}
    snap = {"period_end": "2026-03-31",
            "fields": {"net_income_common": 100.0, "ocf": 65.0, "fcf": -5.0,
                       "revenue": 1000.0},
            "history": {"net_income_common": hist_pat, "ocf": hist_ocf}}
    q = N.quality(snap)
    assert any("each of the last 3 years" in f for f in q["flags"])
    assert any("free cash flow negative" in f for f in q["flags"])


def test_empty_snapshot_is_data_needed_everywhere_and_never_raises():
    for snap in ({}, None, {"fields": {}, "history": {}}):
        n = N.normalised_earnings(snap)
        for k in ("reported_pat", "normalised_pat", "yahoo_normalized",
                  "matches_yahoo", "normalised_eps", "status"):
            assert is_dn(n[k]), k
        q = N.quality(snap)
        for k in ("cash_conversion", "fcf_margin", "receivable_days",
                  "working_capital_days", "net_debt_ebitda", "interest_cover",
                  "debt_equity", "asset_turnover", "score", "status"):
            assert is_dn(q[k]), k


def test_every_result_round_trips_as_json():
    for t in ("TCS", "HDFCBANK", "DLF", "SAIL"):
        s = _snap_from_zip(t)
        for res in (N.normalised_earnings(s), N.quality(s), N.quality(s, "book")):
            assert json.loads(json.dumps(res)) == res
    assert json.loads(json.dumps(N.ASSUMED)) == N.ASSUMED


# --------------------------------------------------------------------------
# Purity
# --------------------------------------------------------------------------
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


def test_normalise_module_is_pure():
    src = _operative_source(ROOT / "trading" / "valuation" / "normalise.py")
    for pat in (r"\bimport\s+(httpx|requests|urllib|yfinance)\b",
                r"\bfrom\s+(httpx|requests|urllib|yfinance)\b",
                r"\b(from|import)\s+web\b",
                r"\bfrom\s+trading(\.|\s+import\s+)(engine|risk|orders|danger)\b",
                r"\bimport\s+trading\.(engine|risk|orders|danger)\b"):
        assert not re.search(pat, src), pat
