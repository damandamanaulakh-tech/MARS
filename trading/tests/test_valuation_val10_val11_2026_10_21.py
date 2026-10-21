"""VAL-10 / VAL-11 and the known-fault facts — owner order 2026-10-21.
Money impact NONE: the valuation chain is display only.

VAL-10: with no stored CPI, GDP or delivery figure, the P/E-bridge row
counts 0 under ANY draft of its comfort / trend / benchmark (before the
fix the app filled the module default and compared it with the draft, so
comfort 4 -> 5 moved every target). A stored figure still moves its row.

VAL-11: four editable numbers change no result yet. They stay editable,
carry a `no_effect` reason, and a draft of only these does not count as
"draft in use" (`effective_keys`, `rec["drafted"]`).

Known-fault facts: every record carries `range_flags` (case values before
the sort, which case was printed as base, mid-cycle, EV-route minorities /
short-term investments, lender / power-utility route) so the Den's
warning rules never parse note text.
"""
from __future__ import annotations

import json

from trading.tests.test_valuation_pipeline_2026_10_14 import _eval, _macro
from trading.valuation import assumptions as ASM
from trading.valuation import pipeline as P
from trading.valuation import scenarios as SC

NO_EFFECT = ["snapshot.publication_lag_days", "terms.dcf_growth_cap_pct",
             "verdict.max_uncertainty_pct", "verdict.watch_band_pct"]

DRAFTS = ({"sheet.inflation_comfort_pct": 5.0},
          {"sheet.gdp_trend_pct": 7.5},
          {"sheet.delivery_benchmark_pct": 50.0})


def _no_band(ticker="TCS.NS", **kw):
    # no peers and no own history: the target is EPS x each case's own
    # closing P/E, so the bridge reaches the printed target
    return _eval(ticker, own_pe_history=lambda s: [], peers=lambda m, e: {}, **kw)


def _factor(rec, cell):
    for f in rec["scenarios"]["pe_bridge"]["factors"]:
        if f["cell"] == cell:
            return f["pct"]
    raise KeyError(cell)


def _targets(rec):
    return ((rec["range"]["low"], rec["range"]["base"], rec["range"]["high"]),
            rec["scenarios"]["model_price"], rec["scenarios"]["eps"],
            rec["scenarios"]["pe_bridge_pct"])


# ------------------------------------------------------------ VAL-10

def test_missing_cpi_gdp_delivery_count_zero_under_any_draft():
    a = _no_band()
    assert a["range"]["status"] == "OK"
    assert "case P/E only" in a["range"]["multiple_band"]["source"]
    for ov in DRAFTS:
        b = _no_band(overrides=ov)
        assert _targets(b) == _targets(a), ov
        for cell in ("E123", "E124", "E128"):
            assert _factor(b, cell) == 0.0, (ov, cell)


def test_market_fills_a_missing_figure_with_the_drafted_comparison():
    base = {"revenue": 1000.0, "ebitda": 180.0, "pat": 100.0, "tax": 30.0,
            "employee_cost": 500.0, "shares": 10.0, "dividend_ps": 2.0,
            "equity": 600.0, "borrowings": 50.0, "net_cash": 50.0,
            "prior_revenue": 900.0, "price_fy_end": 150.0}
    sheet = {"inflation_comfort_pct": 5.0, "gdp_trend_pct": 7.5,
             "delivery_benchmark_pct": 50.0}
    m, missing = P._market({}, {}, {"sector": "X"}, 15.0, base, sheet=sheet)
    assert (m["cpi"], m["gdp"], m["delivery_pct"]) == (5.0, 7.5, 50.0)
    m0, _ = P._market({}, {}, {"sector": "X"}, 15.0, base)
    assert (m0["cpi"], m0["gdp"], m0["delivery_pct"]) == (4.0, 6.5, 45.0)
    assert any(x.startswith("CPI") for x in missing)
    assert any(x.startswith("GDP") for x in missing)
    assert any(x.startswith("delivery") for x in missing)


