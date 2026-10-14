"""Stage 10 — share capital + dilution bridge (trading/valuation/shares.py).
Owner order 2026-10-14 "lets hit the formulas work completion"; money impact
NONE (display only).

Pins: the NSE corporate-action parser on real subject texts (never a guessed
ratio); synthetic split / bonus reconcile, an unexplained jump is named; the
source-restated reading (Yahoo restates history onto today's basis); real
TCS reconciles with its buyback noted; real HDFCBANK's 2023 merger comes out
UNEXPLAINED — the honest reading, a scheme of amalgamation is not a split;
a missing count is DATA_NEEDED; the module is pure.
"""
from __future__ import annotations

import ast
import json
import math
import re
from pathlib import Path

import pytest

from trading.valuation import dn, is_dn
from trading.valuation import shares as S

ROOT = Path(__file__).resolve().parents[2]


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
          "short_term_investments": ("bs", "Other Short Term Investments"),
          "issuance": ("cf", "Issuance Of Capital Stock")}


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
    extra = {}
    for name, (kind, line) in _EXTRA.items():
        s = _ser(st, kind, line)
        if s:
            extra[name] = s
            if name == "issuance":
                hist["issuance"] = s
    return {"ticker": ticker, "period_end": period, "currency": "INR",
            "fields": fields, "history": hist, "data_needed": need, "extra": extra}


def _synthetic(counts: dict, **fields):
    return {"ticker": "SYN", "period_end": max(counts), "currency": "INR",
            "fields": {"shares_outstanding": counts[max(counts)], **fields},
            "history": {"shares_outstanding": dict(counts)}, "data_needed": {}}


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------
@pytest.mark.parametrize("subject,typ,ratio", [
    ("Fv Split Rs.10 To Re.1", "split", 10.0),
    ("Face Value Split (Sub-Division) - From Rs 2/- Per Share To Re 1/- Per Share", "split", 2.0),
    ("Bonus 1:1", "bonus", 1.0),
    ("Bonus 3:2", "bonus", 1.5),
    ("Rights 1:5 @ Premium Rs 95/-", "rights", 0.2),
    ("Buyback", "buyback", None),
    (" Buy Back", "buyback", None),
    ("Dividend - Rs 10 Per Share", "dividend", None),
    ("Int Div-Rs.3/- Per Share Purpose Revised", "dividend", None),
    ("Annual General Meeting", "other", None),
    ("Face Valus Split (Sub-Division) - From Rs 10/- Per To Rs 2/- Per Share", "split", 5.0),
])
def test_parse_corporate_action_examples(subject, typ, ratio):
    p = S.parse_corporate_action(subject)
    assert p["type"] == typ
    if ratio is None:
        assert p["ratio"] is None
    else:
        assert p["ratio"] == pytest.approx(ratio)


def test_parse_reads_amounts_and_combined_rows():
    assert S.parse_corporate_action("Dividend - Rs 10 Per Share")["amount"] == 10.0
    assert S.parse_corporate_action("Rights 1:5 @ Premium Rs 95/-")["premium"] == 95.0
    both = S.parse_corporate_action("Bonus 1:1 And Face Value Split From Rs.10 To Re.1")
    assert both["type"] == "split" and S.share_factor(both) == pytest.approx(20.0)
    tcs18 = S.parse_corporate_action(" Bonus 1:1 /Dividend- Rs 29 Per Share")
    assert tcs18["type"] == "bonus" and S.share_factor(tcs18) == pytest.approx(2.0)


def test_parse_never_guesses_a_ratio():
    for subj in ("Scheme Of Arrangement - Bonus Debentures 6:1",
                 "Bonus Preference Shares 21:1", "Bonus Ncrps 1:116",
                 "Bonus 1 Dvr : 10 Eq Share",
                 "Consolidation Of Equity Shares From Re 1 Per Share To Rs 10 Per Share",
                 "Capital Reduction", "Demerger", ""):
        p = S.parse_corporate_action(subj)
        assert p["type"] == "other" and p["ratio"] is None, subj
        assert S.share_factor(p) == 1.0
    # rights and buybacks move the count only by cash — no factor, ever
    assert S.share_factor(S.parse_corporate_action("Rights 1:5 @ Premium Rs 95/-")) == 1.0
    assert S.share_factor(S.parse_corporate_action("Buyback")) == 1.0


def test_load_corporate_actions_reads_one_symbol():
    rows = S.load_corporate_actions("TCS.NS")
    assert len(rows) >= 70 and {r["symbol"] for r in rows} == {"TCS"}


# --------------------------------------------------------------------------
# Synthetic bridges
# --------------------------------------------------------------------------
def test_a_one_to_five_split_reconciles():
    snap = _synthetic({"2022-03-31": 100e6, "2023-03-31": 500e6})
    ca = [{"subject": "Face Value Split (Sub-Division) - From Rs 5/- Per Share To Re 1/- Per Share",
           "exDate": "15-Sep-2022"}]
    b = S.share_bridge("SYN", snap, ca_rows=ca, shares_csv_row={})
    assert b["explained"] == {"2023-03-31": 5.0}
    assert b["status"] == "reconciled"
    assert b["unexplained_change_pct"]["2022-03-31→2023-03-31"] == pytest.approx(0.0)
    assert b["pairs"][0]["basis"] == "unadjusted"


