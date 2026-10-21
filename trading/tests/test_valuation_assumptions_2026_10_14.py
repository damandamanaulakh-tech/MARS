"""Editable assumptions of the valuation chain — owner order 2026-10-14
("put the formulas in the den now … need editable as said in 40+ points").
Money impact NONE: the registry is read by the Den only.

Pins: every editable row's default equals the module value it stands for
(the registry can never drift from the code); a draft is differences only;
bad fields are refused by name and nothing else is accepted alongside
them; `split` routes each key to the module argument that consumes it;
`pipeline.evaluate(overrides=…)` changes exactly what the key names (case
delta → upside EPS, verdict band → verdict, publication lag → date, ROE
step / ERP → the bank range) and leaves the module defaults untouched;
the money path never imports the registry.
"""
from __future__ import annotations

import inspect
import math

import pytest

from trading import valuation_context as _vc
from trading.valuation import assess as A
from trading.valuation import assumptions as ASM
from trading.valuation import normalise as N
from trading.valuation import pipeline as P
from trading.valuation import scenarios as SC
from trading.valuation import shares as SH
from trading.valuation import snapshot as S
from trading.valuation import terms as T

PRICES = {"TCS.NS": 2156.0, "HDFCBANK.NS": 707.25}


def _macro(snap):
    return {"sensex_start": 72000.0, "sensex_end": 72720.0, "nifty_start": 22000.0,
            "nifty_end": 22220.0, "vix": 13.0, "rf_pct": 7.0, "beta": 1.0,
            "usdinr_now": 95.0, "usdinr_base": 93.5, "price_now": 2156.0}


def _peers(method, exclude):
    return {"pe_median": 24.0, "pb_median": 2.0, "ev_ebitda_median": 9.0,
            "ev_sales_median": 2.5, "n": 5, "source": "test"}


def _eval(ticker, **kw):
    args = dict(price=PRICES[ticker], price_date="2026-10-09",
                sector_map={"TCS.NS": "IT", "HDFCBANK.NS": "Financial"},
                company_name={"TCS.NS": "TATA CONSULTANCY SERVICES LTD",
                              "HDFCBANK.NS": "HDFC BANK LTD"}[ticker],
                price_at=lambda when: 2000.0, own_pe_history=lambda s: [18.0, 22.0, 26.0, 30.0],
                peers=_peers, macro=_macro)
    args.update(kw)
    return P.evaluate(ticker, **args)


def test_the_registry_mirrors_every_module_default():
    # batch 2 (owner 2026-10-21): + range.typed_value_band_pct (VAL-01 / VAL-14b)
    assert len(ASM.EDITABLE) == 51
    assert ASM.defaults()["range.typed_value_band_pct"] == float(
        getattr(SC, "TYPED_VALUE_BAND_PCT", 15.0)) == 15.0
    d = ASM.defaults()
    for name, cell, val, _label in SC._A:                 # the sheet's 29
        assert d[f"sheet.{name}"] == float(val), name
        assert ASM.BY_KEY[f"sheet.{name}"]["cell"] == cell
    for case in ("downside", "upside"):
        for k, v in SC.CASE_DELTAS[case].items():
            assert d[f"case.{case}.{k}"] == float(v)
    assert d["range.book_roe_step_pp"] == SC.BOOK_ROE_STEP_PP
    assert d["range.equity_risk_premium_pct"] == float(_vc.ASSUMED["equity_risk_premium_pct"])
    assert d["range.long_run_growth_pct"] == float(_vc.ASSUMED["long_run_growth_pct"])
    for k in ("attractive_margin_pct", "watch_band_pct", "unattractive_premium_pct",
              "max_range_width_pct", "max_uncertainty_pct"):
        assert d[f"verdict.{k}"] == float(A.ASSUMED[k]["value"])
    assert d["snapshot.publication_lag_days"] == int(S.ASSUMED["publication_lag_days"])
    assert d["snapshot.usd_signature_pe"] == float(S.ASSUMED["usd_signature_pe"])
    assert d["shares.reconcile_tol_pct"] == float(SH.ASSUMED["reconcile_tol_pct"])
    for k in ("cash_conversion_min", "net_debt_ebitda_max", "interest_cover_min"):
        assert d[f"quality.{k}"] == float(N.ASSUMED["thresholds"][k])
    assert d["terms.dcf_growth_cap_pct"] == float(T.ASSUMED["dcf_growth_cap_pct"])
    for r in ASM.EDITABLE:                                  # every row names its source
        assert r["source"] and r["label"] and r["group"] in ASM.GROUP_LABEL


