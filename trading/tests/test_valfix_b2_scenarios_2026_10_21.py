"""Valuation fixes batch 2 — stage 17 / 18 (scenarios.py, assess.py).
Owner choices 2026-10-21; money impact NONE (display only).

Pins:
  * VAL-07 — the three cases are never sorted: out of order = `inverted`
    + "CASES INVERTED — …", and stage 18 gives no verdict on it;
  * VAL-03 — 4+ own readings drop the single lowest and highest; the
    source names them; the band mid (base target) never moves;
  * VAL-09 — the P/E bridge sits BESIDE a banded target (`whatif`); the
    grid prices EPS × band mid, so its centre is the range base;
  * VAL-01 / VAL-14b — typed NAV / embedded value, holding-company and
    conglomerate sum-of-parts, each with the numbers measured in the
    2026-10-20 option studies (report_band / report_ev / report_bridge /
    report_data).
"""
from __future__ import annotations

import json

import pytest

from trading.valuation import assess as A
from trading.valuation import is_dn
from trading.valuation import scenarios as sc


@pytest.fixture(scope="module")
def cases():
    return sc.three_cases(sc.SHEET_BASE, sc.SHEET_DRIVERS, sc.SHEET_MARKET)


def _verdict(r, price):
    return A.assess(price, {"low": r["low"], "base": r["base"], "high": r["high"],
                            "inverted_note": r.get("inverted_note")})


# ------------------------------------------------------------- VAL-07 order

def test_finish_never_sorts_an_inverted_range():
    r = sc.justified_range("ev_ebitda", {}, price=170.0, peer_multiple=8.0,
                           per_share_overrides={"downside": 25.0, "base": 22.0,
                                                "upside": 35.0},
                           net_debt_per_share=10.0, minorities_per_share=0.0)
    assert r["status"] == "OK"
    # downside 25×8−10 = 190 stays first; base 22×8−10 = 166 stays second
    assert (r["low"], r["base"], r["high"]) == (190.0, 166.0, 270.0)
    assert r["inverted"] is True
    assert r["inverted_note"] == "CASES INVERTED — downside ₹190.00 above base ₹166.00"
    assert r["inverted_note"] in r["notes"]
    assert not any("sorted" in n for n in r["notes"])
    v = _verdict(r, 170.0)
    assert v["verdict"] == "insufficient" and v["inverted"] is True
    assert v["reasons"][0].startswith("CASES INVERTED — downside ₹190.00 above base ₹166.00")
    assert "no verdict on an unordered range" in v["reasons"][0]


def test_an_ordered_range_is_not_flagged(cases):
    r = sc.justified_range("earnings", cases, price=715.85)
    assert r["inverted"] is False and r["inverted_note"] == ""
    b = sc.justified_range("book", {}, price=150.0, roe_pct=15.0, bvps=100.0,
                           rf_pct=7.0, beta=1.0)
    assert b["inverted"] is False


def test_flipped_draft_case_steps_render_inverted():
    """A Den §13 draft can flip the case steps; the range must say so."""
    flipped = {"downside": {"revenue_growth_pct": 5.0, "base_ebitda_margin_pct": 1.0,
                            "pe_bridge_extra_pct": 10.0},
               "base": {},
               "upside": {"revenue_growth_pct": -5.0, "base_ebitda_margin_pct": -1.0,
                          "pe_bridge_extra_pct": -10.0}}
    cs = sc.three_cases(sc.SHEET_BASE, sc.SHEET_DRIVERS, sc.SHEET_MARKET, deltas=flipped)
    r = sc.justified_range("earnings", cs, price=715.85)
    assert (r["low"], r["base"], r["high"]) == (838.68, 690.62, 559.64)
    assert r["inverted"] is True
    assert "downside ₹838.68 above base ₹690.62" in r["inverted_note"]
    assert "base ₹690.62 above upside ₹559.64" in r["inverted_note"]
    assert _verdict(r, 715.85)["verdict"] == "insufficient"


