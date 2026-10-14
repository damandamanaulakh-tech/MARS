"""Stages 07-08 — the locked financial snapshot (owner order 2026-10-14,
"lets hit the formulas work completion"). Money impact: NONE.

Pins trading/valuation/snapshot.py against the committed statements zip:
  * TCS reads in rupees as the zip stores them (revenue 2.67 lakh crore,
    Basic EPS 136.01, 3,618,087,518 shares);
  * the ADR trap: INFY (Basic EPS 0.81 — US dollars) is converted at the
    USDINR close on/before each period end, recorded per period; HDFCBANK is
    on the same list but its zip numbers ARE rupees, so it is NOT converted —
    the list alone never converts;
  * a signature on an unlisted name, or a listed name with nothing to check
    against, locks the currency (and every money field) as DATA_NEEDED;
  * point in time: an asof read never sees a period (annual or quarter)
    published after it; supplied result dates replace the ASSUMED lag;
  * TTM is 4 CONSECUTIVE quarters — TCS has no 2025-09-30 column, so it is
    DATA_NEEDED naming the gap, never a 15-month "TTM";
  * the lock hash is stable and moves with any field; records are JSON-safe;
  * the module is pure and no money-path module imports it.
No network: statements come from the committed zip or fixtures.
"""
from __future__ import annotations

import ast
import csv
import json
import re
import tokenize
from pathlib import Path

import pandas as pd
import pytest

from trading import register_v4
from trading.valuation import SNAPSHOT_FIELDS, is_dn
from trading.valuation import snapshot as S

ROOT = Path(__file__).resolve().parents[2]
MOD = ROOT / "trading" / "valuation" / "snapshot.py"


def _csv_fx_on_or_before(day: str) -> tuple[float, str]:
    """Independent read of data/usdinr_daily.csv (not through the module)."""
    best = None
    with open(ROOT / "data" / "usdinr_daily.csv") as f:
        for r in csv.DictReader(f):
            if r["date"] <= day and (best is None or r["date"] > best[1]):
                best = (float(r["close"]), r["date"])
    assert best is not None
    return best


def _fixture_statements(net_income=1.0e9, eps=1.0, revenue=1.0e10,
                        shares=1.0e9, period="2026-03-31",
                        prev="2025-03-31", share_lines=True) -> dict:
    cols = [period, prev]
    is_ = pd.DataFrame(
        {period: [revenue, net_income, net_income, eps, eps, shares, shares],
         prev: [revenue * 0.9, net_income * 0.9, net_income * 0.9, eps * 0.9,
                eps * 0.9, shares, shares]},
        index=["Total Revenue", "Net Income", "Net Income Common Stockholders",
               "Basic EPS", "Diluted EPS", "Basic Average Shares",
               "Diluted Average Shares"])[cols]
    bs_rows = {"Total Assets": [5.0e10, 4.5e10],
               "Stockholders Equity": [2.0e10, 1.8e10]}
    if share_lines:
        bs_rows["Ordinary Shares Number"] = [shares, shares]
    bs = pd.DataFrame(bs_rows, index=cols).T
    cf = pd.DataFrame({"Operating Cash Flow": [1.2e9, 1.0e9]}, index=cols).T
    return {"is": is_, "bs": bs, "cf": cf}


# ------------------------------------------------------------ (a) TCS rupees
def test_tcs_newest_period_in_rupees_as_the_zip_stores_them():
    r = S.snapshot("TCS", price=2156.0)
    assert r["source"] == "yahoo_statements_zip"
    assert r["period_end"] == "2026-03-31"
    f = r["fields"]
    assert f["revenue"] == pytest.approx(2.67021e12)
    assert f["net_income"] == pytest.approx(4.921e11)
    assert f["basic_eps"] == pytest.approx(136.01)
    assert f["shares_outstanding"] == 3_618_087_518
    assert r["currency"] == "INR"
    cc = r["currency_check"]
    assert cc["implied_pe"] == pytest.approx(15.85, abs=0.01)
    assert cc["signature"] is False and cc["on_adr_list"] is False
    assert r["fx"] == {}
    assert r["version"] == "2026-10-14"
    assert r["sources"]["revenue"] == "is:Total Revenue"
    assert r["sources"]["shares_outstanding"] == "bs:Ordinary Shares Number"
    # Yahoo keeps capex NEGATIVE; kept as reported
    assert f["capex"] < 0
    assert f["fcf"] == pytest.approx(f["ocf"] + f["capex"])
    # basis is never guessed
    assert r["basis"] == "unknown"
    assert "consolidated vs standalone" in r["data_needed"]["basis"]
    # every canonical field is present, as a float or DATA_NEEDED
    assert set(f) == set(SNAPSHOT_FIELDS)
    assert all(isinstance(v, float) or is_dn(v) for v in f.values())


