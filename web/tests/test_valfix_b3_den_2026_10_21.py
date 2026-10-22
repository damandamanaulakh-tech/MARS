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
from trading import valuation_context as vc
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
    # the rates leg APPLIED (the OK branch's multiplier — "10-year yield" alone
    # also matches the not-applied branch; review 2026-10-21)
    assert re.search(r"10-year yield [\d.]+% \(2026-05-15\) → [\d.]+% \(.*?\) · × \d\.\d{3} · "
                     r"one extra point", t), "the rates leg applied on an earnings route"
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


class _FrozenDate(date):
    """date.today() fixed at 2026-10-21 — the backfill reads `from datetime
    import date` at call time, so patching the datetime module's `date` is
    what reaches it."""

    @classmethod
    def today(cls):
        return cls(2026, 10, 21)


def test_the_backfill_fills_each_new_index_from_its_own_first_bar(monkeypatch):
    # the clock is frozen (review 2026-10-21): the window is today − 760 days,
    # and from 2027-12-11 that start passes Chemicals' first bar — an unfrozen
    # assertion would fail then with no code change
    import datetime as _dt_mod
    from web import db
    monkeypatch.setattr(_dt_mod, "date", _FrozenDate)
    deep = (_FrozenDate.today() - timedelta(days=900)).isoformat()
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
    from web.tests import conftest
    assert set(conftest.MACRO_FEED_FETCHERS) >= {"fetch_fred", "fetch_nse_all_indices",
                                                 "fetch_rbi_home"}
    with pytest.raises(ConnectionError):
        mf.fetch_fred("fred_sp500")
    with pytest.raises(ConnectionError):
        mf.fetch_nse_all_indices()
    with pytest.raises(ConnectionError):
        mf.fetch_rbi_home()
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


# ============================================ review fixes, round 2 (2026-10-21)
# Builder C: the rates leg (C1-C3), proof by read-back (C4), the feeds job
# (C5, C7), the page truths (C9-C15) and the test-quality pins (C16-C20).

def _yields(monkeypatch, committed: dict, stored_pts: dict) -> None:
    """The committed 10-year file (vv._bond_series) and the stored Upstox
    G-sec readings (the feed: series_points + latest) — the two sources the
    rates leg joins, set by the test."""
    base = pd.Series(list(committed.values()), index=pd.to_datetime(list(committed)))
    pts = sorted(stored_pts.items())
    feeds = _fake_feeds()
    inner = feeds.series_points
    feeds.series_points = lambda s: list(pts) if s == "gsec10y_yield" else inner(s)
    feeds.latest = lambda s: ((pts[-1][1], pts[-1][0], "Upstox test G-sec")
                              if s == "gsec10y_yield" and pts else None)
    monkeypatch.setattr(vv, "_bond_series", lambda: base.copy())
    monkeypatch.setattr(cv, "_macro_feeds", lambda: feeds)
    cv.clear_feed_cache()
    cv.clear_row_cache()


def _rates_line(row: str) -> str:
    m = re.search(r'<div data-den-chain-leg="rates".*?</div>', row, re.S)
    assert m, "the rates line"
    return re.sub(r"\s+\)", ")", _flat(m.group(0))).strip()


# --- C1: a book target already reads today's yield — the rates leg counts 0

def test_a_book_route_counts_the_rates_leg_0_and_says_why(stored):
    rec = cv.row_for("HDFCBANK.NS", state.cfg(), None)
    assert rec["range"]["status"] == "OK" and rec["range"]["family"] == "book"
    mk = rec["market"]
    rt = mk["rates"]
    assert rt["status"] == "COUNTED_0" and rt["multiplier"] is None
    assert rt["why"] == mv.RATES_COUNTED_0_WHY["book"]
    assert rt["rate_then_pct"] is not None and rt["rate_now_pct"] is not None   # shown beside
    cols = [mk["legs"][c] for c in ("typed", "measured") if mk["legs"].get(c)]
    assert cols and all(c["rates_factor"] is None for c in cols)
    assert "rates leg not applied — " + mv.RATES_COUNTED_0_WHY["book"] in mk["legs"]["notes"]
    row = _chain_row(_owner().get("/den?chain=HDFCBANK").text, "HDFCBANK")
    assert 'data-den-chain-rates-status="COUNTED_0"' in row
    line = _rates_line(row)
    assert line.startswith("rates leg: counted 0 — the book-route target already reads today's "
                           "10-year yield (COE = rf + β×ERP)")
    assert "× " not in line and "rates ×" not in _flat(row)


