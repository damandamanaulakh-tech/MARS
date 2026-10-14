"""Valuation chain orchestrator (stages 07-18 in one pass) — owner order
2026-10-14 ("lets hit the formulas work completion"). Money impact NONE.

Pins, on the committed statements zip (no network):
  * a real name comes back with every block the Den reads, JSON-safe;
  * an unknown name reads DATA_NEEDED in every block and never raises;
  * a converted (ADR) name's growth driver is read in constant currency;
  * the book route caps g at the sustainable rate and says so;
  * the EV route gets a width from the cases, not a point;
  * the macro what-if rows the app does not store are counted 0 AND listed;
  * failed_record has the same shape as a real record;
  * the peer table groups by method and carries the four multiples.
"""
from __future__ import annotations

import json

import pytest

from trading.valuation import is_dn
from trading.valuation import pipeline as P

BLOCKS = ("route", "snapshot", "shares", "earnings", "quality", "scenarios",
          "range", "verdict", "terms_rows", "data_needed", "assumptions")

PRICES = {"TCS.NS": 2156.0, "INFY.NS": 1023.4, "HDFCBANK.NS": 707.25,
          "DLF.NS": 648.6, "SAIL.NS": 171.55}


def _price_at(when):
    return 2000.0                       # a flat FY-end price for the tests


def _macro(snap):
    return {"sensex_start": 72000.0, "sensex_end": 72720.0,
            "nifty_start": 22000.0, "nifty_end": 22220.0, "vix": 13.0,
            "rf_pct": 7.0, "beta": 1.0, "usdinr_now": 95.0, "usdinr_base": 93.5,
            "price_now": 2156.0}


def _peers(method, exclude):
    return {"pe_median": 24.0, "pb_median": 2.0, "ev_ebitda_median": 9.0,
            "ev_sales_median": 2.5, "n": 5, "source": "test"}


def _eval(ticker, **kw):
    args = dict(price=PRICES.get(ticker), price_date="2026-10-09",
                sector_map={"TCS.NS": "IT", "INFY.NS": "IT", "HDFCBANK.NS": "Financial",
                            "DLF.NS": "Construction", "SAIL.NS": "Metal"},
                company_name={"TCS.NS": "TATA CONSULTANCY SERVICES LTD",
                              "INFY.NS": "INFOSYS LTD", "HDFCBANK.NS": "HDFC BANK LTD",
                              "DLF.NS": "DLF LTD",
                              "SAIL.NS": "STEEL AUTHORITY OF INDIA LTD"}.get(ticker, ""),
                price_at=_price_at, own_pe_history=lambda s: [18.0, 22.0, 26.0, 30.0],
                peers=_peers, macro=_macro)
    args.update(kw)
    return P.evaluate(ticker, **args)


@pytest.fixture(scope="module")
def tcs():
    return _eval("TCS.NS")


def test_a_real_name_carries_every_block_and_is_json_safe(tcs):
    for b in BLOCKS:
        assert b in tcs, b
    json.dumps(tcs)
    assert tcs["route"]["status"] == "declared"
    assert tcs["route"]["family"] == "earnings"
    assert tcs["snapshot"]["status"] == "OK"
    assert tcs["snapshot"]["period_end"] == "2026-03-31"
    assert tcs["snapshot"]["currency"] == "INR" and tcs["snapshot"]["converted"] is False
    assert tcs["snapshot"]["lock_hash"]
    assert tcs["shares"]["status"] == "reconciled"
    e = tcs["earnings"]
    assert e["status"] == "OK" and e["matches_yahoo"] is True
    assert e["reported_eps"] == pytest.approx(136.01)
    assert e["normalised_eps"] > e["reported_eps"]       # the one-off was a charge
    assert tcs["quality"]["score"] > 90
    sc = tcs["scenarios"]
    assert sc["status"] == "OK"
    assert sc["eps"]["downside"] < sc["eps"]["base"] < sc["eps"]["upside"]
    r = tcs["range"]
    assert r["status"] == "OK" and r["low"] <= r["base"] <= r["high"]
    assert tcs["verdict"]["verdict"] in ("attractive", "watch", "unattractive", "insufficient")
    assert tcs["verdict"]["reasons"]
    assert len(tcs["terms_rows"]) == 52
    names = {t["name"] for t in tcs["terms_rows"]}
    assert "ROE" in names and "Graham number" in names


def test_the_unstored_macro_rows_count_zero_and_are_listed(tcs):
    listed = " ".join(tcs["scenarios"]["macro_data_needed"])
    for word in ("repo", "CPI", "GDP", "Brent", "delivery"):
        assert word in listed, word
    assert any(x.startswith("bridge:") for x in tcs["data_needed"])


