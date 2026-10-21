"""VALFIX batch 3 — the web half (owner choices 2026-10-21). Money impact
NONE — display only; no gate, stop or size reads any of it.

Pins:
  * every §12 row prints the own target AND the market-adjusted target with
    its legs: Sensex at selection and now, β measured beside typed β = 1,
    the sector leg 1:1 beside measured, the rates leg, the currency leg
    through EPS (its tier named), the US indices counted 0; "verdict now"
    reads the market-adjusted range; buy-below = market-adjusted × (1 −
    the sentiment margin);
  * no double count: the own target's P/E bridge reads the Sensex / Nifty
    to the SELECTION DATE (the old code read them to today, then the market
    leg counted selection → now a second time);
  * the selection date: typed (inputs table) → the §11 target's date → the
    publication date; the "use as §11 target" button pushes the row's own
    selection date, and the row then names it as pushed;
  * the sector-currency table: owner-only, a bad value refuses the whole
    table, a page printed unread refuses to save; its default feeds BRSR's
    EUR split;
  * the sector total sits beside the target and never moves it;
  * NSE's sector P/E prints with its session date;
  * the remap list on the page holds all 132 rule-A names (rule B not);
  * the market-feeds job stores FRED + NSE (dated by NSE's session),
    replaces an intraday NSE reading with the close, isolates a failure;
  * the sector backfill fills each new index from its own first bar;
  * no dict this batch hands a template uses a dict-method name as a key.
"""
from __future__ import annotations

import csv
import json
import re
from datetime import date, timedelta
from html import escape, unescape
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from trading import data_refresh as dr
from trading import macro_feeds as mf
from trading.config import Config
from web import auth as auth_mod
from web import scheduler, state
from web import valuation_chain_view as cv
from web import valuation_market_view as mv
from web import valuation_view as vv
from web.main import app

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "trading" / "tests" / "fixtures"
TODAY = date.today()
DICT_METHODS = {"values", "items", "keys", "get", "update", "pop", "popitem", "copy",
                "clear", "setdefault", "fromkeys"}


def _owner() -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD}, follow_redirects=False)
    assert r.status_code == 303
    return c


def _viewer(monkeypatch) -> TestClient:
    monkeypatch.setitem(auth_mod.SEEDED_USERS, "viewer@sourceborn.in",
                        {"role": "viewer", "password": "v-test", "name": "Friend"})
    c = TestClient(app)
    assert c.post("/login", data={"email": "viewer@sourceborn.in",
                                  "password": "v-test"}).status_code == 200
    return c


