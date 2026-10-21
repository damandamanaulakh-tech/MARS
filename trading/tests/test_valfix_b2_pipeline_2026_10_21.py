"""Valuation fixes batch 2 — the pipeline half (builder B), owner choices
2026-10-21. Money impact NONE: the valuation chain is display only (the
boundary test keeps engine / risk / orders / danger / sizing away from it).

Pinned here, on the committed statements zip with no network:
  * VAL-07  metals-only mid-cycle MARGIN on all three cases (exact E17 / E19 /
            E21 / E28 formula); every other EV row on the cases' own EBITDA;
            no EV name inverts; an inverted range keeps its order + flag;
  * VAL-12a one EV rule for the stock and its peers (STI + minorities in the
            peer EV, one `_sti`), the own-multiple round trip, peer base v3;
  * VAL-08  the target starts from normalised PAT on every route, labelled;
            quality and an unexplained share bridge are WARNINGS, not gates;
  * VAL-14a own P/E on each year's normalised PAT ÷ its own share count, odd
            interior years flagged (end years never), computed in evaluate;
  * VAL-02  lenders on filed net worth (>10% off) else tangible book, ROE on
            the average book, peer P/B on the same book;
  * VAL-12b power is not a lender (quality debt lines come back);
  * VAL-01 / VAL-14b conglomerate route + sum-of-parts, holding company,
            typed NAV, typed peer list, the typed-value band draft;
  * item 9  per-render "no effect yet" for the sheet sensitivities;
  * item 10 repo start / end read separately, institutional holding.

Builder D's modules (trading.valuation.segments / filings) are STUBBED in
every test (autouse), so the pins hold whether or not D's CSVs are present.
"""
from __future__ import annotations

import json
import sys
import types

import pytest

import trading.valuation as V
from trading.tests.test_valuation_pipeline_2026_10_14 import _eval, _macro
from trading.valuation import assess as A
from trading.valuation import assumptions as ASM
from trading.valuation import dn
from trading.valuation import normalise as N
from trading.valuation import pipeline as P
from trading.valuation import route as R
from trading.valuation import scenarios as SC
from trading.valuation import snapshot as S

RELIANCE_SEGMENTS = [
    {"segment": "O2C", "ebit_cr": 50758.0, "fy_end": "2026-03-31", "source": "test"},
    {"segment": "Oil and Gas", "ebit_cr": 13464.0, "fy_end": "2026-03-31", "source": "test"},
    {"segment": "Retail", "ebit_cr": 20817.0, "fy_end": "2026-03-31", "source": "test"},
    {"segment": "Digital Services", "ebit_cr": 48211.0, "fy_end": "2026-03-31", "source": "test"},
    {"segment": "Others", "ebit_cr": 3448.0, "fy_end": "2026-03-31", "source": "test"},
]
BAJAJ_STAKES = [
    {"subsidiary": "BAJAJ-AUTO", "shares_held": 95547251, "stake_pct": 34.19,
     "source": "test", "asof": "2026-06-30"},
    {"subsidiary": "BAJAJFINSV", "shares_held": 614832190, "stake_pct": 38.41,
     "source": "test", "asof": "2026-06-30"},
]
STATE: dict = {"seg": {}, "hold": {}, "fil": {}}


@pytest.fixture(autouse=True)
def _stub_builder_d(monkeypatch):
    """Deterministic stand-ins for trading.valuation.segments / filings."""
    STATE.update({"seg": {}, "hold": {}, "fil": {}})
    m = types.ModuleType("trading.valuation.segments")
    m.segment_ebit = lambda s: [dict(r) for r in STATE["seg"].get(s, [])]
    m.holdco_stakes = lambda s: [dict(r) for r in STATE["hold"].get(s, [])]
    f = types.ModuleType("trading.valuation.filings")
    f.net_worth = lambda s, fy: STATE["fil"].get((s, fy))
    monkeypatch.setitem(sys.modules, "trading.valuation.segments", m)
    monkeypatch.setitem(sys.modules, "trading.valuation.filings", f)
    monkeypatch.setattr(V, "segments", m, raising=False)
    monkeypatch.setattr(V, "filings", f, raising=False)
    yield


# ================================================================ VAL-07