def test_a_stored_cpi_still_moves_the_inflation_row_with_the_draft():
    stored = lambda s: {**_macro(s), "cpi_pct": 6.0}                    # noqa: E731
    k = float(SC.SHEET_ASSUMPTIONS["pe_per_1pp_inflation_pct"]["value"])
    a = _no_band(macro=stored)
    b = _no_band(macro=stored, overrides={"sheet.inflation_comfort_pct": 5.0})
    assert _factor(a, "E123") == SC.xround((6.0 - 4.0) * k, 2) != 0.0
    assert _factor(b, "E123") == SC.xround((6.0 - 5.0) * k, 2)
    assert a["range"]["base"] != b["range"]["base"]
    assert not any(x.startswith("CPI") for x in a["scenarios"]["macro_data_needed"])


def test_stored_gdp_and_delivery_move_their_rows_and_leave_the_missing_list():
    stored = lambda s: {**_macro(s), "gdp_pct": 7.0, "delivery_pct": 50.0}  # noqa: E731
    a = _no_band(macro=stored)
    b = _no_band(macro=stored, overrides={"sheet.gdp_trend_pct": 7.5,
                                          "sheet.delivery_benchmark_pct": 50.0})
    assert _factor(a, "E124") != 0.0 and _factor(a, "E128") != 0.0
    assert _factor(b, "E124") != _factor(a, "E124")
    assert _factor(b, "E128") == 0.0                      # 50 stored vs benchmark 50
    md = a["scenarios"]["macro_data_needed"]
    assert not any(x.startswith(("GDP", "delivery")) for x in md)
    assert any(x.startswith("delivery") for x in _no_band()["scenarios"]["macro_data_needed"])


# ------------------------------------------------------------ VAL-11

def test_the_four_no_effect_rows_are_named_and_still_editable():
    # batch 2 (item 9, owner 2026-10-21): the sheet sensitivities whose input
    # the app feeds as a neutral 0 are no-effect too, worked out per render —
    # the four fixed rows stay; with every sheet input live only they remain
    every_input_live = {k: True for k in ASM.DEFAULT_LIVE}
    assert ASM.no_effect_keys(every_input_live) == NO_EFFECT
    assert set(NO_EFFECT) < set(ASM.no_effect_keys())
    for r in ASM.EDITABLE:
        if r["key"] in NO_EFFECT:
            assert isinstance(r["no_effect"], str) and len(r["no_effect"]) > 20
            assert r["key"] in ASM.NO_EFFECT_REASONS
        else:
            assert r["no_effect"] is None, r["key"]
    rows = {r["key"]: r for r in ASM.describe({"verdict.watch_band_pct": 20.0})}
    assert rows["verdict.watch_band_pct"]["edited"] and rows["verdict.watch_band_pct"]["no_effect"]
    vals, errs = ASM.coerce({"verdict.watch_band_pct": "20", "terms.dcf_growth_cap_pct": "6"})
    assert not errs and set(vals) == {"verdict.watch_band_pct", "terms.dcf_growth_cap_pct"}


def test_effective_keys_leave_out_the_no_effect_rows():
    only = {"verdict.max_uncertainty_pct": 50.0, "verdict.watch_band_pct": 20.0,
            "terms.dcf_growth_cap_pct": 6.0, "snapshot.publication_lag_days": 90}
    assert ASM.effective_keys(only) == []
    assert ASM.effective_keys({**only, "sheet.beta": 1.3}) == ["sheet.beta"]
    # the lag does take effect once an as-of date is given
    assert ASM.effective_keys(only, asof="2026-06-30") == ["snapshot.publication_lag_days"]
    ov = ASM.split({**only, "sheet.beta": 1.3})
    assert ov["n_edited"] == 5 and ov["effective"] == ["sheet.beta"] and ov["n_effective"] == 1
    assert ov["no_effect_edited"] == NO_EFFECT


def test_a_draft_of_only_the_no_effect_rows_is_not_a_draft_in_use():
    only = {"verdict.max_uncertainty_pct": 50.0, "verdict.watch_band_pct": 20.0,
            "terms.dcf_growth_cap_pct": 6.0, "snapshot.publication_lag_days": 90}
    a = _eval("TCS.NS")
    b = _eval("TCS.NS", overrides=only)
    assert b["drafted"] is False
    assert b["assumptions"]["drafted_effective"] == []
    assert b["assumptions"]["drafted_no_effect"] == NO_EFFECT
    assert b["assumptions"]["drafted"] == NO_EFFECT          # still listed as typed
    assert _targets(b) == _targets(a)
    assert b["verdict"]["verdict"] == a["verdict"]["verdict"]
    c = _eval("TCS.NS", overrides={**only, "case.upside.revenue_growth_pct": 15.0})
    assert c["drafted"] is True
    assert c["assumptions"]["drafted_effective"] == ["case.upside.revenue_growth_pct"]