def _flat(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


def _chain_row(html: str, ticker: str) -> str:
    i = html.find("den_chain_rows_marker")
    j = html.find("</table>", i)
    for row in re.findall(r"<tr\b.*?</tr>", html[i:j], re.S):
        cell = re.search(r"<td[^>]*>(.*?)</td>", row, re.S)
        if cell and re.sub(r"<[^>]+>|\s", "", cell.group(1)).startswith(ticker):
            return row
    raise AssertionError(f"no §12 row for {ticker}")


def _fred_points(series: str, fid: str) -> list:
    rows = mf.parse_fred_csv((FIX / f"fred_{fid}_2026.txt").read_text(), series)
    return [(r["date"], r["value"]) for r in rows]


POINTS = {s: _fred_points(s, fid) for s, fid in mf.FRED_SERIES.items()}
NSE_PAYLOAD = json.loads((FIX / "nse_allIndices_2026-10-19.json").read_text())
NSE_ROWS = {r["series"]: r for r in mf.parse_nse_all_indices(NSE_PAYLOAD, mf.nse_index_names())}


def _fake_feeds():
    return SimpleNamespace(
        latest=lambda series: None,
        at=lambda series, when: None,
        inst_holding=lambda sym, pe: None,
        series_points=lambda s: list(POINTS.get(s) or (
            [(NSE_ROWS[s]["date"], NSE_ROWS[s]["value"])] if s in NSE_ROWS else [])),
        latest_row=lambda s: dict(NSE_ROWS[s]) if s in NSE_ROWS else None)


def _it_series() -> pd.Series:
    nifty = cv._file_index_field("nifty_close").dropna()
    return nifty[nifty.index >= "2023-01-01"] * 1.35


@pytest.fixture
def stored(monkeypatch):
    """A fake settings store, fixed peers, the FRED / NSE fixtures as the
    stored feeds, a stored Nifty IT series — no Mongo, no network."""
    docs: dict = {}
    monkeypatch.setattr(vv.db, "save_settings",
                        lambda d, doc_id="default": docs.setdefault(doc_id, {}).update(d))
    monkeypatch.setattr(vv.db, "load_settings",
                        lambda doc_id="default": dict(docs.get(doc_id, {})))
    vv.LAST["targets"], vv.LAST["assumptions"] = {}, {}
    cv.LAST_ASSUMPTIONS.clear()
    cv.LAST_INPUTS.clear()
    mv.LAST_SECTOR_FX.clear()
    mv.clear_caches()
    cv.clear_feed_cache()
    monkeypatch.setattr(cv, "peer_multiples",
                        lambda method, exclude="": {"n": 3, "pe_median": 24.0, "pb_median": 2.0,
                                                    "ev_ebitda_median": 9.0,
                                                    "ev_sales_median": 2.5, "source": "test peers"})
    monkeypatch.setattr(cv, "peer_table_rows", lambda: {})
    monkeypatch.setattr(cv, "_macro_feeds", _fake_feeds)
    monkeypatch.setattr(cv, "_held_names", lambda p: [])
    it = _it_series()
    monkeypatch.setattr(vv, "_sector_table", lambda since=None: {"IT": it.copy()})
    yield docs
    vv.LAST["targets"], vv.LAST["assumptions"] = {}, {}
    cv.LAST_ASSUMPTIONS.clear()
    cv.LAST_INPUTS.clear()
    mv.LAST_SECTOR_FX.clear()
    mv.clear_caches()
    cv.clear_feed_cache()


def _tcs(c=None) -> tuple[str, str]:
    html = (c or _owner()).get("/den?chain=TCS").text
    row = _chain_row(html, "TCS")
    return html, row


# -------------------------------------------------- both targets, every leg

def test_a_row_prints_both_targets_and_every_leg(stored):
    _html, row = _tcs()
    t = _flat(row)
    assert "den_chain_market_marker" in row and "den_chain_legs_marker" in row
    assert "own target ₹" in t and "market-adjusted" in t
    assert 'data-den-chain-target-now="measured"' in row
    assert 'data-den-chain-target-now="typed"' in row and "(β = 1, 1:1)" in t
    assert "legs since 2026-05-15 (results publication date — period end + 45 days (ASSUMED))" in t
    sx = re.search(r"Sensex ([\d,.]+) \(2026-05-15\) → ([\d,.]+) \(2026-07-17\)", t)
    assert sx, "the Sensex at selection and now"
    assert "typed β = 1" in t and "measured (" in t
    assert "Nifty IT" in t and "coefficient 1:1 and" in t and "minus Sensex" in t
    assert "10-year yield" in t
    for name in ("S&P 500", "Nasdaq", "Dow Jones"):
        assert name in t                                   # TCS is IT: Nasdaq shown
    assert t.count("— counted 0") >= 3 and "already inside the Sensex: δ 0.286" in t
    assert "verdict now" in t and "den_chain_verdict_now_marker" in row
    assert "own target" in t and 'data-den-chain-buy-below' in row
    assert "sentiment margin" in t


def test_the_currency_leg_names_its_tier_and_the_rbi_rate_is_printed_beside(stored):
    _html, row = _tcs()
    t = _flat(row)
    assert 'data-den-chain-fx-leg="brsr"' in row               # TCS files BRSR exports 94.1%
    assert "exports from India only" in t and "exports 94.1% of turnover" in t
    assert "USD/INR 95.97 (2026-05-15) →" in t                 # FRED, dated
    assert "EUR/INR" in t and "Fed H.10 weekly" in t
    assert "rupee row E101 counted 0 in this chain" in t


def test_no_double_count_the_bridge_reads_the_market_to_the_selection_date(stored):
    rec = cv.row_for("TCS.NS", state.cfg(), None)
    m = rec["macro_inputs"]
    sensex = cv._file_index_field("sensex_close").dropna()
    at = lambda d: float(sensex[sensex.index <= d].iloc[-1])   # noqa: E731
    assert m["index_end_date"] == "2026-05-15" and m["index_end_is_selection"] is True
    assert m["sensex_end"] == pytest.approx(at("2026-05-15"))
    assert m["sensex_start"] == pytest.approx(at("2026-03-31"))
    leg = rec["market"]["sensex"]
    assert leg["start_date"] == "2026-05-15" and leg["start"] == pytest.approx(m["sensex_end"])
    # the bridge's part × the market leg's part = the whole move, counted once
    whole = leg["end"] / m["sensex_start"]
    assert (m["sensex_end"] / m["sensex_start"]) * (1 + leg["pct"] / 100) == pytest.approx(whole)
    assert rec["market"]["bridge_end"] == "2026-05-15"


def test_a_typed_selection_date_and_shares_win(stored):
    c = _owner()
    r = c.post("/den/valuation/inputs",
               data={"ticker": "tcs", "usd_share_pct": "48.3", "eur_share_pct": "15.2",
                     "selection_date": "2026-06-15"}, follow_redirects=False)
    assert "vi=saved" in r.headers["location"]
    row = stored["valuation_inputs"]["inputs"]["TCS"]
    assert (row["usd_share_pct"], row["eur_share_pct"], row["selection_date"]) == \
        (48.3, 15.2, "2026-06-15")
    _html, trow = _tcs(c)
    t = _flat(trow)
    assert "legs since 2026-06-15 (typed in the inputs table)" in t
    assert 'data-den-chain-fx-leg="typed"' in trow and "USD 48.3% · EUR 15.2%" in t
    rec = cv.row_for("TCS.NS", state.cfg(), None)
    assert rec["macro_inputs"]["index_end_date"] == "2026-06-15"
    bad = c.post("/den/valuation/inputs", data={"ticker": "tcs", "usd_share_pct": "80",
                                                "eur_share_pct": "30"}, follow_redirects=False)
    assert "vi=invalid" in bad.headers["location"]


def test_the_push_carries_the_selection_date_and_the_row_reads_it_back(stored):
    c = _owner()
    _html, row = _tcs(c)
    form = re.search(r'<form method="post" action="/den/valuation".*?</form>', row, re.S)
    assert form, "the use-as-§11 button"
    asof = re.search(r'name="asof" value="([^"]+)"', form.group(0)).group(1)
    basis = re.search(r'name="basis" value="([^"]+)"', form.group(0)).group(1)
    assert asof == "2026-05-15" and "valued as of 2026-05-15" in basis
    target = re.search(r'name="target" value="([^"]+)"', form.group(0)).group(1)
    r = c.post("/den/valuation", data={"ticker": "TCS.NS", "target": target, "asof": "2026-06-01",
                                       "basis": basis}, follow_redirects=False)
    assert "val=saved" in r.headers["location"]
    _h2, row2 = _tcs(c)
    t2 = _flat(row2)
    assert "legs since 2026-06-01 (the §11 target's date (pushed from §12))" in t2


def test_the_sector_currency_table_is_owner_only_and_refuses_a_bad_value(stored, monkeypatch):
    c = _owner()
    i = mv.sector_names().index("IT services / ER&D")
    r = c.post("/den/valuation/sector-fx", data={"loaded": "1", f"fx_usd_{i}": "60",
                                                  f"fx_eur_{i}": "20", f"fx_pp_{i}": "0.3"},
               follow_redirects=False)
    assert "sfx=saved" in r.headers["location"]
    assert stored["valuation_sector_fx"]["sectors"] == {
        "IT services / ER&D": {"usd_share_pct": 60.0, "eur_share_pct": 20.0,
                               "pass_through_pp": 0.3}}
    _html, row = _tcs(c)
    t = _flat(row)
    assert "EUR part by your IT services / ER&D default EUR 20 : USD 60" in t
    assert "pt = 0.3 ÷" in t and "your sector table (IT services / ER&D)" in t
    page = c.get("/den").text
    assert "den_sector_fx_marker" in page and f'name="fx_usd_{i}" value="60"' in page
    before = dict(stored["valuation_sector_fx"])
    for data in ({"loaded": "1", f"fx_usd_{i}": "abc"},
                 {"loaded": "1", f"fx_usd_{i}": "70", f"fx_eur_{i}": "40"},
                 {"loaded": "0", f"fx_usd_{i}": "10"}):
        bad = c.post("/den/valuation/sector-fx", data=data, follow_redirects=False)
        assert "sfx=invalid" in bad.headers["location"], data
    assert stored["valuation_sector_fx"] == before
    v = _viewer(monkeypatch)
    assert v.post("/den/valuation/sector-fx", data={"loaded": "1"}).status_code == 403


def test_the_sector_total_sits_beside_the_target_and_never_moves_it(stored, monkeypatch):
    base = [{"ticker": t, "method": "earnings", "shares_outstanding": 1e9, "pat": 1e11,
             "sector": "IT services / ER&D", "family": "earnings",
             "fy": {"2026-03-31": {"shares": 1e9, "pat": 1e11}}}
            for t in ("TCS.NS", "INFY.NS")]
    by = {"earnings": [{"ticker": "TCS.NS", "price": 2000.0}, {"ticker": "INFY.NS", "price": 1000.0}]}
    monkeypatch.setitem(cv._PEER_CACHE, "base", base)
    monkeypatch.setitem(cv._PEER_CACHE, "by_method", by)
    rec = cv.row_for("TCS.NS", state.cfg(), None)
    st = rec["sector_total"]
    assert st["status"] == "OK" and st["n_priced"] == 2
    assert st["mcap_cr"] == pytest.approx(3000e9 / 1e7)
    assert st["pe_now"] == pytest.approx(3000e9 / 2e11)
    assert st["nse"]["index"] == "NIFTY IT" and st["nse"]["pe"] == 19.5
    assert st["nse"]["date"] == "2026-10-19" and st["nse"]["closed"] is True
    monkeypatch.setattr(mv, "sector_block", lambda rec, idx=None: {"status": "DATA_NEEDED",
                                                                    "why": "off", "nse": {}})
    cv.clear_row_cache()
    off = cv.row_for("TCS.NS", state.cfg(), None)
    assert off["range"]["base"] == rec["range"]["base"]
    assert off["market"]["target_now"] == rec["market"]["target_now"]


def test_nse_pe_prints_with_its_session_on_the_row(stored):
    _html, row = _tcs()
    t = _flat(row)
    assert "den_chain_nse_pe_marker" in row
    assert "NSE NIFTY IT: P/E 19.5 · P/B 5.52 (session 2026-10-19" in t
    assert "moves no target" in t


def test_the_page_lists_every_rule_a_name_and_no_rule_b_name(stored):
    page = _owner().get("/den").text
    assert "den_chain_remap_marker" in page
    rows = list(csv.DictReader((ROOT / "data" / "remapped_names.csv").open()))
    for r in rows:
        mark = f'data-den-chain-remap-row="{escape(r["symbol"])}"'      # ARE&M → ARE&amp;M
        if r["change_rule"].startswith("A"):
            assert mark in page, r["symbol"]
        else:
            assert mark not in page, r["symbol"]
    live = mv.remapped_names(dict(Config().sector_map))
    assert live["n"] >= 132
    tt = next(x for x in live["rows"] if x["ticker"] == "TATATECH")
    assert (tt["old"], tt["new"]) == ("Nifty 50", "Nifty IT")


def test_no_dict_handed_to_a_template_uses_a_dict_method_name(stored):
    rec = cv.row_for("TCS.NS", state.cfg(), None)

    def walk(x, path="rec"):
        if isinstance(x, dict):
            bad = DICT_METHODS & set(map(str, x))
            assert not bad, f"{path}: {bad}"
            for k, v in x.items():
                walk(v, f"{path}.{k}")
        elif isinstance(x, (list, tuple)):
            for i, v in enumerate(x):
                walk(v, f"{path}[{i}]")

    walk(rec.get("market"), "market")
    walk(rec.get("sector_total"), "sector_total")
    walk(mv.sector_fx_section(), "sfx")
    walk(mv.remapped_names(dict(Config().sector_map)), "remapped")
    page = _owner().get("/den?chain=TCS").text
    assert "built-in method" not in page and "bound method" not in page


# ------------------------------------------------------- the feeds job

class _FakeMacroDB:
    def __init__(self):
        self.rows: dict = {}

    def load(self, series=None):
        out = [dict(r) for r in self.rows.values() if series is None or r["series"] == series]
        return sorted(out, key=lambda r: r["date"])

    def save(self, rows):
        for r in rows:
            self.rows[(r["series"], r["date"])] = dict(r)
        return len(rows)


def _job(monkeypatch, fred, nse):
    from web import db
    fake = _FakeMacroDB()
    monkeypatch.setattr(db, "is_connected", lambda: True)
    monkeypatch.setattr(db, "load_macro_rows", fake.load)
    monkeypatch.setattr(db, "save_macro_rows", fake.save)
    monkeypatch.setattr(mf, "fetch_fred", fred)
    monkeypatch.setattr(mf, "fetch_nse_all_indices", nse)
    return fake


def _fixture_fred(series, last_iso="", timeout=30.0):
    fid = mf.FRED_SERIES[series]
    rows = mf.parse_fred_csv((FIX / f"fred_{fid}_2026.txt").read_text(), series)
    return [r for r in rows if not last_iso or r["date"] > last_iso]


def _journal_since(n0):
    return [x for x in state.portfolio().decision_journal[n0:]
            if str(x.get("action", "")).startswith("MARKET_FEEDS")]


def test_the_job_stores_fred_and_nse_dated_by_nses_session(monkeypatch):
    fake = _job(monkeypatch, _fixture_fred, lambda timeout=30.0: json.loads(json.dumps(NSE_PAYLOAD)))
    n0 = len(state.portfolio().decision_journal)
    scheduler._market_feeds_refresh()
    assert {s for s, _d in fake.rows} >= set(mf.FRED_SERIES)
    nse = [r for (s, _d), r in fake.rows.items() if s.startswith("nse_pe:")]
    assert nse and {r["date"] for r in nse} == {"2026-10-19"}
    assert all(r["session_closed"] for r in nse)
    j = _journal_since(n0)
    assert any(x["action"] == "MARKET_FEEDS" and "USD/INR" in x["why"] and "session 2026-10-19"
               in x["why"] for x in j)
    # a second pass with nothing new is quiet about what landed
    n1 = len(state.portfolio().decision_journal)
    scheduler._market_feeds_refresh()
    assert not any(x["action"] == "MARKET_FEEDS" for x in _journal_since(n1))


def test_an_intraday_reading_is_replaced_by_the_close_and_a_failure_is_isolated(monkeypatch):
    today = mf._today_ist()
    stamp = today.strftime("%d-%b-%Y")
    live = dict(NSE_PAYLOAD, timestamp=f"{stamp} 11:00")
    shut = dict(NSE_PAYLOAD, timestamp=f"{stamp} 15:30")
    calls = []

    def nse(timeout=30.0):
        calls.append(1)
        return json.loads(json.dumps(live if len(calls) == 1 else shut))

    def dead(series, last_iso="", timeout=30.0):
        raise ConnectionError("FRED unreachable")
    fake = _job(monkeypatch, dead, nse)
    n0 = len(state.portfolio().decision_journal)
    scheduler._market_feeds_refresh()
    it = fake.rows[("nse_pe:NIFTY IT", today.isoformat())]
    assert it["session_closed"] is False and "intraday" in it["source"]
    j = _journal_since(n0)
    assert any(x["action"] == "MARKET_FEEDS_FAILED" and "FRED" in x["why"] for x in j)
    assert any(x["action"] == "MARKET_FEEDS" for x in j)      # NSE landed anyway
    scheduler._market_feeds_refresh()                         # the 15:35 pass
    it2 = fake.rows[("nse_pe:NIFTY IT", today.isoformat())]
    assert it2["session_closed"] is True and len(calls) == 2
    scheduler._market_feeds_refresh()                         # the close is held: no fetch
    assert len(calls) == 2


def test_a_stale_fred_reading_is_flagged_on_the_row():
    old = (TODAY - timedelta(days=30)).isoformat()
    fresh = (TODAY - timedelta(days=3)).isoformat()
    assert mv._fred_stale({"status": "OK", "end_date": old}, "fred_usdinr")["stale"] is True
    assert mv._fred_stale({"status": "OK", "end_date": fresh}, "fred_usdinr")["stale"] is False
    assert mv._fred_stale({"status": "OK", "end_date": (TODAY - timedelta(days=10)).isoformat()},
                          "fred_sp500")["stale"] is True             # daily: 7 days


def test_the_backfill_fills_each_new_index_from_its_own_first_bar(monkeypatch):
    from web import db
    deep = (TODAY - timedelta(days=900)).isoformat()
    old16 = [k for k in dr.SECTOR_INDEX_KEYS
             if k not in ("Healthcare", "Consumer Durables", "Oil & Gas", "Chemicals",
                          "Cement", "Pvt Bank")]
    monkeypatch.setattr(db, "is_connected", lambda: True)
    monkeypatch.setattr(db, "load_sector_rows",
                        lambda since_iso="": [{"date": deep, "sector": k, "close": 1.0}
                                              for k in old16])
    monkeypatch.setattr(db, "save_sector_rows", lambda rows: len(rows))
    calls = []

    class _B:
        def get_daily_candles(self, key, start, end):
            calls.append((key, start, end))
            idx = pd.bdate_range(start, end)
            return pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 2.0},
                                index=idx) if len(idx) else None
    monkeypatch.setattr("trading.data.history_broker", lambda: _B())
    scheduler._SECTOR_BACKFILL_DAY.clear()
    try:
        rep = scheduler.backfill_sector_history(days=760, force=True)
    finally:
        scheduler._SECTOR_BACKFILL_DAY.clear()
    asked = {k: s for k, s, _e in calls}
    assert set(asked) == {dr.SECTOR_INDEX_KEYS[k] for k in
                          ("Healthcare", "Consumer Durables", "Oil & Gas", "Chemicals",
                           "Cement", "Pvt Bank")}
    assert asked["NSE_INDEX|Nifty Cement"] == "2026-05-11"
    assert asked["NSE_INDEX|Nifty Chemicals"] == "2025-11-10"
    assert rep["fetched"] > 0