def test_line_item_mapping_covers_the_canonical_fields_in_order():
    assert tuple(S.LINE_ITEMS) == SNAPSHOT_FIELDS
    assert S.LINE_ITEMS["capex"] == (("cf", "Capital Expenditure"),)
    assert S.LINE_ITEMS["cash"][-1] == ("cf", "End Cash Position")


def test_missing_line_items_are_data_needed_naming_the_label():
    tcs = S.snapshot("TCS", price=2156.0)
    assert "Salaries And Wages" in tcs["fields"]["employee_cost"]["reason"]
    hdfc = S.snapshot("HDFCBANK", price=707.25)
    # a bank has no EBITDA / cost of revenue — a legitimate DATA_NEEDED
    assert is_dn(hdfc["fields"]["ebitda"])
    assert hdfc["fields"]["ebitda"]["reason"] == \
        "line item is:EBITDA not in statements for HDFCBANK"
    assert is_dn(hdfc["fields"]["cost_of_revenue"])
    assert "not in statements" in hdfc["data_needed"]["ebit"]
    sail = S.snapshot("SAIL")
    assert "Goodwill" in sail["fields"]["goodwill"]["reason"]
    # a label that exists but is blank at the period says "blank"
    assert "blank for SAIL at 2026-03-31" in sail["fields"]["interest_income"]["reason"]
    dlf = S.snapshot("DLF")
    assert dlf["fields"]["inventory"] == pytest.approx(247_174_750_000.0)


# ---------------------------------------------------------- (b) INFY convert
def test_infy_signature_on_the_list_converts_at_the_period_end_close():
    raw = register_v4.load_statements("INFY")
    r = S.snapshot("INFY", price=1023.4)
    cc = r["currency_check"]
    assert cc["on_adr_list"] is True and cc["signature"] is True
    assert cc["implied_pe"] == pytest.approx(1023.4 / 0.81, rel=1e-4)
    assert r["currency"] == "USD→INR (converted)"
    rate, fx_date = _csv_fx_on_or_before("2026-03-31")
    assert r["fx"]["2026-03-31"] == {"rate": rate, "fx_date": fx_date}
    assert S.usdinr_at("2026-03-31") == (rate, fx_date)
    f = r["fields"]
    assert f["basic_eps"] == pytest.approx(0.81 * rate)
    assert f["revenue"] == pytest.approx(20_158_000_000 * rate)
    # counts and the tax rate are never converted
    assert f["shares_outstanding"] == pytest.approx(4_046_940_812)
    assert f["basic_shares_avg"] == pytest.approx(4_112_814_745)
    assert f["tax_rate"] == pytest.approx(0.264)
    # each period carries its OWN rate
    r25, d25 = _csv_fx_on_or_before("2025-03-31")
    assert r["fx"]["2025-03-31"] == {"rate": r25, "fx_date": d25}
    assert r25 != rate
    rev25 = float(raw["is"].loc["Total Revenue", "2025-03-31"])
    assert r["history"]["revenue"]["2025-03-31"] == pytest.approx(rev25 * r25)
    # quarters too
    q = "2025-12-31"
    rq, dq = _csv_fx_on_or_before(q)
    assert r["fx"][q] == {"rate": rq, "fx_date": dq}
    qrev = float(raw["qis"].loc["Total Revenue", q])
    assert r["quarterly"]["revenue"][q] == pytest.approx(qrev * rq)
    assert any("converted USD→INR" in w for w in r["warnings"])


def test_usdinr_at_returns_none_before_the_file_starts():
    assert S.usdinr_at("2000-01-01") is None


# ------------------------------------------------- (c) the list never converts
def test_hdfcbank_on_the_list_but_rupees_is_not_converted():
    r = S.snapshot("HDFCBANK", price=707.25)
    cc = r["currency_check"]
    assert cc["on_adr_list"] is True
    assert cc["signature"] is False
    assert cc["implied_pe"] == pytest.approx(707.25 / 45.89, rel=1e-4)
    assert r["currency"] == "INR"
    assert r["fx"] == {}
    assert r["fields"]["basic_eps"] == pytest.approx(45.89)
    assert r["fields"]["net_income"] == pytest.approx(704_793_400_000)