def test_an_unknown_name_is_data_needed_everywhere_and_never_raises():
    r = _eval("ZZNOSUCH.NS", price=None, company_name="")
    assert r["route"]["status"] == "UNDECLARED"
    assert r["snapshot"]["status"] == "DATA_NEEDED" and "no statements" in r["snapshot"]["why"]
    assert r["earnings"]["status"] == "DATA_NEEDED"
    assert r["scenarios"]["status"] == "DATA_NEEDED"
    assert r["range"]["status"] == "DATA_NEEDED"
    assert r["verdict"]["verdict"] == "insufficient"
    # only the two market-only terms (CAPM from rf + β × ERP, and β itself)
    # can compute without statements; every statement-fed term is DATA_NEEDED
    computed = {t["name"] for t in r["terms_rows"] if t["value"] is not None}
    assert computed <= {"Cost of equity (CAPM)", "Beta"}, computed
    json.dumps(r)


def test_a_converted_name_reads_growth_in_constant_currency():
    r = _eval("INFY.NS")
    assert r["snapshot"]["converted"] is True
    assert r["snapshot"]["currency"].startswith("USD")
    assert r["snapshot"]["fx"]                            # rate (date) printed
    notes = " ".join(r["scenarios"]["notes"])
    assert "constant currency" in notes
    # rupee growth for FY26 was ~14% (the rupee fell 85 → 93); the business grew ~5%
    assert r["scenarios"]["drivers"]["revenue_growth_pct"] < 10


def test_the_book_route_caps_growth_at_the_sustainable_rate():
    r = _eval("HDFCBANK.NS")
    assert r["route"]["family"] == "book"
    assert r["scenarios"]["status"] == "DATA_NEEDED"      # no EBITDA chain for a lender
    assert "sustainable" in r["range"]["method_text"]
    assert r["quality"]["score"] is None                  # lender: score DATA_NEEDED


def test_the_nav_route_is_data_needed_with_a_labelled_pb_fallback():
    r = _eval("DLF.NS")
    assert r["route"]["family"] == "nav"
    assert r["range"]["status"] == "DATA_NEEDED"
    assert "NAV" in r["range"]["why"]
    fb = r["range"]["fallback"]
    assert fb and fb["label"].startswith("P/B") and fb["base"] > 0


def test_the_ev_route_has_a_width_from_the_cases():
    r = _eval("SAIL.NS")
    assert r["route"]["family"] == "ev_ebitda"
    assert "PSU" in " ".join(r["route"]["overlays"])
    rg = r["range"]
    assert rg["status"] == "OK"
    assert rg["high"] > rg["low"]
    assert "mid-cycle" in rg["method_text"]


def test_failed_record_has_the_same_shape_as_a_real_one(tcs):
    f = P.failed_record("ZZ.NS", "boom")
    for b in BLOCKS:
        assert b in f, b
    assert set(f["route"]) >= {"status", "label", "method", "family", "rule", "overlays"}
    assert set(f["range"]) >= {"status", "low", "base", "high", "method_text", "why"}
    assert f["verdict"]["verdict"] == "insufficient" and f["verdict"]["reasons"] == ["boom"]
    json.dumps(f)


def test_the_peer_table_groups_by_method_with_four_multiples(monkeypatch):
    monkeypatch.setattr(P, "statement_symbols", lambda: ["TCS", "INFY", "HDFCBANK", "SAIL"])
    sm = {"TCS.NS": "IT", "INFY.NS": "IT", "HDFCBANK.NS": "Financial", "SAIL.NS": "Metal"}
    names = {"HDFCBANK.NS": "HDFC BANK LTD", "SAIL.NS": "STEEL AUTHORITY OF INDIA LTD"}
    tbl = P.peer_table(lambda t: PRICES.get(t), sm, names)
    assert set(tbl) >= {"earnings", "bank_pb_roe", "metals_midcycle"}
    it = {r["ticker"]: r for r in tbl["earnings"]}
    assert set(it) == {"TCS.NS", "INFY.NS"}
    assert it["TCS.NS"]["pe"] == pytest.approx(2156.0 / 136.01, rel=1e-3)
    assert it["INFY.NS"]["pe"] and 10 < it["INFY.NS"]["pe"] < 20     # converted, not 1,263
    for row in tbl["metals_midcycle"]:
        assert set(row) == {"ticker", "pe", "ev_ebitda", "pb", "ev_sales"}


def test_assumed_for_page_prints_the_numbers_the_den_header_reads():
    a = P.assumed_for_page()
    assert a["publication_lag_days"] == 45
    assert a["case_growth_pp"] == 5.0 and a["case_margin_pp"] == 1.0 and a["case_pe_pct"] == 10.0
    assert a["attractive_discount_pct"] == 10.0
    assert P.statement_coverage() == 200


def test_the_record_never_reaches_the_money_path():
    import inspect
    src = inspect.getsource(P)
    for banned in ("import httpx", "import requests", "from web", "trading.engine",
                   "trading.risk", "trading.orders", "trading.danger", "place_order"):
        assert banned not in src, banned
    assert is_dn(P.dn("x"))