def test_an_earnings_route_still_applies_the_rates_leg(stored):
    rec = cv.row_for("TCS.NS", state.cfg(), None)
    rt = rec["market"]["rates"]
    assert rec["range"]["family"] == "earnings" and rt["status"] == "OK"
    assert rec["market"]["legs"]["typed"]["rates_factor"] == pytest.approx(rt["multiplier"])
    assert rec["market"]["legs"]["measured"]["rates_factor"] == pytest.approx(rt["multiplier"])
    assert not any(n.startswith("rates leg not applied") for n in rec["market"]["legs"]["notes"])


# --- C2: the then-reading's age

def test_a_then_yield_more_than_30_days_before_selection_is_data_needed(stored, monkeypatch):
    # the June file read across the gap before the stored Upstox tail used to
    # pass as "the yield at the selection date"
    _yields(monkeypatch, {"2026-03-02": 7.10, "2026-03-09": 7.05}, {})
    rec = cv.row_for("TCS.NS", state.cfg(), None)
    rt = rec["market"]["rates"]
    assert rt["status"] == "DATA_NEEDED" and rt["multiplier"] is None
    assert rt["why"] == ("10-year yield at the selection date unknown — nearest stored reading "
                         "2026-03-09, 67 days earlier")
    assert rec["market"]["legs"]["typed"]["rates_factor"] is None
    line = _rates_line(_tcs()[1])
    assert "10-year yield not applied — 10-year yield at the selection date unknown — nearest " \
           "stored reading 2026-03-09, 67 days earlier" in line
    assert mv.ASSUMED["rate_then_max_gap_days"]["value"] == 30


def test_a_then_yield_6_to_30_days_old_prints_stale_and_is_applied(stored, monkeypatch):
    _yields(monkeypatch, {"2026-05-05": 7.00, "2026-06-08": 6.90}, {"2026-07-16": 6.50})
    rt = cv.row_for("TCS.NS", state.cfg(), None)["market"]["rates"]
    line = _rates_line(_tcs()[1])
    assert "7.00% (2026-05-05, STALE — 10 days before the selection date)" in line
    assert rt["status"] == "OK" and rt.get("rate_then_stale") is True
    assert rt.get("rate_then_gap_days") == 10 and rt["rate_then_date"] == "2026-05-05"
    assert mv.ASSUMED["rate_then_stale_days"]["value"] == 5
    # 3 days (a weekend) is fresh
    _yields(monkeypatch, {"2026-05-12": 7.00, "2026-06-08": 6.90}, {"2026-07-16": 6.50})
    mv.clear_caches()
    rt2 = cv.row_for("TCS.NS", state.cfg(), None)["market"]["rates"]
    assert rt2["status"] == "OK" and rt2["rate_then_stale"] is False
    assert "STALE" not in _rates_line(_tcs()[1]).split("→")[0]


# --- C3: the now-reading at the row's own price date

def test_the_rates_leg_reads_the_yield_on_the_rows_own_price_date(stored, monkeypatch):
    # TCS's price here is the repo copy's 2026-07-17: the Sensex, sector and
    # currency legs end there, so the yield must too — not the feed's newest
    _yields(monkeypatch, {"2026-05-15": 7.00, "2026-06-08": 6.90},
            {"2026-07-16": 6.50, "2026-10-20": 6.00})
    rec = cv.row_for("TCS.NS", state.cfg(), None)
    assert rec["price_date"] == "2026-07-17"
    rt = rec["market"]["rates"]
    assert rt["status"] == "OK"
    assert (rt["rate_now_pct"], rt.get("rate_now_date")) == (6.50, "2026-07-16")
    assert rt["rate_now_source"] == "Upstox G-sec 2026-07-16" and rt["rate_now_stale"] is False
    erp, g = vc.ASSUMED["equity_risk_premium_pct"], vc.ASSUMED["long_run_growth_pct"]
    assert rt["multiplier"] == pytest.approx((7.00 + erp - g) / (6.50 + erp - g))
    line = _rates_line(_tcs()[1])
    assert "→ 6.50% (Upstox G-sec 2026-07-16)" in line and "STALE" not in line