def test_metals_midcycle_margin_moves_all_three_cases_by_the_exact_formula():
    r = _eval("SAIL.NS")
    assert r["route"]["method"] == "metals_midcycle"
    snap = S.snapshot("SAIL", price=171.55)
    he, hr = P._hist(snap, "ebitda"), P._hist(snap, "revenue")
    ms = [he[k] / hr[k] * 100 for k in he if hr.get(k)]
    assert len(ms) >= 3
    mm = sum(ms) / len(ms)
    cells = {c: r["detail"]["cases"][c]["cells"] for c in SC.CASES}
    m0 = cells["base"]["E21"]
    br = r["range"]["bridge"]
    for c in SC.CASES:
        want = (cells[c]["E19"] - cells[c]["E17"] * (m0 - mm) / 100.0) / cells[c]["E28"]
        assert br["metric_per_share"][c] == pytest.approx(want, rel=1e-12), c
        assert br["metric_source"][c] == "override"
    assert br["metric_per_share"]["downside"] < br["metric_per_share"]["base"] \
        < br["metric_per_share"]["upside"]
    f = r["range_flags"]
    assert f["midcycle_margin"] is True and f["inverted"] is False
    assert "mid-cycle MARGIN" in r["range"]["method_text"]


def test_other_ev_rows_read_each_cases_own_ebitda_without_midcycle():
    r = _eval("LT.NS", price=3661.9, sector_map={"LT.NS": "Construction"},
              company_name="LARSEN & TOUBRO LTD")
    assert r["range_flags"]["family"] == "ev_ebitda" and r["route"]["method"] != "metals_midcycle"
    br = r["range"]["bridge"]
    assert set(br["metric_source"].values()) == {"case"}
    cases = r["detail"]["cases"]
    for c in SC.CASES:
        assert br["metric_per_share"][c] == pytest.approx(
            cases[c]["ebitda"] / cases[c]["shares"], rel=1e-12)
    assert r["range_flags"]["midcycle_margin"] is False
    assert "mid-cycle" not in r["range"]["method_text"]


def test_no_ev_route_name_inverts_on_the_statement_set():
    from trading.tests.test_valuation_pipeline_2026_10_14 import _peers
    ev, inverted = 0, []
    for sym in P.statement_symbols():
        r = P.evaluate(sym + ".NS", price=500.0, price_at=lambda w: 500.0,
                       peers=_peers, macro=_macro)
        if r["range_flags"]["family"] in ("ev_ebitda", "ev_sales"):
            ev += 1
            if r["range_flags"]["inverted"]:
                inverted.append(sym)
    assert ev >= 80 and inverted == []


def test_an_inverted_range_keeps_its_order_and_is_flagged(monkeypatch):
    # a draft (or data) that puts the downside above the base: never sorted
    monkeypatch.setattr(P, "_midcycle_margin_overrides",
                        lambda snap, cases: ({"downside": 40.0, "base": 30.0, "upside": 50.0},
                                             "test overrides"))
    r = _eval("SAIL.NS")
    rg, f = r["range"], r["range_flags"]
    assert rg["inverted"] is True and rg["inverted_note"].startswith("CASES INVERTED")
    assert rg["low"] > rg["base"]                            # downside kept as the low
    assert f["inverted"] is True and f["printed_case"] == {
        "low": "downside", "base": "base", "high": "upside"}
    assert f["inverted_note"].startswith("CASES INVERTED")
    assert r["verdict"]["verdict"] == "insufficient"


# ================================================================ VAL-12a

def test_peer_base_carries_sti_minorities_ebit_book_and_version(monkeypatch):
    monkeypatch.setattr(P, "statement_symbols", lambda: ["LT", "HDFCBANK"])
    sm = {"LT.NS": "Construction", "HDFCBANK.NS": "Financial"}
    base = P.peer_base(lambda t: 1000.0, sm, {"HDFCBANK.NS": "HDFC BANK LTD"})
    by = {b["ticker"]: b for b in base}
    assert P.PEER_BASE_VERSION == 3 and all(b["version"] == 3 for b in base)
    assert "minority_interest_bs" in P.PEER_BASE_FIELDS
    lt = by["LT.NS"]
    snap = S.snapshot("LT", price=1000.0)
    assert lt["short_term_investments"] == P._sti(snap) and lt["short_term_investments"] > 0
    assert lt["minority_interest_bs"] == P._f(snap, "minority_interest_bs")
    assert lt["ebit"] == (P._f(snap, "ebit") if P._f(snap, "ebit") is not None
                          else P._f(snap, "operating_income"))
    hb = by["HDFCBANK.NS"]
    assert hb["method"] == "bank_pb_roe" and hb["book"] > 0 and hb["book_basis"]


def test_peer_ev_uses_the_stocks_own_rule(monkeypatch):
    monkeypatch.setattr(P, "statement_symbols", lambda: ["LT"])
    base = P.peer_base(lambda t: 3661.9, {"LT.NS": "Construction"}, {})
    row = P.peer_rows_from_base(base, lambda t: 3661.9)[base[0]["method"]][0]
    b = base[0]
    ev = (3661.9 * b["shares_outstanding"] + b["total_debt"] - b["cash"]
          - b["short_term_investments"] + b["minority_interest_bs"])
    assert row["ev_ebitda"] == pytest.approx(ev / b["ebitda"], rel=1e-12)
    assert row["ev_ebit"] == pytest.approx(ev / b["ebit"], rel=1e-12)
    assert row["price"] == 3661.9
    assert "short-term investments" in P.PEER_EV_RULE and "minorities" in P.PEER_EV_RULE


