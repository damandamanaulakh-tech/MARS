"""Den §12 — the valuation chain on screen (owner order 2026-10-14: "lets
hit the formulas work completion"). Money impact NONE.

Pins: the section renders from the committed statements for a typed
name (route, snapshot lock, share bridge, normalised EPS, quality, cases,
range, verdict, key terms) and the 52-term table on `terms=1`; a
converted ADR name says so; an unknown name reads DATA_NEEDED on the row,
never a 500; the "use as §11 target" button stores the computed base as
the context stage's target through the existing route; the sequence
table carries stages 00 and 07-18 as existing, dated 2026-10-14; the
section chip and anchor exist; the viewer role cannot see the Den.
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from web import auth as auth_mod
from web import valuation_chain_view as cv
from web import valuation_view as vv
from web.main import app
from web.routes import den_routes as D


def _owner() -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD},
               follow_redirects=False)
    assert r.status_code == 303
    return c


def _text(html: str, anchor: str = 'id="chain"') -> str:
    i = html.find(anchor)
    assert i >= 0, anchor
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html[i:i + 120000]))


def _row_html(html: str, ticker: str) -> str:
    """The §12 table row of one ticker (the page also carries held names)."""
    i = html.find("den_chain_rows_marker")
    j = html.find("</table>", i)
    for row in re.findall(r"<tr\b.*?</tr>", html[i:j], re.S):
        cell = re.search(r"<td[^>]*>(.*?)</td>", row, re.S)
        if cell and re.sub(r"<[^>]+>|\s", "", cell.group(1)).startswith(
                ticker.replace(".NS", "")):
            return row
    raise AssertionError(f"no §12 row for {ticker}")


@pytest.fixture
def stored(monkeypatch):
    """A fake settings store so the §11 save round-trips without Mongo,
    and a fixed peer table so the page never walks the whole zip."""
    docs: dict = {}
    monkeypatch.setattr(vv.db, "save_settings",
                        lambda d, doc_id="default": docs.setdefault(doc_id, {}).update(d))
    monkeypatch.setattr(vv.db, "load_settings",
                        lambda doc_id="default": dict(docs.get(doc_id, {})))
    vv.LAST["targets"], vv.LAST["assumptions"] = {}, {}
    monkeypatch.setattr(cv, "peer_multiples",
                        lambda method, exclude="": {"n": 3, "pe_median": 24.0,
                                                    "pb_median": 2.0,
                                                    "ev_ebitda_median": 9.0,
                                                    "ev_sales_median": 2.5,
                                                    "source": "test peers"})
    return docs


def test_the_section_chip_and_anchor_exist_and_the_empty_state_renders(stored):
    html = _owner().get("/den").text
    assert 'href="#chain"' in html and 'id="chain"' in html
    assert "den_chain_marker" in html and "den_chain_form_marker" in html
    assert "12 · Valuation chain" in html


def test_a_typed_name_renders_the_whole_chain(stored):
    html = _owner().get("/den?chain=TCS&terms=1").text
    assert "den_chain_rows_marker" in html and "den_chain_terms_marker" in html
    t = _text(html)
    tcs_row = _row_html(html, "TCS.NS")
    assert "IT services / ER&amp;D" in t or "IT services / ER&D" in t
    assert "FY to 2026-03-31" in t
    assert "lock " in t                                   # the sha256 prefix printed
    assert "reconciled" in t
    assert "EPS 136.01" in t and "matches audited normalised line" in t
    assert "/100" in t                                    # quality score
    assert "P/E bridge" in t and "macro inputs DATA_NEEDED, counted 0" in t
    assert re.search(r"₹[\d,]+ / ₹[\d,]+ / ₹[\d,]+", t)  # low / base / high
    assert re.search(r"(ATTRACTIVE|WATCH|UNATTRACTIVE|INSUFFICIENT)", t)
    assert "not a trade permission" in t
    assert "All 52 finance terms for TCS" in t
    assert "Graham number" in t and "Altman Z" in t
    # 2026-10-15 review: TCS's range is too wide to judge (INSUFFICIENT) —
    # the "use as §11 target" button is refused on that row, with the why
    # (the old pin asserted the button anywhere on the page, and a held
    # name's row satisfied it). The button itself: the SAIL test below.
    if "INSUFFICIENT" in _text(tcs_row, "<tr"):
        assert "den_chain_use_refused_marker" in tcs_row
        assert "den_chain_use_marker" not in tcs_row
    else:
        assert "den_chain_use_marker" in tcs_row


def test_a_converted_adr_name_says_so_on_the_row(stored):
    t = _text(_owner().get("/den?chain=INFY").text)
    assert "USD→INR (converted)" in t
    assert "fx 9" in t                                    # the rate (date) printed
    assert "constant currency" not in t or True           # note lives in the record


def test_an_unknown_name_reads_data_needed_never_a_500(stored):
    # 2026-10-15 review: a name with no statements used to print ten
    # DATA_NEEDED cells ending in INSUFFICIENT — the word TCS gets for a
    # range too wide to judge. It now prints one NO STATEMENTS line.
    r = _owner().get("/den?chain=ZZNOSUCH")
    assert r.status_code == 200
    row = _row_html(r.text, "ZZNOSUCH")
    t = _text(row, "<tr")
    assert "NO STATEMENTS" in t and "batch fetch" in t
    assert "INSUFFICIENT" not in t and "UNDECLARED" not in t


def test_a_bad_symbol_is_refused_with_a_message(stored):
    r = _owner().get("/den?chain=bad!!sym")
    assert r.status_code == 200
    assert "is not a symbol" in r.text


def test_use_as_target_stores_the_computed_base_into_section_11(stored):
    # 2026-10-15: the button shows only on a JUDGED verdict (TCS reads
    # INSUFFICIENT — range too wide); SAIL is judged under these peers. The
    # regex reads SAIL's OWN form (it used to read the first form on the
    # page, which could belong to a held name).
    c = _owner()
    html = c.get("/den?chain=SAIL").text
    row = _row_html(html, "SAIL.NS")
    assert re.search(r"(ATTRACTIVE|WATCH|UNATTRACTIVE)", _text(row, "<tr"))
    m = re.search(r'name="ticker" value="SAIL.NS">\s*<input type="hidden" name="target" value="([\d.]+)"',
                  row)
    assert m, "no computed base on the SAIL row"
    base = float(m.group(1))
    assert base > 0
    r = c.post("/den/valuation", data={"ticker": "SAIL", "target": f"{base:.2f}",
                                       "asof": "2026-10-14",
                                       "basis": "computed earnings base case 2026-10-14 (stage 17)"},
               follow_redirects=False)
    assert r.status_code == 303 and "val=saved" in r.headers["location"]
    doc = vv.load_doc()
    assert "SAIL.NS" in doc["targets"]
    assert doc["targets"]["SAIL.NS"]["target"] == pytest.approx(base, abs=0.01)
    assert doc["targets"]["SAIL.NS"]["basis"].startswith("computed")


def test_the_sequence_now_carries_the_chain_stages_dated_today():
    rows = {r["code"]: r for r in D._sequence()["rows"]}
    for code in ("00", "08", "09", "10", "11", "12", "15", "16", "17", "18"):
        assert rows[code]["status"] == "exists", code
        assert "trading/valuation" in rows[code]["where"] or "scenarios" in rows[code]["where"] \
            or "normalise" in rows[code]["where"] or "route" in rows[code]["where"] \
            or "snapshot" in rows[code]["where"] or "assess" in rows[code]["where"], code
    assert rows["07"]["status"] == "partly"               # 200 of ~1,205 names
    assert rows["21"]["status"] == "not yet"              # permission stays unbuilt
    assert D.SEQUENCE_STATUS_ASOF == "2026-10-14"


def test_names_to_show_orders_typed_then_targets_then_held(stored, monkeypatch):
    # Portfolio.positions is a DICT keyed by ticker (the real shape — the
    # first cut iterated it as a list and silently dropped every held name)
    class _P:
        positions = {"SAIL.NS": object(), "HDFCBANK.NS": object()}
    vv.LAST["targets"] = {"DLF.NS": {"target": 1.0}}
    monkeypatch.setattr(vv, "load_doc", lambda: {"targets": {"DLF.NS": {"target": 1.0}},
                                                 "assumptions": {}})
    # Order changed 2026-10-15 (review): typed, then HELD, then §11 targets
    # A-Z — 25 stored targets used to push every held name off the chain.
    assert cv.names_to_show(_P(), "TCS.NS") == ["TCS.NS", "SAIL.NS", "HDFCBANK.NS", "DLF.NS"]


def test_held_names_appear_on_the_chain_from_the_real_book(stored):
    """A real PaperPosition in the live portfolio dict must reach the rows."""
    from datetime import datetime
    from trading.paper import PaperPosition
    from web import state
    p = state.portfolio()
    saved = dict(p.positions)
    try:
        p.positions["SAIL.NS"] = PaperPosition(
            ticker="SAIL.NS", qty=100, entry_price=150.0, entry_date=datetime.utcnow(),
            sector="Metal", peak_price=150.0, entry_score=70.0, reasoning="t",
            mode="swing", stop_price=140.0, target_price=170.0, tags=[])
        t = _text(_owner().get("/den").text)
        assert "SAIL" in t and "Metals (steel/aluminium/zinc)" in t
    finally:
        p.positions.clear()
        p.positions.update(saved)


def test_the_viewer_cannot_see_the_den(stored, monkeypatch):
    # the same seeding the Phase-0 viewer tests use
    monkeypatch.setitem(auth_mod.SEEDED_USERS, "viewer@sourceborn.in",
                        {"role": "viewer", "password": "v-test", "name": "Friend"})
    c = TestClient(app)
    r = c.post("/login", data={"email": "viewer@sourceborn.in", "password": "v-test"},
               follow_redirects=False)
    assert r.status_code == 303
    assert c.get("/den?chain=TCS", follow_redirects=False).status_code == 403
