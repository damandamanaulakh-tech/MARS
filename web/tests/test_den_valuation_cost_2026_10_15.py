"""Den §11 cost fix (2026-10-15, the same fault class as §12's "Mars den
always go down nt opening"). Money impact NONE — §11 is display only and no
gate / stop / size reads it.

What was wrong, each verified against the code before the fix:
  * every Den open loaded the FULL twelve-year price document of EVERY
    stored §11 target (`load_ohlcv(ticker, tail=600)` reads the whole doc,
    then keeps 600 rows) and read the WHOLE sector_index collection once
    per target — 25 targets = ~50 Atlas reads per open, during scans too;
  * a target dated before the 600-row tail read "series starts 2024-…,
    after the valuation date" although the store holds the name from 2014;
  * `POST /den/valuation` is async and ran the blocking sector backfill
    (up to 16 network calls) ON the event loop, freezing every request;
  * the backfill's once-a-day guard was set only when it fetched, so every
    save re-read the whole sector collection; and `ensure_sector_depth`
    ignored the typed date (always 760 days);
  * §11 had no time budget and no busy / memory guard (§12 has both);
  * the table's first cell was no-wrap and ~480 px wide, so at 1366 px the
    context target, buy-below and verify columns sat off-screen.

Pins (every one fails on the pre-fix code):
  * N targets cost ONE batched store read ($in + $slice), never a per-name
    full-document load; a name the store lacks alone falls back;
  * the sector rows are read once per render and kept per process;
  * an old target reads the stored years; a date past the page's cap says
    "this page reads the last N sessions" instead of blaming the data;
  * the POST runs the backfill (and the settings writes) off the loop;
  * two saves read the sector store once; a deeper date gets one attempt;
  * ensure_sector_depth asks for the days the typed date needs, capped;
  * a busy box / a spent time budget computes no new row and says why,
    kept rows still show;
  * the context target and buy-below columns sit beside the stock.
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import web.db as db
from web import auth as auth_mod
from web import state
from web import valuation_view as vv
from web.main import app

SECTORS = ("IT", "Fin Service", "FMCG", "Pharma", "Realty", "Infra")


def _owner() -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD},
               follow_redirects=False)
    assert r.status_code == 303
    return c


def _settings(monkeypatch) -> dict:
    """A dict-backed settings store, so targets round-trip without Mongo."""
    docs: dict = {}
    monkeypatch.setattr(vv.db, "save_settings",
                        lambda d, doc_id="default": docs.setdefault(doc_id, {}).update(d))
    monkeypatch.setattr(vv.db, "load_settings",
                        lambda doc_id="default": dict(docs.get(doc_id, {})))
    vv.LAST["targets"], vv.LAST["assumptions"] = {}, {}
    return docs


def _quiet_inputs(monkeypatch, index: pd.DatetimeIndex) -> None:
    """Synthetic index / VIX / bond / FII so nothing reads a committed file
    or the network; the store fakes below are the only thing counted."""
    vix = pd.Series(12.5 + 1.2 * np.sin(np.arange(len(index)) / 9.0), index=index)
    nifty = pd.Series(np.linspace(20000, 23000, len(index)), index=index)
    monkeypatch.setattr(vv, "_index_field",
                        lambda field: nifty.copy() if field == "nifty_close" else vix.copy())
    monkeypatch.setattr(vv, "_bond_series",
                        lambda: pd.Series(np.linspace(6.9, 7.1, len(index)), index=index))
    monkeypatch.setattr(vv, "_fii_5d", lambda: -100.0)


class FakeStore:
    """The Mongo store as §11 sees it: twelve-year OHLCV docs and a sector
    collection, every call counted."""

    def __init__(self, names, start="2014-06-02", sector_from=None):
        self.days = pd.bdate_range(start, date.today() - timedelta(days=1))
        rng = np.random.default_rng(7)
        self.docs = {}
        for t in names:
            c = 1000 * np.cumprod(1 + rng.normal(0, 0.01, len(self.days)))
            self.docs[t] = {
                "_id": t, "ticker": t,
                "start": self.days[0].date().isoformat(),
                "end": self.days[-1].date().isoformat(),
                "candles": [[d.isoformat(), float(x), float(x), float(x), float(x), 1e5]
                            for d, x in zip(self.days, c)]}
        sd = self.days if sector_from is None else self.days[self.days >= sector_from]
        self.sector_rows = []
        for k, s in enumerate(SECTORS):
            c = (100.0 + k) * np.cumprod(1 + rng.normal(0, 0.008, len(sd)))
            self.sector_rows += [{"date": d.date().isoformat(), "sector": s,
                                  "close": float(x)} for d, x in zip(sd, c)]
        self.calls = {"history_docs": [], "load_history_doc": [], "load_sector_rows": []}

    def history_docs(self, tickers, rows=0):
        names = list(tickers)
        self.calls["history_docs"].append((sorted(names), int(rows)))
        out = {}
        for t in names:
            d = self.docs.get(t)
            if d:
                out[t] = dict(d, candles=d["candles"][-int(rows):] if rows else d["candles"])
        return out

    def load_history_doc(self, t):
        self.calls["load_history_doc"].append(t)
        return self.docs.get(t)

    def load_sector_rows(self, since_iso=""):
        self.calls["load_sector_rows"].append(since_iso)
        return [r for r in self.sector_rows if not since_iso or r["date"] >= since_iso]


@pytest.fixture
def store(monkeypatch):
    names = ["TCS.NS", "INFY.NS", "SBIN.NS", "ITC.NS", "SUNPHARMA.NS"]
    fs = FakeStore(names)
    monkeypatch.setattr(db, "is_connected", lambda: True)
    monkeypatch.setattr(db, "history_docs", fs.history_docs)
    monkeypatch.setattr(db, "load_history_doc", fs.load_history_doc)
    monkeypatch.setattr(db, "load_sector_rows", fs.load_sector_rows)
    fs.settings = _settings(monkeypatch)
    _quiet_inputs(monkeypatch, fs.days)
    vv.clear_series_cache()
    yield fs
    vv.clear_series_cache()
    vv.LAST["targets"], vv.LAST["assumptions"] = {}, {}


def _target(t: str, asof: str = "2025-06-02", target: float = 1000.0) -> None:
    ok, err = vv.save_target({"ticker": t, "target": target, "asof": asof,
                              "basis": "", "ts": "t"}, {})
    assert ok, err


# --------------------------------------------------------- store reads

def test_n_targets_cost_one_batched_store_read(store):
    names = ["TCS.NS", "INFY.NS", "SBIN.NS", "ITC.NS", "SUNPHARMA.NS"]
    for t in names:
        _target(t)
    out = vv.section(state.cfg(), state.portfolio())
    assert len(store.calls["history_docs"]) == 1, store.calls["history_docs"]
    asked, rows = store.calls["history_docs"][0]
    assert asked == sorted(names) and 0 < rows <= vv.PRICE_TAIL_MAX
    assert store.calls["load_history_doc"] == []          # no full-document load
    assert [r["status"] for r in out["rows"]] == ["OK"] * len(names)


def test_the_bounded_reads_moved_no_number(store):
    """A target the old 600-row read could serve gets the SAME reading from
    the batched $slice read and the bounded sector read as from the full
    stored history."""
    from trading import valuation_context as vc
    from trading.history_store import records_to_df
    _target("TCS.NS", asof="2025-06-02")
    _target("SBIN.NS", asof="2025-11-03")
    cfg, p = state.cfg(), state.portfolio()
    rows = {r["ticker"]: r for r in vv.section(cfg, p)["rows"]}
    sent = vv.sentiment_inputs(cfg, p, vv._index_field("india_vix_close"))
    a = vv.assumptions_in_use({})
    for t, asof in (("TCS.NS", "2025-06-02"), ("SBIN.NS", "2025-11-03")):
        idx, label = vv.bench_for(t, cfg)
        pts = [(r["date"], r["close"]) for r in store.sector_rows if r["sector"] == idx]
        bench = pd.Series([c for _, c in pts], index=pd.to_datetime([d for d, _ in pts]))
        full = records_to_df(store.docs[t])["Close"]
        want = vc.context(t, 1000.0, asof, full, bench, label, bond=vv._bond_series(),
                          rate_now_pct=a.get("rate_now"), erp_pct=a["erp"],
                          growth_pct=a["growth"], inflation_shift_pp=a["infl_shift"],
                          sentiment=sent, news_verdict=vv._news_verdict(p, t))
        got = rows[t]
        assert got["status"] == want["status"] == "OK"
        for k in ("price_then", "price_now", "context_one_to_one", "context_measured"):
            assert got[k] == pytest.approx(want[k]), k
        for leg, k in (("sector", "beta"), ("sector", "move_pct"), ("verify", "residual_pct"),
                       ("sentiment", "buy_below_measured")):
            assert got["legs"][leg][k] == pytest.approx(want["legs"][leg][k]), (leg, k)


def test_sector_rows_are_read_once_per_render_and_kept(store):
    for t in ("TCS.NS", "INFY.NS", "SBIN.NS", "ITC.NS", "SUNPHARMA.NS"):
        _target(t)
    out = vv.section(state.cfg(), state.portfolio())
    assert len(store.calls["load_sector_rows"]) == 1, store.calls["load_sector_rows"]
    # bounded: the β window plus the oldest valuation date, not the whole store
    assert store.calls["load_sector_rows"][0] > store.days[0].date().isoformat()
    assert all(r["legs"]["sector"]["status"] == "OK" for r in out["rows"])
    vv.clear_row_cache()                     # recompute the rows, keep the sector table
    vv.section(state.cfg(), state.portfolio())
    assert len(store.calls["load_sector_rows"]) == 1
    vv.clear_series_cache()                  # a forgotten table is read again, once
    vv.section(state.cfg(), state.portfolio())
    assert len(store.calls["load_sector_rows"]) == 2


def test_a_name_the_store_lacks_alone_falls_back_to_load_ohlcv(store, monkeypatch):
    for t in ("TCS.NS", "INFY.NS", "ZZNOSTORE.NS"):
        _target(t)
    asked = []
    idx = pd.bdate_range("2024-01-01", periods=500)
    frame = pd.DataFrame({"Close": np.linspace(100, 150, len(idx)), "Volume": 1.0}, index=idx)
    monkeypatch.setattr("web.candle_chart.load_ohlcv",
                        lambda t, tail=220: asked.append((t, tail)) or frame.tail(tail))
    out = vv.section(state.cfg(), state.portfolio())
    assert len(store.calls["history_docs"]) == 1
    assert [t for t, _ in asked] == ["ZZNOSTORE.NS"]
    assert {r["ticker"]: r["status"] for r in out["rows"]} == {
        "INFY.NS": "OK", "TCS.NS": "OK", "ZZNOSTORE.NS": "OK"}


def test_an_old_target_reads_the_stored_years_not_a_600_row_tail(store):
    _target("TCS.NS", asof="2020-03-02")
    r = vv.section(state.cfg(), state.portfolio())["rows"][0]
    assert r["status"] == "OK", r.get("why")
    assert "2020-02-20" <= r["price_then_date"] <= "2020-03-02"
    _, rows = store.calls["history_docs"][0]
    assert rows > 600 and rows <= vv.PRICE_TAIL_MAX


def test_a_date_past_the_page_cap_says_so_and_does_not_blame_the_data(store, monkeypatch):
    monkeypatch.setattr(vv, "PRICE_TAIL_MAX", 300)
    _target("TCS.NS", asof="2016-01-04")             # the store holds TCS from 2014
    r = vv.section(state.cfg(), state.portfolio())["rows"][0]
    assert r["status"] == "DATA_NEEDED"
    assert "this page reads the last 300 sessions" in r["why"]
    assert "after the valuation date" not in r["why"]
    assert "2014-06-02" in r["why"]                  # where the store really starts


def test_a_name_the_store_starts_after_the_date_keeps_the_true_reason(store):
    _target("TCS.NS", asof="2012-01-02")             # older than the store itself
    r = vv.section(state.cfg(), state.portfolio())["rows"][0]
    assert r["status"] == "DATA_NEEDED"
    assert "series starts 2014-06-02, after the valuation date" in r["why"]


# ----------------------------------------------------- the POST routes

def test_the_post_runs_the_backfill_and_the_store_writes_off_the_event_loop(monkeypatch):
    _settings(monkeypatch)
    seen: list = []

    def where() -> bool:
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False

    real_save, real_clear = vv.save_target, vv.clear_target
    monkeypatch.setattr(vv, "ensure_sector_depth",
                        lambda asof: seen.append(("backfill", asof, where())) or {})
    monkeypatch.setattr(vv, "save_target",
                        lambda e, a: seen.append(("save", e["ticker"], where())) or real_save(e, a))
    monkeypatch.setattr(vv, "clear_target",
                        lambda t: seen.append(("clear", t, where())) or real_clear(t))
    c = _owner()
    r = c.post("/den/valuation", data={"ticker": "tcs", "target": "4000",
                                       "asof": "2025-06-02"}, follow_redirects=False)
    assert r.status_code == 303 and "val=saved" in r.headers["location"]
    r = c.post("/den/valuation/clear", data={"ticker": "tcs"}, follow_redirects=False)
    assert "val=cleared" in r.headers["location"]
    assert seen == [("save", "TCS.NS", False), ("backfill", "2025-06-02", False),
                    ("clear", "TCS.NS", False)]
    vv.LAST["targets"], vv.LAST["assumptions"] = {}, {}


class _Broker:
    def __init__(self):
        self.calls = []

    def get_daily_candles(self, key, start, end):
        self.calls.append((key, start, end))
        idx = pd.bdate_range(start, end)
        if len(idx) == 0:
            return None
        return pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0,
                             "Close": np.linspace(100, 110, len(idx))}, index=idx)


def test_two_saves_read_the_sector_store_once_and_a_deeper_date_gets_one_try(monkeypatch):
    from web import scheduler
    _settings(monkeypatch)
    reads: list = []
    deep = (date.today() - timedelta(days=900)).isoformat()
    monkeypatch.setattr(db, "is_connected", lambda: True)
    # VALFIX batch 3 (2026-10-21): the backfill reads each sector's OWN
    # oldest row (a sector never stored is filled), so the deep store holds
    # every sector at the deep date
    from trading import data_refresh as _dr
    monkeypatch.setattr(db, "load_sector_rows",
                        lambda since_iso="": reads.append(since_iso) or
                        [{"date": deep, "sector": k, "close": 1.0}
                         for k in _dr.SECTOR_INDEX_KEYS])
    monkeypatch.setattr(db, "save_sector_rows", lambda rows: len(rows))
    br = _Broker()
    monkeypatch.setattr("trading.data.history_broker", lambda: br)
    scheduler._SECTOR_BACKFILL_DAY.clear()
    try:
        c = _owner()
        for _ in range(2):
            r = c.post("/den/valuation", data={"ticker": "tcs", "target": "4000",
                                               "asof": "2025-06-02"}, follow_redirects=False)
            assert "val=saved" in r.headers["location"]
        assert len(reads) == 1 and br.calls == []         # deep enough, read ONCE
        # an older valuation date needs a deeper store: one more read, one fetch
        r = c.post("/den/valuation", data={"ticker": "infy", "target": "1500",
                                           "asof": "2019-01-15"}, follow_redirects=False)
        assert "val=saved" in r.headers["location"]
        assert len(reads) == 2 and br.calls
        want = (date.today() - timedelta(days=vv.sector_days_for("2019-01-15"))).isoformat()
        assert want <= "2019-01-15"
        # the two recent indices are asked only from their own first bar —
        # the deep store already reaches it, so nothing is asked for them
        assert {s for _, s, _ in br.calls} == {want}
        assert not any("Cement" in k or "Chemicals" in k for k, _, _ in br.calls)
        # the same depth again the same day: no read, no fetch
        n = len(br.calls)
        c.post("/den/valuation", data={"ticker": "infy", "target": "1500",
                                       "asof": "2019-01-15"}, follow_redirects=False)
        assert len(reads) == 2 and len(br.calls) == n
    finally:
        scheduler._SECTOR_BACKFILL_DAY.clear()
        vv.LAST["targets"], vv.LAST["assumptions"] = {}, {}


def test_ensure_sector_depth_asks_for_the_days_the_typed_date_needs(monkeypatch):
    from web import scheduler
    asked: list = []
    monkeypatch.setattr(scheduler, "backfill_sector_history",
                        lambda days=None, force=False: asked.append(days) or {"stored": 0})
    vv.ensure_sector_depth("2019-01-15")
    vv.ensure_sector_depth(date.today().isoformat())
    vv.ensure_sector_depth("1999-01-04")
    need = (date.today() - date(2019, 1, 15)).days + vv.SECTOR_MARGIN_DAYS
    assert asked == [min(need, vv.SECTOR_FEED_MAX_DAYS),
                     scheduler.SECTOR_HISTORY_DAYS,      # the β window still wants ~2 years
                     vv.SECTOR_FEED_MAX_DAYS]            # capped at one feed request


# ------------------------------------------------- busy and the budget

def test_a_busy_box_computes_no_new_row_and_kept_rows_still_show(store):
    from web import scheduler
    _target("TCS.NS")
    first = vv.section(state.cfg(), state.portfolio())
    assert first["computed"] == 1 and first["rows"][0]["status"] == "OK"
    _target("INFY.NS")
    n_docs, n_sec = len(store.calls["history_docs"]), len(store.calls["load_sector_rows"])
    assert scheduler._ROTATION_LOCK.acquire(blocking=False)
    try:
        out = vv.section(state.cfg(), state.portfolio())
    finally:
        scheduler._ROTATION_LOCK.release()
    rows = {r["ticker"]: r for r in out["rows"]}
    assert rows["TCS.NS"]["status"] == "OK" and out["cached"] == 1
    assert rows["INFY.NS"].get("not_computed") and "scan rotation" in rows["INFY.NS"]["why"]
    assert out["skipped_busy"] == 1 and "scan rotation" in out["busy"]
    # nothing was read while the box was busy
    assert len(store.calls["history_docs"]) == n_docs
    assert len(store.calls["load_sector_rows"]) == n_sec


def test_the_time_budget_stops_new_rows_and_later_opens_fill_them(store, monkeypatch):
    names = ["TCS.NS", "INFY.NS", "SBIN.NS", "ITC.NS"]
    for t in names:
        _target(t)
    computed: list = []

    def slow_row(ticker, entry, cfg, p, **k):
        computed.append(ticker)
        time.sleep(0.4)
        return {"ticker": ticker, "target": float(entry["target"]), "asof": entry["asof"],
                "status": "DATA_NEEDED", "why": "fake computed row", "legs": {}}

    monkeypatch.setattr(vv, "row_for", slow_row)
    monkeypatch.setenv("SB_DEN_CHAIN_BUDGET_S", "0.6")
    cfg, p = state.cfg(), state.portfolio()
    a = vv.section(cfg, p)
    assert (a["computed"], a["skipped_budget"], a["cached"]) == (2, 2, 0)
    assert [bool(r.get("not_computed")) for r in a["rows"]] == [False, False, True, True]
    assert "time budget" in a["rows"][-1]["why"]
    b = vv.section(cfg, p)
    assert (b["cached"], b["computed"], b["skipped_budget"]) == (2, 2, 0)
    assert sorted(computed) == sorted(names)              # each row computed once


def test_the_guard_reads_the_same_knobs_as_section_12(monkeypatch):
    from web import valuation_chain_view as cv
    monkeypatch.delenv("SB_DEN_CHAIN_BUDGET_S", raising=False)
    assert vv.time_budget_s() == cv.time_budget_s() == 20.0
    import web.memory_relief as mr
    monkeypatch.setenv("SB_DEN_CHAIN_RSS_CEILING_MB", "380")
    monkeypatch.setattr(mr, "rss_mb", lambda: 450.0)
    assert "450 MiB" in vv.busy_reason() and "380" in vv.busy_reason()
    monkeypatch.setattr(mr, "rss_mb", lambda: 300.0)
    assert vv.busy_reason() == ""


# ---------------------------------------------------------------- page

@pytest.fixture
def page(monkeypatch):
    """The 2026-10-12 page fixture: no Mongo, the chart loader and the
    sector series stubbed (the existing §11 tests' shape)."""
    docs = _settings(monkeypatch)
    idx = pd.bdate_range("2024-06-03", periods=420)
    rng = np.random.default_rng(3)
    b = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.01, len(idx))), index=idx)
    rb = b.pct_change().fillna(0).to_numpy()
    s = pd.Series(1000 * np.cumprod(1 + 1.5 * rb + rng.normal(0, 0.002, len(idx))), index=idx)
    monkeypatch.setattr("web.candle_chart.load_ohlcv",
                        lambda t, tail=220: pd.DataFrame({"Close": s, "Volume": 1.0}))
    monkeypatch.setattr(vv, "_sector_series", lambda name: b if name == "IT" else None)
    _quiet_inputs(monkeypatch, idx)
    monkeypatch.setattr(vv, "ensure_sector_depth", lambda asof: {})
    yield docs
    vv.LAST["targets"], vv.LAST["assumptions"] = {}, {}