# ------------------------------------------------- review fixes, pinned

def test_a_currency_factor_of_exactly_zero_reaches_the_target(stored, monkeypatch):
    # the old code read `factor or 1.0`: a real 0 became "no currency effect"
    from trading.valuation import currency as CUR
    real = CUR.fx_eps
    monkeypatch.setattr(CUR, "fx_eps",
                        lambda *a, **k: {**real(*a, **k), "status": "OK", "factor": 0.0})
    rec = cv.row_for("TCS.NS", state.cfg(), None)
    typed = rec["market"]["legs"]["typed"]
    assert typed["fx_factor"] == 0.0 and typed["target"] == 0.0
    assert rec["market"]["buy_below"] is None


def test_a_totals_build_without_the_store_is_not_kept_for_the_day(monkeypatch):
    mv.clear_caches()
    builds, saved = [], []
    monkeypatch.setattr(vv.db, "load_settings", lambda doc_id="default": {})
    monkeypatch.setattr(vv.db, "save_settings", lambda d, doc_id="default": saved.append(doc_id))
    monkeypatch.setattr(mv, "_build_totals",
                        lambda base: (builds.append(1), ({"earnings": {}}, False))[1])
    try:
        mv.totals_built([{}])
        mv.totals_built([{}])
        assert len(builds) == 2 and saved == []          # rebuilt once the store answers
        monkeypatch.setattr(mv, "_build_totals",
                            lambda base: (builds.append(1), ({"earnings": {}}, True))[1])
        mv.totals_built([{}])
        mv.totals_built([{}])
        assert len(builds) == 3 and saved == [mv.TOTALS_DOC_ID]
    finally:
        mv.clear_caches()