@pytest.mark.parametrize("sym,px", [("LT", 3661.9), ("RELIANCE", 1202.0), ("SAIL", 168.97)])
def test_own_multiple_round_trip_returns_own_price(monkeypatch, sym, px):
    monkeypatch.setattr(P, "statement_symbols", lambda: [sym])
    base = P.peer_base(lambda t: px, {}, {})
    rows = P.peer_rows_from_base(base, lambda t: px)
    own = rows[base[0]["method"]][0]["ev_ebitda"]
    snap = S.snapshot(sym, price=px)
    by, _ = P._base_year(snap, lambda w: px)
    kw = P._range_inputs("ev_ebitda", snap, by, {}, {"ev_ebitda_median": own}, [], {},
                         method=base[0]["method"], sym=sym)
    for k in [k for k in kw if k.startswith("_")]:
        kw.pop(k)
    rg = SC.justified_range("ev_ebitda", {}, price=px, **kw)
    assert rg["base"] == pytest.approx(px, abs=0.01)


def test_one_sti_definition_reaches_net_cash_net_debt_and_the_flags():
    snap = S.snapshot("LT", price=3661.9)
    sti = P._sti(snap)
    assert sti == N._extra(snap, "short_term_investments") and sti > 0
    debt, cash = P._f(snap, "total_debt"), P._f(snap, "cash")
    assert N.quality(snap, "ev_ebitda")["net_debt"] == pytest.approx(debt - cash - sti, rel=1e-9)
    by, _ = P._base_year(snap, lambda w: 3661.9)
    assert by["net_cash"] == pytest.approx((cash + sti - debt) / P.CR, rel=1e-9)
    r = _eval("LT.NS", price=3661.9, sector_map={"LT.NS": "Construction"},
              company_name="LARSEN & TOUBRO LTD")
    sh = P._f(snap, "shares_outstanding")
    assert r["range_flags"]["net_debt_per_share"] == pytest.approx((debt - cash - sti) / sh)
    assert r["range_flags"]["ev_rule_unified"] is True


# ================================================================ VAL-08

def test_the_target_starts_from_normalised_pat():
    r = _eval("TCS.NS")
    assert r["earnings"]["target_starts_from"] == "normalised"
    assert r["range_flags"]["target_starts_from"] == "normalised"
    base = r["detail"]["cases"]["base"]
    assert base["inputs"]["base"]["pat"] == pytest.approx(51974.16, abs=0.01)   # ₹ cr
    assert base["cells"]["D29"] == 143.65                                     # base-year EPS
    assert r["earnings"]["normalised_eps"] == pytest.approx(143.65, abs=0.01)


def test_reported_pat_is_the_labelled_fallback(monkeypatch):
    real = N.normalised_earnings

    def no_norm(snap):
        out = dict(real(snap))
        out["normalised_pat"] = dn("test: no unusual-items line")
        out["history"] = {}
        return out

    monkeypatch.setattr(N, "normalised_earnings", no_norm)
    r = _eval("TCS.NS")
    assert r["earnings"]["target_starts_from"] == "reported (normalised DATA_NEEDED)"
    assert any("REPORTED PAT" in n for n in r["scenarios"]["notes"])
    snap = S.snapshot("TCS", price=2156.0)
    assert r["detail"]["cases"]["base"]["inputs"]["base"]["pat"] == pytest.approx(
        P._f(snap, "net_income_common") / P.CR, rel=1e-12)


def test_banks_start_from_normalised_pat_too():
    r = _eval("HDFCBANK.NS")
    assert r["earnings"]["target_starts_from"] == "normalised"
    snap = S.snapshot("HDFCBANK", price=707.25)
    ne = N.normalised_earnings(snap)
    pat, basis = P.target_pat(ne, snap)
    assert basis == "normalised" and pat == ne["normalised_pat"]