def test_ev_note_says_mid_cycle_is_for_the_metals_row_only():
    kw = dict(price=100.0, peer_multiple=8.0, net_debt_per_share=0.0,
              minorities_per_share=0.0)
    given = sc.justified_range("ev_ebitda", {}, per_share_overrides={
        "downside": 9.0, "base": 10.0, "upside": 11.0}, **kw)
    assert any(n.startswith("MID-CYCLE: the case EBITDA per share comes from the caller")
               for n in given["notes"])
    plain = sc.justified_range("ev_ebitda", {}, ebitda_per_share=10.0, **kw)
    assert any("mid-cycle applies to the metals row only" in n for n in plain["notes"])
    assert not any("other cyclicals" in n for n in given["notes"] + plain["notes"])


def test_assess_words_an_unordered_range_without_the_stage17_note():
    v = A.assess(100.0, {"low": 120.0, "base": 110.0, "high": 150.0})
    assert v["verdict"] == "insufficient" and v["inverted"] is True
    assert v["reasons"][0].startswith("CASES INVERTED — downside ₹120.00 above base ₹110.00")
    ok = A.assess(100.0, {"low": 80.0, "base": 110.0, "high": 150.0})
    assert ok["inverted"] is False


# ------------------------------------------------------------ VAL-03 trim

def test_four_or_more_own_years_drop_the_lowest_and_highest():
    b = sc.multiple_band(32.78, [27.83, 30.79, 26.87, 17.34])
    assert b["low"] == 26.87 and b["high"] == 32.78
    assert b["mid"] == pytest.approx((32.78 + (27.83 + 26.87) / 2) / 2)
    assert b["dropped"]["lowest"] == 17.34 and b["dropped"]["highest"] == 30.79
    assert "dropped the lowest 17.34" in b["source"]
    assert "the highest 30.79" in b["source"]
    assert b["readings"] == 4 and b["readings_used"] == 2


def test_three_own_years_are_all_kept():
    b = sc.multiple_band(51.66, [89.69, 79.09, 67.67])
    assert (b["low"], b["high"]) == (51.66, 89.69)
    assert b["mid"] == pytest.approx((51.66 + 79.09) / 2)
    assert b["dropped"] is None and "dropped" not in b["source"]


def test_trim_never_moves_the_base():
    for hist in ([27.83, 30.79, 26.87, 17.34], [61.0, 40.5, 37.8, 52.2, 45.0],
                 [10.0, 11.0, 30.0, 12.0, 13.0, 9.0]):
        on = sc.multiple_band(28.5, hist)
        off = sc.multiple_band(28.5, hist, trim_min_readings=99)
        assert on["mid"] == pytest.approx(off["mid"])
        assert on["low"] >= off["low"] and on["high"] <= off["high"]


def test_dropped_readings_are_named_by_period_when_rows_are_given():
    rows = [{"period": "2023-03-31", "pe": 27.83}, {"period": "2024-03-31", "pe": 30.79},
            {"period": "2025-03-31", "pe": 26.87}, {"period": "2026-03-31", "pe": 17.34}]
    b = sc.multiple_band(32.78, rows)
    assert "17.34 (2026-03-31)" in b["source"] and "30.79 (2024-03-31)" in b["source"]
    assert b["dropped"]["lowest_period"] == "2026-03-31"
    assert b["mid"] == sc.multiple_band(32.78, [27.83, 30.79, 26.87, 17.34])["mid"]


def test_ev_routes_band_is_unchanged():
    b = sc.multiple_band(7.684150, None, peer_only_point=True)
    assert (b["low"], b["mid"], b["high"]) == (7.684150, 7.684150, 7.684150)
    assert "single multiple" in b["source"]


def test_tcs_shaped_band_gets_a_verdict(cases):
    """TCS's own history (FY23-26 27.83 / 30.79 / 26.87 / 17.34) and IT peer
    32.78 on the sheet's cases: the trim narrows the range under the 60% cap."""
    hist = [27.83, 30.79, 26.87, 17.34]
    on = sc.justified_range("earnings", cases, price=450.0, peer_multiple=32.78,
                            own_multiple_history=hist)
    off = sc.justified_range("earnings", cases, price=450.0, peer_multiple=32.78,
                             own_multiple_history=hist, band_trim_min_readings=99)
    assert on["base"] == off["base"]
    v_on, v_off = _verdict(on, 450.0), _verdict(off, 450.0)
    assert v_off["verdict"] == "insufficient" and v_off["range_width_pct"] > 60
    assert v_on["range_width_pct"] < 60 and v_on["verdict"] == "attractive"
    assert on["assumptions_used"]["band_trim_min_readings"]["value"] == 4