# --------------------------------------- (d) signature on an unlisted name
def test_signature_on_a_name_not_on_the_list_locks_currency_as_data_needed():
    stm = _fixture_statements(net_income=1.0e9, eps=1.0)
    r = S.snapshot("FAKECO", price=1200.0, statements=stm, shares_csv={})
    assert r["currency_check"]["signature"] is True
    assert r["currency_check"]["on_adr_list"] is False
    assert is_dn(r["currency"])
    assert "P/E 1200" in r["currency"]["reason"]
    assert "not on the ADR list" in r["currency"]["reason"]
    for name in ("revenue", "net_income", "basic_eps", "equity", "ocf"):
        assert is_dn(r["fields"][name]), name
        assert "currency unverified" in r["fields"][name]["reason"]
    # share counts are not money — they stay
    assert r["fields"]["shares_outstanding"] == pytest.approx(1.0e9)
    assert is_dn(r["history"]["revenue"])
    assert all(is_dn(v) for v in r["ttm"].values())
    assert "currency" in r["data_needed"]


# ---------------------------------------- (e) on the list, nothing to check
def test_listed_name_without_a_price_is_unverified_not_converted():
    r = S.snapshot("INFY", price=None)
    assert is_dn(r["currency"])
    assert "no price to check" in r["currency"]["reason"]
    assert is_dn(r["fields"]["revenue"]) and is_dn(r["fields"]["basic_eps"])
    assert "no price to check" in r["fields"]["revenue"]["reason"]
    assert r["fields"]["shares_outstanding"] == pytest.approx(4_046_940_812)
    assert r["fx"] == {}


def test_unlisted_name_without_a_price_stays_inr():
    r = S.snapshot("TCS")
    assert r["currency"] == "INR"
    assert r["currency_check"]["implied_pe"] is None


# ------------------------------------------------------------ (f) PIT
def test_asof_picks_the_period_published_by_then():
    r = S.snapshot("TCS", asof="2026-05-01")
    assert r["period_end"] == "2025-03-31"          # FY26 publishes ~05-15
    assert r["publication_date"] == "2025-05-15"
    assert r["publication_assumed"] is True
    assert r["fields"]["revenue"] == pytest.approx(2_553_240_000_000)
    assert r["fields"]["basic_eps"] == pytest.approx(134.19)
    # nothing published after asof leaks in — annual or quarterly
    assert "2026-03-31" not in r["history"]["revenue"]
    assert max(r["quarterly"]["revenue"]) == "2025-12-31"
    assert any("ASSUMED" in w for w in r["warnings"])


def test_supplied_result_dates_move_the_point_in_time():
    r = S.snapshot("TCS", asof="2026-05-01",
                   result_dates={"TCS": {"2026-03-31": "2026-04-09"}})
    assert r["period_end"] == "2026-03-31"
    assert r["publication_date"] == "2026-04-09"
    assert r["publication_assumed"] is False
    assert "2026-03-31" in r["quarterly"]["revenue"]
    # a result date AFTER asof keeps the period hidden even inside the lag
    r2 = S.snapshot("TCS", asof="2026-05-01",
                    result_dates={"TCS.NS": {"2026-03-31": "2026-05-20"}})
    assert r2["period_end"] == "2025-03-31"


def test_asof_before_any_publication_is_data_needed_everywhere():
    r = S.snapshot("TCS", asof="2023-01-01")
    assert r["period_end"] is None
    assert all(is_dn(v) for v in r["fields"].values())
    assert "no statement published by 2023-01-01" in r["fields"]["revenue"]["reason"]
    assert len(r["lock_hash"]) == 64


def test_the_current_shares_csv_never_leaks_into_an_asof_record():
    stm = _fixture_statements(share_lines=False)
    now = S.snapshot("FAKECO", statements=stm, shares_csv={"FAKECO": 7.5e8})
    assert now["fields"]["shares_outstanding"] == pytest.approx(7.5e8)
    assert now["sources"]["shares_outstanding"].startswith("shares_csv:")
    assert any("current snapshot" in w for w in now["warnings"])
    past = S.snapshot("FAKECO", asof="2026-06-30", statements=stm,
                      shares_csv={"FAKECO": 7.5e8})
    assert is_dn(past["fields"]["shares_outstanding"])
    assert "leak" in past["fields"]["shares_outstanding"]["reason"]


# ------------------------------------------------------------ (g) no data
def test_a_name_with_no_statements_is_source_none_and_still_locked():
    r = S.snapshot("NOSUCHCO", statements={}, fundamentals={}, shares_csv={})
    assert r["source"] == "none"
    assert r["period_end"] is None
    assert set(r["fields"]) == set(SNAPSHOT_FIELDS)
    assert all(is_dn(v) for v in r["fields"].values())
    assert is_dn(r["currency"])
    assert re.fullmatch(r"[0-9a-f]{64}", r["lock_hash"])