def test_quality_and_share_bridge_are_warnings_not_gates():
    w = P.verdict_warnings({"score": 40.0}, {"status": "reconciled"})
    assert len(w) == 1 and "below 50" in w[0]
    w = P.verdict_warnings({"score": 90.0}, {"status": "unexplained",
                                             "unexplained_pairs": ["2023-03-31→2024-03-31"]})
    assert len(w) == 1 and "2023-03-31→2024-03-31" in w[0]
    assert P.verdict_warnings({"score": None}, {"status": "reconciled"}) == []
    r = _eval("HDFCBANK.NS")                            # merger year = unexplained bridge
    assert any("share count change" in x for x in r["verdict"]["warnings"])
    rg = r["range"]
    gate_free = A.assess(r["price"], {"low": rg["low"], "base": rg["base"], "high": rg["high"],
                                      "family": "book"})
    assert r["verdict"]["verdict"] == gate_free["verdict"]


# ================================================================ VAL-14a

def _synthetic(eps_by_year, shares=1.0):
    periods = sorted(eps_by_year)
    return ({"period_end": periods[-1],
             "history": {"basic_eps": {p: 999.0 for p in periods},
                         "shares_outstanding": {p: shares for p in periods}}},
            {"history": {p: v * shares for p, v in eps_by_year.items()}})


def test_own_pe_uses_normalised_pat_over_each_years_own_share_count():
    snap = {"period_end": "2026-03-31",
            "history": {"basic_eps": {"2025-03-31": 1.0, "2026-03-31": 1.0},
                        "shares_outstanding": {"2025-03-31": 10.0, "2026-03-31": 20.0}}}
    ne = {"history": {"2025-03-31": 100.0, "2026-03-31": 300.0}}
    rows = P.own_pe_rows(snap, lambda p: {"2025-03-31": 50.0, "2026-03-31": 60.0}[p], ne)
    assert [r["period"] for r in rows] == ["2025-03-31", "2026-03-31"]
    assert rows[0]["eps"] == 10.0 and rows[0]["pe"] == 5.0          # 100 / 10 shares
    assert rows[1]["eps"] == 15.0 and rows[1]["pe"] == 4.0          # 300 / 20 shares
    assert all(r["eps_basis"].startswith("normalised PAT") for r in rows)


def test_missing_normalised_or_shares_falls_back_to_basic_eps_and_says_so():
    snap = {"period_end": "2026-03-31",
            "history": {"basic_eps": {"2025-03-31": 4.0, "2026-03-31": 5.0},
                        "shares_outstanding": {"2026-03-31": 10.0}}}
    ne = {"history": {"2026-03-31": 60.0}}
    rows = P.own_pe_rows(snap, lambda p: 40.0, ne)
    assert rows[0]["eps"] == 4.0 and rows[0]["eps_basis"].startswith("basic EPS")
    assert rows[1]["eps"] == 6.0 and rows[1]["eps_basis"].startswith("normalised PAT")


def test_end_years_are_never_flagged_and_an_interior_spike_is():
    snap, ne = _synthetic({"2023-03-31": 5.35, "2024-03-31": 18.89,
                           "2025-03-31": 32.18, "2026-03-31": 60.61})
    rows = P.own_pe_rows(snap, lambda p: 100.0, ne)
    assert [r["flag"] for r in rows] == [None, None, None, None]   # a fast grower
    snap, ne = _synthetic({"2023-03-31": 15.0, "2024-03-31": 16.0,
                           "2025-03-31": 27.0, "2026-03-31": 16.5})
    rows = P.own_pe_rows(snap, lambda p: 100.0, ne)
    assert [bool(r["flag"]) for r in rows] == [False, False, True, False]
    assert "above 1.5×" in rows[2]["flag"]
    snap, ne = _synthetic({"2023-03-31": 55.0, "2024-03-31": 26.0, "2025-03-31": 43.0})
    rows = P.own_pe_rows(snap, lambda p: 100.0, ne)
    assert "below 1/1.5" in rows[1]["flag"]


def test_itc_fy25_is_flagged_and_kept_in_the_band():
    r = _eval("ITC.NS", price=260.8, sector_map={"ITC.NS": "FMCG"}, company_name="ITC LTD",
              own_pe_history=None)
    rows = {x["period"]: x for x in r["range"]["own_history"]}
    flagged = sorted(p for p, x in rows.items() if x["flag"])
    assert flagged == ["2025-03-31"]
    assert r["range"]["own_history_source"] == "computed"
    assert "4 readings" in r["range"]["multiple_band"]["source"]   # shown, not dropped


def test_evaluate_computes_the_own_history_itself_and_names_a_caller_override():
    a = _eval("HDFCBANK.NS", own_pe_history=None)
    rows = a["range"]["own_history"]
    snap = S.snapshot("HDFCBANK", price=707.25)
    sh = P._hist(snap, "shares_outstanding")
    assert [r["shares"] for r in rows] == [sh[r["period"]] for r in rows]   # own-year shares
    by = {r["period"]: r for r in rows}
    # FY24 on its own (post-merger) count: ~17.7× at a flat 2,000, not 8×
    assert by["2024-03-31"]["pe"] == pytest.approx(
        2000.0 / (N.normalised_earnings(snap)["history"]["2024-03-31"]
                  / sh["2024-03-31"]), rel=1e-12)
    assert a["range"]["own_history_source"] == "computed"
    b = _eval("TCS.NS")                                   # the fixture injects a list
    assert b["range"]["own_history_source"] == "caller" and b["range"]["own_history"]
    json.dumps(a["range"]["own_history"])


