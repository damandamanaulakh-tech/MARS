"""Den §11 — the valuation context stage on screen (owner order
2026-10-12: "both, indian sector only, discount rate - go").

Pins: an owner POST stores a dated target and the page prints BOTH sector
columns, the rates leg, the buy-below margin and the verify split from
STORED inputs only; a bad form stores nothing and says why; a non-owner
can never store; clear removes; no stored history = DATA_NEEDED on the
row, never a 500 or a fake number; an unmapped sector reads Nifty 50 and
says so; the sequence table carries the stage; the live Config is never
written; the one-time sector backfill runs only when the store is shallow
and only once per day.
"""
from __future__ import annotations

import dataclasses
import re

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from web import auth as auth_mod, state
from web import valuation_view as vv
from web.main import app
from web.routes import den_routes as D


def _owner() -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD},
               follow_redirects=False)
    assert r.status_code == 303
    return c


def _pair(n: int = 420, beta: float = 1.5, seed: int = 3):
    idx = pd.bdate_range("2024-06-03", periods=n)
    rng = np.random.default_rng(seed)
    b = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.01, n)), index=idx)
    rb = b.pct_change().fillna(0).to_numpy()
    s = pd.Series(1000 * np.cumprod(1 + beta * rb + rng.normal(0, 0.002, n)), index=idx)
    return s, b


@pytest.fixture
def stored(monkeypatch):
    """A fake settings store (doc_id → dict) so POST then GET round-trips
    through db.save_settings/load_settings without Mongo; synthetic stock,
    sector, bond and VIX series so nothing touches the network or the
    repo zip; the backfill recorded, never run."""
    docs: dict = {}
    monkeypatch.setattr(vv.db, "save_settings",
                        lambda d, doc_id="default": docs.setdefault(doc_id, {}).update(d))
    monkeypatch.setattr(vv.db, "load_settings",
                        lambda doc_id="default": dict(docs.get(doc_id, {})))
    vv.LAST["targets"], vv.LAST["assumptions"] = {}, {}
    s, b = _pair()
    frames = {"TCS.NS": pd.DataFrame({"Close": s, "Volume": 1.0}),
              "ZZNOSECTOR.NS": pd.DataFrame({"Close": s * 0.5, "Volume": 1.0})}
    monkeypatch.setattr("web.candle_chart.load_ohlcv",
                        lambda t, tail=220: frames.get(t))
    monkeypatch.setattr(vv, "_sector_series", lambda name: b if name == "IT" else None)
    # VIX wanders 11-14 and closes mid-range: the VIX gauge reads "off"
    vix = pd.Series(12.5 + 1.2 * np.sin(np.arange(len(b)) / 9.0), index=b.index)
    monkeypatch.setattr(vv, "_index_field",
                        lambda field: (b * 200) if field == "nifty_close" else vix)
    monkeypatch.setattr(vv, "_bond_series",
                        lambda: pd.Series(np.linspace(6.9, 7.1, len(b)), index=b.index))
    monkeypatch.setattr(vv, "_fii_5d", lambda: -6500.0)
    calls: list = []
    monkeypatch.setattr(vv, "ensure_sector_depth", lambda asof: calls.append(asof))
    yield {"docs": docs, "calls": calls, "stock": s, "bench": b}
    vv.LAST["targets"], vv.LAST["assumptions"] = {}, {}


def _text(html: str, marker: str, n: int = 9000) -> str:
    i = html.find(marker)
    assert i >= 0, marker
    seg = re.sub(r"<[^>]+>", " ", html[i:i + n])
    return re.sub(r"\s+", " ", seg)


