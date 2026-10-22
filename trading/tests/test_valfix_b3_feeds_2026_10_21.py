"""VALFIX batch 3 — the feeds, the index mapping and the sheet's own E101
(owner choices and answers 2026-10-21). Display only; no network here —
the FRED and NSE payloads are committed raw fixtures (tests/fixtures).

Pins:
  * FRED fredgraph.csv: an EMPTY value (today's files) and '.' (FRED's
    HTML / API) are both missing — no row, never 0; a header naming another
    series gives nothing; the old 'DATE' header is accepted;
  * NSE allIndices: dated by the snapshot's own `timestamp` (19-Oct-2026
    15:30, read on the 10-20 holiday — never the fetch time); a reading
    stamped before 15:30 is INTRADAY; a row's `previousDay` dates it only
    when the timestamp is missing and previousDayVal == last; "0" P/E on a
    fixed-income row is no reading; every app index is found (3 by alias);
  * the six verified index keys are in SECTOR_INDEX_KEYS with their NSE
    names; the tail fetch seeds a sector the store never held; the history
    fetch can be asked for some sectors only;
  * the mapping: strict rule A for a name with no app label (TATATECH →
    Nifty IT), the owner's six for Healthcare / Chemicals / Consumer
    Durables / Construction Materials / Oil & Gas, the app label wins, rule
    B NOT applied (the 132-name list on the page: the web test);
  * the live chain counts E101 0, the owner's Tata sheet keeps E101 / E114
    cell for cell.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd
import pytest

from trading import data_refresh as dr
from trading import macro_feeds as mf
from trading import valuation_context as vc
from trading.config import Config
from trading.valuation import pipeline as P
from trading.valuation import scenarios as sc

ROOT = Path(__file__).resolve().parents[2]
FIX = Path(__file__).resolve().parent / "fixtures"


# ---------------------------------------------------------------- FRED

def test_fred_empty_and_dot_are_missing_never_zero():
    raw = (FIX / "fred_DEXINUS_2026.txt").read_text()
    assert "\n2026-07-03,\n" in raw                 # a holiday as FRED serves it today
    rows = mf.parse_fred_csv(raw, "fred_usdinr", url="u", fetched_iso="f")
    dates = [r["date"] for r in rows]
    assert "2026-07-03" not in dates and dates == sorted(dates)
    assert all(r["value"] > 0 for r in rows)
    assert rows[-1]["date"] == "2026-10-16" and rows[-1]["value"] == 94.96
    assert rows[-1]["source"].startswith("FRED DEXINUS") and rows[-1]["url"] == "u"
    dot = "observation_date,SP500\n2026-10-15,7744.06\n2026-10-16,.\n2026-10-19,7690.73\n"
    got = mf.parse_fred_csv(dot, "fred_sp500")
    assert [r["date"] for r in got] == ["2026-10-15", "2026-10-19"]
    assert mf.parse_fred_csv("DATE,SP500\n2026-10-15,1.0\n", "fred_sp500")[0]["value"] == 1.0
    assert mf.parse_fred_csv(dot, "fred_dow") == []            # header names SP500
    assert mf.parse_fred_csv("", "fred_sp500") == []


def test_every_fred_fixture_parses_to_its_series():
    for series, fid in mf.FRED_SERIES.items():
        rows = mf.parse_fred_csv((FIX / f"fred_{fid}_2026.txt").read_text(), series)
        assert len(rows) > 150 and all(r["series"] == series for r in rows), fid
    assert mf.fred_kind("fred_usdinr") == "weekly" and mf.fred_kind("fred_sp500") == "daily"
    assert set(mf.FRED_SERIES) <= set(mf.SERIES)


# ----------------------------------------------------------------- NSE

@pytest.fixture(scope="module")
def payload():
    return json.loads((FIX / "nse_allIndices_2026-10-19.json").read_text())


def test_nse_rows_are_dated_by_the_snapshot_session_not_the_fetch(payload):
    rows = mf.parse_nse_all_indices(payload, mf.nse_index_names(), fetched_iso="2026-10-21T05:18")
    assert rows and {r["date"] for r in rows} == {"2026-10-19"}
    assert all(r["session_closed"] for r in rows)
    pe = {r["index"]: r["value"] for r in rows if r["series"].startswith("nse_pe:")}
    assert pe["NIFTY IT"] == 19.5 and pe["NIFTY 50"] == 19.33
    assert pe["NIFTY FINANCIAL SERVICES"] == 15.05            # alias of Fin Service
    pb = {r["index"]: r["value"] for r in rows if r["series"].startswith("nse_pb:")}
    assert pb["NIFTY IT"] == 5.52
    # every app index (the 16 + the six of 2026-10-21 + Nifty 50) is found
    assert set(pe) == set(dr.SECTOR_NSE_NAME.values())
    # the fixed-income row's "0" is no reading
    allrows = mf.parse_nse_all_indices(payload)
    assert not any(r["index"] == "NIFTY 10 YR BENCHMARK G-SEC" for r in allrows)


def test_an_intraday_snapshot_is_labelled_and_previous_day_never_dates_it(payload):
    live = dict(payload, timestamp="21-Oct-2026 11:43")
    rows = mf.parse_nse_all_indices(live, ["NIFTY IT"])
    # the row's previousDay still says 19-Oct (yesterday's session) — not used
    assert {r["date"] for r in rows} == {"2026-10-21"}
    assert not any(r["session_closed"] for r in rows) and "intraday" in rows[0]["source"]
    assert mf.nse_session("21-Oct-2026 15:30") == ("2026-10-21", True)
    assert mf.nse_session("21-Oct-2026 09:20") == ("2026-10-21", False)
    no_ts = dict(payload, timestamp="")
    r2 = mf.parse_nse_all_indices(no_ts, ["NIFTY IT"])
    assert {r["date"] for r in r2} == {"2026-10-19"}          # previousDayVal == last
    moved = {"data": [dict(next(r for r in payload["data"] if r["index"] == "NIFTY IT"),
                           previousDayVal=1.0)]}
    assert mf.parse_nse_all_indices(moved, ["NIFTY IT"]) == []   # cannot be dated


# ------------------------------------------------------- the six keys

SIX = {"Healthcare": ("NSE_INDEX|NIFTY HEALTHCARE", "NIFTY HEALTHCARE INDEX"),
       "Consumer Durables": ("NSE_INDEX|NIFTY CONSR DURBL", "NIFTY CONSUMER DURABLES"),
       "Oil & Gas": ("NSE_INDEX|NIFTY OIL AND GAS", "NIFTY OIL & GAS"),
       "Chemicals": ("NSE_INDEX|Nifty Chemicals", "NIFTY CHEMICALS"),
       "Cement": ("NSE_INDEX|Nifty Cement", "NIFTY CEMENT"),
       "Pvt Bank": ("NSE_INDEX|Nifty Pvt Bank", "NIFTY PRIVATE BANK")}


def test_the_six_verified_keys_and_their_nse_names():
    for name, (key, nse) in SIX.items():
        assert dr.SECTOR_INDEX_KEYS[name] == key and dr.SECTOR_NSE_NAME[name] == nse
    assert len(dr.SECTOR_INDEX_KEYS) == 22
    assert set(dr.SECTOR_INDEX_KEYS) <= set(dr.SECTOR_NSE_NAME)
    assert dr.SECTOR_INDEX_FIRST_BAR == {"Chemicals": "2025-11-10", "Cement": "2026-05-11"}


class _Broker:
    def __init__(self):
        self.calls = []

    def get_daily_candles(self, key, start, end):
        self.calls.append((key, start, end))
        idx = pd.bdate_range(start, end)
        return pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 2.0},
                            index=idx) if len(idx) else None


def test_the_tail_seeds_a_sector_the_store_never_held():
    br = _Broker()
    today = pd.Timestamp.today().normalize()
    recent = (today - pd.Timedelta(days=3)).date().isoformat()
    last_by = {k: recent for k in dr.SECTOR_INDEX_KEYS if k not in SIX}
    rows = dr.fetch_sector_tail(br, recent, last_by_sector=last_by)
    starts = {k: s for k, s, _e in br.calls}
    for name, (key, _n) in SIX.items():
        assert starts[key] < recent, name                   # seeded 30 days back
    assert starts["NSE_INDEX|Nifty IT"] > recent             # only after its own last row
    assert {r["sector"] for r in rows} >= set(SIX)


def test_the_history_fetch_can_be_asked_for_some_sectors_only():
    br = _Broker()
    dr.fetch_sector_history(br, "2026-05-01", "2026-05-08", sectors=["Cement", "IT"])
    assert {k for k, _s, _e in br.calls} == {"NSE_INDEX|Nifty Cement", "NSE_INDEX|Nifty IT"}


# ------------------------------------------------------------- mapping

SM = dict(Config().sector_map)


@pytest.mark.parametrize("ticker,index", [
    ("TATATECH", "IT"),                  # no app label → its industry (rule A)
    ("HINDPETRO", "Oil & Gas"),          # Oil & Gas industry, no Energy label
    ("RELIANCE", "Energy"),              # labelled Energy keeps Nifty Energy
    ("KIMS", "Healthcare"),
    ("APOLLOHOSP", "Pharma"),            # the Pharma label keeps Nifty Pharma
    ("ULTRACEMCO", "Infra"),             # Construction stays on Nifty Infra
    ("BLUESTARCO", "Consumer Durables"),
    ("SRF", "Chemicals"),                # the app's Chemicals label
    ("DLF", "Realty"),                   # the ticker override
    ("BOSCHLTD", None),                  # rule B NOT applied
    ("ADANIENT", None),
])
def test_one_mapping_for_sections_11_and_12(ticker, index):
    got = vc.index_for(f"{ticker}.NS", SM)
    assert got["index"] == index, got
    if index is None:
        assert got["label"] == "Nifty 50" and got["why"]


def test_construction_materials_go_to_nifty_cement_and_its_beta_waits_for_252_sessions():
    assert vc.INDUSTRY_INDEX_FOR["Construction Materials"] == "Cement"
    idx = pd.bdate_range("2026-05-11", periods=110)
    bench = pd.Series(range(100, 210), index=idx, dtype=float)
    stock = bench * 1.01
    b = vc.beta(stock, bench, min_bench_sessions=252)
    assert b["beta"] is None and "β needs 252" in b["why"]


def test_the_industry_record_matches_the_code():
    rec = {r["nifty500_industry"]: r["nse_index"]
           for r in csv.DictReader((ROOT / "data" / "industry_to_index.csv").open())}
    strict = {k: v for k, v in rec.items() if v != "NONE"}
    for ind, idx in strict.items():
        # the owner's Oil & Gas index replaces Energy for an unlabelled oil name
        want = "Oil & Gas" if ind.startswith("Oil Gas") else idx
        assert vc.INDUSTRY_INDEX_FOR[ind] == want, ind
    added = set(vc.INDUSTRY_INDEX_FOR) - set(strict)
    assert added == {"Healthcare", "Chemicals", "Consumer Durables", "Construction Materials"}


# ---------------------------------------------------- E101: live vs sheet

def test_the_live_chain_counts_e101_zero_for_every_sector():
    macro = {"usdinr_now": 95.0, "usdinr_base": 90.0}
    m, _miss = P._market(macro, {}, {"sector": "IT services / ER&D"}, None, {})
    assert m["usdinr"] == m["usdinr_base"]                 # E101 = 0
    assert P.live_inputs(macro, {"sector": "IT services / ER&D"}, {}, True)["usdinr"] is False
    assert P.ASSUMED["currency_effect_routes"]["value"] == []


def test_the_owners_tata_sheet_keeps_e101_and_e114_cell_for_cell():
    out = sc.fy_scenario(sc.SHEET_BASE, sc.SHEET_DRIVERS, sc.SHEET_MARKET)
    cells = out["cells"]
    # the workbook's own cached values (research/valuation/…2026-10-09.xlsx)
    assert (cells["E78"], cells["E101"], cells["E114"], cells["E116"]) == \
        (96.74, 1.94, -32.04, -34.01)
    assert (cells["E19"], cells["E29"], cells["E135"]) == (1097.69, 18.28, 690.62)
    assert sc.SHEET_MARKET["usdinr_base"] == 94.9
