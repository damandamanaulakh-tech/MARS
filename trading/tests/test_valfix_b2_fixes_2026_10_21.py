"""Valuation batch 2 — the reviewer's defects, fixed (2026-10-21). Money
impact NONE — everything here is display only; no gate, stop or size reads
any of it (the boundary test pins that).

Pins (the reviewer's numbering):
  1. a typed segment multiple reaches the segment whatever the case, by its
     bracketed short name or by its peer group ("O2C=20" → "Oil to
     Chemicals (O2C)"), and a key that matches nothing is NAMED, never
     swallowed; the pipeline's segment table carries the group;
  2. the 52-terms EV is the ONE EV rule — short-term investments come off,
     the same line the pipeline's EV bridge and the peer table read;
  3. a typed NAV drops the "NAV not held — P/B fallback" note;
  4. a typed peer list on the book route is a cross-check only — the range
     does not move and the note says so;
  9. a lender's equity line checked against a filing within 10% (no
     tangible line) is "filing_checked", not an unchecked feed equity;
  + lead's (a): RBI's own 10-year benchmark is parsed from the home page's
     Government Securities Market box (real excerpt fetched 2026-10-21),
     stored as rbi_gsec10y with the bond and the box's own date, and
     latest_row() hands the bond name back.
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from trading import macro_feeds as mf
from trading.tests.test_valuation_pipeline_2026_10_14 import _eval
from trading.valuation import pipeline as P
from trading.valuation import scenarios as SC
from trading.valuation import snapshot as S
from trading.valuation import terms as T

FX = Path(__file__).resolve().parent / "fixtures"

# RELIANCE's FY26 segments AS FILED (data/segment_ebit.csv names) with one
# flat peer multiple each, so a typed number shows exactly where it landed.
SEGS = [{"segment": "Oil to Chemicals (O2C)", "ebit_cr": 50758.0},
        {"segment": "Oil and Gas", "ebit_cr": 13464.0},
        {"segment": "Retail", "ebit_cr": 20817.0},
        {"segment": "Digital Services", "ebit_cr": 48211.0},
        {"segment": "Others", "ebit_cr": 3448.0}]
GROUPS = ["O2C", "Oil & Gas", "Retail", "Digital", "Others"]
GIVEN = {s["segment"]: {"multiple": 10.0, "source": "peer median EV/EBIT", "peers": ["IOC"],
                        "group": g} for s, g in zip(SEGS, GROUPS)}
KW = dict(shares_outstanding=1353.25e7, net_debt_per_share=10.0, minorities_per_share=5.0)


# ============================================================ defect 1

def test_typed_segment_keys_match_by_case_bracket_and_group():
    assert SC.typed_segment_multiple("Oil to Chemicals (O2C)", {"o2c": 20}) == (20.0, "o2c")
    assert SC.typed_segment_multiple("Oil to Chemicals (O2C)",
                                     {"OIL TO CHEMICALS (o2c)": 21}) == (21.0, "OIL TO CHEMICALS (o2c)")
    assert SC.typed_segment_multiple("Digital Services", {"Digital": 12},
                                     group="Digital") == (12.0, "Digital")
    # no group known → the group name alone cannot reach it
    assert SC.typed_segment_multiple("Digital Services", {"Digital": 12}) == (None, None)
    # another segment's key never leaks over
    assert SC.typed_segment_multiple("Others", {"O2C": 20}, group="Others") == (None, None)
    # a non-positive or non-numeric value is not a multiple
    assert SC.typed_segment_multiple("Retail", {"retail": 0}) == (None, None)
    assert SC.typed_segment_multiple("Retail", {"retail": "x"}) == (None, None)
    assert SC.typed_segment_multiple("Retail", None) == (None, None)


def test_the_reviewers_case_o2c_and_retail_both_apply_and_a_stray_key_is_named():
    typed = {"O2C": 20.0, "Retail": 40.0, "Nope": 5.0}
    r = SC.justified_range("sotp", {}, price=1202.0, segments=SEGS, segment_multiples=GIVEN,
                           owner_inputs={"segment_multiples": typed}, **KW)
    rows = {x["segment"]: x for x in r["bridge"]["segments"]}
    o2c = rows["Oil to Chemicals (O2C)"]
    assert o2c["multiple"] == 20.0 and o2c["multiple_source"] == "typed in the Den inputs table as O2C"
    assert rows["Retail"]["multiple"] == 40.0 and "as Retail" in rows["Retail"]["multiple_source"]
    assert rows["Digital Services"]["multiple"] == 10.0
    assert rows["Digital Services"]["multiple_source"].startswith("peer median")
    assert r["bridge"]["typed_unmatched"] == ["Nope"]
    assert r["bridge"]["segment_names"] == [s["segment"] for s in SEGS]
    note = [n for n in r["notes"] if "Nope=5" in n]
    assert note and "NOT applied" in note[0] and "Oil to Chemicals (O2C)" in note[0]
    # the value moved by exactly the two typed multiples, nothing else
    base = SC.justified_range("sotp", {}, price=1202.0, segments=SEGS, segment_multiples=GIVEN,
                              **KW)
    assert r["bridge"]["ev_cr"] - base["bridge"]["ev_cr"] == pytest.approx(
        50758.0 * (20 - 10) + 20817.0 * (40 - 10))
    assert base["bridge"]["typed_unmatched"] == []
    assert not [n for n in base["notes"] if "NOT applied" in n]


def test_pipeline_segment_table_carries_the_group_even_without_a_peer_median():
    segs = [{"segment": "Digital Services", "ebit_cr": 1.0},
            {"segment": "Oil to Chemicals (O2C)", "ebit_cr": 1.0}]
    out = P._segment_multiples(segs, {}, {}, "RELIANCE")        # no peers priced at all
    assert out["Digital Services"] == {"multiple": None, "group": "Digital", "source": "",
                                       "peers": []}
    assert out["Oil to Chemicals (O2C)"]["group"] == "O2C"
    idx = {"IOC": {"ev_ebit": 9.0}, "BPCL": {"ev_ebit": 7.0}}
    out = P._segment_multiples(segs, idx, {}, "RELIANCE")
    assert out["Oil to Chemicals (O2C)"]["multiple"] == 8.0 and \
        out["Oil to Chemicals (O2C)"]["peers"] == ["IOC", "BPCL"]
    # the group travels into the range: a typed GROUP name reaches the segment
    r = SC.justified_range("sotp", {}, price=1202.0, segments=segs, segment_multiples=out,
                           owner_inputs={"segment_multiples": {"digital": 12.0}}, **KW)
    rows = {x["segment"]: x for x in r["bridge"]["segments"]}
    assert rows["Digital Services"]["multiple"] == 12.0 and r["bridge"]["typed_unmatched"] == []


# ============================================================ defect 2

def test_the_terms_ev_takes_short_term_investments_off_like_the_peer_rule():
    snap = {"fields": {"shares_outstanding": 100.0, "total_debt": 50.0, "cash": 20.0,
                       "minority_interest_bs": 5.0, "revenue": 400.0, "ebitda": 60.0},
            "extras": {"short_term_investments": 30.0}}
    ev = 10.0 * 100 + 50 - 20 - 30 + 5
    assert T.compute("EV", snap, price=10.0) == pytest.approx(ev)
    d = T.detail("EV", snap, price=10.0)
    assert d["parts"]["short_term_investments"] == 30.0
    assert "short-term investments" in d["formula"] and "short-term investments" in d["parts"]["rule"]
    assert T.compute("EV/EBITDA", snap, price=10.0) == pytest.approx(ev / 60.0)
    assert T.detail("P/S, EV/Sales", snap, price=10.0)["parts"]["ev_sales"] == pytest.approx(ev / 400.0)
    # no line → counted 0, the pipeline's own convention
    snap2 = {"fields": dict(snap["fields"]), "extras": {}}
    assert T.compute("EV", snap2, price=10.0) == pytest.approx(ev + 30.0)
    assert T.detail("EV", snap2, price=10.0)["parts"]["short_term_investments"] == 0.0


def test_the_terms_and_the_pipeline_read_the_same_short_term_investments_line():
    snap = S.snapshot("TCS", price=2156.0)
    sti = T.detail("EV", snap, price=2156.0)["parts"]["short_term_investments"]
    assert sti == P._sti(snap) and sti > 0


# ============================================================ defect 3

def test_a_typed_nav_drops_the_not_held_fallback_note():
    typed = _eval("DLF.NS", owner_inputs={"nav_ps": 1000.0, "nav_asof": "2026-09-30"})
    assert typed["range"]["status"] == "OK" and typed["range"]["base"] == 1000.0
    assert "NAV not held" not in typed["range"]["method_text"]
    untyped = _eval("DLF.NS")
    assert untyped["range"]["status"] == "DATA_NEEDED"
    assert "NAV not held" in untyped["range"]["method_text"]


# ============================================================ defect 4

def test_a_peer_list_on_the_book_route_is_a_cross_check_only():
    peers = {"bank_pb_roe": [{"ticker": "ICICIBANK.NS", "price": 1000.0, "pb": 3.0},
                             {"ticker": "SBIN.NS", "price": 800.0, "pb": 1.5}]}
    a = _eval("HDFCBANK.NS")
    b = _eval("HDFCBANK.NS", owner_inputs={"HDFCBANK": {"peer_list": ["ICICIBANK", "SBIN"]}},
              peer_rows=peers)
    assert a["range"]["status"] == "OK" and b["range_flags"]["peer_list"] == ["ICICIBANK", "SBIN"]
    assert (b["range"]["low"], b["range"]["base"], b["range"]["high"]) == \
        (a["range"]["low"], a["range"]["base"], a["range"]["high"])
    t = b["range"]["method_text"]
    assert "peer P/B cross-check only" in t and "median pb 2.25" in t
    assert "the range does not use it" in t and "in place of the route median" not in t


# ============================================================ defect 9

def test_an_equity_line_within_10pct_of_the_filing_is_filing_checked(monkeypatch):
    snap = copy.deepcopy(S.snapshot("HDFCBANK", price=707.25))
    pe = snap["period_end"]
    feed = P._hist(snap, "equity")[pe]
    snap["extra"].pop("tangible_book", None)              # no tangible line this time
    snap.get("extras", {}).pop("tangible_book", None)
    filed = feed / P.CR * 1.05                             # 4.8% off → inside the 10% tolerance
    monkeypatch.setattr(P, "_filing_net_worth",
                        lambda sym, period: (filed, "test filing") if period == pe else None)
    book, basis = P._lender_book("HDFCBANK", snap, pe)
    assert book == pytest.approx(feed / P.CR)
    assert basis.startswith("feed equity checked against the filing")
    assert "4.8% off" in basis and "test filing" in basis and "no tangible-book line" in basis
    assert "no filing on file" not in basis
    base = {"shares": P._f(snap, "shares_outstanding"), "pat": 1.0}
    assert P._lender_inputs("HDFCBANK", snap, base)[3]["lender_book_source"] == "filing_checked"
    # the unchecked case keeps its old label
    monkeypatch.setattr(P, "_filing_net_worth", lambda sym, period: None)
    _, basis2 = P._lender_book("HDFCBANK", snap, pe)
    assert basis2.startswith("feed equity — no tangible-book line, no filing on file")
    assert P._lender_inputs("HDFCBANK", snap, base)[3]["lender_book_source"] == "feed_equity"


# ============================================ lead's (a): RBI benchmark

def test_rbi_benchmark_parses_the_real_market_trends_box():
    page = (FX / "rbi_home_gsec_market_2026-10-21.html").read_text()
    p = mf.parse_rbi_gsec_market(page, "2026-10-21")
    assert (p["bond"], p["yield_pct"], p["date"]) == ("6.94% GS 2036", 7.3725, "2026-10-16")
    assert [x["bond"] for x in p["papers"]] == ["6.20% GS 2029", "6.36% GS 2031", "6.94% GS 2036",
                                                "7.06% GS 2041", "7.63% GS 2056"]
    rows = mf.rbi_gsec_row(p, "2026-10-21T09:20:00+05:30")
    assert [(r["series"], r["date"], r["value"], r["bond"]) for r in rows] == [
        ("rbi_gsec10y", "2026-10-16", 7.3725, "6.94% GS 2036")]
    assert "display only" in rows[0]["source"] and rows[0]["url"] == mf.RBI_HOME_URL
    assert "rbi_gsec10y" in mf.SERIES
    # the rates excerpt (no G-sec box) and a blank page give NO row — nothing dated by guess
    for html in ((FX / "rbi_home_rates_2026-10-21.html").read_text(),
                 "<html><body>maintenance</body></html>"):
        q = mf.parse_rbi_gsec_market(html, "2026-10-21")
        assert q["yield_pct"] is None and q["date"] is None and mf.rbi_gsec_row(q, "x") == []
    # papers but no "# as on" footnote → no date → no row
    q = mf.parse_rbi_gsec_market("Government Securities Market 6.94% GS 2036 : 7.37% #",
                                 "2026-10-21")
    assert q["bond"] == "6.94% GS 2036" and q["date"] is None and mf.rbi_gsec_row(q, "x") == []
    # a read year with no 9-11-year paper on the box → no benchmark
    assert mf.parse_rbi_gsec_market(page, "2040-01-01")["bond"] is None
    # the repo / USD-INR parser is untouched by the box
    assert mf.parse_rbi_home(page, "2026-10-21")["repo_pct"] is None


def test_latest_row_carries_the_bond_and_fetch_rbi_home_appends_the_benchmark(monkeypatch):
    rows = [{"series": "gsec10y_yield", "date": "2026-10-19", "value": 6.9276,
             "bond": "7.54% GS 2036", "source": "Upstox"},
            {"series": "rbi_gsec10y", "date": "2026-10-16", "value": 7.3725,
             "bond": "6.94% GS 2036", "source": "RBI"}]
    keep = mf._reader
    mf.set_reader(lambda series=None: [r for r in rows if not series or r["series"] == series])
    try:
        assert mf.latest_row("gsec10y_yield")["bond"] == "7.54% GS 2036"
        assert mf.latest("rbi_gsec10y")[:2] == (7.3725, "2026-10-16")
        assert mf.latest_row("nothing") is None
    finally:
        mf.set_reader(keep)
    page = ((FX / "rbi_home_rates_2026-10-21.html").read_text()
            + (FX / "rbi_home_gsec_market_2026-10-21.html").read_text())

    class _R:
        text = page
        status_code = 200

        def raise_for_status(self):
            return None

    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _R())
    got = mf.fetch_rbi_home()
    assert [r["series"] for r in got] == ["rbi_repo", "rbi_usdinr", "rbi_gsec10y"]
    assert got[2]["bond"] == "6.94% GS 2036" and got[2]["date"] == "2026-10-16"