def test_a_now_yield_far_behind_the_price_date_prints_stale_with_its_age(stored, monkeypatch):
    _yields(monkeypatch, {"2026-05-15": 7.00, "2026-06-08": 6.90}, {"2026-10-20": 6.00})
    rt = cv.row_for("TCS.NS", state.cfg(), None)["market"]["rates"]
    assert (rt["rate_now_pct"], rt.get("rate_now_date")) == (6.90, "2026-06-08")
    assert rt["rate_now_stale"] is True and rt["rate_now_age_days"] == 39
    line = _rates_line(_tcs()[1])
    assert "→ 6.90% (10-year file 2026-06-08, STALE — 39 days before the row's price date)" in line


# --- C4: a save is proved by reading it back

def _post_it_fx(c, usd="60", eur="20"):
    i = mv.sector_names().index("IT services / ER&D")
    return c.post("/den/valuation/sector-fx", data={"loaded": "1", f"fx_usd_{i}": usd,
                                                     f"fx_eur_{i}": eur}, follow_redirects=False)


@pytest.mark.parametrize("connected", [False, True])
def test_a_sector_table_the_store_did_not_keep_is_said_unsaved(stored, monkeypatch, connected):
    from urllib.parse import unquote
    from web import db
    # save_settings returns silently when no database is connected and
    # swallows a refused write — the old route then said "stored"
    monkeypatch.setattr(vv.db, "save_settings", lambda d, doc_id="default": None)
    monkeypatch.setattr(db, "is_connected", lambda: connected)
    c = _owner()
    r = _post_it_fx(c)
    loc = unquote(r.headers["location"])
    assert "sfx=unsaved" in loc and "sfx=saved" not in loc
    want = mv.NOT_STORED_REFUSED if connected else mv.NOT_STORED_NO_DB
    assert want in loc and "lost at the next restart" in want
    assert "valuation_sector_fx" not in stored                  # nothing reached the store
    page = c.get(r.headers["location"]).text
    i = page.find("den_sector_fx_unsaved_marker")
    assert i > 0 and want in _flat(page[i:i + 600])
    assert "Sector table stored" not in page
    # the in-memory table still applies on this process — and says so on every open
    t = _flat(_tcs(c)[1])
    assert "EUR part by your IT services / ER&D default EUR 20 : USD 60" in t
    assert "den_sector_fx_unsaved_marker" in c.get("/den").text
    k = mv.sector_names().index("IT services / ER&D")
    assert f'name="fx_usd_{k}" value="60"' in c.get("/den").text


def test_a_sector_table_that_reads_back_is_stored_and_clears_the_unsaved_notice(stored, monkeypatch):
    real = vv.db.save_settings
    monkeypatch.setattr(vv.db, "save_settings", lambda d, doc_id="default": None)
    c = _owner()
    assert "sfx=unsaved" in _post_it_fx(c).headers["location"]
    monkeypatch.setattr(vv.db, "save_settings", real)        # the store answers again
    r = _post_it_fx(c, usd="55")
    assert r.headers["location"].endswith("sfx=saved#sector-fx")
    assert stored["valuation_sector_fx"]["sectors"]["IT services / ER&D"]["usd_share_pct"] == 55.0
    assert "den_sector_fx_unsaved_marker" not in c.get("/den").text


