"""Stage 00/09 — the valuation basis by sector, chosen FIRST
(trading/valuation/route.py). Owner order 2026-10-14 "lets hit the formulas
work completion"; money impact NONE (display only).

Pins: the owner's 21 sector rows are all there, verbatim and coded; real
names route to the right row on the app's own sector map; a coarse label is
UNDECLARED, never forced; the Nifty-500 fallback and the unmapped case; the
PSU overlay is ASSUMED and says so; the module is pure.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from trading.valuation import route as R

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def sector_map():
    from trading.config import Config
    return dict(Config().sector_map)


# --------------------------------------------------------------------------
# The 21 rows
# --------------------------------------------------------------------------
def test_sector_methods_has_exactly_the_owners_21_rows():
    rows = R.SECTOR_METHODS
    assert len(rows) == 21
    assert [r["row"] for r in rows] == list(range(1, 22))
    for r in rows:
        for k in ("sector", "primary_basis", "per_share_metric", "key_drivers",
                  "bad_time_behaviour", "policy_links", "data_have", "data_missing",
                  "method", "family"):
            assert isinstance(r[k], str) and r[k].strip(), (r["row"], k)
        assert r["family"] in R.FAMILIES, r
    methods = [r["method"] for r in rows]
    assert len(methods) == len(set(methods)), "method codes must be unique"


def test_rows_are_verbatim_from_the_task_book():
    by = {r["row"]: r for r in R.SECTOR_METHODS}
    assert by[1]["sector"] == "IT services / ER&D"
    assert by[2]["primary_basis"].startswith("P/B × ROE (justified P/B = (ROE−g)/(COE−g))")
    assert by[8]["primary_basis"] == "EV/EBITDA at MID-CYCLE margin; P/B at trough"
    assert by[9]["data_have"] == "Statements; Brent NOT stored"
    assert by[13]["per_share_metric"] == "NAV per share"
    assert by[20]["per_share_metric"] == "per route" and by[20].get("overlay_only")
    assert by[21]["primary_basis"] == "Sum-of-parts with holding discount (40-60%)"
    fam = {r["method"]: r["family"] for r in R.SECTOR_METHODS}
    assert fam["bank_pb_roe"] == "book" and fam["realty_nav"] == "nav"
    assert fam["insurance_ev"] == "embedded_value" and fam["holding_sotp"] == "sotp"
    assert fam["earnings"] == "earnings" and fam["metals_midcycle"] == "ev_ebitda"


# --------------------------------------------------------------------------
# Real names on the app's own sector map
# --------------------------------------------------------------------------
def test_hdfcbank_routes_to_banks_book(sector_map):
    r = R.route_for("HDFCBANK.NS", sector_map, "HDFC Bank Limited")
    assert (r["label"], r["label_source"]) == ("Financial", "sector_map")
    assert r["status"] == "declared"
    assert r["method"] == "bank_pb_roe" and r["family"] == "book"
    assert r["per_share_metric"] == "Book value per share, EPS"
    assert "BANK" in r["rule"] and "ASSUMED" in r["rule"]
    assert r["overlays"] == []                     # a private bank is no PSU


def test_dlf_routes_to_real_estate_nav(sector_map):
    r = R.route_for("DLF", sector_map, "DLF Limited")
    assert r["label"] == "Construction"
    assert r["method"] == "realty_nav" and r["family"] == "nav"
    assert r["sector"] == "Real estate"


def test_sail_routes_to_metals_with_the_psu_overlay(sector_map):
    r = R.route_for("SAIL", sector_map, "Steel Authority of India Limited")
    assert r["method"] == "metals_midcycle" and r["family"] == "ev_ebitda"
    assert len(r["overlays"]) == 1
    psu = r["overlays"][0]
    assert psu["method"] == "psu_overlay"
    assert "ASSUMED" in psu["source"] and "not parsed" in psu["source"]
    assert "dividend yield floor" in psu["basis"]


def test_tcs_routes_to_it_services_earnings(sector_map):
    r = R.route_for("TCS", sector_map, "Tata Consultancy Services Limited")
    assert r["method"] == "earnings" and r["family"] == "earnings"
    assert r["per_share_metric"] == "Diluted EPS"
    assert r["overlays"] == []


def test_bajaj_finance_is_an_nbfc_and_icici_lombard_an_insurer(sector_map):
    b = R.route_for("BAJFINANCE", sector_map, "Bajaj Finance Limited")
    assert b["method"] == "nbfc_pb" and b["family"] == "book"
    i = R.route_for("ICICIGI", sector_map,
                    "ICICI Lombard General Insurance Company Limited")
    assert i["method"] == "insurance_ev" and i["family"] == "embedded_value"


def test_an_amc_with_life_in_its_name_is_still_an_amc(sector_map):
    r = R.route_for("NAM-INDIA", sector_map, "Nippon Life India Asset Management Ltd.")
    assert r["method"] == "amc_pe"


def test_pharma_laboratories_are_not_routed_to_hospitals(sector_map):
    assert R.route_for("DRREDDY", sector_map,
                       "Dr. Reddy's Laboratories Ltd.")["method"] == "pharma_ev_ebitda"
    assert R.route_for("APOLLOHOSP", sector_map,
                       "Apollo Hospitals Enterprise Ltd.")["method"] == "hospital_ev_ebitda"


def test_holding_companies_route_to_sum_of_parts_but_an_nbfc_stays(sector_map):
    h = R.route_for("BAJAJHLDNG", sector_map, "Bajaj Holdings & Investment Ltd.")
    assert h["method"] == "holding_sotp" and h["family"] == "sotp"
    c = R.route_for("CHOLAFIN", sector_map,
                    "Cholamandalam Investment and Finance Company Ltd.")
    assert c["method"] == "nbfc_pb"


def test_a_media_name_is_undeclared_never_forced(sector_map):
    r = R.route_for("ZEEL", sector_map, "Zee Entertainment Enterprises Limited")
    assert r["label"] == "Media"
    assert r["status"] == "UNDECLARED"
    assert r["method"] is None and r["family"] is None and r["basis"] is None
    assert "UNDECLARED" in r["rule"]
    if "nearest" in r:                              # a suggestion, never a route
        assert "suggestion only" in r["nearest"]["note"]


def test_nifty500_file_is_the_fallback_label(sector_map):
    look = R.load_nifty500_industry()
    assert look["360ONE"]["industry"] == "Financial Services"
    assert "360ONE.NS" not in sector_map
    assert R.label_for("360ONE", sector_map) == ("Financial Services", "nifty500_industry")
    r = R.route_for("360ONE", sector_map)
    assert r["label_source"] == "nifty500_industry"
    assert r["company"] == "360 ONE WAM Ltd."
    assert r["status"] == "declared" and r["family"] == "book"


def test_a_name_in_neither_source_is_unmapped_and_undeclared():
    assert R.label_for("NOSUCHCO", {}, industry_lookup={}) == ("", "unmapped")
    r = R.route_for("NOSUCHCO", {}, industry_lookup={})
    assert r["label_source"] == "unmapped" and r["status"] == "UNDECLARED"
    assert r["method"] is None and "review" in r["rule"]


def test_unknown_label_falls_through_to_the_nifty_file():
    look = {"FOO": {"industry": "Information Technology", "company": "Foo Ltd."}}
    assert R.label_for("FOO", {"FOO.NS": "Unknown"}, look) == \
        ("Information Technology", "nifty500_industry")
    assert R.route_for("FOO", {"FOO.NS": "Unknown"}, industry_lookup=look)["method"] == "earnings"


def test_private_names_do_not_wear_the_psu_overlay(sector_map):
    for t, name in (("HINDUNILVR", "Hindustan Unilever Limited"),
                    ("DALBHARAT", "Dalmia Bharat Ltd."),
                    ("CONCORDBIO", "Concord Biotech Ltd."),
                    ("CUB", "City Union Bank Ltd.")):
        assert R.route_for(t, sector_map, name)["overlays"] == [], t
    for t, name in (("BANKBARODA", "Bank of Baroda"), ("CONCOR",
                    "Container Corporation of India Ltd."), ("HAL",
                    "Hindustan Aeronautics Ltd.")):
        assert R.route_for(t, sector_map, name)["overlays"], t


def test_every_real_name_routes_without_raising(sector_map):
    look = R.load_nifty500_industry()
    syms = set(look) | {k.replace(".NS", "") for k in sector_map}
    seen = set()
    for s in syms:
        r = R.route_for(s, sector_map, industry_lookup=look)
        assert r["status"] in ("declared", "UNDECLARED")
        if r["status"] == "declared":
            assert r["family"] in R.FAMILIES
            seen.add(r["method"])
    assert len(seen) >= 18          # nearly every row is reachable from real labels


def test_route_and_assumed_round_trip_as_json(sector_map):
    for t in ("HDFCBANK", "SAIL", "ZEEL", "NOSUCHCO"):
        r = R.route_for(t, sector_map)
        assert json.loads(json.dumps(r)) == r
    a = json.loads(json.dumps(R.ASSUMED))
    assert a == R.ASSUMED
    assert a["label_rules"] and a["psu_keywords"] and "ASSUMED" in a["psu_source"]
    assert json.loads(json.dumps(R.SECTOR_METHODS)) == R.SECTOR_METHODS


# --------------------------------------------------------------------------
# Purity
# --------------------------------------------------------------------------
def _operative_source(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(getattr(body[0], "value", None), ast.Constant) and \
                    isinstance(body[0].value.value, str):
                spans.append((body[0].lineno, body[0].end_lineno or body[0].lineno))
    keep = []
    for i, line in enumerate(text.splitlines(), start=1):
        if any(a <= i <= b for a, b in spans) or line.strip().startswith("#"):
            continue
        keep.append(line.split("  #")[0])
    return "\n".join(keep)


def test_route_module_is_pure():
    src = _operative_source(ROOT / "trading" / "valuation" / "route.py")
    for pat in (r"\bimport\s+(httpx|requests|urllib|yfinance)\b",
                r"\bfrom\s+(httpx|requests|urllib|yfinance)\b",
                r"\b(from|import)\s+web\b",
                r"\bfrom\s+trading(\.|\s+import\s+)(engine|risk|orders|danger)\b",
                r"\bimport\s+trading\.(engine|risk|orders|danger)\b"):
        assert not re.search(pat, src), pat