def test_web_tests_cannot_reach_the_macro_feed_network():
    with pytest.raises(ConnectionError):
        mf.fetch_fred("fred_sp500")
    with pytest.raises(ConnectionError):
        mf.fetch_nse_all_indices()
    secs, src = mf.fetch_gsec_list()
    assert secs and "blocked" in src


def test_a_selection_after_the_rows_price_leaves_the_own_target_standing(stored):
    # TCS's stored price here is the repo copy's (2026-07-17): legs from
    # 2026-08-01 to 07-17 would run backwards — the own target stands, said
    c = _owner()
    r = c.post("/den/valuation/inputs", data={"ticker": "tcs", "selection_date": "2026-08-01"},
               follow_redirects=False)
    assert "vi=saved" in r.headers["location"]
    rec = cv.row_for("TCS.NS", state.cfg(), None)
    mk = rec["market"]
    assert mk["legs"]["status"] == "SAME_DAY" and mk["target_now"] == rec["range"]["base"]
    assert "older than the selection date (2026-08-01)" in mk["price_before_selection"]
    future = (TODAY + timedelta(days=5)).isoformat()
    bad = c.post("/den/valuation/inputs", data={"ticker": "tcs", "selection_date": future},
                 follow_redirects=False)
    assert "vi=invalid" in bad.headers["location"] and "future" in unescape(
        bad.headers["location"].replace("%20", " "))