def test_a_totals_build_the_store_did_not_keep_says_so_on_the_row(stored, monkeypatch):
    base = [{"ticker": t, "method": "earnings", "shares_outstanding": 1e9, "pat": 1e11,
             "sector": "IT services / ER&D", "family": "earnings",
             "fy": {"2026-03-31": {"shares": 1e9, "pat": 1e11}}}
            for t in ("TCS.NS", "INFY.NS")]
    monkeypatch.setitem(cv._PEER_CACHE, "base", base)
    monkeypatch.setitem(cv._PEER_CACHE, "by_method", {"earnings": [
        {"ticker": "TCS.NS", "price": 2000.0}, {"ticker": "INFY.NS", "price": 1000.0}]})
    monkeypatch.setattr(mv, "_build_totals", lambda b: ({"earnings": {}}, True))
    monkeypatch.setattr(vv.db, "save_settings", lambda d, doc_id="default": None)
    _groups, src = mv.totals_built(base)
    assert src.startswith("process — NOT stored: no database connected")
    assert mv.TOTALS_DOC_ID not in stored
    rec = cv.row_for("TCS.NS", state.cfg(), None)
    assert rec["sector_total"]["built_source"] == src
    row = _tcs()[1]
    assert "den_chain_totals_unsaved_marker" in row
    assert "sector totals: process — NOT stored: no database connected" in _flat(row)


def test_a_totals_build_that_reads_back_is_stored_and_prints_no_notice(stored, monkeypatch):
    monkeypatch.setattr(mv, "_build_totals", lambda b: ({"earnings": {}}, True))
    _groups, src = mv.totals_built([{}])
    assert src == "built" and stored[mv.TOTALS_DOC_ID]["day"] == date.today().isoformat()


# --- C5 / C7: the feeds job counts what the store acknowledged, inside a budget

def test_rows_the_store_did_not_acknowledge_are_not_journaled_as_stored(monkeypatch):
    from web import db
    _job(monkeypatch, _fixture_fred, lambda timeout=30.0: json.loads(json.dumps(NSE_PAYLOAD)))
    monkeypatch.setattr(db, "save_macro_rows", lambda rows: 0)       # a refused write
    n0 = len(state.portfolio().decision_journal)
    scheduler._market_feeds_refresh()
    j = _journal_since(n0)
    assert not any(x["action"] == "MARKET_FEEDS" for x in j)
    fail = [x["why"] for x in j if x["action"] == "MARKET_FEEDS_FAILED"]
    assert len(fail) == 1
    for fid in mf.FRED_SERIES.values():
        assert re.search(rf"FRED {fid}: (\d+) of \1 row\(s\) NOT stored \(write refused\?\)",
                         fail[0]), fid
    assert re.search(r"NSE allIndices: (\d+) of \1 row\(s\) NOT stored", fail[0])


def test_a_partial_write_counts_only_the_acknowledged_rows(monkeypatch):
    from web import db
    fake = _job(monkeypatch, _fixture_fred, lambda timeout=30.0: json.loads(json.dumps(NSE_PAYLOAD)))
    real = fake.save
    monkeypatch.setattr(db, "save_macro_rows", lambda rows: max(0, real(rows) - 1))
    n0 = len(state.portfolio().decision_journal)
    scheduler._market_feeds_refresh()
    j = _journal_since(n0)
    ok = [x["why"] for x in j if x["action"] == "MARKET_FEEDS"]
    fail = [x["why"] for x in j if x["action"] == "MARKET_FEEDS_FAILED"]
    assert ok and fail
    rows = {fid: [r for (s, _d), r in fake.rows.items() if s == series]
            for series, fid in mf.FRED_SERIES.items()}
    for series, fid in mf.FRED_SERIES.items():
        n = len(rows[fid])
        assert f"FRED {fid}: 1 of {n} row(s) NOT stored" in fail[0]
        assert f"FRED {fid}; {n - 1} day(s))" in ok[0]          # what was acknowledged