# ================================================================ VAL-02

def test_lender_on_average_tangible_book_without_a_filing():
    r = _eval("HDFCBANK.NS")
    snap = S.snapshot("HDFCBANK", price=707.25)
    tb = snap["extra"]["tangible_book"]
    pat = N.normalised_earnings(snap)["normalised_pat"]
    roe = pat / ((tb["2026-03-31"] + tb["2025-03-31"]) / 2) * 100
    f = r["range_flags"]
    assert f["lender_book_source"] == "tangible" and "no filing" in f["lender_book_basis"]
    assert "average book" in r["range"]["method_text"]
    br = r["range"]["bridge"]
    assert br["roe_pct"]["base"] == pytest.approx(roe, rel=1e-9)
    assert br["roe_pct"]["base"] == pytest.approx(14.29, abs=0.01)
    assert br["bvps"]["base"] == pytest.approx(tb["2026-03-31"] / P._f(snap, "shares_outstanding"),
                                               rel=1e-9)


def test_filed_net_worth_replaces_a_feed_equity_off_by_more_than_10pct():
    STATE["fil"][("HDFCBANK", "2026-03-31")] = (581514.36, "test filing FY26")
    r = _eval("HDFCBANK.NS")
    f = r["range_flags"]
    assert f["lender_book_source"] == "filing" and "40% off" in f["lender_book_basis"]
    snap = S.snapshot("HDFCBANK", price=707.25)
    assert r["range"]["bridge"]["bvps"]["base"] == pytest.approx(
        581514.36 * P.CR / P._f(snap, "shares_outstanding"), rel=1e-9)


def test_a_filing_within_10pct_keeps_the_tangible_book():
    snap = S.snapshot("ICICIBANK", price=1300.0)
    feed = P._hist(snap, "equity")["2026-03-31"] / P.CR
    STATE["fil"][("ICICIBANK", "2026-03-31")] = (feed * 0.96, "test filing")
    book, basis = P._lender_book("ICICIBANK", snap, "2026-03-31")
    assert basis.startswith("feed tangible book") and "within 10%" in basis
    assert book == pytest.approx(snap["extra"]["tangible_book"]["2026-03-31"] / P.CR)


def test_one_years_book_uses_year_end_and_says_so(monkeypatch):
    monkeypatch.setattr(P, "_prior_period", lambda snap, period: None)
    r = _eval("HDFCBANK.NS")
    assert "YEAR-END book" in r["range"]["method_text"]


def test_peer_pb_on_the_same_book_definition(monkeypatch):
    STATE["fil"][("HDFCBANK", "2026-03-31")] = (581514.36, "test filing FY26")
    monkeypatch.setattr(P, "statement_symbols", lambda: ["HDFCBANK"])
    base = P.peer_base(lambda t: 712.0, {"HDFCBANK.NS": "Financial"}, {"HDFCBANK.NS": "HDFC BANK LTD"})
    assert base[0]["book"] == pytest.approx(581514.36 * P.CR)
    row = P.peer_rows_from_base(base, lambda t: 712.0)["bank_pb_roe"][0]
    assert row["pb"] == pytest.approx(712.0 / (581514.36 * P.CR / base[0]["shares_outstanding"]))


# ================================================================ VAL-12b

def test_power_is_not_a_lender_and_its_debt_lines_compute():
    r = _eval("NTPC.NS", price=309.0, sector_map={"NTPC.NS": "Utilities"}, company_name="NTPC LTD")
    assert r["route"]["method"] == "power_regulated" and not r["range_flags"]["lender"]
    q = r["quality"]
    assert q["net_debt_ebitda"] == pytest.approx(4.32, abs=0.01)
    assert q["interest_cover"] == pytest.approx(3.19, abs=0.01)
    assert q["debt_equity"] == pytest.approx(1.33, abs=0.01)
    assert any("net debt/EBITDA" in x for x in q["flags"])       # generic 3× limit
    assert "not meaningful for a lender" not in json.dumps(r["quality"])
    assert _eval("HDFCBANK.NS")["quality"]["score"] is None      # banks stay lenders