# ---------------------------------------------------- VAL-09 what-if, grid

def _market(nifty_pct: float) -> dict:
    m = dict(sc.SHEET_MARKET)
    for a, b in (("nifty_start", "nifty_end"), ("sensex_start", "sensex_end")):
        m[b] = m[a] * (1 + nifty_pct / 100.0)
    return m


def test_band_range_ignores_bridge_and_whatif_follows_it():
    hist = [61.0, 40.5, 37.8, 52.2, 45.0]
    flat = sc.three_cases(sc.SHEET_BASE, sc.SHEET_DRIVERS, _market(0.0))
    down = sc.three_cases(sc.SHEET_BASE, sc.SHEET_DRIVERS, _market(-8.0))
    r1 = sc.justified_range("earnings", flat, price=715.85, peer_multiple=28.5,
                            own_multiple_history=hist)
    r2 = sc.justified_range("earnings", down, price=715.85, peer_multiple=28.5,
                            own_multiple_history=hist)
    assert (r1["low"], r1["base"], r1["high"]) == (r2["low"], r2["base"], r2["high"])
    assert r1["whatif"]["base"] != r2["whatif"]["base"]
    for c in sc.CASES:
        assert r1["whatif"][c] == flat[c]["model_price"]
        assert r2["whatif"][c] == down[c]["model_price"]
    assert r1["bridge"]["pe_bridge_moves_range"] is False
    assert "not the range" in r1["whatif_note"]


def test_no_band_has_no_separate_whatif(cases):
    r = sc.justified_range("earnings", cases, price=715.85)
    assert r["whatif"] is None and r["whatif_note"] == ""
    assert r["bridge"]["pe_bridge_moves_range"] is True


def test_grid_centre_equals_range_base_with_a_band(cases):
    r = sc.justified_range("earnings", cases, price=715.85, peer_multiple=28.5,
                           own_multiple_history=[61.0, 40.5, 37.8, 52.2, 45.0])
    g, mid = r["sensitivity"], r["multiple_band"]["mid"]
    assert g["status"] == "OK" and g["basis"] == "band_mid"
    assert g["centre"] == r["base"]
    assert [x["price"] for x in g["multiple_row"]] == [
        sc.xround(18.28 * mid * 0.9, 2), r["base"], sc.xround(18.28 * mid * 1.1, 2)]
    d = cases["base"]["inputs"]["drivers"]
    s = sc.fy_scenario(sc.SHEET_BASE, {**d, "revenue_growth_pct": d["revenue_growth_pct"] - 1,
                                       "base_ebitda_margin_pct": d["base_ebitda_margin_pct"] - 1},
                       sc.SHEET_MARKET)
    assert g["prices"][0][0] == sc.xround(s["eps"] * mid, 2)


def test_grid_without_a_band_is_unchanged(cases):
    g = sc.earnings_grid(cases)
    assert g["basis"] == "case_pe" and g["centre"] == cases["base"]["model_price"]
    assert g == sc.earnings_grid(cases, None)
    assert g["multiple_row"][1]["closing_pe"] == cases["base"]["closing_pe"]


# ------------------------------------------- VAL-01 / VAL-14b typed values

def test_nav_row_says_no_target_until_typed():
    r = sc.justified_range("nav", {}, price=150.0, roe_pct=15.0, bvps=100.0,
                           rf_pct=7.0, beta=1.0)
    assert r["status"] == "DATA_NEEDED"
    assert r["reason"].startswith("no target by design — type the NAV per share in the "
                                  "Den inputs table")
    assert r["fallback"]["base"] == 175.0


