"""Den valuation fixes, batch 1 (owner order 2026-10-21, chosen one by one).
Money impact NONE — display only; no gate, stop or size reads any of it.

Pins:
  * VAL-13a — the IT rupee row prints the USD-INR "now" date and says
    STALE past 10 days;
  * VAL-15 — the row prints E101 with the base rate it actually used
    (93.48 on 2026-03-31 for TCS), never the sheet's 94.9;
  * VAL-13b — a §11 target dated today prints "nothing to adjust yet"
    instead of a context target equal to itself;
  * VAL-13c — a missing 10-year yield is not applied as × 1.0: the
    context target is the sector leg only and says so; with the sector
    leg missing too there is no context target at all;
  * VAL-11 — the four no-effect numbers are labelled on §13 and editing
    only them never switches §12 to DRAFT;
  * known faults — VAL-07 (inverted cases), VAL-01 (conglomerate),
    VAL-02 (lender equity line), VAL-12a (minorities / short-term
    investments vs peers), VAL-12b (power on the bank formula), all from
    ONE function.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from html import unescape

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from trading import valuation_context as vc
from web import auth as auth_mod
from web import valuation_chain_view as cv
from web import valuation_view as vv
from web.main import app

NO_EFFECT = ("verdict.max_uncertainty_pct", "verdict.watch_band_pct",
             "terms.dcf_growth_cap_pct", "snapshot.publication_lag_days")


def _owner() -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD},
               follow_redirects=False)
    assert r.status_code == 303
    return c


def _flat(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


def _section(html: str, anchor: str, n: int = 120000) -> str:
    i = html.find(anchor)
    assert i >= 0, anchor
    return html[i:i + n]


def _ctx_rows(html: str) -> str:
    """The §11 rows table only (raw html)."""
    i = html.find("den_valuation_rows_marker")
    assert i >= 0
    return html[i:html.find("</table>", i)]


def _chain_row(html: str, ticker: str) -> str:
    """The §12 table row of one ticker (raw html)."""
    i = html.find("den_chain_rows_marker")
    j = html.find("</table>", i)
    for row in re.findall(r"<tr\b.*?</tr>", html[i:j], re.S):
        cell = re.search(r"<td[^>]*>(.*?)</td>", row, re.S)
        if cell and re.sub(r"<[^>]+>|\s", "", cell.group(1)).startswith(ticker):
            return row
    raise AssertionError(f"no §12 row for {ticker}")


@pytest.fixture
def stored(monkeypatch):
    """A fake settings store (no Mongo) and a fixed peer table."""
    docs: dict = {}
    monkeypatch.setattr(vv.db, "save_settings",
                        lambda d, doc_id="default": docs.setdefault(doc_id, {}).update(d))
    monkeypatch.setattr(vv.db, "load_settings",
                        lambda doc_id="default": dict(docs.get(doc_id, {})))
    vv.LAST["targets"], vv.LAST["assumptions"] = {}, {}
    cv.LAST_ASSUMPTIONS.clear()
    monkeypatch.setattr(cv, "peer_multiples",
                        lambda method, exclude="": {"n": 3, "pe_median": 24.0,
                                                    "pb_median": 2.0,
                                                    "ev_ebitda_median": 9.0,
                                                    "ev_sales_median": 2.5,
                                                    "source": "test peers"})
    yield docs
    vv.LAST["targets"], vv.LAST["assumptions"] = {}, {}
    cv.LAST_ASSUMPTIONS.clear()


# ------------------------------------------------ §11 synthetic inputs

def _pair_to_today(n: int = 420, beta: float = 1.4, seed: int = 5):
    idx = pd.bdate_range(end=pd.Timestamp(date.today()), periods=n)
    rng = np.random.default_rng(seed)
    b = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.01, n)), index=idx)
    rb = b.pct_change().fillna(0).to_numpy()
    s = pd.Series(1000 * np.cumprod(1 + beta * rb + rng.normal(0, 0.002, n)), index=idx)
    return s, b


@pytest.fixture
def ctx_inputs(stored, monkeypatch):
    """Synthetic stock / sector / bond / VIX series reaching today; the
    backfill recorded, never run. `bond` and `sector` can be switched off."""
    s, b = _pair_to_today()
    frames = {"TCS.NS": pd.DataFrame({"Close": s, "Volume": 1.0})}
    monkeypatch.setattr("web.candle_chart.load_ohlcv", lambda t, tail=220: frames.get(t))
    state = {"bond": pd.Series(np.linspace(6.9, 7.1, len(b)), index=b.index),
             "sector": b}
    monkeypatch.setattr(vv, "_sector_series",
                        lambda name: state["sector"] if name == "IT" else None)
    vix = pd.Series(12.5 + 1.2 * np.sin(np.arange(len(b)) / 9.0), index=b.index)
    monkeypatch.setattr(vv, "_index_field",
                        lambda field: (b * 200) if field == "nifty_close" else vix)
    monkeypatch.setattr(vv, "_bond_series", lambda: state["bond"])
    monkeypatch.setattr(vv, "_fii_5d", lambda: None)
    monkeypatch.setattr(vv, "ensure_sector_depth", lambda asof: None)
    return state


def _store_target(c: TestClient, asof: str, target: str = "4000") -> None:
    r = c.post("/den/valuation", data={"ticker": "tcs", "target": target, "asof": asof},
               follow_redirects=False)
    assert r.status_code == 303 and "val=saved" in r.headers["location"]


# ------------------------------------------------------------ VAL-13b

def test_a_target_dated_today_has_nothing_to_adjust_yet(ctx_inputs):
    c = _owner()
    today = date.today().isoformat()
    _store_target(c, today)
    html = c.get("/den").text
    rows = _ctx_rows(html)
    assert "den_valuation_dated_today_marker" in rows
    t = _flat(rows)
    assert "nothing to adjust yet — the target is dated today" in t
    assert f"₹4,000.00 on {today}" in t
    # no context target equal to the target itself, no "rates leg only"
    assert "rates leg only" not in t and "vs price" not in t
    assert "Buy below on the target itself" in t


def test_dated_today_in_the_pure_context_prints_no_context_target():
    s, b = _pair_to_today()
    r = vc.context("TCS.NS", 4000.0, date.today().isoformat(), s, b, "Nifty IT",
                   bond=pd.Series(7.0, index=s.index), today=date.today())
    assert r["status"] == "OK" and r["dated_today"] is True
    assert r["context_one_to_one"] is None and r["context_measured"] is None
    assert r["context_rates_only"] is None
    assert r["context_note"] == "nothing to adjust yet — the target is dated today"
    sm = r["legs"]["sentiment"]
    assert sm["buy_below_on_target"] == pytest.approx(4000.0 * (1 - sm["discount_pct"] / 100))
    # yesterday is a real reading again
    y = (date.today() - timedelta(days=30)).isoformat()
    r2 = vc.context("TCS.NS", 4000.0, y, s, b, "Nifty IT",
                    bond=pd.Series(7.0, index=s.index), today=date.today())
    assert r2["dated_today"] is False and r2["context_one_to_one"] is not None


# ------------------------------------------------------------ VAL-13c

def test_a_missing_yield_is_labelled_and_not_applied_as_times_one(ctx_inputs):
    ctx_inputs["bond"] = None
    c = _owner()
    asof = (date.today() - timedelta(days=60)).isoformat()
    _store_target(c, asof)
    rows = _ctx_rows(c.get("/den").text)
    assert "den_valuation_no_rates_marker" in rows
    t = _flat(rows)
    assert "sector leg only · no rates adjustment — 10-year yield DATA_NEEDED" in t
    assert "not applied — the context target is not adjusted for rates" in t
    assert "× 1.000" not in t


def test_both_legs_missing_prints_no_context_target_never_the_raw_target(ctx_inputs):
    ctx_inputs["bond"] = None
    ctx_inputs["sector"] = None
    c = _owner()
    asof = (date.today() - timedelta(days=60)).isoformat()
    _store_target(c, asof)
    rows = _ctx_rows(c.get("/den").text)
    assert "den_valuation_no_context_marker" in rows
    t = _flat(rows)
    assert "no context target — the sector leg and the 10-year yield are both DATA_NEEDED" in t
    assert "rates leg only" not in t and "(rates only)" not in t


# ---------------------------------------------------- VAL-13a / VAL-15

def test_the_it_rupee_row_prints_the_rate_dates_the_base_used_and_stale(stored):
    html = _owner().get("/den?chain=TCS").text
    row = _chain_row(html, "TCS")
    assert "den_chain_fx_marker" in row
    t = _flat(row)
    # the committed USD-INR file ends 2026-08-13 — far past 10 days
    assert "USD-INR 95.40 on 2026-08-13" in t
    assert "data-den-chain-fx-stale" in row and "STALE" in t and "days old" in t
    # VAL-15: the base actually used, never the sheet's 94.9
    assert "93.48 on 2026-03-31" in t
    assert "E101 = ROUND((E78 / 93.48 - 1)*100,2)" in t
    fx = re.search(r"<div[^>]*data-den-chain-fx>.*?E101 = [^<]*</div>", row, re.S)
    assert fx and "94.9" not in fx.group(0)


def test_fx_block_stale_rule_and_non_it_rows():
    rec = {"route": {"sector": "IT services / ER&D"},
           "detail": {"cases": {"base": {"inputs": {"market": {"usdinr": 95.0,
                                                               "usdinr_base": 90.0}},
                                         "cells": {"E101": 5.56}}}}}
    mac = {"usdinr_now": 95.0, "usdinr_base": 90.0, "usdinr_now_date": "2026-10-10",
           "usdinr_base_date": "2026-03-31"}
    fresh = cv.fx_block(rec, mac, today=date(2026, 10, 20))
    assert fresh["applies"] and fresh["counted"] and fresh["age_days"] == 10
    assert fresh["stale"] is False                       # 10 days is not "older than 10"
    old = cv.fx_block(rec, mac, today=date(2026, 10, 21))
    assert old["stale"] is True and old["age_days"] == 11
    assert "90.00" in old["formula"] and "2026-03-31" in old["formula"]
    assert cv.fx_block({"route": {"sector": "Banks (private/PSU)"}}, mac) == {"applies": False}
    # a missing rate: the row is counted 0 and says so, with no invented base
    miss = cv.fx_block(rec, {**mac, "usdinr_base": None, "usdinr_base_date": None},
                       today=date(2026, 10, 20))
    assert miss["counted"] is False and miss["pct"] is None


# --------------------------------------------------------------- VAL-11

def test_section_13_labels_the_four_no_effect_numbers(stored):
    html = _owner().get("/den").text
    f = _section(html, 'id="formulas"')
    for k in NO_EFFECT:
        m = re.search(rf'data-den-formulas-no-effect="{re.escape(k)}".*?</label>', f, re.S)
        assert m, k
        assert "no effect yet — " in _flat(m.group(0)), k
    assert "den_formulas_no_effect_note_marker" in f
    # a number that does change results carries no such label
    assert 'data-den-formulas-no-effect="sheet.beta"' not in f


def test_editing_only_no_effect_numbers_never_switches_section_12_to_draft(stored):
    c = _owner()
    r = c.post("/den/valuation/assumptions",
               data={"verdict.watch_band_pct": "50", "terms.dcf_growth_cap_pct": "1",
                     "verdict.max_uncertainty_pct": "5",
                     "snapshot.publication_lag_days": "300"},
               follow_redirects=False)
    assert r.status_code == 303 and "fa=saved" in r.headers["location"]
    assert set(stored["valuation_assumptions"]["values"]) == set(NO_EFFECT)
    html = c.get("/den?chain=TCS").text
    chain = _section(html, 'id="chain"')
    assert "den_chain_draft_marker" not in html
    assert "DRAFT ASSUMPTIONS IN USE" not in _flat(chain[:20000])
    assert "den_chain_no_effect_marker" in chain
    row = _chain_row(html, "TCS")
    assert ">draft<" not in row                            # no per-row draft tag
    t13 = _flat(_section(html, 'id="formulas"'))
    assert "No drafted number changes a result yet" in t13
    assert "currently drafted" not in t13
    assert "✕ clear the draft" in t13                       # the stored draft can still be cleared


def test_a_real_edit_beside_a_no_effect_one_counts_only_the_real_one(stored):
    c = _owner()
    c.post("/den/valuation/assumptions",
           data={"verdict.max_range_width_pct": "200", "verdict.watch_band_pct": "50"},
           follow_redirects=False)
    html = c.get("/den?chain=TCS").text
    t12 = _flat(_section(html, 'id="chain"')[:20000])
    assert "DRAFT ASSUMPTIONS IN USE — 1 number(s) differ" in t12
    assert "verdict.max_range_width_pct" in t12
    assert "no effect yet on any result: verdict.watch_band_pct" in t12
    row = _chain_row(html, "TCS")
    assert ">draft<" in row
    m = re.search(r'title="([^"]*)">draft<', row)
    assert m and m.group(1) == "verdict.max_range_width_pct"


# --------------------------------------------------------- known faults

def _full(ticker: str, **flags) -> dict:
    """A minimal full §12 record with a printed range."""
    fam = flags.get("family", "earnings")
    return {"ticker": ticker, "snapshot": {"source": cv.FULL_STATEMENTS_SOURCE},
            "route": {"sector": flags.pop("sector", "x"), "method": flags.get("method", "x"),
                      "family": fam},
            "range": {"status": "OK", "low": 1.0, "base": 2.0, "high": 3.0, "family": fam,
                      "notes": []},
            "range_flags": flags}


def _codes(rec: dict) -> list:
    return [f["code"] for f in cv.known_faults(rec)]


def test_known_fault_rules_live_in_one_function():
    inv = _full("SAIL.NS", family="ev_ebitda", method="metals_midcycle", inverted=True,
                printed_base_case="downside", midcycle_base=True)
    f = cv.known_faults(inv)
    assert [x["code"] for x in f] == ["VAL-07"]
    assert "printed target is the downside case" in f[0]["text"]
    assert "mid-cycle" in f[0]["text"]
    assert "VAL-01" in _codes(_full("RELIANCE.NS", family="ev_ebitda", method="oil_gas"))
    assert "VAL-01" in _codes(_full("M&M.NS", family="ev_ebitda", method="auto"))
    mi = _full("X.NS", family="ev_ebitda", method="auto", minorities_per_share=12.5,
               short_term_investments_per_share=0.0)
    f = cv.known_faults(mi)
    assert [x["code"] for x in f] == ["VAL-12a"]
    assert "₹12.50/share for minority interest" in f[0]["text"]
    assert _codes(_full("HDFCBANK.NS", family="book", method="bank_pb_roe", lender=True)) == ["VAL-02"]
    assert _codes(_full("NTPC.NS", family="book", method="power_regulated",
                        power_utility=True)) == ["VAL-12b"]
    # nothing printed, nothing flagged; a plain row is clean
    clean = _full("TCS.NS", family="earnings", method="earnings", inverted=False)
    assert _codes(clean) == []
    no_range = _full("RELIANCE.NS", family="ev_ebitda", method="oil_gas")
    no_range["range"]["status"] = "DATA_NEEDED"
    assert _codes(no_range) == []
    # a record built before range_flags existed falls back to the route / notes
    old = _full("HDFCBANK.NS", family="book", method="bank_pb_roe")
    old.pop("range_flags")
    assert _codes(old) == ["VAL-02"]
    old_inv = _full("SAIL.NS", family="ev_ebitda", method="metals_midcycle")
    old_inv.pop("range_flags")
    old_inv["range"]["notes"] = ["a case inverted (downside above base …) — values sorted"]
    assert _codes(old_inv) == ["VAL-07"]


def test_an_inverted_ev_row_prints_the_val07_warning_naming_the_case(stored, monkeypatch):
    real = cv.row_for

    def inverted(t, *a, **k):
        rec = real(t, *a, **k)
        if t == "TCS.NS":
            rec["range_flags"] = {**(rec.get("range_flags") or {}), "family": "ev_ebitda",
                                  "inverted": True, "printed_base_case": "upside",
                                  "printed_case": {"low": "base", "base": "upside",
                                                   "high": "downside"},
                                  "midcycle_base": True, "minorities_per_share": 0.0,
                                  "short_term_investments_per_share": 0.0}
        return rec

    monkeypatch.setattr(cv, "row_for", inverted)
    html = _owner().get("/den?chain=TCS").text
    row = _chain_row(html, "TCS")
    assert "den_chain_fault_marker" in row and 'data-den-chain-fault="VAL-07"' in row
    assert "⚠ known fault VAL-07 — " in _flat(row)
    assert "the printed target is the upside case, not the base case" in _flat(row)


def test_reliance_lender_and_power_rows_carry_their_known_faults(stored):
    c = _owner()
    for typed, code in (("RELIANCE", "VAL-01"), ("HDFCBANK", "VAL-02"), ("NTPC", "VAL-12b")):
        html = c.get(f"/den?chain={typed}").text
        row = _chain_row(html, typed)
        assert f'data-den-chain-fault="{code}"' in row, typed
        assert f"⚠ known fault {code} — " in _flat(row), typed
    rel = _flat(_chain_row(c.get("/den?chain=RELIANCE").text, "RELIANCE"))
    assert "no sum-of-parts yet" in rel
    hdfc = _flat(_chain_row(c.get("/den?chain=HDFCBANK").text, "HDFCBANK"))
    assert "equity line from the feed not checked against the bank's reported net worth" in hdfc
    ntpc = _flat(_chain_row(c.get("/den?chain=NTPC").text, "NTPC"))
    assert "valued with the bank formula; debt checks off" in ntpc


def test_a_clean_earnings_row_carries_no_known_fault(stored):
    row = _chain_row(_owner().get("/den?chain=TCS").text, "TCS")
    # the row must be a fully computed one, else 'no warning' proves nothing
    assert "den_chain_fx_marker" in row
    assert "den_chain_no_range_marker" not in row and "den_chain_not_computed_marker" not in row
    assert "den_chain_fault_marker" not in row