def test_the_feeds_job_stops_on_its_time_budget_and_names_what_it_skipped(monkeypatch):
    import time as _time
    calls, nse_calls = [], []

    def slow(series, last_iso="", timeout=30.0):
        calls.append(series)
        _time.sleep(0.05)                                    # past a 0.01 s budget
        return _fixture_fred(series, last_iso)

    def nse(timeout=30.0):
        nse_calls.append(1)
        return json.loads(json.dumps(NSE_PAYLOAD))
    _job(monkeypatch, slow, nse)
    n0 = len(state.portfolio().decision_journal)
    scheduler._market_feeds_refresh(time_budget_s=0.01)
    first, *rest = list(mf.FRED_SERIES.items())
    assert calls == [first[0]] and nse_calls == []
    ok = [x["why"] for x in _journal_since(n0) if x["action"] == "MARKET_FEEDS"]
    skipped = ", ".join([f"FRED {fid}" for _s, fid in rest] + ["NSE allIndices"])
    assert len(ok) == 1 and f"STOPPED on the 0.01 s budget: {skipped} skipped — next slot" in ok[0]
    assert f"FRED {first[1]}; " in ok[0]                       # the one that ran landed


def test_the_feeds_budget_reads_its_env_and_defaults_to_90_seconds(monkeypatch):
    # the slot's own call (no argument) reads the env: a tiny budget stops it
    # after one series
    calls = []
    _job(monkeypatch, lambda series, last_iso="", timeout=30.0: (
        calls.append(series), __import__("time").sleep(0.05), [])[2],
         lambda timeout=30.0: {})
    monkeypatch.setenv("SB_MARKET_FEEDS_BUDGET_S", "0.01")
    scheduler._market_feeds_refresh()
    assert len(calls) == 1
    assert scheduler.MARKET_FEEDS_ASSUMED["budget_s"]["value"] == 90.0
    assert "30 s timeout" in scheduler.MARKET_FEEDS_ASSUMED["budget_s"]["source"]
    monkeypatch.delenv("SB_MARKET_FEEDS_BUDGET_S", raising=False)
    assert scheduler.market_feeds_budget_s() == 90.0
    monkeypatch.setenv("SB_MARKET_FEEDS_BUDGET_S", "45")
    assert scheduler.market_feeds_budget_s() == 45.0
    monkeypatch.setenv("SB_MARKET_FEEDS_BUDGET_S", "0")
    assert scheduler.market_feeds_budget_s() is None          # <= 0 = unbounded
    monkeypatch.setenv("SB_MARKET_FEEDS_BUDGET_S", "abc")
    assert scheduler.market_feeds_budget_s() == 90.0


# --- C9 / C10 / C11: the sector-total and NSE line

def test_a_failed_sector_total_prints_data_needed_not_blank_nse_numbers(stored, monkeypatch):
    def boom(rec, idx=None):
        raise RuntimeError("totals exploded")
    monkeypatch.setattr(mv, "sector_block", boom)
    row = _tcs()[1]
    m = re.search(r"data-den-chain-nse-pe>.*?</div>", row, re.S)
    nse = _flat(m.group(0))
    assert "P/E DATA_NEEDED — the sector totals could not be read — see the log" in nse
    assert "intraday" not in nse and "session ," not in nse and "P/E ·" not in nse


def test_a_sector_total_data_needed_names_its_reason(stored, monkeypatch):
    # nothing in the group priced: the block's reason lived in fy_why only
    monkeypatch.setitem(cv._PEER_CACHE, "base", [
        {"ticker": "TCS.NS", "method": "earnings", "shares_outstanding": 1e9, "pat": 1e11,
         "fy": {}}])
    monkeypatch.setitem(cv._PEER_CACHE, "by_method", {"earnings": []})
    monkeypatch.setattr(mv, "totals_built", lambda base: ({}, "built"))
    rec = cv.row_for("TCS.NS", state.cfg(), None)
    assert rec["sector_total"]["status"] == "DATA_NEEDED"
    assert rec["sector_total"]["why"] == "no group name is priced"
    assert "sector total DATA_NEEDED — no group name is priced" in _flat(_tcs()[1])