def test_fundamentals_csv_is_the_fallback_when_the_zip_has_no_file():
    fund = {"annualTotalRevenue": {"2026-03-31": 5.0e10, "2025-03-31": 4.0e10},
            "annualNetIncome": {"2026-03-31": 5.0e9, "2025-03-31": 4.0e9},
            "annualBasicEPS": {"2026-03-31": 10.0},
            "quarterlyTotalRevenue": {"2026-03-31": 1.3e10}}
    r = S.snapshot("CSVONLY", statements={}, fundamentals=fund, shares_csv={})
    assert r["source"] == "fundamentals_csv"
    assert r["period_end"] == "2026-03-31"
    assert r["fields"]["revenue"] == pytest.approx(5.0e10)
    assert r["sources"]["revenue"] == "fundamentals_csv:annualTotalRevenue"
    assert r["history"]["revenue"] == {"2026-03-31": 5.0e10, "2025-03-31": 4.0e10}
    assert is_dn(r["fields"]["ebitda"])
    assert "not carried by the fundamentals csv" in r["fields"]["ebitda"]["reason"]


# ------------------------------------------------------- (h) lock hash
def test_lock_hash_is_stable_and_moves_with_any_field():
    a = S.snapshot("TCS", price=2156.0)
    b = S.snapshot("TCS", price=2156.0)
    assert a["lock_hash"] == b["lock_hash"]
    assert S.lock_hash_of(a) == a["lock_hash"]
    raw = register_v4.load_statements("TCS")
    moved = {k: v.copy() for k, v in raw.items()}
    moved["is"].loc["Total Revenue", "2026-03-31"] += 1.0
    c = S.snapshot("TCS", price=2156.0, statements=moved)
    assert c["fields"]["revenue"] == pytest.approx(a["fields"]["revenue"] + 1.0)
    assert c["lock_hash"] != a["lock_hash"]
    # the committed zip's cached frames were not touched by the copy
    assert S.snapshot("TCS", price=2156.0)["lock_hash"] == a["lock_hash"]


# ------------------------------------------------------- (i) JSON safe
@pytest.mark.parametrize("ticker,price", [("TCS", 2156.0), ("INFY", 1023.4),
                                          ("INFY", None), ("HDFCBANK", 707.25),
                                          ("DLF", None), ("SAIL", None)])
def test_records_round_trip_through_json_unchanged(ticker, price):
    r = S.snapshot(ticker, price=price)
    assert json.loads(json.dumps(r, allow_nan=False)) == r


# ------------------------------------------------------- (j) quarters / TTM
def test_tcs_quarters_are_read_but_its_ttm_names_the_missing_quarter():
    r = S.snapshot("TCS")
    q = r["quarterly"]["revenue"]
    assert q["2026-06-30"] == pytest.approx(722_750_000_000)
    assert "2025-09-30" not in q                  # Yahoo skipped the quarter
    assert len(q) <= S.QUARTERS_KEPT
    for f in S.QUARTERLY_FIELDS:
        assert is_dn(r["ttm"][f])
        assert "2025-09-30" in r["ttm"][f]["reason"]
    assert r["ttm_quarters"] == []


def test_ttm_is_the_sum_of_four_consecutive_quarters():
    raw = register_v4.load_statements("ASIANPAINT")["qis"]
    qs = ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"]
    r = S.snapshot("ASIANPAINT")
    assert r["ttm_quarters"] == qs
    for field, label in (("revenue", "Total Revenue"), ("net_income", "Net Income"),
                         ("operating_income", "Operating Income"),
                         ("basic_eps", "Basic EPS")):
        want = sum(float(raw.loc[label, q]) for q in qs)
        assert r["ttm"][field] == pytest.approx(want), field


# ------------------------------------------------------------ purity
def _code_tokens(path: Path) -> str:
    """The module's code with comments and every string literal removed."""
    out = []
    with open(path, "rb") as f:
        for tok in tokenize.tokenize(f.readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING, tokenize.ENCODING):
                continue
            out.append(tok.string)
    return " ".join(out)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            names.add(mod)
            names.update(f"{mod}.{a.name}" for a in node.names)
    return names


def test_snapshot_module_is_pure():
    code = _code_tokens(MOD)
    for bad in ("httpx", "requests", "urllib", "yfinance", "web .",
                "__import__", "importlib"):
        assert bad not in code, bad
    for name in _imports(MOD):
        parts = set(name.split("."))
        assert not parts & {"httpx", "requests", "urllib", "yfinance", "web"}, name
        assert not parts & {"engine", "risk", "orders", "danger"}, name


def test_no_money_path_module_imports_the_valuation_package():
    for mod in ("engine.py", "risk.py", "orders.py", "danger.py"):
        src = (ROOT / "trading" / mod).read_text()
        assert "trading.valuation" not in src, mod
        assert "from trading import valuation" not in src, mod