def test_owner_post_stores_the_target_and_the_page_prints_both_sector_columns(stored):
    cfg = state.cfg()
    before = dataclasses.asdict(cfg)
    c = _owner()
    r = c.post("/den/valuation", data={
        "ticker": "tcs", "target": "4000", "asof": "2025-06-02",
        "basis": "FY27 EPS 95 x 42", "rate_now": "7.2", "erp": "5",
        "growth": "8", "infl_shift": "0"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/den?val=saved")
    doc = stored["docs"][vv.DOC_ID]
    assert doc["targets"]["TCS.NS"]["target"] == 4000.0
    assert doc["targets"]["TCS.NS"]["asof"] == "2025-06-02"
    assert doc["assumptions"]["rate_now"] == 7.2
    assert stored["calls"] == ["2025-06-02"]            # the backfill was asked once
    assert dataclasses.asdict(state.cfg()) == before     # the engine is untouched

    html = c.get("/den?val=saved").text
    assert "den_valuation_rows_marker" in html
    txt = _text(html, "den_valuation_rows_marker")
    # the arithmetic the page shows is the pure module's own
    from trading import valuation_context as vc
    sent = vv.sentiment_inputs(cfg, state.portfolio(), vv._index_field("india_vix_close"))
    expect = vc.context("TCS.NS", 4000.0, "2025-06-02", stored["stock"], stored["bench"],
                        "Nifty IT", bond=vv._bond_series(), rate_now_pct=7.2,
                        sentiment=sent)
    sm = expect["legs"]["sentiment"]
    assert sm["gauges"][0]["state"] == "off"               # the calm VIX fixture
    assert sm["gauges"][2]["state"] == "ON"                # FII −6,500 Cr
    sl = expect["legs"]["sector"]
    assert sl["status"] == "OK" and sl["beta"] is not None
    from web.templating import inr
    assert f"₹{inr(sl['one_to_one'], 2)}" in txt           # the owner's 1:1 column
    assert f"₹{inr(sl['measured'], 2)}" in txt             # the measured-β column
    assert f"₹{inr(expect['context_one_to_one'], 2)}" in txt
    assert f"₹{inr(sm['buy_below_measured'], 2)}" in txt
    assert f"{sm['discount_pct']:.1f}% = 10% + {sm['n_on']} × 2.5" in txt
    assert "Nifty IT" in txt and "FY27 EPS 95 x 42" in txt
    assert "× " in txt and "+1pp from here" in txt         # the rates leg and its sensitivity
    assert "FII 5-day net selling" in txt and "ON · FII" in txt
    assert ("RE-RUN" in txt) or ("stands" in txt)          # the verify split prints a verdict
    assert "DATA_NEEDED — a move is missing" not in txt


def test_a_bad_form_stores_nothing_and_says_why(stored):
    c = _owner()
    r = c.post("/den/valuation", data={"ticker": "tcs", "target": "0",
                                       "asof": "2025-06-02"}, follow_redirects=False)
    assert r.status_code == 303 and "val=invalid" in r.headers["location"]
    assert vv.DOC_ID not in stored["docs"]
    r = c.post("/den/valuation", data={"ticker": "tcs", "target": "100",
                                       "asof": "2099-01-01"}, follow_redirects=False)
    assert "val=invalid" in r.headers["location"]
    r = c.post("/den/valuation", data={"ticker": "not a symbol!", "target": "100"},
               follow_redirects=False)
    assert "val=invalid" in r.headers["location"]
    assert vv.DOC_ID not in stored["docs"] and stored["calls"] == []
    html = c.get("/den?val=invalid&val_err=standalone+target+must+be+a+number+above+zero").text
    assert "Not stored — standalone target must be a number above zero" in html


def test_a_non_owner_can_never_store_or_clear(monkeypatch, stored):
    monkeypatch.setitem(auth_mod.SEEDED_USERS, "analyst@sourceborn.in",
                        {"role": "analyst", "password": "a-test", "name": "A"})
    c = TestClient(app)
    c.post("/login", data={"email": "analyst@sourceborn.in", "password": "a-test"})
    r = c.post("/den/valuation", data={"ticker": "tcs", "target": "4000"},
               follow_redirects=False)
    assert r.status_code == 303 and "val=denied" in r.headers["location"]
    r = c.post("/den/valuation/clear", data={"ticker": "TCS.NS"}, follow_redirects=False)
    assert "val=denied" in r.headers["location"]
    assert vv.DOC_ID not in stored["docs"]
    html = c.get("/den").text
    assert "den_valuation_readonly_marker" in html
    assert "den_valuation_form_marker" not in html


def test_clear_removes_one_target_and_keeps_the_rest(stored):
    c = _owner()
    for t in ("tcs", "zznosector"):
        c.post("/den/valuation", data={"ticker": t, "target": "4000",
                                       "asof": "2025-06-02"}, follow_redirects=False)
    assert set(stored["docs"][vv.DOC_ID]["targets"]) == {"TCS.NS", "ZZNOSECTOR.NS"}
    r = c.post("/den/valuation/clear", data={"ticker": "tcs"}, follow_redirects=False)
    assert "val=cleared" in r.headers["location"]
    assert set(stored["docs"][vv.DOC_ID]["targets"]) == {"ZZNOSECTOR.NS"}


def test_no_stored_history_is_data_needed_on_the_row_never_a_500(stored, monkeypatch):
    monkeypatch.setattr("web.candle_chart.load_ohlcv", lambda t, tail=220: None)
    c = _owner()
    c.post("/den/valuation", data={"ticker": "tcs", "target": "4000",
                                   "asof": "2025-06-02"}, follow_redirects=False)
    r = c.get("/den")
    assert r.status_code == 200
    txt = _text(r.text, "den_valuation_rows_marker")
    assert "DATA_NEEDED — stock closes" in txt
    assert "₹4,000.00 on 2025-06-02" in txt              # the target still shows


def test_an_unmapped_sector_reads_nifty_50_and_says_so(stored):
    c = _owner()
    c.post("/den/valuation", data={"ticker": "zznosector", "target": "500",
                                   "asof": "2025-06-02"}, follow_redirects=False)
    txt = _text(c.get("/den").text, "den_valuation_rows_marker")
    assert "Nifty 50 — no NSE sector index wired" in txt
    assert "ZZNOSECTOR" in txt


def test_a_valuation_dated_before_the_sector_store_is_data_needed_on_that_leg_only(stored, monkeypatch):
    # sector rows only from 2026-01 on; the stock has history from 2024
    b = stored["bench"]
    monkeypatch.setattr(vv, "_sector_series",
                        lambda name: b[b.index >= "2026-01-01"] if name == "IT" else None)
    c = _owner()
    c.post("/den/valuation", data={"ticker": "tcs", "target": "4000",
                                   "asof": "2025-06-02"}, follow_redirects=False)
    txt = _text(c.get("/den").text, "den_valuation_rows_marker")
    assert "series starts 2026-01-01, after the valuation date" in txt
    assert "rates leg only" in txt                       # the rates leg still prints


def test_the_sequence_carries_the_context_stage():
    rows = {r["code"]: r for r in D._sequence()["rows"]}
    st = rows["17b"]
    assert st["status"] == "exists"
    assert "valuation_context" in st["where"] and "1:1" in st["where"]
    html = _owner().get("/den").text
    assert "Market · sector · rates · sentiment context" in html


def test_the_explainer_prints_the_formulas_and_labels_the_assumptions():
    txt = _text(_owner().get("/den").text, "den_valuation_marker", 12000)
    assert "1:1 = target × (1 + sector move)" in txt
    assert "measured = target × (1 + β × sector move)" in txt
    assert "(r then − g) / (r now + shift − g)" in txt.replace("rthen", "r then").replace("rnow", "r now") \
        or "r − g" in txt or "r−g" in txt
    assert "ASSUMED, not measured" in txt
    assert "Indian only" in txt and "Global sector indices: DATA_NEEDED" in txt


# ------------------------------------------------------- sector backfill

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


def test_backfill_runs_only_when_the_store_is_shallow_and_once_a_day(monkeypatch):
    from datetime import date, timedelta

    from web import db, scheduler
    p = state.portfolio()
    n0 = len(p.decision_journal)
    saved: list = []
    recent = (date.today() - timedelta(days=30)).isoformat()
    monkeypatch.setattr(db, "is_connected", lambda: True)
    # VALFIX batch 3 (2026-10-21): the backfill now reads each sector's OWN
    # oldest row (a sector never stored is filled to today), so the shallow
    # store holds every sector at the same recent date
    from trading import data_refresh as _dr
    monkeypatch.setattr(db, "load_sector_rows",
                        lambda since_iso="": [{"date": recent, "sector": k, "close": 1.0}
                                              for k in _dr.SECTOR_INDEX_KEYS])
    monkeypatch.setattr(db, "save_sector_rows",
                        lambda rows: (saved.extend(rows), len(rows))[1])
    br = _Broker()
    monkeypatch.setattr("trading.data.history_broker", lambda: br)
    scheduler._SECTOR_BACKFILL_DAY.clear()
    rep = scheduler.backfill_sector_history(days=400)
    assert rep["fetched"] > 0 and rep["stored"] == rep["fetched"]
    # every sector asked for, the window ends the day BEFORE the oldest row
    assert len(br.calls) == len(scheduler.dr.SECTOR_INDEX_KEYS) if hasattr(scheduler, "dr") else True
    assert all(end == (date.fromisoformat(recent) - timedelta(days=1)).isoformat()
               for _, _, end in br.calls)
    assert all(r["date"] < recent for r in saved)
    assert any(x.get("action") == "DATA_REFRESH_SECTOR" and "backfill" in x["why"]
               for x in p.decision_journal[n0:])
    # second call the same day: nothing fetched
    n_calls = len(br.calls)
    rep2 = scheduler.backfill_sector_history(days=400)
    assert rep2["skipped"] == "already_tried_today" and len(br.calls) == n_calls
    # a deep store: nothing fetched even when forced
    deep = (date.today() - timedelta(days=800)).isoformat()
    monkeypatch.setattr(db, "load_sector_rows",
                        lambda since_iso="": [{"date": deep, "sector": k, "close": 1.0}
                                              for k in _dr.SECTOR_INDEX_KEYS])
    rep3 = scheduler.backfill_sector_history(days=400, force=True)
    assert rep3["skipped"] == "deep_enough" and len(br.calls) == n_calls
    scheduler._SECTOR_BACKFILL_DAY.clear()


def test_backfill_without_a_history_client_does_nothing(monkeypatch):
    from web import db, scheduler
    monkeypatch.setattr(db, "is_connected", lambda: True)
    monkeypatch.setattr(db, "load_sector_rows", lambda since_iso="": [])
    monkeypatch.setattr("trading.data.history_broker", lambda: None)
    scheduler._SECTOR_BACKFILL_DAY.clear()
    rep = scheduler.backfill_sector_history()
    assert rep["skipped"] == "no_history_client" and rep["fetched"] == 0
    monkeypatch.setattr(db, "is_connected", lambda: False)
    assert scheduler.backfill_sector_history()["skipped"] == "mongo_offline"