def test_a_short_index_history_is_never_called_a_52_week_high(stored, monkeypatch):
    it100 = _it_series().tail(100)
    monkeypatch.setattr(vv, "_sector_table", lambda since=None: {"IT": it100.copy()})
    nse = _flat(re.search(r"data-den-chain-nse-pe>.*?</div>", _tcs()[1], re.S).group(0))
    assert "its high over the 100 stored sessions (short of 52 weeks)" in nse
    assert "52-week high" not in nse
    hi = mv._index_below_high(it100)
    assert hi["n"] == 100 and hi["full_year"] is False
    assert mv._index_below_high(_it_series())["full_year"] is True


# --- C12 / C13 / C14: the verdict-now basis and the bridge note

def test_a_price_before_the_selection_date_is_the_verdict_lines_reason_too(stored):
    c = _owner()
    c.post("/den/valuation/inputs", data={"ticker": "tcs", "selection_date": "2026-08-01"},
           follow_redirects=False)
    t = _flat(_tcs(c)[1])
    assert "selected today" not in t                         # one truth per row
    # the verdict line AND the range cell both give the real reason
    assert t.count("older than the selection date (2026-08-01)") >= 2


def test_a_section_11_target_dated_today_renders_the_row(stored):
    # found while pinning C12: a SAME_DAY row's legs carry no rel_pct, and the
    # sector line tested `lg.rel_pct is not none` — an Undefined is "not none"
    # in Jinja, so '%+.2f' % Undefined raised and the WHOLE Den answered 500
    # for any §11 target dated today (the live path: type a target today)
    c = _owner()
    r = c.post("/den/valuation", data={"ticker": "TCS.NS", "target": "4000",
                                       "asof": TODAY.isoformat(), "basis": "typed"},
               follow_redirects=False)
    assert "val=saved" in r.headers["location"]
    page = c.get("/den?chain=TCS")
    assert page.status_code == 200
    rec = cv.row_for("TCS.NS", state.cfg(), None)
    assert rec["market"]["selection"]["date"] == TODAY.isoformat()
    assert rec["market"]["legs"]["status"] == "SAME_DAY"
    t = _flat(_chain_row(page.text, "TCS"))
    assert "minus Sensex" not in t                           # no relative move on a same-day row


def test_a_nifty_50_fallback_name_never_claims_a_measured_sector_coefficient(stored):
    rec = cv.row_for("ASIANPAINT.NS", state.cfg(), None)
    mk = rec["market"]
    assert mk["index"]["index"] is None and mk["now_column"] == "measured"
    t = _flat(_chain_row(_owner().get("/den?chain=ASIANPAINT").text, "ASIANPAINT"))
    assert "market-adjusted, measured β (no sector index — sector leg counted 0)" in t
    assert "measured β and sector coefficient" not in t
    # the typed column's verdict beside it claims no 1:1 sector leg either
    assert "(no sector index — sector leg counted 0) · β = 1: " in t
    # an indexed name keeps the full wording
    assert "market-adjusted, measured β and sector coefficient" in _flat(_tcs()[1])


def test_the_bridge_note_names_the_period_end_when_the_typed_date_is_earlier(stored):
    c = _owner()
    c.post("/den/valuation/inputs", data={"ticker": "tcs", "selection_date": "2026-03-16"},
           follow_redirects=False)
    rec = cv.row_for("TCS.NS", state.cfg(), None)
    assert rec["market"]["selection"]["date"] == "2026-03-16"
    assert rec["market"]["bridge_end"] == "2026-03-31"           # clamped up to the period end
    t = _flat(_tcs(c)[1])
    assert "to 2026-03-31 (the statements' period end — the typed date is earlier)" in t
    assert "2026-03-31 (the selection date)" not in t
    # the default (publication) date reads as the selection date
    c.post("/den/valuation/inputs/remove", data={"ticker": "TCS"}, follow_redirects=False)
    cv.clear_row_cache()
    assert 'data-den-chain-bridge-end="selection"' in _tcs(c)[1]


# --- C15: the remap list names the rule that moved each name