def test_a_one_to_one_bonus_reconciles():
    snap = _synthetic({"2022-03-31": 100e6, "2023-03-31": 200.5e6})
    ca = [{"subject": "Bonus 1:1", "exDate": "01-Jun-2022"}]
    b = S.share_bridge("SYN", snap, ca_rows=ca, shares_csv_row={})
    assert b["explained"]["2023-03-31"] == 2.0
    assert b["status"] == "reconciled"


def test_a_ten_percent_jump_with_no_action_is_unexplained_and_named():
    snap = _synthetic({"2021-03-31": 100e6, "2022-03-31": 100.5e6, "2023-03-31": 110.55e6})
    b = S.share_bridge("SYN", snap, ca_rows=[], shares_csv_row={})
    assert b["status"] == "unexplained"
    assert b["unexplained_pairs"] == ["2022-03-31→2023-03-31"]
    assert b["unexplained_change_pct"]["2022-03-31→2023-03-31"] == pytest.approx(10.0)
    assert "2022-03-31→2023-03-31" in b["note"] and "UNEXPLAINED" in b["note"]


def test_a_source_restated_series_reconciles_and_says_so():
    snap = _synthetic({"2022-03-31": 200e6, "2023-03-31": 200.4e6})
    b = S.share_bridge("SYN", snap, ca_rows=[{"subject": "Bonus 1:1",
                                              "exDate": "01-Jun-2022"}], shares_csv_row={})
    assert b["status"] == "reconciled"
    assert b["pairs"][0]["basis"] == "restated_by_source"
    assert "restated" in b["note"]


def test_rights_in_the_window_never_explain_a_jump():
    snap = _synthetic({"2022-03-31": 100e6, "2023-03-31": 120e6})
    b = S.share_bridge("SYN", snap, ca_rows=[{"subject": "Rights 1:5 @ Premium Rs 95/-",
                                              "exDate": "01-Aug-2022"}], shares_csv_row={})
    assert b["status"] == "unexplained" and b["explained"]["2023-03-31"] == 1.0
    assert "rights issue in the window" in b["note"]


def test_missing_snapshot_shares_is_data_needed():
    b = S.share_bridge("SYN", {"fields": {}, "history": {}}, ca_rows=[], shares_csv_row={})
    assert is_dn(b["status"]) and is_dn(b["basic_now"]) and is_dn(b["dilutive_instruments"])
    assert is_dn(b["esop_pool"])
    one = S.share_bridge("SYN", _synthetic({"2023-03-31": 1e6}), ca_rows=[], shares_csv_row={})
    assert is_dn(one["status"])
    assert S.share_bridge("SYN", {}, ca_rows=[], shares_csv_row={})["pairs"] == []


# --------------------------------------------------------------------------
# Real names
# --------------------------------------------------------------------------
def test_tcs_reconciles_with_its_buyback_noted():
    snap = _snap_from_zip("TCS")
    so = snap["history"]["shares_outstanding"]
    assert so["2023-03-31"] == 3_659_051_373 and so["2024-03-31"] == 3_618_087_518
    b = S.share_bridge("TCS", snap)
    assert b["status"] == "reconciled"
    pair = b["pairs"][0]
    assert (pair["from"], pair["to"]) == ("2023-03-31", "2024-03-31")
    assert pair["change_pct"] == pytest.approx(-1.12, abs=0.01)
    assert pair["unexplained_change_pct"] < S.ASSUMED["reconcile_tol_pct"]
    assert "buyback" in b["note"]
    assert b["dilutive_instruments"] == pytest.approx(0.0)
    assert b["basic_now"] == 3_618_087_518
    assert any("Buy" in a["detail"] or "buyback" == a["type"] for a in b["actions"])


def test_hdfcbank_merger_year_is_unexplained_the_honest_reading():
    snap = _snap_from_zip("HDFCBANK")
    b = S.share_bridge("HDFCBANK", snap)
    assert b["status"] == "unexplained"
    assert "2023-03-31→2024-03-31" in b["unexplained_pairs"]
    assert b["unexplained_change_pct"]["2023-03-31→2024-03-31"] > 30
    assert "UNEXPLAINED" in b["note"] and "2023-03-31→2024-03-31" in b["note"]
    # the 2025 1:1 bonus falls in FY26 but the source restated history
    last = b["pairs"][-1]
    assert last["explained_factor"] == 2.0 and last["basis"] == "restated_by_source"
    assert last["within_tol"]
    # the diluted series the source did NOT restate is flagged, not believed
    assert any("diluted average" in f and "below basic" in f for f in b["flags"])
    assert b["csv_check"]["agrees"] is True


def test_bridge_round_trips_as_json():
    for t in ("TCS", "HDFCBANK", "DLF", "SAIL"):
        b = S.share_bridge(t, _snap_from_zip(t))
        assert json.loads(json.dumps(b)) == b


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


def test_shares_module_is_pure():
    src = _operative_source(ROOT / "trading" / "valuation" / "shares.py")
    for pat in (r"\bimport\s+(httpx|requests|urllib|yfinance)\b",
                r"\bfrom\s+(httpx|requests|urllib|yfinance)\b",
                r"\b(from|import)\s+web\b",
                r"\bfrom\s+trading(\.|\s+import\s+)(engine|risk|orders|danger)\b",
                r"\bimport\s+trading\.(engine|risk|orders|danger)\b"):
        assert not re.search(pat, src), pat
