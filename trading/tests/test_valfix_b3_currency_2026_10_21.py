"""VALFIX batch 3 — the currency leg THROUGH EPS for every sector (owner
order 2026-10-21: "through EPS, USD + EUR" … "it will be used on all sector
shares"). Display only.

Pins trading/valuation/currency.py:
  * the study's TCS window A (selection 2026-05-15 → 2026-10-16) on the
    committed FRED fixtures: USD/INR −1.05%, EUR/INR −4.42%, pt 0.924,
    EPS × 0.9891 (−0.47 USD, −0.62 EUR) — the old code divided by 100 twice
    and read × 0.99989;
  * the share tiers in the owner's order: typed → filed split → BRSR
    (USD, EUR by the sector default ratio, "exports from India only") →
    sector default → DATA_NEEDED counted 0; never a built-in share;
  * filed-split mapping rules (other → USD, GBP reported not used, a table
    mixing business lines gives nothing, KPITTECH reads its deck);
  * an importer (negative share) moves EPS the other way; a missing margin
    or move counts its part 0 and says so;
  * the sector table refuses the whole form on one bad value.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from trading import macro_feeds as mf
from trading.valuation import currency as CUR

FIX = Path(__file__).resolve().parent / "fixtures"


def _fred(series: str, fid: str) -> pd.Series:
    rows = mf.parse_fred_csv((FIX / f"fred_{fid}_2026.txt").read_text(), series)
    return pd.Series([r["value"] for r in rows],
                     index=pd.to_datetime([r["date"] for r in rows]))


@pytest.fixture(scope="module")
def moves():
    return CUR.fx_moves(_fred("fred_usdinr", "DEXINUS"), _fred("fred_usdeur", "DEXUSEU"),
                        "2026-05-15")


def test_the_rupee_moves_since_selection_match_the_study(moves):
    u, e = moves["usd"], moves["eur"]
    assert (u["start"], u["start_date"], u["end"], u["end_date"]) == \
        (95.97, "2026-05-15", 94.96, "2026-10-16")
    assert u["pct"] == pytest.approx(-1.05, abs=0.005)
    # EUR/INR = DEXINUS × DEXUSEU on the dates both carry a value
    assert e["start"] == pytest.approx(95.97 * 1.1627, abs=1e-6)
    assert e["end"] == pytest.approx(94.96 * 1.1231, abs=1e-6)
    assert e["pct"] == pytest.approx(-4.42, abs=0.005)


def test_tcs_window_a_reads_the_studys_0_9891(moves):
    fx = CUR.fx_eps(136.0, 27.07, 0.25, 48.3, 15.2, moves["usd"]["pct"], moves["eur"]["pct"])
    assert fx["status"] == "OK" and fx["counted"] is True
    assert fx["pt"] == pytest.approx(0.924, abs=0.001)          # 100 × 0.25 ÷ 27.07
    assert fx["usd_part_pct"] == pytest.approx(-0.47, abs=0.005)
    assert fx["eur_part_pct"] == pytest.approx(-0.62, abs=0.005)
    assert fx["factor"] == pytest.approx(0.9891, abs=0.0001)    # the old code: 0.99989
    assert fx["eps_adjusted"] == pytest.approx(136.0 * fx["factor"])


def test_an_importer_moves_eps_the_other_way_and_a_full_exporter_by_pt():
    # a 1% weaker rupee, 100% exporter: EPS + pt %; a 30% importer: − 0.3 × pt %
    up = CUR.fx_eps(10.0, 20.0, 0.25, 100.0, 0.0, 1.0, 0.0)
    assert up["pt"] == pytest.approx(1.25) and up["factor"] == pytest.approx(1.0125)
    imp = CUR.fx_eps(10.0, 20.0, 0.25, -30.0, 0.0, 1.0, 0.0)
    assert imp["factor"] == pytest.approx(1 - 0.3 * 0.0125)


def test_a_missing_input_counts_its_part_zero_and_says_so():
    none = CUR.fx_eps(10.0, 20.0, 0.25, None, None, 1.0, 1.0)
    assert none["status"] == "DATA_NEEDED" and none["factor"] == 1.0 and not none["counted"]
    nomargin = CUR.fx_eps(10.0, None, 0.25, 50.0, 0.0, 1.0, 0.0)
    assert nomargin["status"] == "DATA_NEEDED" and "margin" in nomargin["why"][0]
    noeur = CUR.fx_eps(10.0, 20.0, 0.25, 40.0, 20.0, 1.0, None)
    assert noeur["status"] == "OK" and noeur["eur_part_pct"] == 0.0
    assert noeur["factor"] == pytest.approx(1 + 0.4 * 1.25 / 100)
    assert "EUR/INR move DATA_NEEDED" in noeur["why"][0]
    # a share of exactly 0 is a real 0 — counted, factor 1
    zero = CUR.fx_eps(10.0, 20.0, 0.25, 0.0, 0.0, 5.0, 5.0)
    assert zero["status"] == "OK" and zero["counted"] and zero["factor"] == 1.0


# ------------------------------------------------------------------ tiers

TABLE = {"IT services / ER&D": {"usd_share_pct": 60.0, "eur_share_pct": 20.0,
                                "pass_through_pp": 0.3},
         "Oil & gas / OMC": {"usd_share_pct": -40.0}}


def test_a_typed_share_wins_every_other_tier():
    r = CUR.resolve_shares("WIPRO", {"usd_share_pct": 10.0}, "IT services / ER&D", TABLE)
    assert r["tier"] == "typed" and (r["usd_pct"], r["eur_pct"]) == (10.0, 0.0)
    assert "EUR not typed — counted 0" in r["detail"]


def test_the_filed_split_comes_second_other_counts_usd_gbp_is_not_used():
    r = CUR.resolve_shares("WIPRO", None, "IT services / ER&D", TABLE)
    assert r["tier"] == "filed"
    assert r["usd_pct"] == pytest.approx(62.38 + 11.11, abs=0.01)   # other → USD
    assert r["eur_pct"] == pytest.approx(26.51, abs=0.01)
    assert "NSE results XBRL" in r["source"]
    k = CUR.resolve_shares("KPITTECH", None, "IT services / ER&D", TABLE)
    assert k["tier"] == "filed" and "investor deck" in k["source"]
    assert k["eur_pct"] == 48.0 and k["usd_pct"] == pytest.approx(27.4 + 24.6)
    # a GBP line is reported, never used
    gbp = CUR._split_from_lines([("UK", "GBP", 18.0), ("US", "USD", 50.0)])
    assert gbp["usd_pct"] == 50.0 and gbp["eur_pct"] == 0.0 and gbp["gbp_pct"] == 18.0


def test_a_table_mixing_business_lines_and_regions_gives_no_share():
    # BHARTIARTL / TATASTEEL file MIXED_BUSINESS_GEO tables
    assert CUR.filed_split("BHARTIARTL") is None
    assert CUR.filed_split("TATASTEEL") is None


def test_brsr_is_third_counted_usd_with_the_eur_part_by_the_sector_ratio():
    r = CUR.resolve_shares("TCS", None, "IT services / ER&D", TABLE)
    assert r["tier"] == "brsr" and r["caveat"] == "exports from India only"
    # batch-3 review B6: the two parts add back to the filed 94.1 EXACTLY at
    # 2 decimals (the defect rounded each part on its own: 70.57 + 23.52 =
    # 94.09, which the old abs=0.01 tolerance let through)
    eur = round(94.1 * 20 / 80, 2)                      # 23.52 (23.524999… in floats)
    assert (eur, round(94.1 - eur, 2)) == (23.52, 70.58)
    assert r["eur_pct"] == eur and r["usd_pct"] == round(94.1 - eur, 2)
    assert round(r["usd_pct"] + r["eur_pct"], 2) == 94.1
    plain = CUR.resolve_shares("TCS", None, "IT services / ER&D", {})
    assert plain["tier"] == "brsr" and plain["usd_pct"] == pytest.approx(94.1)
    assert plain["eur_pct"] == 0.0 and "all counted as USD" in plain["detail"]


def test_brsr_ahead_of_an_importer_default_says_the_default_is_not_used():
    r = CUR.resolve_shares("RELIANCE", None, "Oil & gas / OMC", TABLE)
    assert r["tier"] == "brsr"
    assert "importer's" in r["detail"] and "not used" in r["detail"]


def test_the_sector_default_is_fourth_and_nothing_is_never_a_built_in_share(tmp_path):
    empty = tmp_path / "empty.csv"
    empty.write_text("symbol,status,export_pct_of_turnover\n")
    orig = CUR.BRSR_CSV
    try:
        CUR.BRSR_CSV = empty
        CUR._CACHE.clear()
        r = CUR.resolve_shares("TCS", None, "IT services / ER&D", TABLE)
        assert r["tier"] == "sector" and (r["usd_pct"], r["eur_pct"]) == (60.0, 20.0)
        none = CUR.resolve_shares("TCS", None, "Banks (private/PSU)", TABLE)
        assert none["tier"] == "none" and none["usd_pct"] is None and none["eur_pct"] is None
    finally:
        CUR.BRSR_CSV = orig
        CUR._CACHE.clear()


def test_pass_through_is_per_sector_and_starts_at_the_sheets_b152():
    assert CUR.pass_through("IT services / ER&D", TABLE) == (0.3, "your sector table (IT services / ER&D)")
    pp, src = CUR.pass_through("Banks (private/PSU)", TABLE)
    assert pp == 0.25 and "B152" in src
    assert CUR.ASSUMED["pass_through_pp"]["value"] == 0.25


def test_the_sector_table_refuses_the_whole_form_on_one_bad_value():
    ok, err = CUR.parse_sector_table({"A": {"usd_share_pct": "60", "eur_share_pct": "-10",
                                            "pass_through_pp": ""}})
    assert err == [] and ok == {"A": {"usd_share_pct": 60.0, "eur_share_pct": -10.0}}
    for bad in ({"usd_share_pct": "abc"}, {"usd_share_pct": "nan"},
                {"usd_share_pct": "101"}, {"pass_through_pp": "-1"},
                {"usd_share_pct": "70", "eur_share_pct": "40"}):
        _t, errs = CUR.parse_sector_table({"A": bad})
        assert errs, bad


def test_every_constant_is_sourced():
    for k, v in CUR.ASSUMED.items():
        assert v.get("source"), k
    for k, v in CUR.MEASURED.items():
        assert v.get("source_url") and v.get("note"), k


def test_the_fx_move_ends_at_the_rows_own_now():
    u, e = _fred("fred_usdinr", "DEXINUS"), _fred("fred_usdeur", "DEXUSEU")
    got = CUR.fx_moves(u, e, "2026-05-15", end="2026-07-17")
    assert got["usd"]["end_date"] <= "2026-07-17" and got["eur"]["end_date"] <= "2026-07-17"
    assert got["usd"]["end"] == float(u[u.index <= "2026-07-17"].iloc[-1])
    early = CUR.fx_moves(u, e, "2026-05-15", end="2025-01-01")
    assert early["usd"]["status"] == "DATA_NEEDED"


def test_a_zero_share_needs_no_margin_and_is_counted():
    # HDFCBANK files BRSR exports 0%: no EBITDA margin (a bank) must not
    # turn a real 0 into DATA_NEEDED
    r = CUR.resolve_shares("HDFCBANK", None, "Banks (private/PSU)", {})
    assert r["tier"] == "brsr" and (r["usd_pct"], r["eur_pct"]) == (0.0, 0.0)
    fx = CUR.fx_eps(50.0, None, 0.25, r["usd_pct"], r["eur_pct"], 1.0, 1.0)
    assert fx["status"] == "OK" and fx["counted"] and fx["factor"] == 1.0
    assert "no currency effect" in fx["note"]


# ------------------------------------------------- batch-3 review fixes (B1, B2, B7)

def test_a_selection_on_or_after_the_newest_fred_reading_is_data_needed_never_0pct():
    # B1: FRED is weekly and 3-10 days late — a recent selection date finds
    # the same newest reading at both ends. That is NO reading, not a 0% move.
    u, e = _fred("fred_usdinr", "DEXINUS"), _fred("fred_usdeur", "DEXUSEU")
    newest = u.index[-1].date().isoformat()
    assert newest == "2026-10-16"
    for sel in (newest, "2026-10-20"):
        mv = CUR.fx_moves(u, e, sel)
        for k in ("usd", "eur"):
            assert mv[k]["status"] == "DATA_NEEDED" and mv[k]["pct"] is None, (sel, k)
        assert mv["usd"]["why"] == ("no USD/INR (FRED DEXINUS) reading after the selection "
                                    "date (newest 2026-10-16)")
        assert "reading after the selection date" in mv["eur"]["why"]
    # the row's own "now" sitting on the selection-date reading: the same
    weekly = pd.Series([95.0, 96.0], index=pd.to_datetime(["2026-10-09", "2026-10-16"]))
    mid = CUR.fx_moves(weekly, None, "2026-10-12", end="2026-10-14")["usd"]
    assert mid["status"] == "DATA_NEEDED" and "(newest 2026-10-09)" in mid["why"]
    # the currency block is then not counted: factor 1 = "not applied", said
    mv = CUR.fx_moves(u, e, newest)
    fx = CUR.fx_eps(136.0, 27.07, 0.25, 48.3, 15.2, mv["usd"]["pct"], mv["eur"]["pct"],
                    usd_why=mv["usd"]["why"], eur_why=mv["eur"]["why"])
    assert fx["status"] == "DATA_NEEDED" and fx["counted"] is False and fx["applied"] is False
    assert fx["factor"] == 1.0
    assert any("reading after the selection date" in w and "USD part is counted 0" in w
               for w in fx["why"])
    assert any("not applied" in w for w in fx["why"])
    # a selection date before the newest reading still reproduces the study
    a = CUR.fx_moves(u, e, "2026-05-15")
    assert a["usd"]["pct"] == pytest.approx(-1.05, abs=0.005)
    ok = CUR.fx_eps(136.0, 27.07, 0.25, 48.3, 15.2, a["usd"]["pct"], a["eur"]["pct"])
    assert ok["counted"] and ok["factor"] == pytest.approx(0.9891, abs=0.0001)


def test_one_missing_part_beside_a_real_zero_move_is_still_counted():
    # B1: "not counted" only when EVERY part with a share lacks its move —
    # a USD move of exactly 0% is a real reading (the old code read the
    # total 0 + one missing part as DATA_NEEDED)
    fx = CUR.fx_eps(10.0, 20.0, 0.25, 40.0, 20.0, 0.0, None)
    assert fx["status"] == "OK" and fx["counted"] is True and fx["factor"] == 1.0
    assert fx["why"] == ["EUR/INR move DATA_NEEDED — the EUR part is counted 0"]


def test_a_blank_sector_share_reads_zero_in_the_brsr_tier_as_in_the_sector_tier(tmp_path):
    # B2: one owner entry, one reading — EUR 30 / USD blank means USD 0 in
    # the BRSR ratio exactly as it does in the sector tier
    eur_only = {"IT services / ER&D": {"eur_share_pct": 30.0}}
    r = CUR.resolve_shares("TCS", None, "IT services / ER&D", eur_only)
    assert r["tier"] == "brsr"
    assert r["eur_pct"] == 94.1 and r["usd_pct"] == 0.0
    assert "USD left blank, read as 0" in r["detail"]
    usd_only = {"IT services / ER&D": {"usd_share_pct": 50.0}}
    u = CUR.resolve_shares("TCS", None, "IT services / ER&D", usd_only)
    assert (u["usd_pct"], u["eur_pct"]) == (94.1, 0.0)
    assert "EUR left blank, read as 0" in u["detail"]
    zero = CUR.resolve_shares("TCS", None, "IT services / ER&D",
                              {"IT services / ER&D": {"usd_share_pct": 0.0, "eur_share_pct": 0.0}})
    assert zero["usd_pct"] == 94.1 and "no sector default ratio — all counted as USD" in zero["detail"]
    # the sector tier reads the same entry the same way
    empty = tmp_path / "empty.csv"
    empty.write_text("symbol,status,export_pct_of_turnover\n")
    orig = CUR.BRSR_CSV
    try:
        CUR.BRSR_CSV = empty
        CUR._CACHE.clear()
        s = CUR.resolve_shares("TCS", None, "IT services / ER&D", eur_only)
        assert s["tier"] == "sector" and (s["usd_pct"], s["eur_pct"]) == (0.0, 30.0)
    finally:
        CUR.BRSR_CSV = orig
        CUR._CACHE.clear()


def test_the_deck_based_split_names_the_deck_and_says_why_the_xbrl_was_not_used():
    # B7: KPITTECH reads its investor deck — the row must not also call it
    # the NSE results XBRL, and the reason the XBRL was set aside is printed
    k = CUR.resolve_shares("KPITTECH", None, "IT services / ER&D", TABLE)
    assert k["tier"] == "filed"
    assert k["source"].startswith("the company's investor deck, FY to 2026-03-31 (")
    assert "XBRL)" not in k["source"] and "NSE results XBRL" not in k["source"]
    assert CUR.MEASURED["KPITTECH"]["note"] in k["detail"]          # printed on the row
    assert k["note"] == CUR.MEASURED["KPITTECH"]["note"]
    assert k.get("source_kind") == "investor_deck"
    w = CUR.resolve_shares("WIPRO", None, "IT services / ER&D", TABLE)
    assert w["source"].startswith("the company's filed geographic split (NSE results XBRL), FY to")
    assert "investor deck" not in w["source"]
    assert w.get("source_kind") == "xbrl"