def test_quality_default_is_unchanged_without_the_lender_argument():
    snap = S.snapshot("NTPC", price=309.0)
    assert N.quality(snap, "book")["score"]["status"] == "DATA_NEEDED"
    assert isinstance(N.quality(snap, "book", lender=False)["score"], float)
    assert N.quality(snap, "ev_ebitda", lender=True)["score"]["status"] == "DATA_NEEDED"


# ================================================================ VAL-01 / VAL-14b

def test_route_marks_a_symbol_with_segment_rows_a_conglomerate():
    look = lambda s: RELIANCE_SEGMENTS if s == "RELIANCE" else []      # noqa: E731
    rt = R.route_for("RELIANCE.NS", {}, "RELIANCE INDUSTRIES LTD", segment_lookup=look)
    assert rt["method"] == R.CONGLOMERATE_METHOD and rt["family"] == "sotp"
    assert rt["status"] == "declared" and rt["sector_method"] == "oil_gas"
    assert rt["sector_family"] == "ev_ebitda" and "5 segment" in rt["rule"]
    plain = R.route_for("RELIANCE.NS", {}, "RELIANCE INDUSTRIES LTD", segment_lookup=lambda s: [])
    assert plain["method"] == "oil_gas" and "sector_method" not in plain
    hold = R.route_for("BAJAJHLDNG.NS", {"BAJAJHLDNG.NS": "Financial"},
                       "BAJAJ HOLDINGS & INVESTMENT LTD", segment_lookup=lambda s: [{"x": 1}])
    assert hold["method"] == "holding_sotp"                  # a holding company stays one
    assert R.method_row(R.CONGLOMERATE_METHOD)["family"] == "sotp"
    assert len(R.SECTOR_METHODS) == 21                       # the owner's rows untouched


def test_a_conglomerate_stays_in_its_sector_peer_group(monkeypatch):
    STATE["seg"]["RELIANCE"] = RELIANCE_SEGMENTS
    monkeypatch.setattr(P, "statement_symbols", lambda: ["RELIANCE"])
    base = P.peer_base(lambda t: 1202.0, {}, {"RELIANCE.NS": "RELIANCE INDUSTRIES LTD"})
    assert base[0]["method"] == "oil_gas"


def _peer_rows():
    mk = lambda t, **k: {"ticker": f"{t}.NS", **k}                    # noqa: E731
    return {"oil_gas": [mk("IOC", ev_ebit=4.0), mk("BPCL", ev_ebit=5.0),
                        mk("HINDPETRO", ev_ebit=6.0), mk("ONGC", ev_ebit=8.0),
                        mk("OIL", ev_ebit=7.0)],
            "retail_ev_ebitda": [mk("DMART", ev_ebit=60.0), mk("TRENT", ev_ebit=50.0)],
            "telecom_ev_ebitda": [mk("BHARTIARTL", ev_ebit=20.0)],
            "earnings": [mk("INFY", pe=20.0), mk("HCLTECH", pe=22.0), mk("WIPRO", pe=18.0)],
            "auto": [mk("BAJAJ-AUTO", price=10287.0)],
            "nbfc_pb": [mk("BAJAJFINSV", price=1700.0)]}


def test_conglomerate_sum_of_parts_from_segment_ebit_and_default_peers():
    STATE["seg"]["RELIANCE"] = RELIANCE_SEGMENTS
    r = _eval("RELIANCE.NS", price=1202.0, company_name="RELIANCE INDUSTRIES LTD",
              peer_rows=_peer_rows)
    assert r["route"]["method"] == R.CONGLOMERATE_METHOD
    assert r["route"]["sector_method"] == "oil_gas"
    rg = r["range"]
    assert rg["status"] == "OK" and r["range_flags"]["sum_of_parts"] == "conglomerate"
    mult = {"O2C": 5.0, "Oil and Gas": 7.5, "Retail": 55.0, "Digital Services": 20.0,
            "Others": 5.0}
    ev_cr = sum(s["ebit_cr"] * mult[s["segment"]] for s in RELIANCE_SEGMENTS)
    snap = S.snapshot("RELIANCE", price=1202.0)
    sh = P._f(snap, "shares_outstanding")
    nd = (P._f(snap, "total_debt") - P._f(snap, "cash") - P._sti(snap)) / sh
    mi = P._f(snap, "minority_interest_bs") / sh
    eq = ev_cr * P.CR / sh - nd - mi
    assert rg["base"] == pytest.approx(eq, abs=0.01)
    assert rg["low"] == pytest.approx(eq * 0.85, abs=0.01)
    assert rg["high"] == pytest.approx(eq * 1.15, abs=0.01)
    assert rg["single_route"] and rg["single_route"]["family"] == "ev_ebitda"
    segs = {s["segment"]: s for s in rg["bridge"]["segments"]}
    assert segs["O2C"]["peers"] == ["IOC", "BPCL", "HINDPETRO"]
    json.dumps(r)