def test_typed_nav_gives_a_range_and_a_verdict():
    r = sc.justified_range("nav", {}, price=150.0, roe_pct=15.0, bvps=100.0,
                           rf_pct=7.0, beta=1.0,
                           owner_inputs={"nav_ps": 200.0, "nav_asof": "2026-09-30"})
    assert r["status"] == "OK" and r["family"] == "nav"
    assert (r["low"], r["base"], r["high"]) == (170.0, 200.0, 230.0)
    assert "as of 2026-09-30" in r["method_text"]
    assert r["bridge"]["nav_ps"] == 200.0 and r["bridge"]["band_pct"] == 15.0
    assert r["fallback"]["base"] == 175.0             # P/B kept beside, cross-check only
    v = _verdict(r, 150.0)
    assert v["verdict"] == "attractive"
    r10 = sc.justified_range("nav", {}, price=150.0, owner_inputs={"nav_ps": 200.0},
                             typed_value_band_pct=10.0)
    assert (r10["low"], r10["high"]) == (180.0, 220.0)
    assert "date not typed" in r10["method_text"]


def test_typed_band_outside_0_100_is_not_used_and_says_so():
    r = sc.justified_range("nav", {}, price=None, owner_inputs={"nav_ps": 100.0},
                           typed_value_band_pct=150.0)
    assert (r["low"], r["high"]) == (85.0, 115.0)
    assert "outside 0-100" in r["bridge"]["band_source"]


def test_embedded_value_typed_and_untyped():
    r = sc.justified_range("embedded_value", {}, price=600.0,
                           owner_inputs={"ev_ps": 700.0, "vnb_ps": 40.0,
                                         "ev_asof": "2026-03-31"})
    assert r["status"] == "OK"
    assert (r["low"], r["base"], r["high"]) == (595.0, 700.0, 805.0)
    assert r["bridge"]["vnb_ps"] == 40.0 and r["bridge"]["ev_asof"] == "2026-03-31"
    assert any("VNB per share ₹40.00" in n for n in r["notes"])
    blank = sc.justified_range("embedded_value", {}, price=600.0, owner_inputs={})
    assert blank["status"] == "DATA_NEEDED"
    assert blank["reason"].startswith("no target by design — type the embedded value per "
                                      "share (and VNB) in the Den inputs table")


def test_sotp_without_inputs_is_labelled_not_judged():
    r = sc.justified_range("sotp", {}, price=100.0)
    assert r["status"] == "DATA_NEEDED"
    assert r["reason"].startswith("no target by design — sum-of-parts needs")
    assert all(is_dn(r[k]) for k in ("low", "base", "high"))
    assert A.assess(100.0, r)["verdict"] == "insufficient"


# ------------------------------------------------- holding companies (sotp)

BAJAJ_STAKES = [{"subsidiary": "BAJAJ-AUTO", "shares_held": 95_547_251, "stake_pct": 34.19,
                 "source": "NSE shareholding XBRL", "asof": "2026-06-30"},
                {"subsidiary": "BAJAJFINSV.NS", "shares_held": 614_832_190,
                 "stake_pct": 38.41}]
BAJAJ_PRICES = {"BAJAJ-AUTO": 10287.0, "BAJAJFINSV": 1700.0}
# 11.13 cr shares: the report's ₹18,223.07 gross per share
BAJAJ_SHARES = (95_547_251 * 10287.0 + 614_832_190 * 1700.0) / 18223.07


def test_holdco_sotp_reads_listed_stakes_and_typed_discount():
    r = sc.justified_range("sotp", {}, price=11265.0, holdco_stakes=BAJAJ_STAKES,
                           subsidiary_prices=BAJAJ_PRICES, shares_outstanding=BAJAJ_SHARES)
    assert r["status"] == "OK" and r["bridge"]["variant"] == "holding_company"
    assert r["bridge"]["gross_per_share"] == pytest.approx(18223.07, abs=0.01)
    assert r["bridge"]["gross_value_cr"] == pytest.approx(202810.93, abs=0.01)
    # default 50% (ASSUMED) → low / base / high at 60 / 50 / 40 %
    assert (r["low"], r["base"], r["high"]) == pytest.approx((7289.23, 9111.54, 10933.84),
                                                             abs=0.01)
    assert r["bridge"]["discount_pct"] == {"low": 60.0, "base": 50.0, "high": 40.0}
    assert "ASSUMED default 50%" in r["bridge"]["discount_source"]
    assert [s["subsidiary"] for s in r["bridge"]["stakes"]] == ["BAJAJ-AUTO", "BAJAJFINSV"]
    typed = sc.justified_range("sotp", {}, price=11265.0, holdco_stakes=BAJAJ_STAKES,
                               subsidiary_prices=BAJAJ_PRICES,
                               shares_outstanding=BAJAJ_SHARES,
                               owner_inputs={"holding_discount_pct": 40.0})
    assert typed["base"] == pytest.approx(10933.84, abs=0.01)
    assert typed["bridge"]["discount_pct"] == {"low": 50.0, "base": 40.0, "high": 30.0}
    assert typed["bridge"]["discount_source"] == "typed in the Den inputs table"