def test_the_remap_list_prints_the_rule_that_moved_the_name(stored):
    page = _owner().get("/den").text
    m = re.search(r'data-den-chain-remap-row="ACUTAAS".*?</div>', page, re.S)
    assert m and "(app label 'Chemicals')" in _flat(m.group(0))
    assert "(Healthcare)" not in _flat(m.group(0))
    m2 = re.search(r'data-den-chain-remap-row="INDIACEM".*?</div>', page, re.S)
    assert "(Nifty-500 industry 'Construction Materials' (rule A))" in _flat(m2.group(0))


# --- C16: the RENDERED USD/INR line ends at the row's own price date

def _fx_line(row: str) -> str:
    m = re.search(r"USD/INR .*?EUR/INR", _flat(row))
    assert m, "the USD/INR line"
    return m.group(0)


def test_the_rendered_usd_inr_line_ends_at_the_rows_price_date(stored):
    line = _fx_line(_tcs()[1])
    # ending at FRED's newest (2026-10-16, 94.96, −1.05%) would flip the sign
    assert "USD/INR 95.97 (2026-05-15) → 96.28 (2026-07-17) +0.32%" in line
    assert "STALE" not in line and "2026-10-16" not in line


def test_the_rendered_usd_inr_line_is_stale_when_fred_stops_far_before_the_price(stored,
                                                                                  monkeypatch):
    feeds = _fake_feeds()
    inner = feeds.series_points
    feeds.series_points = lambda s: ([p for p in POINTS[s] if p[0] <= "2026-06-26"]
                                     if s == "fred_usdinr" else inner(s))
    monkeypatch.setattr(cv, "_macro_feeds", lambda: feeds)
    cv.clear_feed_cache()
    line = _fx_line(_tcs()[1])
    end = max(p[0] for p in POINTS["fred_usdinr"] if p[0] <= "2026-06-26")
    age = (date(2026, 7, 17) - date.fromisoformat(end)).days
    assert age > mf.FRED_STALE_DAYS[mf.fred_kind("fred_usdinr")]
    assert f"({end}) " in line and f"STALE — {age} days old" in line


# --- C18: NSE's STALE and intraday labels on a rendered row

def _nse_feeds(monkeypatch, **row_over):
    rows = {k: dict(v, **row_over) for k, v in NSE_ROWS.items()}
    feeds = _fake_feeds()
    feeds.latest_row = lambda s: dict(rows[s]) if s in rows else None
    monkeypatch.setattr(cv, "_macro_feeds", lambda: feeds)
    cv.clear_feed_cache()
    cv.clear_row_cache()


def _nse_line(row: str) -> str:
    return _flat(re.search(r"data-den-chain-nse-pe>.*?</div>", row, re.S).group(0))


def test_an_old_nse_session_prints_stale_with_its_day_count(stored, monkeypatch):
    n = mv.NSE_STALE_DAYS
    old = (TODAY - timedelta(days=n + 1)).isoformat()
    _nse_feeds(monkeypatch, date=old, session_closed=True)
    assert f"STALE — {n + 1} days old" in _nse_line(_tcs()[1])
    edge = (TODAY - timedelta(days=n)).isoformat()             # the 10th day is still fresh
    _nse_feeds(monkeypatch, date=edge, session_closed=True)
    assert "STALE" not in _nse_line(_tcs()[1])
    assert n == mv.ASSUMED["nse_stale_days"]["value"] == 10    # the rule lives in ASSUMED


def test_an_intraday_nse_reading_is_labelled_on_the_row(stored, monkeypatch):
    stamp = TODAY.strftime("%d-%b-%Y") + " 11:00"
    _nse_feeds(monkeypatch, date=TODAY.isoformat(), session_closed=False, timestamp=stamp)
    row = _tcs()[1]
    assert "data-den-chain-nse-intraday" in row
    assert f"intraday reading {stamp}" in _nse_line(row)


# --- C20: §11 waits for 252 sessions on a newly launched index

def test_bench_min_sessions_is_252_only_for_a_newly_launched_index():
    assert vv.bench_min_sessions("Cement") == 252
    assert vv.bench_min_sessions("Chemicals") == 252
    assert vv.bench_min_sessions("IT") is None and vv.bench_min_sessions(None) is None