def test_the_totals_build_reads_the_store_in_two_batches_never_per_name(monkeypatch):
    from web import db
    reads = []
    monkeypatch.setattr(db, "is_connected", lambda: True)
    monkeypatch.setattr(db, "history_docs",
                        lambda tickers, rows=0: reads.append((list(tickers), rows)) or {})
    base = [{"ticker": t, "method": "earnings", "shares_outstanding": 1e6,
             "fy": {"2025-03-31": {}, "2026-03-31": {}}}
            for t in ("TCS.NS", "NOTINZIP1.NS", "NOTINZIP2.NS")]
    groups, complete = mv._build_totals(base)
    assert len(reads) == 2 and complete is True
    assert reads[0][0] == ["NOTINZIP1.NS", "NOTINZIP2.NS"]      # FY-end closes the zip lacks
    assert reads[1] == (["TCS.NS", "NOTINZIP1.NS", "NOTINZIP2.NS"], mv.HIGH_ROWS)
    assert set(groups) == {"earnings"}


def test_fred_staleness_counts_back_from_the_rows_own_price_date():
    # the move ends at the row's price date on purpose; "stale" is measured
    # from there, not from today (the old flag read 97 days on a repo price)
    mv_ = {"status": "OK", "end_date": "2026-07-16"}
    assert mv._fred_stale(mv_, "fred_sp500", "2026-07-17")["stale"] is False
    assert mv._fred_stale(mv_, "fred_sp500", "2026-07-30")["stale"] is True
    assert mv._fred_stale(mv_, "fred_usdinr", "2026-07-25")["stale"] is False   # weekly: 14