def test_coerce_keeps_differences_only_and_refuses_bad_fields_by_name():
    v, e = ASM.coerce({"case.upside.revenue_growth_pct": " 8 ",
                       "verdict.attractive_margin_pct": "",          # blank = default
                       "verdict.watch_band_pct": "10",               # equals default → dropped
                       "nonsense.key": "5"})
    assert v == {"case.upside.revenue_growth_pct": 8.0} and e == []
    v, e = ASM.coerce({"sheet.beta": "abc", "snapshot.publication_lag_days": "60.5",
                       "range.long_run_growth_pct": "99", "terms.dcf_growth_cap_pct": "nan",
                       "case.downside.revenue_growth_pct": "-7"})
    assert v == {"case.downside.revenue_growth_pct": -7.0}   # the good field survives…
    assert len(e) == 4                                      # …and every bad one is named
    assert any("sheet.beta" in x for x in e) and any("whole number" in x for x in e)
    assert any("outside" in x for x in e) and any("finite" in x for x in e)
    assert ASM.clean({"sheet.beta": float("inf"), "case.upside.pe_bridge_extra_pct": 10.0,
                      "snapshot.publication_lag_days": "70"}) == {"snapshot.publication_lag_days": 70}


def test_split_routes_every_key_to_the_argument_that_consumes_it():
    ov = ASM.split({"sheet.beta": 1.3, "case.upside.revenue_growth_pct": 9.0,
                    "verdict.watch_band_pct": 12.0, "quality.interest_cover_min": 3.0,
                    "range.book_roe_step_pp": 3.0, "snapshot.publication_lag_days": 30,
                    "shares.reconcile_tol_pct": 5.0, "terms.dcf_growth_cap_pct": 12.0})
    assert ov["sheet"] == {"beta": 1.3}
    assert ov["case_deltas"]["upside"]["revenue_growth_pct"] == 9.0
    assert ov["case_deltas"]["downside"] == SC.CASE_DELTAS["downside"]   # untouched case kept
    assert ov["verdict"] == {"watch_band_pct": 12.0}
    assert ov["quality"] == {"interest_cover_min": 3.0}
    assert ov["range"] == {"book_roe_step_pp": 3.0}
    assert ov["snapshot"] == {"publication_lag_days": 30}
    assert ov["shares"] == {"reconcile_tol_pct": 5.0}
    assert ov["terms"] == {"dcf_growth_cap_pct": 12.0}
    assert ov["n_edited"] == 8 and len(ov["edited"]) == 8
    assert ASM.split(None)["n_edited"] == 0 and ASM.split(None)["case_deltas"] == SC.CASE_DELTAS