def test_holdco_discount_is_clamped_and_a_bad_typed_one_is_not_used():
    hi = sc.justified_range("sotp", {}, price=None, holdco_stakes=BAJAJ_STAKES,
                            subsidiary_prices=BAJAJ_PRICES, shares_outstanding=BAJAJ_SHARES,
                            owner_inputs={"holding_discount_pct": 95.0})
    assert hi["low"] == 0.0 and hi["bridge"]["discount_pct"]["low"] == 100.0
    bad = sc.justified_range("sotp", {}, price=None, holdco_stakes=BAJAJ_STAKES,
                             subsidiary_prices=BAJAJ_PRICES, shares_outstanding=BAJAJ_SHARES,
                             owner_inputs={"holding_discount_pct": 140.0})
    assert bad["bridge"]["discount_pct"]["base"] == 50.0
    assert "outside 0-100" in bad["bridge"]["discount_source"]


def test_holdco_missing_price_or_shares_is_data_needed_naming_it():
    r = sc.justified_range("sotp", {}, price=11265.0, holdco_stakes=BAJAJ_STAKES,
                           subsidiary_prices={"BAJAJ-AUTO": 10287.0},
                           shares_outstanding=BAJAJ_SHARES)
    assert r["status"] == "DATA_NEEDED"
    assert "BAJAJFINSV: no price for the listed subsidiary" in r["reason"]
    assert r["bridge"]["gross_per_share"] is None      # never a partial sum
    r2 = sc.justified_range("sotp", {}, price=None, holdco_stakes=BAJAJ_STAKES,
                            subsidiary_prices=BAJAJ_PRICES)
    assert r2["status"] == "DATA_NEEDED" and "own share count" in r2["reason"]


# ---------------------------------------------- conglomerates (RELIANCE)

# report_data VAL-01 B: segment EBIT (NSE FY26 XBRL) and the peer EVs.
REL_SEGMENTS = [{"segment": "O2C", "ebit_cr": 50758.0, "fy_end": "2026-03-31",
                 "source": "NSE XBRL"},
                {"segment": "Oil and Gas", "ebit_cr": 13464.0},
                {"segment": "Retail", "ebit_cr": 20817.0},
                {"segment": "Digital Services", "ebit_cr": 48211.0},
                {"segment": "Others", "ebit_cr": 3448.0}]
REL_EV_CR = {"O2C": 242057.0, "Oil and Gas": 106195.0, "Retail": 1212182.0,
             "Digital Services": 1012957.0, "Others": 16443.0}
REL_MULT = {s["segment"]: {"multiple": REL_EV_CR[s["segment"]] / s["ebit_cr"],
                           "source": "peer median EV/EBIT", "peers": ["IOC", "BPCL"]}
            for s in REL_SEGMENTS}
REL_SHARES = 1353.25e7
REL_KW = dict(shares_outstanding=REL_SHARES, net_debt_per_share=260673e7 / REL_SHARES,
              minorities_per_share=181836e7 / REL_SHARES)


def test_sotp_sums_segment_ebit_times_segment_multiples_less_net_debt_and_minorities():
    r = sc.justified_range("sotp", {}, price=1202.0, segments=REL_SEGMENTS,
                           segment_multiples=REL_MULT, **REL_KW)
    assert r["status"] == "OK" and r["bridge"]["variant"] == "conglomerate"
    assert r["bridge"]["ev_cr"] == pytest.approx(2589834.0, abs=0.5)
    assert (r["low"], r["base"], r["high"]) == pytest.approx((1348.78, 1586.79, 1824.81),
                                                             abs=0.02)
    assert [s["segment"] for s in r["bridge"]["segments"]] == [s["segment"] for s in REL_SEGMENTS]
    assert _verdict(r, 1202.0)["verdict"] == "attractive"


