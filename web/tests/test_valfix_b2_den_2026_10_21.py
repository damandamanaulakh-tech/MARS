"""Den valuation fixes, batch 2 — the web half (owner choice cards
2026-10-21). Money impact NONE — display only; no gate, stop or size reads
any of it.

Pins:
  * item 8  — the Den inputs table (settings doc valuation_inputs): owner
    POST stores ONE row, a bad field refuses the whole row, at most 40
    symbols, remove, viewer 403; the row reaches pipeline.evaluate and a
    saved row recomputes the kept §12 row; a typed NAV gives the range, a
    typed peer list replaces the route median; a NAV route with nothing
    typed prints "no target by design" pointing at the table;
  * item 10 — the macro inputs read trading.macro_feeds: the Upstox 10-year
    yield (dated, STALE past 10 days; none stored → the June file,
    labelled), the RBI repo rate at the base period end AND now (two
    numbers — the old code set both to one), an unverifiable repo start
    counts 0 with its reason, USD/INR now from the RBI feed, FII + DII
    holding then → now per stock;
  * item 7  — the what-if line sits beside the range, the P/E bridge says
    it moves the what-if price, not the range;
  * item 1  — an inverted row carries the red "cases inverted" tag;
  * item 4  — own P/E history by FY with odd years flagged, the target's
    starting profit, verdict warnings;
  * item 9  — §13's "no effect yet" labels and §12's draft banner follow
    what the rows were really fed;
  * item 12 — VAL-01 goes for a group with a typed peer list.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from html import unescape
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from trading.valuation import pipeline as P
from web import auth as auth_mod
from web import valuation_chain_view as cv
from web import valuation_view as vv
from web.main import app

TODAY = date.today()
REPO_KEY = "sheet.pe_per_1pp_repo_pct"


def _owner() -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD}, follow_redirects=False)
    assert r.status_code == 303
    return c


def _viewer(monkeypatch) -> TestClient:
    monkeypatch.setitem(auth_mod.SEEDED_USERS, "viewer@sourceborn.in",
                        {"role": "viewer", "password": "v-test", "name": "Friend"})
    c = TestClient(app)
    assert c.post("/login", data={"email": "viewer@sourceborn.in",
                                  "password": "v-test"}).status_code == 200
    return c


def _flat(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


def _section(html: str, anchor: str, n: int = 200000) -> str:
    i = html.find(anchor)
    assert i >= 0, anchor
    return html[i:i + n]


def _chain_row(html: str, ticker: str) -> str:
    i = html.find("den_chain_rows_marker")
    j = html.find("</table>", i)
    for row in re.findall(r"<tr\b.*?</tr>", html[i:j], re.S):
        cell = re.search(r"<td[^>]*>(.*?)</td>", row, re.S)
        if cell and re.sub(r"<[^>]+>|\s", "", cell.group(1)).startswith(ticker):
            return row
    raise AssertionError(f"no §12 row for {ticker}")


PEER_ROWS = {
    "earnings": [{"ticker": "INFY.NS", "price": 1500.0, "pe": 20.0, "pb": 6.0},
                 {"ticker": "WIPRO.NS", "price": 250.0, "pe": 18.0, "pb": 3.0}],
    "oil_gas": [{"ticker": "IOC.NS", "price": 140.0, "pe": 8.0, "ev_ebitda": 6.0, "ev_ebit": 9.0},
                {"ticker": "BPCL.NS", "price": 300.0, "pe": 7.0, "ev_ebitda": 5.0, "ev_ebit": 8.0}],
}


@pytest.fixture
def stored(monkeypatch):
    """A fake settings store (no Mongo), fixed peers, no feeds."""
    docs: dict = {}
    monkeypatch.setattr(vv.db, "save_settings",
                        lambda d, doc_id="default": docs.setdefault(doc_id, {}).update(d))
    monkeypatch.setattr(vv.db, "load_settings",
                        lambda doc_id="default": dict(docs.get(doc_id, {})))
    vv.LAST["targets"], vv.LAST["assumptions"] = {}, {}
    cv.LAST_ASSUMPTIONS.clear()
    cv.LAST_INPUTS.clear()
    monkeypatch.setattr(cv, "peer_multiples",
                        lambda method, exclude="": {"n": 3, "pe_median": 24.0, "pb_median": 2.0,
                                                    "ev_ebitda_median": 9.0,
                                                    "ev_sales_median": 2.5, "source": "test peers"})
    monkeypatch.setattr(cv, "peer_table_rows", lambda: dict(PEER_ROWS))
    monkeypatch.setattr(cv, "_macro_feeds", lambda: None)
    # reviewer defect 10 (2026-10-21): §12 also computes every HELD name; a
    # stale fixture position in the persisted /tmp book (HOLD1.NS) made the
    # evaluate-call pins read ['TCS.NS', 'HOLD1.NS'] — the held book is empty here
    monkeypatch.setattr(cv, "_held_names", lambda p: [])
    yield docs
    vv.LAST["targets"], vv.LAST["assumptions"] = {}, {}
    cv.LAST_ASSUMPTIONS.clear()
    cv.LAST_INPUTS.clear()


def _fake_feeds(*, rf_days: int = 2, repo_start=(6.00, "2026-03-31", "RBI MPC press release"),
                inst=(35.0, 37.5, "2026-03-31", "2026-06-30")):
    d = lambda n: (TODAY - timedelta(days=n)).isoformat()   # noqa: E731
    latest = {"gsec10y_yield": (7.05, d(rf_days), "Upstox 6.33% GS 2035"),
              "rbi_repo": (5.50, d(1), "RBI home page"),
              "rbi_usdinr": (88.10, d(1), "RBI reference rate")}
    return SimpleNamespace(
        latest=lambda series: latest.get(series),
        at=lambda series, when: repo_start if series == "rbi_repo" else None,
        inst_holding=lambda sym, pe: inst)


# ------------------------------------------------------------ item 8

def test_an_owner_row_is_stored_and_section_12_reads_the_typed_nav(stored):
    c = _owner()
    r = c.post("/den/valuation/inputs",
               data={"ticker": "dlf", "nav_ps": "1000", "nav_asof": "2026-09-30",
                     "note": "Q2 NAV"}, follow_redirects=False)
    assert r.status_code == 303 and "vi=saved" in r.headers["location"]
    row = stored["valuation_inputs"]["inputs"]["DLF"]
    assert row["nav_ps"] == 1000.0 and row["nav_asof"] == "2026-09-30" and row["note"] == "Q2 NAV"
    html = c.get("/den?chain=DLF").text
    t = _flat(_chain_row(html, "DLF"))
    assert "₹850 / ₹1,000 / ₹1,150" in t                 # typed NAV × (1 ∓ 15%)
    tbl = _section(html, "den_inputs_marker", 20000)
    assert "den_inputs_rows_marker" in tbl and 'data-den-inputs-row="DLF"' in tbl
    assert "Q2 NAV" in _flat(tbl)


@pytest.mark.parametrize("field,value", [
    ("nav_ps", "abc"), ("nav_ps", "-5"), ("nav_ps", "inf"), ("ev_ps", "0"),
    ("holding_discount_pct", "120"), ("nav_asof", "30-09-2026"),
    ("segment_multiples", "O2C=x"), ("segment_multiples", "O2C 7.5"),
    ("peer_list", "IOC bad$sym"),
])
def test_a_bad_field_refuses_the_whole_row(stored, field, value):
    data = {"ticker": "DLF", "nav_ps": "900", field: value}
    r = _owner().post("/den/valuation/inputs", data=data, follow_redirects=False)
    assert r.status_code == 303 and "vi=invalid" in r.headers["location"], (field, value)
    assert "valuation_inputs" not in stored                  # nothing stored
    assert not cv.LAST_INPUTS


def test_an_empty_row_is_refused_and_the_table_caps_its_symbols(stored, monkeypatch):
    c = _owner()
    r = c.post("/den/valuation/inputs", data={"ticker": "DLF", "note": "only a note"},
               follow_redirects=False)
    assert "vi=invalid" in r.headers["location"] and "nothing+typed" in r.headers["location"] \
        or "nothing%20typed" in r.headers["location"]
    monkeypatch.setattr(cv, "MAX_INPUT_SYMBOLS", 2)
    for t in ("DLF", "GODREJPROP"):
        assert "vi=saved" in c.post("/den/valuation/inputs", data={"ticker": t, "nav_ps": "500"},
                                    follow_redirects=False).headers["location"]
    r = c.post("/den/valuation/inputs", data={"ticker": "OBEROIRLTY", "nav_ps": "500"},
               follow_redirects=False)
    assert "vi=invalid" in r.headers["location"] and "at+most+2" in r.headers["location"] \
        or "at%20most%202" in r.headers["location"]
    # replacing an existing symbol is still allowed at the cap
    assert "vi=saved" in c.post("/den/valuation/inputs", data={"ticker": "DLF", "nav_ps": "600"},
                                follow_redirects=False).headers["location"]
    assert stored["valuation_inputs"]["inputs"]["DLF"]["nav_ps"] == 600.0


def test_parse_reads_segments_peers_and_discount(stored):
    sym, row, errors = cv.parse_inputs_form({
        "ticker": "reliance.ns", "segment_multiples": "O2C=7.5; Retail = 30\nDigital=12",
        "peer_list": "ioc, bpcl  RELIANCE", "holding_discount_pct": "0", "ev_asof": ""})
    assert errors == [] and sym == "RELIANCE"
    assert row["segment_multiples"] == {"O2C": 7.5, "Retail": 30.0, "Digital": 12.0}
    assert row["peer_list"] == ["IOC", "BPCL"]                # the stock itself is dropped
    assert row["holding_discount_pct"] == 0.0


def test_remove_drops_one_row_and_a_viewer_gets_403_everywhere(stored, monkeypatch):
    c = _owner()
    c.post("/den/valuation/inputs", data={"ticker": "DLF", "nav_ps": "1000"})
    c.post("/den/valuation/inputs", data={"ticker": "SBILIFE", "ev_ps": "600", "vnb_ps": "40"})
    r = c.post("/den/valuation/inputs/remove", data={"ticker": "DLF"}, follow_redirects=False)
    assert r.status_code == 303 and "vi=removed" in r.headers["location"]
    assert set(stored["valuation_inputs"]["inputs"]) == {"SBILIFE"}
    v = _viewer(monkeypatch)
    assert v.get("/den").status_code == 403
    for path, data in (("/den/valuation/inputs", {"ticker": "DLF", "nav_ps": "1"}),
                       ("/den/valuation/inputs/remove", {"ticker": "SBILIFE"})):
        assert v.post(path, data=data, follow_redirects=False).status_code == 403
    assert set(stored["valuation_inputs"]["inputs"]) == {"SBILIFE"}


def test_the_row_reaches_evaluate_and_a_save_recomputes_the_kept_row(stored, monkeypatch):
    seen: list = []
    real = P.evaluate

    def spy(ticker, **kw):
        seen.append((ticker, kw.get("owner_inputs"), kw.get("peer_rows"), kw.get("price_of")))
        return real(ticker, **kw)

    monkeypatch.setattr(P, "evaluate", spy)
    c = _owner()
    c.get("/den?chain=TCS")
    c.get("/den?chain=TCS")
    assert [s[0] for s in seen] == ["TCS.NS"]                # the second open kept the row
    assert seen[0][1] is None                                # empty table → nothing passed
    assert callable(seen[0][2]) and seen[0][3] is cv.subsidiary_price
    c.post("/den/valuation/inputs", data={"ticker": "TCS", "peer_list": "INFY WIPRO"})
    html = c.get("/den?chain=TCS").text
    assert [s[0] for s in seen] == ["TCS.NS", "TCS.NS"]      # the save recomputed it
    assert seen[1][1]["TCS"]["peer_list"] == ["INFY", "WIPRO"]
    # VAL-01 general fix: the typed list replaces the route median
    t = _flat(_chain_row(html, "TCS"))
    assert "median pe 19.00 of YOUR peer list (INFY, WIPRO)" in t


def test_a_nav_route_with_nothing_typed_points_at_the_inputs_table(stored):
    html = _owner().get("/den?chain=DLF").text
    row = _chain_row(html, "DLF")
    assert "den_chain_by_design_marker" in row
    t = _flat(row)
    assert "NO TARGET BY DESIGN" in t and "Den inputs table" in t
    assert 'href="#chain-inputs"' in row
    assert "den_chain_no_range_marker" not in row


# ------------------------------------------------------------ item 10

def test_macro_inputs_read_the_stored_feeds_with_their_dates(stored, monkeypatch):
    monkeypatch.setattr(cv, "_macro_feeds", lambda: _fake_feeds())
    m = cv._macro_inputs({"period_end": "2026-03-31"}, None, "TCS.NS")
    assert m["rf_pct"] == 7.05 and m["rf_feed"] is True and m["rf_stale"] is False
    assert m["rf_date"] == (TODAY - timedelta(days=2)).isoformat()
    assert "Upstox" in m["rf_source"]
    # the bug fixed: the start and the end are two readings, not one number
    assert (m["repo_start_pct"], m["repo_end_pct"]) == (6.00, 5.50)
    assert m["repo_start_date"] == "2026-03-31"
    assert m["usdinr_now"] == 88.10 and m["usdinr_now_source"].startswith("RBI")
    assert (m["inst_holding_start"], m["inst_holding_end"]) == (35.0, 37.5)
    assert (m["inst_holding_start_date"], m["inst_holding_end_date"]) == ("2026-03-31", "2026-06-30")


def test_the_fed_rows_move_the_bridge_and_leave_the_missing_list(stored, monkeypatch):
    from web import state
    monkeypatch.setattr(cv, "_macro_feeds", lambda: _fake_feeds())
    rec = cv.row_for("TCS.NS", state.cfg(), None)
    cells = rec["detail"]["cases"]["base"]["cells"]
    assert cells["E74"] == pytest.approx(-0.5)                # repo 6.00 → 5.50
    assert cells["E82"] == pytest.approx(2.5)                 # FII + DII 35.0 → 37.5
    miss = " ".join(rec["scenarios"]["macro_data_needed"])
    assert "repo" not in miss.lower() and "institutional" not in miss.lower()
    assert rec["assumptions"]["live_inputs"]["repo"] is True
    assert rec["assumptions"]["live_inputs"]["inst_holding"] is True
    html = _owner().get("/den?chain=TCS").text
    row = _chain_row(html, "TCS")
    assert "den_chain_macro_marker" in row
    t = _flat(row)
    assert "10y 7.05%" in t and "Upstox G-sec" in t
    assert "repo 6.00% → 5.50%" in t and "FII+DII 35.0% → 37.5%" in t
    assert "(RBI reference rate)" in t and "USD-INR 88.10" in t


def test_an_old_yield_is_stale_and_no_feed_falls_back_to_the_june_file(stored, monkeypatch):
    monkeypatch.setattr(cv, "_macro_feeds", lambda: _fake_feeds(rf_days=30))
    m = cv._macro_inputs({"period_end": "2026-03-31"}, None, "TCS.NS")
    assert m["rf_stale"] is True and m["rf_age_days"] == 30
    cv.clear_feed_cache()
    monkeypatch.setattr(cv, "_macro_feeds", lambda: None)
    m = cv._macro_inputs({"period_end": "2026-03-31"}, None, "TCS.NS")
    assert m["rf_source"].startswith("June file (stale)")
    assert m["rf_date"] == "2026-06-08" and m["rf_stale"] is True and m["rf_feed"] is False
    assert m["repo_start_pct"] is None and m["inst_holding_start"] is None
    html = _owner().get("/den?chain=TCS").text
    row = _chain_row(html, "TCS")
    assert "data-den-chain-rf-stale" in row and "June file" in _flat(row)


def test_an_unverified_repo_start_counts_zero_with_its_reason(stored, monkeypatch):
    from web import state
    monkeypatch.setattr(cv, "_macro_feeds", lambda: _fake_feeds(repo_start=None))
    m = cv._macro_inputs({"period_end": "2026-03-31"}, None, "TCS.NS")
    assert m["repo_start_pct"] is None and m["repo_end_pct"] == 5.50
    assert "no RBI-verified repo rate on file for 2026-03-31" in m["repo_start_why"]
    rec = cv.row_for("TCS.NS", state.cfg(), None)
    assert rec["detail"]["cases"]["base"]["cells"]["E74"] == pytest.approx(0.0)
    miss = " ".join(rec["scenarios"]["macro_data_needed"])
    assert "RBI repo rate at the base period end — DATA_NEEDED" in miss
    assert "no RBI-verified repo rate on file" in miss
    assert rec["assumptions"]["live_inputs"]["repo"] is False


# ------------------------------------------------------- items 7, 1, 4

def test_the_whatif_sits_beside_the_range_and_the_bridge_says_so(stored):
    html = _owner().get("/den?chain=TCS").text
    row = _chain_row(html, "TCS")
    assert "den_chain_whatif_marker" in row
    t = _flat(row)
    assert "what-if (your sheet): ₹" in t
    assert "moves with your P/E bridge, not the range" in t
    assert "moves the what-if price, not the range" in t
    rec = cv.row_for("TCS.NS", __import__("web.state", fromlist=["cfg"]).cfg(), None)
    assert rec["range"]["whatif"]["base"] != rec["range"]["base"]


def test_own_history_target_basis_and_warnings_print(stored, monkeypatch):
    html = _owner().get("/den?chain=TCS").text
    row = _chain_row(html, "TCS")
    assert "den_chain_own_history_marker" in row and "own P/E by FY:" in _flat(row)
    assert 'data-den-chain-target-basis="normalised"' in row
    assert "target starts from normalised PAT" in _flat(row)
    real = cv.row_for

    def flagged(t, *a, **k):
        rec = real(t, *a, **k)
        h = rec["range"]["own_history"]
        h[1] = {**h[1], "flag": "odd year: EPS 9.99 is above 1.5× both neighbours — shown, not dropped"}
        rec["verdict"]["warnings"] = ["earnings quality 40/100 is below 50 (ASSUMED)"]
        rec["earnings"]["target_starts_from"] = "reported (normalised DATA_NEEDED)"
        return rec

    cv.clear_row_cache()
    monkeypatch.setattr(cv, "row_for", flagged)
    row = _chain_row(_owner().get("/den?chain=TCS").text, "TCS")
    t = _flat(row)
    assert "data-den-chain-odd-year" in row and "odd year: EPS 9.99" in t and "⚑" in t
    assert "den_chain_warning_marker" in row
    assert "⚠ warning — earnings quality 40/100 is below 50" in t
    assert "target starts from reported (normalised DATA_NEEDED)" in t


def test_an_inverted_range_prints_the_red_tag_in_case_order(stored, monkeypatch):
    real = cv.row_for

    def inverted(t, *a, **k):
        rec = real(t, *a, **k)
        rec["range"].update({"low": 5000.0, "base": 4000.0, "high": 6000.0, "inverted": True,
                             "inverted_note": "CASES INVERTED — downside ₹5,000 above base ₹4,000"})
        return rec

    monkeypatch.setattr(cv, "row_for", inverted)
    row = _chain_row(_owner().get("/den?chain=TCS").text, "TCS")
    t = _flat(row)
    assert "den_chain_inverted_marker" in row and "cases inverted" in t
    assert "₹5,000 / ₹4,000 / ₹6,000" in t                   # kept as computed, never sorted


# ------------------------------------------------------------ item 9

def test_no_effect_labels_and_the_draft_banner_follow_what_was_fed(stored, monkeypatch):
    c = _owner()
    c.post("/den/valuation/assumptions", data={REPO_KEY: "-9"})
    # nothing feeds the repo row: the number changes nothing
    html = c.get("/den?chain=TCS").text
    f13 = _section(html, 'id="formulas"')
    assert f'data-den-formulas-no-effect="{REPO_KEY}"' in f13
    assert "den_chain_draft_marker" not in html
    assert 'data-den-formulas-live-key="repo" data-on="0"' in f13
    # the RBI feed answers: the same draft now changes TCS's row
    cv.clear_row_cache()
    monkeypatch.setattr(cv, "_macro_feeds", lambda: _fake_feeds())
    html = c.get("/den?chain=TCS").text
    f13 = _section(html, 'id="formulas"')
    assert f'data-den-formulas-no-effect="{REPO_KEY}"' not in f13
    assert 'data-den-formulas-live-key="repo" data-on="1"' in f13
    assert "den_chain_draft_marker" in html
    t12 = _flat(_section(html, 'id="chain"', 20000))
    assert f"DRAFT ASSUMPTIONS IN USE — 1 number(s) differ from the defaults ({REPO_KEY})" in t12
    assert ">draft<" in _chain_row(html, "TCS")


def test_page_live_is_the_union_of_the_rows_and_falls_back_to_the_feeds(stored, monkeypatch):
    rows = [{"row_kind": "full", "assumptions": {"live_inputs": {"repo": False, "market": True}}},
            {"row_kind": "full", "assumptions": {"live_inputs": {"repo": True, "market": False}}},
            {"row_kind": "not_computed", "assumptions": {"live_inputs": {"cpi": True}}}]
    live = cv.page_live(rows)
    assert live["repo"] is True and live["market"] is True and live.get("cpi") is False
    monkeypatch.setattr(cv, "_macro_feeds", lambda: _fake_feeds())
    fb = cv.page_live([])
    assert fb["repo"] is True and fb["inst_holding"] is True and fb["cpi"] is False


# ------------------------------------------------------------ item 12

def test_a_group_with_a_typed_peer_list_loses_its_val01_line(stored):
    c = _owner()
    row = _chain_row(c.get("/den?chain=RELIANCE").text, "RELIANCE")
    rec_flags = cv.row_for("RELIANCE.NS", __import__("web.state", fromlist=["cfg"]).cfg(),
                           None).get("range_flags") or {}
    if rec_flags.get("sum_of_parts") or rec_flags.get("family") == "sotp":
        assert 'data-den-chain-fault="VAL-01"' not in row      # segment rows on file
    else:
        assert 'data-den-chain-fault="VAL-01"' in row
        assert "inputs table" in _flat(row)
    c.post("/den/valuation/inputs", data={"ticker": "RELIANCE", "peer_list": "IOC BPCL"})
    row = _chain_row(c.get("/den?chain=RELIANCE").text, "RELIANCE")
    assert 'data-den-chain-fault="VAL-01"' not in row
    assert "den_chain_fault_marker" not in row