# ------------------------------------------------------------ known-fault facts

# batch 2 (owner 2026-10-21) added the facts the Den reads to retire the
# fixed warnings: lender book basis (VAL-02), typed peer list / sum-of-parts
# (VAL-01), the unified EV rule (VAL-12a), the mid-cycle margin (VAL-07)
FLAG_KEYS = {"family", "method", "sector", "case_values", "inverted", "printed_case",
             "printed_base_case", "midcycle_base", "net_debt_per_share",
             "minorities_per_share", "short_term_investments_per_share",
             "peer_ev_rule", "lender", "power_utility", "book_route",
             "inverted_note", "midcycle_margin", "ev_rule_unified",
             "lender_book_source", "lender_book_basis", "peer_list", "sum_of_parts",
             "target_starts_from"}


def test_ev_route_with_an_inverted_case_names_the_printed_base_case():
    r = _eval("SAIL.NS")
    f = r["range_flags"]
    assert set(f) == FLAG_KEYS
    assert f["family"] == "ev_ebitda" and f["midcycle_base"] is True
    cv = f["case_values"]
    # batch 2 (VAL-07, owner 2026-10-21): the mid-cycle MARGIN now applies to
    # all three metals cases, so they come out in order (the old mid-cycle
    # base sat below the downside); stage 17 never sorts — low IS downside
    assert cv["downside"] < cv["base"] < cv["upside"]
    assert f["inverted"] is False and f["printed_base_case"] == "base"
    assert (r["range"]["low"], r["range"]["base"], r["range"]["high"]) == (
        cv["downside"], cv["base"], cv["upside"])
    assert isinstance(f["minorities_per_share"], float)
    assert isinstance(f["short_term_investments_per_share"], float)
    assert isinstance(f["net_debt_per_share"], float)
    assert f["peer_ev_rule"] and not f["lender"] and not f["book_route"]


def test_range_flags_from_a_synthetic_inverted_ev_range():
    rng = {"per_share": {"downside": 167.95, "base": 159.46, "upside": 241.85}}
    kw = {"per_share_overrides": {"base": 10.0}, "net_debt_per_share": 5.0,
          "minorities_per_share": 1.5}
    snap = {"fields": {"shares_outstanding": 100.0}, "period_end": "2026-03-31",
            "extra": {"short_term_investments": {"2026-03-31": 250.0}}}
    f = P._range_flags("ev_ebitda", {"method": "oil_gas", "sector": "Oil & gas / OMC"},
                       rng, kw, snap)
    assert f["inverted"] is True
    assert f["printed_case"] == {"low": "base", "base": "downside", "high": "upside"}
    assert f["minorities_per_share"] == 1.5
    assert f["short_term_investments_per_share"] == 2.5
    ordered = P._range_flags("earnings", {"method": "earnings"},
                             {"bridge": {"eps": {"downside": 9.0, "base": 10.0, "upside": 11.0}},
                              "multiple_band": {"low": 15.0, "mid": 20.0, "high": 25.0}},
                             {}, {})
    assert ordered["inverted"] is False and ordered["printed_base_case"] == "base"
    assert ordered["case_values"] == {"downside": 135.0, "base": 200.0, "upside": 275.0}


def test_book_route_lender_and_power_utility_are_flagged():
    r = _eval("HDFCBANK.NS")
    f = r["range_flags"]
    assert f["book_route"] and f["lender"] and f["method"] == "bank_pb_roe"
    assert not f["power_utility"] and f["inverted"] is False
    assert f["minorities_per_share"] is None and f["peer_ev_rule"] == ""
    p = _eval("NTPC.NS", price=330.0, sector_map={"NTPC.NS": "Power"},
              company_name="NTPC LTD")
    g = p["range_flags"]
    assert g["family"] == "book" and g["power_utility"] and not g["lender"]


def test_failed_record_carries_the_same_flag_and_draft_shape():
    f = P.failed_record("ZZ.NS", "boom")
    assert set(f["range_flags"]) == FLAG_KEYS
    assert f["range_flags"]["inverted"] is False
    assert f["assumptions"]["drafted_effective"] == [] and f["drafted"] is False
    json.dumps(f)
    json.dumps(_eval("SAIL.NS")["range_flags"])