def test_owner_typed_segment_multiple_wins():
    typed = sc.justified_range("sotp", {}, price=1202.0, segments=REL_SEGMENTS,
                               segment_multiples=REL_MULT,
                               owner_inputs={"segment_multiples": {"Retail": 40.0}}, **REL_KW)
    retail = next(s for s in typed["bridge"]["segments"] if s["segment"] == "Retail")
    assert retail["multiple"] == 40.0 and retail["multiple_source"].startswith("typed")
    base = sc.justified_range("sotp", {}, price=1202.0, segments=REL_SEGMENTS,
                              segment_multiples=REL_MULT, **REL_KW)
    drop = 20817.0 * (REL_MULT["Retail"]["multiple"] - 40.0) * 1e7 / REL_SHARES
    assert base["base"] - typed["base"] == pytest.approx(drop, abs=0.02)


def test_conglomerate_without_a_multiple_is_labelled_not_judged():
    mult = {k: v for k, v in REL_MULT.items() if k != "Digital Services"}
    r = sc.justified_range("sotp", {}, price=1202.0, segments=REL_SEGMENTS,
                           segment_multiples=mult, **REL_KW)
    assert r["status"] == "DATA_NEEDED"
    assert ("segment Digital Services: no EV/EBIT multiple — type one in the Den inputs "
            "table") in r["reason"]
    assert A.assess(1202.0, r)["verdict"] == "insufficient"


def test_any_route_with_segment_rows_becomes_the_sum_of_parts():
    r = sc.justified_range("ev_ebitda", {}, price=1202.0, peer_multiple=3.94,
                           per_share_overrides={"downside": 80.0, "base": 90.0,
                                                "upside": 100.0},
                           segments=REL_SEGMENTS, segment_multiples=REL_MULT, **REL_KW)
    assert r["family"] == "sotp" and r["route_family"] == "ev_ebitda"
    assert r["base"] == pytest.approx(1586.79, abs=0.02)
    single = r["single_route"]
    assert single["family"] == "ev_ebitda" and single["status"] == "OK"
    assert any("conglomerate: sum-of-parts replaces" in n for n in r["notes"])


def test_segment_rows_without_numbers_leave_the_route_alone():
    r = sc.justified_range("ev_ebitda", {}, price=100.0, peer_multiple=8.0,
                           per_share_overrides={"downside": 9.0, "base": 10.0, "upside": 11.0},
                           net_debt_per_share=0.0, minorities_per_share=0.0,
                           segments=[{"segment": "O2C", "ebit_cr": None}])
    assert r["family"] == "ev_ebitda" and "single_route" not in r


def test_every_family_has_its_printed_rule():
    assert set(sc.FAMILIES) <= set(sc.FAMILY_RULE_TEXT)
    assert "metals row" in sc.FAMILY_RULE_TEXT["ev_ebitda"]
    assert "4+ readings" in sc.FAMILY_RULE_TEXT["earnings"]
    assert "never sorted" in sc.FAMILY_RULE_TEXT["all"]


# ------------------------------------------------------------- JSON safety

def test_new_outputs_survive_a_json_round_trip(cases):
    outs = [
        sc.justified_range("earnings", cases, price=715.85, peer_multiple=28.5,
                           own_multiple_history=[{"period": "2026-03-31", "pe": 17.34},
                                                 30.79, 26.87, 27.83]),
        sc.justified_range("nav", {}, price=150.0, owner_inputs={"nav_ps": 200.0}),
        sc.justified_range("embedded_value", {}, price=600.0,
                           owner_inputs={"ev_ps": 700.0, "vnb_ps": 40.0}),
        sc.justified_range("sotp", {}, price=11265.0, holdco_stakes=BAJAJ_STAKES,
                           subsidiary_prices=BAJAJ_PRICES, shares_outstanding=BAJAJ_SHARES),
        sc.justified_range("ev_ebitda", {}, price=1202.0, peer_multiple=3.94,
                           per_share_overrides={"downside": 80.0, "base": 90.0,
                                                "upside": 100.0},
                           segments=REL_SEGMENTS, segment_multiples=REL_MULT, **REL_KW),
        sc.justified_range("sotp", {}, price=100.0),
        sc.ASSUMED,
    ]
    for o in outs:
        text = json.dumps(o, allow_nan=False)
        assert json.loads(text) == o
