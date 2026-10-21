"""Macro feeds for the valuation chain (owner order 2026-10-21, VALFIX batch 2
item 10). Pure tests: bond maths against published price/yield pairs, and
every parser run on REAL payload excerpts committed under fixtures/ (the
fault-mask lesson: a parser tested on an invented shape proves nothing).
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

from trading import macro_feeds as mf

ROOT = Path(__file__).resolve().parents[2]
FX = Path(__file__).resolve().parent / "fixtures"


def _candles(name: str) -> list:
    return json.loads((FX / name).read_text())["data"]["candles"]


def _frame(candles: list) -> pd.DataFrame:
    """Same shape as UpstoxClient.get_daily_candles returns."""
    df = pd.DataFrame([c[:6] for c in candles],
                      columns=["ts", "Open", "High", "Low", "Close", "Volume"])
    df["ts"] = (pd.to_datetime(df["ts"], utc=True).dt.tz_convert("Asia/Kolkata")
                .dt.tz_localize(None).dt.normalize())
    return df.set_index("ts").sort_index()


@pytest.fixture(autouse=True)
def _no_reader():
    old = mf._reader                     # restore what the app injected at boot
    mf.set_reader(None)
    yield
    mf.set_reader(old)


# ------------------------------------------------------------- bond maths
def test_price_matches_the_published_excel_example():
    # Microsoft's PRICE example: 15-Feb-2008, 15-Nov-2017, 5.75%, 6.5% -> 94.63436
    assert mf.bond_price(6.5, 5.75, "2017-11-15", "2008-02-15") == pytest.approx(94.63436, abs=5e-6)


def test_yield_matches_the_published_excel_example():
    # Microsoft's YIELD example: 15-Feb-2008, 15-Nov-2016, 5.75%, 95.04287 -> 6.50%
    assert mf.bond_yield(95.04287, 5.75, "2016-11-15", "2008-02-15") == pytest.approx(6.5, abs=1e-5)


def test_a_par_bond_on_a_coupon_date_yields_its_coupon():
    assert mf.bond_yield(100.0, 7.54, "2036-05-23", "2026-05-23") == pytest.approx(7.54, abs=1e-9)


def test_price_and_yield_round_trip_on_a_real_close():
    # 7.54% GS 2036 closed 104.23 on 2026-10-19 (Upstox), settles T+1
    y = mf.bond_yield(104.23, 7.54, "2036-05-23", "2026-10-20")
    assert y == pytest.approx(6.9276, abs=1e-4)
    assert mf.bond_price(y, 7.54, "2036-05-23", "2026-10-20") == pytest.approx(104.23, abs=1e-8)


def test_settlement_is_the_next_weekday():
    assert mf.settle_date("2026-10-16") == "2026-10-19"      # Friday -> Monday
    assert mf.settle_date("2026-10-19") == "2026-10-20"


def test_days_30_360():
    from datetime import date
    assert mf.days_30_360(date(2026, 5, 23), date(2026, 10, 20)) == 147
    assert mf.days_30_360(date(2026, 1, 31), date(2026, 2, 28)) == 28


# ------------------------------------------------------------- bond list
def test_rbi_outstanding_list_parses_and_matches_the_committed_table():
    page = (FX / "rbi_gsec_outstanding_2026-10-19.html").read_text()
    rows = {r["isin"]: r for r in mf.parse_gsec_list(page)}
    assert mf.parse_gsec_asof(page) == "2026-10-19"
    assert rows["IN0020220029"] == {"isin": "IN0020220029", "name": "7.54% GS 2036",
                                    "coupon_pct": 7.54, "issue": "2022-05-23",
                                    "maturity": "2036-05-23"}
    for ref in mf.reference_securities():
        assert rows[ref["isin"]] == ref, ref["isin"]


def test_ten_year_window_on_the_reference_list():
    names = [c["name"] for c in mf.ten_year_candidates(mf.reference_securities(), "2026-10-21")]
    assert names == ["6.67% GS 2035", "6.94% GS 2036", "7.54% GS 2036",
                     "8.33% GS 2036", "7.41% GS 2036", "7.18% GS 2037"]


def test_most_traded_is_picked_on_real_candles():
    c754 = [(c[0][:10], c[4], c[5]) for c in reversed(_candles("upstox_754gs2036_candles_2026-10-20.json"))]
    c694 = [(c[0][:10], c[4], c[5]) for c in reversed(_candles("upstox_694gs2036_candles_2026-10-20.json"))]
    assert mf.pick_most_traded({"IN0020260025": c694, "IN0020220029": c754}) == "IN0020220029"
    assert mf.pick_most_traded({}) is None
    assert mf.pick_most_traded({"X": [("2026-10-19", 100.0, 0)]}) is None


class _FakeClient:
    def __init__(self, frames):
        self.frames, self.asked = frames, []

    def get_daily_candles(self, key, frm, to):
        self.asked.append(key)
        return self.frames.get(key)


def test_fetch_gsec10y_picks_the_busiest_paper_and_dates_each_yield():
    from datetime import date
    client = _FakeClient({
        "NSE_EQ|IN0020220029": _frame(_candles("upstox_754gs2036_candles_2026-10-20.json")),
        "NSE_EQ|IN0020260025": _frame(_candles("upstox_694gs2036_candles_2026-10-20.json")),
    })
    rows = mf.fetch_gsec10y(client, "2026-10-14", securities=mf.reference_securities(),
                            today=date(2026, 10, 21))
    assert len(client.asked) == 6                      # every 9-11-year candidate asked
    assert [r["date"] for r in rows] == ["2026-10-15", "2026-10-16", "2026-10-19"]
    last = rows[-1]
    assert last["series"] == "gsec10y_yield" and last["bond"] == "7.54% GS 2036"
    assert last["value"] == pytest.approx(6.9276, abs=1e-4) and last["price"] == 104.23
    assert "7.54% GS 2036" in last["source"] and "2026-10-19" in last["source"]
    assert last["url"].startswith("https://api.upstox.com/v2/historical-candle/NSE_EQ%7CIN0020220029/")
    assert last["volume_10d"] == pytest.approx(5304421.0)


def test_fetch_gsec10y_without_a_client_or_candles_returns_nothing():
    assert mf.fetch_gsec10y(None) == []
    assert mf.fetch_gsec10y(_FakeClient({}), "", securities=mf.reference_securities()) == []


# ------------------------------------------------------------- RBI home
def test_rbi_home_parser_on_the_real_page():
    page = (FX / "rbi_home_rates_2026-10-21.html").read_text()
    p = mf.parse_rbi_home(page, "2026-10-21")
    assert p == {"repo_pct": 5.5, "repo_date": "2026-10-21",
                 "usdinr": 94.7691, "usdinr_date": "2026-10-19"}
    rows = mf.rbi_home_rows(p, "2026-10-21T05:00:00+05:30")
    assert [(r["series"], r["date"], r["value"]) for r in rows] == [
        ("rbi_repo", "2026-10-21", 5.5), ("rbi_usdinr", "2026-10-19", 94.7691)]
    assert all(r["url"] == mf.RBI_HOME_URL for r in rows)


def test_a_page_without_the_values_gives_no_rows():
    p = mf.parse_rbi_home("<html><body>maintenance</body></html>", "2026-10-21")
    assert p["repo_pct"] is None and p["usdinr"] is None
    assert mf.rbi_home_rows(p, "x") == []


# --------------------------------------------------------- stored readings
def test_latest_and_at_read_the_injected_store():
    store = [
        {"series": "gsec10y_yield", "date": "2026-10-15", "value": 6.97, "source": "a"},
        {"series": "gsec10y_yield", "date": "2026-10-19", "value": 6.93, "source": "b"},
        {"series": "rbi_usdinr", "date": "2026-10-19", "value": 94.7691, "source": "c"},
    ]
    mf.set_reader(lambda series=None: [r for r in store if series in (None, r["series"])])
    assert mf.latest("gsec10y_yield") == (6.93, "2026-10-19", "b")
    assert mf.at("gsec10y_yield", "2026-10-16") == (6.97, "2026-10-15", "a")
    assert mf.at("gsec10y_yield", "2026-10-01") is None
    assert mf.latest("rbi_usdinr") == (94.7691, "2026-10-19", "c")


def test_no_store_and_a_broken_store_read_as_never_read():
    assert mf.latest("gsec10y_yield") is None

    def boom(series=None):
        raise RuntimeError("mongo down")
    mf.set_reader(boom)
    assert mf.latest("gsec10y_yield") is None and mf.stored_rows() == []


def test_staleness_is_ten_days():
    from datetime import date
    assert not mf.is_stale("2026-10-11", today=date(2026, 10, 21))
    assert mf.is_stale("2026-10-10", today=date(2026, 10, 21))
    assert mf.is_stale("not a date")


# ------------------------------------------------------------- repo history
def test_committed_repo_history_is_rbi_sourced_and_continuous():
    rows = mf.repo_history()
    assert len(rows) >= 20
    for r in rows:
        assert r["source_url"].startswith("https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=")
        assert "policy repo rate" in r["sentence"]
        assert r["fetched"]
    for prev, cur in zip(rows, rows[1:]):
        assert cur["repo_before_pct"] == pytest.approx(prev["repo_after_pct"]), cur["decision_date"]


def test_repo_at_base_period_ends():
    assert mf.repo_at("2026-03-31")[0] == pytest.approx(5.25)
    assert mf.repo_at("2025-03-31")[0] == pytest.approx(6.25)
    assert mf.repo_at("2024-03-31")[0] == pytest.approx(6.5)
    first = mf.repo_history()[0]["decision_date"]
    assert mf.repo_at("2020-01-01") is None and first > "2020-01-01"


def test_repo_after_the_last_decision_needs_a_live_reading():
    last = mf.repo_history()[-1]
    assert mf.at("rbi_repo", "2026-10-20") is None            # nothing read since
    mf.set_reader(lambda series=None: [
        {"series": "rbi_repo", "date": "2026-10-21", "value": last["repo_after_pct"],
         "source": "RBI home page"}])
    v, d, src = mf.at("rbi_repo", "2026-10-20")
    assert v == last["repo_after_pct"] and d == "2026-10-20" and "RBI home" in src
    # start (base period end) and end (today) are different numbers
    assert mf.at("rbi_repo", "2026-03-31")[0] != mf.latest("rbi_repo")[0]


def test_repo_gap_between_decisions_is_not_proved():
    hist = [{"decision_date": "2026-01-01", "repo_after_pct": 5.0, "repo_before_pct": 5.0},
            {"decision_date": "2026-03-01", "repo_after_pct": 5.5, "repo_before_pct": 5.25}]
    assert mf.repo_at("2026-02-01", history=hist) is None      # 5.0 vs 5.25: an unseen change
    hist[1]["repo_before_pct"] = 5.0
    assert mf.repo_at("2026-02-01", history=hist)[0] == 5.0


def test_resolution_parser_on_real_rbi_text():
    sys.path.insert(0, str(ROOT / "scripts"))
    import fetch_rbi_repo_history as f
    ex = json.loads((FX / "rbi_mpc_resolution_excerpts_2026-10-21.json").read_text())
    got = {k: f.parse_resolution(v["excerpt"]) for k, v in ex.items() if k != "_source"}
    assert (got["57637"]["action"], got["57637"]["repo_after_pct"]) == ("unchanged", 6.5)
    assert (got["60957"]["action"], got["60957"]["repo_after_pct"]) == ("unchanged", 5.5)
    assert (got["63742"]["action"], got["63742"]["change_bps"], got["63742"]["repo_before_pct"]) == ("hike", 25, 5.25)
    assert (got["61749"]["action"], got["61749"]["change_bps"], got["61749"]["repo_after_pct"]) == ("cut", -25, 5.25)
    assert got["63742"]["decision_date"] == "2026-10-07"
    assert f.parse_resolution("Resolution of the Monetary Policy Committee nothing here") is None


# ------------------------------------------------------ institutional hold
def _shp(symbol):
    with (ROOT / "data" / "shareholding_institutional.csv").open() as fh:
        return {r["quarter"]: float(r["fii_pct"]) + float(r["dii_pct"])
                for r in csv.DictReader(fh) if r["symbol"] == symbol and r["fii_pct"] and r["dii_pct"]}


def test_inst_holding_start_at_the_period_end_quarter_end_at_the_latest():
    want = _shp("TCS")
    got = mf.inst_holding("TCS", "2026-03-31")
    assert got == (pytest.approx(want["31-MAR-2026"]), pytest.approx(want["30-JUN-2026"]),
                   "2026-03-31", "2026-06-30")
    assert mf.inst_holding("TCS.NS", "2026-05-15")[2] == "2026-03-31"


def test_inst_holding_none_before_the_first_quarter_or_unknown_symbol():
    assert mf.inst_holding("TCS", "2025-03-31") is None
    assert mf.inst_holding("NO_SUCH_NAME", "2026-03-31") is None


# ---------------------------------------- committed filing data (VAL-02/01/14b)
def test_every_lender_in_the_statement_set_has_a_filing_row_for_both_year_ends():
    from trading.config import Config
    from trading.valuation import filings as F, pipeline as P, route as R
    names = {}
    with (ROOT / "data" / "upstox_nse_equities.csv").open() as fh:
        names = {r["ticker"]: r.get("name", "") for r in csv.DictReader(fh)}
    sm = Config().sector_map
    lenders = []
    for s in P.statement_symbols():
        rt = R.route_for(f"{s}.NS", sm, names.get(f"{s}.NS", ""))
        if (rt.get("sector_method") or rt.get("method")) in ("bank_pb_roe", "nbfc_pb"):
            lenders.append(s)
    assert len(lenders) >= 30
    for s in lenders:
        for fy in ("2026-03-31", "2025-03-31"):
            r = F.filing(s, fy)
            assert r is not None, (s, fy)
            assert r["list_url"].startswith("https://www.nseindia.com/api/") and r["fetched"]
            if r["net_worth_cr"]:
                assert r["xbrl_url"].startswith("https://nsearchives.nseindia.com/")
            else:
                assert r["note"], (s, fy)          # a gap says why


def test_filed_net_worth_reads_the_verified_figures():
    from trading.valuation import filings as F
    v, src = F.net_worth("HDFCBANK.NS", "2026-03-31")
    assert v == pytest.approx(581514.36) and "nsearchives" in src and "Consolidated" in src
    # paid-up EQUITY capital, not the balance-sheet "Capital" line
    assert F.net_worth("ICICIBANK", "2026-03-31")[0] == pytest.approx(376889.30)
    assert F.net_worth("BAJFINANCE", "2026-03-31")[0] == pytest.approx(113999.02)
    assert F.net_worth("GROWW", "2025-03-31") is None      # listed after Mar-2025
    assert F.net_worth("TCS", "2026-03-31") is None        # not a lender


def test_reliance_segments_add_to_the_filed_total():
    from trading.valuation import segments as SG
    rows = SG.segment_ebit("RELIANCE.NS")
    got = {r["segment"]: r["ebit_cr"] for r in rows}
    assert got == {"Oil to Chemicals (O2C)": 50758.0, "Oil and Gas": 13464.0,
                   "Retail": 20817.0, "Digital Services": 48211.0, "Others": 3448.0}
    assert sum(got.values()) == pytest.approx(136698.0)
    assert all(r["fy_end"] == "2026-03-31" and "nsearchives" in r["source"] for r in rows)
    assert SG.segment_ebit("TCS") == []


def test_bajaj_holdings_stakes_are_read_by_name_from_the_filings():
    from trading.valuation import segments as SG
    st = {r["subsidiary"]: r for r in SG.holdco_stakes("BAJAJHLDNG")}
    assert set(st) == {"BAJAJ-AUTO", "BAJAJFINSV"}
    assert st["BAJAJ-AUTO"]["shares_held"] == 95547251
    assert st["BAJAJFINSV"]["shares_held"] == 614832190
    assert all("SHP_" in r["source"] and "BAJAJ HOLDINGS" in r["source"] for r in st.values())
    # report_data check: at the 19-Oct closes the listed stakes ≈ ₹2,02,811 cr
    val = (st["BAJAJ-AUTO"]["shares_held"] * 10287 + st["BAJAJFINSV"]["shares_held"] * 1700) / 1e7
    assert val == pytest.approx(202811, abs=1)
    assert SG.holdco_stakes("TATAINVEST") == []            # left for the owner