def test_section_11_prints_beta_needs_252_for_a_200_session_cement_index(monkeypatch):
    import numpy as np
    docs: dict = {}
    monkeypatch.setattr(vv.db, "save_settings",
                        lambda d, doc_id="default": docs.setdefault(doc_id, {}).update(d))
    monkeypatch.setattr(vv.db, "load_settings",
                        lambda doc_id="default": dict(docs.get(doc_id, {})))
    vv.LAST["targets"], vv.LAST["assumptions"] = {}, {}
    n = 200
    idx = pd.bdate_range(end="2026-10-16", periods=n)
    rng = np.random.default_rng(7)
    b = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.01, n)), index=idx)
    s = pd.Series(1000 * np.cumprod(1 + 1.4 * b.pct_change().fillna(0).to_numpy()
                                    + rng.normal(0, 0.002, n)), index=idx)
    frames = {t: pd.DataFrame({"Close": s, "Volume": 1.0}) for t in ("INDIACEM.NS", "TCS.NS")}
    monkeypatch.setattr("web.candle_chart.load_ohlcv", lambda t, tail=220: frames.get(t))
    # the same 200 sessions as Nifty Cement (launched 2026-05-11) and as Nifty IT
    monkeypatch.setattr(vv, "_sector_series", lambda name: b if name in ("Cement", "IT") else None)
    monkeypatch.setattr(vv, "_index_field", lambda field: b * 200)
    monkeypatch.setattr(vv, "_bond_series", lambda: pd.Series(7.0, index=idx))
    monkeypatch.setattr(vv, "_fii_5d", lambda: None)
    monkeypatch.setattr(vv, "ensure_sector_depth", lambda asof: None)
    assert vv.bench_for("INDIACEM.NS", state.cfg())[0] == "Cement"
    assert vv.bench_for("TCS.NS", state.cfg())[0] == "IT"
    try:
        c = _owner()
        for t in ("indiacem", "tcs"):
            r = c.post("/den/valuation", data={"ticker": t, "target": "500", "asof": "2026-08-03"},
                       follow_redirects=False)
            assert "val=saved" in r.headers["location"], t
        html = c.get("/den").text
        i = html.find("den_valuation_rows_marker")
        rows = re.findall(r"<tr\b.*?</tr>", html[i:html.find("</table>", i)], re.S)
        cem = next(_flat(x) for x in rows if "INDIACEM" in x)
        it = next(_flat(x) for x in rows if re.search(r">\s*TCS\b", x))
        assert "β DATA_NEEDED — the index has 200 sessions" in cem and "β needs 252" in cem
        # the same 200 sessions on a long-standing index DO read a β (the guard bites)
        assert re.search(r"β \d\.\d+ over 199 sessions", it)
    finally:
        vv.LAST["targets"], vv.LAST["assumptions"] = {}, {}


# --- Builder B hand-off: the names left out of the 4-year panel are named

def test_the_sector_line_names_the_names_left_out_of_the_four_year_panel(stored, monkeypatch):
    base = [{"ticker": t, "method": "earnings", "shares_outstanding": 1e9, "pat": 1e11,
             "sector": "IT services / ER&D", "family": "earnings",
             "fy": {"2026-03-31": {"shares": 1e9, "pat": 1e11}}}
            for t in ("TCS.NS", "INFY.NS")]
    monkeypatch.setitem(cv._PEER_CACHE, "base", base)
    monkeypatch.setitem(cv._PEER_CACHE, "by_method", {"earnings": [
        {"ticker": "TCS.NS", "price": 2000.0}, {"ticker": "INFY.NS", "price": 1000.0}]})
    real = mv.sector_block
    monkeypatch.setattr(mv, "sector_block",
                        lambda rec, idx=None: {**real(rec, idx), "off_cycle": ["SIEMENS.NS"]})
    row = _tcs()[1]
    assert "left out of the 4-yr panel (a different year-end): SIEMENS" in _flat(row)
    assert mv.TOTALS_VERSION == 2                    # the window changed: old docs rebuilt