def test_the_page_prints_why_rows_were_not_computed(page, monkeypatch):
    c = _owner()
    c.post("/den/valuation", data={"ticker": "tcs", "target": "4000",
                                   "asof": "2025-06-02"}, follow_redirects=False)
    monkeypatch.setattr(vv, "busy_reason", lambda: "a scan rotation is running on the box")
    html = c.get("/den").text
    assert "den_valuation_busy_marker" in html and "den_valuation_not_computed_marker" in html
    txt = " ".join(re.sub(r"<[^>]+>", " ", html).split())
    assert "NOT COMPUTED on this open" in txt and "scan rotation is running" in txt
    assert "₹4,000.00 on 2025-06-02" in txt               # the target itself still shows


def test_the_context_target_and_buy_below_sit_beside_the_stock(page):
    c = _owner()
    c.post("/den/valuation", data={"ticker": "tcs", "target": "4000",
                                   "asof": "2025-06-02"}, follow_redirects=False)
    html = c.get("/den").text
    i = html.find("den_valuation_rows_marker")
    assert i >= 0
    table = html[i:html.find("</table>", i)]
    head = table[:table.find("</thead>")]
    heads = [" ".join(re.sub(r"<[^>]+>", " ", h).split())
             for h in re.findall(r"<th[^>]*>(.*?)</th>", head, re.S)]
    assert heads[0].startswith("Stock · target")
    assert heads[1].startswith("Context target") and heads[2].startswith("Buy below")
    assert len(heads) == 9
    # the first cell wraps (only the ticker line stays on one line)
    first_td = re.search(r"<tbody>\s*(?:\{.*?\}\s*)*<tr[^>]*>\s*<td([^>]*)>", table, re.S)
    assert first_td, "no row rendered"
    attrs = first_td.group(1)
    assert "whitespace-nowrap" not in attrs and "min-w-[14rem]" in attrs
    # the second / third cells carry the context target and the buy-below price
    body = table[table.find("<tbody>"):]
    tds = re.findall(r"<td[^>]*>(.*?)</td>", body, re.S)
    txt = [" ".join(re.sub(r"<[^>]+>", " ", t).split()) for t in tds]
    assert "vs price" in txt[1]
    assert "₹" in txt[2] and "vs price" not in txt[2]