def test_a_typed_segment_multiple_wins_and_a_typed_band_moves_the_range():
    STATE["seg"]["RELIANCE"] = RELIANCE_SEGMENTS
    owner = {"RELIANCE": {"segment_multiples": {"Retail": 30.0}}}
    a = _eval("RELIANCE.NS", price=1202.0, company_name="RELIANCE INDUSTRIES LTD",
              peer_rows=_peer_rows)
    b = _eval("RELIANCE.NS", price=1202.0, company_name="RELIANCE INDUSTRIES LTD",
              peer_rows=_peer_rows, owner_inputs=owner,
              overrides={"range.typed_value_band_pct": 10.0})
    segs = {s["segment"]: s for s in b["range"]["bridge"]["segments"]}
    assert segs["Retail"]["multiple"] == 30.0
    assert b["range"]["base"] < a["range"]["base"]
    assert b["range"]["low"] == pytest.approx(b["range"]["base"] * 0.9, abs=0.01)
    assert b["drafted"] is True and "range.typed_value_band_pct" in \
        b["assumptions"]["drafted_effective"]


def test_holding_company_sum_of_parts_from_listed_stakes():
    STATE["hold"]["BAJAJHLDNG"] = BAJAJ_STAKES
    prices = {"BAJAJ-AUTO.NS": 10287.0, "BAJAJFINSV.NS": 1700.0}
    r = _eval("BAJAJHLDNG.NS", price=11265.0, sector_map={"BAJAJHLDNG.NS": "Financial"},
              company_name="BAJAJ HOLDINGS & INVESTMENT LTD", price_of=prices.get)
    assert r["route"]["method"] == "holding_sotp"
    rg = r["range"]
    assert rg["bridge"]["gross_per_share"] == pytest.approx(18223.07, abs=0.01)
    assert (rg["low"], rg["base"], rg["high"]) == (7289.23, 9111.53, 10933.84)
    assert r["range_flags"]["sum_of_parts"] == "holding_company"
    # the peer table's price is the fallback when no price source is injected
    c = _eval("BAJAJHLDNG.NS", price=11265.0, sector_map={"BAJAJHLDNG.NS": "Financial"},
              company_name="BAJAJ HOLDINGS & INVESTMENT LTD", peer_rows=_peer_rows)
    assert c["range"]["base"] == 9111.53
    d = _eval("BAJAJHLDNG.NS", price=11265.0, sector_map={"BAJAJHLDNG.NS": "Financial"},
              company_name="BAJAJ HOLDINGS & INVESTMENT LTD", price_of=prices.get,
              owner_inputs={"holding_discount_pct": 40.0})
    assert d["range"]["base"] == pytest.approx(18223.07 * 0.6, abs=0.01)


def test_typed_nav_gives_a_range_and_the_owner_table_shapes_agree():
    row = {"nav_ps": 900.0, "nav_asof": "2026-09-30"}
    outs = [_eval("DLF.NS", owner_inputs=x)
            for x in (row, {"DLF": row}, {"inputs": {"DLF": row}})]
    for r in outs:
        assert (r["range"]["low"], r["range"]["base"], r["range"]["high"]) == (765.0, 900.0, 1035.0)
        assert r["owner_inputs"]["nav_ps"] == 900.0
    assert _eval("DLF.NS")["range"]["status"] == "DATA_NEEDED"     # no input, no target


def test_a_typed_peer_list_replaces_the_route_median():
    a = _eval("TCS.NS")
    b = _eval("TCS.NS", owner_inputs={"TCS": {"peer_list": ["INFY", "HCLTECH", "WIPRO", "ZZZ"]}},
              peer_rows=_peer_rows)
    assert b["range_flags"]["peer_list"] == ["INFY", "HCLTECH", "WIPRO"]
    assert "YOUR peer list" in b["range"]["method_text"] and "20.00" in b["range"]["method_text"]
    assert b["range"]["multiple_band"]["source"].startswith("peer 20.00")
    assert a["range"]["base"] != b["range"]["base"]


# ================================================================ item 9

def test_sheet_sensitivities_have_no_effect_while_their_input_is_neutral():
    sheet_no = [k for k in ASM.no_effect_keys() if k.startswith("sheet.")]
    assert len(sheet_no) == 26 and len(ASM.SHEET_INPUT) == 29
    assert set(ASM.SHEET_INPUT) - set(sheet_no) == {
        "sheet.beta", "sheet.sensex_weight_pct", "sheet.margin_gain_per_1pct_rupee_pp"}
    why = ASM.no_effect_reason("sheet.capex_per_10pp_wfh")
    assert why and "driver is 0" in why and not why.startswith("no effect")
    live = ASM.no_effect_keys({"repo": True, "inst_holding": True})
    assert "sheet.pe_per_1pp_repo_pct" not in live and "sheet.pe_per_1pp_inst_holding_pct" not in live
    assert "sheet.beta" in ASM.no_effect_keys({"chain": False})
    rows = {r["key"]: r for r in ASM.describe({}, live={"repo": True})}
    assert rows["sheet.pe_per_1pp_repo_pct"]["no_effect"] is None
    assert rows["sheet.pe_per_1pp_inflation_pct"]["no_effect"]