def test_a_draft_changes_exactly_what_it_names_and_the_defaults_stay():
    before = (dict(SC.CASE_DELTAS["upside"]), int(S.ASSUMED["publication_lag_days"]),
              float(A.ASSUMED["max_range_width_pct"]["value"]))
    # 3 own readings: the VAL-03 trim (4+ readings, owner 2026-10-21) would
    # narrow the 4-reading fixture band and judge TCS without the draft
    hist3 = lambda s: [18.0, 24.0, 30.0]                   # noqa: E731
    a = _eval("TCS.NS", own_pe_history=hist3)
    b = _eval("TCS.NS", own_pe_history=hist3,
              overrides={"case.upside.revenue_growth_pct": 15.0,
                                   "verdict.max_range_width_pct": 200.0,
                                   "snapshot.publication_lag_days": 60})
    assert a["drafted"] is False and b["drafted"] is True
    assert b["assumptions"]["drafted"] == ["case.upside.revenue_growth_pct",
                                           "snapshot.publication_lag_days",
                                           "verdict.max_range_width_pct"]
    # the upside case moved, the base and downside did not
    assert b["scenarios"]["eps"]["upside"] > a["scenarios"]["eps"]["upside"]
    assert b["scenarios"]["eps"]["base"] == a["scenarios"]["eps"]["base"]
    assert b["scenarios"]["eps"]["downside"] == a["scenarios"]["eps"]["downside"]
    # the width cap lifted → the verdict is no longer "insufficient"
    assert a["verdict"]["verdict"] == "insufficient" and b["verdict"]["verdict"] != "insufficient"
    # the publication date moved by 15 days
    from datetime import date
    da = date.fromisoformat(a["snapshot"]["publication_date"])
    dbb = date.fromisoformat(b["snapshot"]["publication_date"])
    assert (dbb - da).days == 15
    # and nothing in the modules changed
    assert (dict(SC.CASE_DELTAS["upside"]), int(S.ASSUMED["publication_lag_days"]),
            float(A.ASSUMED["max_range_width_pct"]["value"])) == before
    assert math.isfinite(b["range"]["base"]) and b["range"]["base"] == a["range"]["base"]


def test_the_bank_route_reads_the_roe_step_and_erp_draft():
    a = _eval("HDFCBANK.NS")
    b = _eval("HDFCBANK.NS", overrides={"range.book_roe_step_pp": 1.0,
                                        "range.equity_risk_premium_pct": 4.0})
    assert a["route"]["family"] == "book"
    assert (a["range"]["low"], a["range"]["base"], a["range"]["high"]) != \
           (b["range"]["low"], b["range"]["base"], b["range"]["high"])
    # a smaller ROE step = a narrower range. Batch 2 (VAL-02, owner 2026-10-21):
    # the bank's ROE now reads ~14% on its average tangible book, so the lower
    # ERP lifts every value more than the step trims the high — judge the width
    wa = (a["range"]["high"] - a["range"]["low"]) / a["range"]["base"]
    wb = (b["range"]["high"] - b["range"]["low"]) / b["range"]["base"]
    assert wb < wa
    assert b["drafted"] and "range.equity_risk_premium_pct" in b["assumptions"]["drafted"]


def test_chain_formulas_prints_every_computed_cell_of_the_sheet():
    cf = SC.chain_formulas()
    cells = {c["cell"] for c in cf}
    assert {"E17", "E19", "E22", "E29", "E118", "E132", "E134", "E135"} <= cells
    assert all(c["formula"] and c["formula"] != "input" for c in cf)
    assert any(c["formula"] == "ROUND(E22/E28,2)" for c in cf)      # EPS
    assert len(cf) >= 60


def test_the_money_path_never_imports_the_registry_or_the_draft():
    from trading import danger, engine, orders, risk, sizing, targets, exits
    for mod in (engine, risk, orders, danger, sizing, targets, exits):
        src = "\n".join(l for l in inspect.getsource(mod).splitlines()
                        if not l.strip().startswith("#"))
        # Item 19 (owner order 2026-10-21): the engine may read
        # trading.valuation_exit (pure, injected readers) — never the
        # registry, the Den draft or the valuation package itself.
        for banned in ("valuation.assumptions", "valuation import assumptions",
                       "LAST_ASSUMPTIONS", "load_assumptions", "valuation_chain_view",
                       "from .valuation import", "from .valuation.", "trading.valuation."):
            assert banned not in src, (mod.__name__, banned)
    src = inspect.getsource(ASM)
    for banned in ("from web", "import web", "trading.engine", "trading.risk", "httpx", "requests"):
        assert banned not in src, banned