def test_the_record_says_which_inputs_were_live_and_counts_drafts_by_it():
    fed = lambda s: {**_macro(s), "repo_start_pct": 6.0, "repo_end_pct": 5.5,      # noqa: E731
                     "inst_holding_start": 20.0, "inst_holding_end": 22.0}
    ov = {"sheet.pe_per_1pp_repo_pct": -8.0}
    a = _eval("TCS.NS", overrides=ov)
    b = _eval("TCS.NS", overrides=ov, macro=fed)
    assert a["assumptions"]["live_inputs"]["repo"] is False and a["drafted"] is False
    assert "sheet.pe_per_1pp_repo_pct" in a["assumptions"]["drafted_no_effect"]
    assert b["assumptions"]["live_inputs"]["repo"] is True and b["drafted"] is True
    assert "sheet.pe_per_1pp_repo_pct" not in b["assumptions"]["no_effect_keys"]
    bank = _eval("HDFCBANK.NS", overrides={"sheet.beta": 1.3})
    assert bank["assumptions"]["live_inputs"]["chain"] is False and bank["drafted"] is False


# ================================================================ item 10

def _factor(rec, cell):
    for f in rec["scenarios"]["pe_bridge"]["factors"]:
        if f["cell"] == cell:
            return f["pct"]
    raise KeyError(cell)


def test_repo_start_and_end_are_read_separately():
    fed = lambda s: {**_macro(s), "repo_start_pct": 6.0, "repo_end_pct": 5.5}  # noqa: E731
    r = _eval("TCS.NS", macro=fed)
    c = r["detail"]["cases"]["base"]
    assert c["cells"]["D73"] == 6.0 and c["cells"]["E73"] == 5.5
    k = SC.SHEET_ASSUMPTIONS["pe_per_1pp_repo_pct"]["value"]
    assert _factor(r, "E122") == SC.xround(-0.5 * k, 2) != 0.0
    assert not any("repo" in x for x in r["scenarios"]["macro_data_needed"])


def test_a_repo_start_that_cannot_be_verified_counts_zero_with_its_reason():
    fed = lambda s: {**_macro(s), "repo_end_pct": 5.5,                          # noqa: E731
                     "repo_start_why": "RBI page for 2026-03-31 not fetched"}
    r = _eval("TCS.NS", macro=fed)
    assert _factor(r, "E122") == 0.0
    md = " ".join(r["scenarios"]["macro_data_needed"])
    assert "repo rate at the base period end" in md and "not fetched" in md
    m, _ = P._market({"repo_pct": 5.5}, {}, {"sector": "X"}, 15.0,
                     {"pat": 10.0, "shares": 1.0, "price_fy_end": 100.0})
    assert m["repo_start"] == m["repo_end"] == 5.5            # older callers: no move


def test_institutional_holding_reaches_the_bridge():
    fed = lambda s: {**_macro(s), "inst_holding_start": 20.0, "inst_holding_end": 22.5}  # noqa: E731
    r = _eval("TCS.NS", macro=fed)
    k = SC.SHEET_ASSUMPTIONS["pe_per_1pp_inst_holding_pct"]["value"]
    assert _factor(r, "E127") == SC.xround(2.5 * k, 2)
    assert not any(x.startswith("institutional") for x in r["scenarios"]["macro_data_needed"])
    assert any(x.startswith("institutional") for x in _eval("TCS.NS")["scenarios"]["macro_data_needed"])


# ================================================================ shape

def test_failed_record_and_real_records_carry_the_new_keys():
    f = P.failed_record("ZZ.NS", "boom")
    for rec in (f, _eval("TCS.NS"), _eval("HDFCBANK.NS")):
        assert "warnings" in rec["verdict"] and "own_history" in rec["range"]
        assert "inverted" in rec["range"] and "whatif" in rec["range"]
        assert "single_route" in rec["range"] and "target_starts_from" in rec["earnings"]
        assert "live_inputs" in rec["assumptions"] and "no_effect_keys" in rec["assumptions"]
        assert "sector_method" in rec["route"]
        json.dumps(rec, default=str)
    assert P.VERSION == "2026-10-21"
