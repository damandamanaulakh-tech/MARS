"""Valuation batch 2 — the reviewer's defects, the web half (fixer,
2026-10-21). Money impact NONE — display only.

Pins (the reviewer's numbering):
  1. the §12 row of a conglomerate prints its segments AS FILED with the
     multiple each was priced on, a typed key lands by case / bracket /
     group, a stray key is named on the row, the inputs help lists the real
     names on file, and (lead's (b)) the row says the multiples are the
     owner's to type;
  5. a save of the inputs table or of a walk branch is REFUSED when the
     stored copy could not be read — nothing written, the stored rows kept;
  7. the repo rate is stored daily but journaled only when it changes;
  8. the 10-year row names the bond actually used, and (lead's (a)) RBI's
     own benchmark prints beside it as display only — on §12 and the walk.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from html import unescape
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from trading import macro_feeds as mf
from web import auth as auth_mod
from web import db
from web import den_walk as W
from web import scheduler, state
from web import valuation_chain_view as cv
from web import valuation_view as vv
from web.main import app

PEER_ROWS = {
    "earnings": [{"ticker": "INFY.NS", "price": 1500.0, "pe": 20.0, "pb": 6.0},
                 {"ticker": "WIPRO.NS", "price": 250.0, "pe": 18.0, "pb": 3.0}],
    "oil_gas": [{"ticker": "IOC.NS", "price": 140.0, "pe": 8.0, "ev_ebitda": 6.0, "ev_ebit": 9.0},
                {"ticker": "BPCL.NS", "price": 300.0, "pe": 7.0, "ev_ebitda": 5.0, "ev_ebit": 8.0}],
}


def _owner() -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD}, follow_redirects=False)
    assert r.status_code == 303
    return c


def _flat(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


def _chain_row(html: str, ticker: str) -> str:
    i = html.find("den_chain_rows_marker")
    j = html.find("</table>", i)
    for row in re.findall(r"<tr\b.*?</tr>", html[i:j], re.S):
        cell = re.search(r"<td[^>]*>(.*?)</td>", row, re.S)
        if cell and re.sub(r"<[^>]+>|\s", "", cell.group(1)).startswith(ticker):
            return row
    raise AssertionError(f"no §12 row for {ticker}")


def _node(html: str, nid: str) -> str:
    i = html.find(f'data-walk-node="{nid}"')
    assert i >= 0, nid
    j = min(x for x in (html.find("<ul>", i), html.find("</li>", i)) if x > 0)
    return html[i:j]


@pytest.fixture
def stored(monkeypatch):
    """A fake settings store (no Mongo), fixed peers, no feeds, no held book."""
    docs: dict = {}
    monkeypatch.setattr(vv.db, "save_settings",
                        lambda d, doc_id="default": docs.setdefault(doc_id, {}).update(d))
    monkeypatch.setattr(vv.db, "load_settings",
                        lambda doc_id="default": dict(docs.get(doc_id, {})))
    vv.LAST["targets"], vv.LAST["assumptions"] = {}, {}
    cv.LAST_ASSUMPTIONS.clear()
    cv.LAST_INPUTS.clear()
    W.LAST_BRANCHES.clear()
    cv.clear_feed_cache()
    monkeypatch.setattr(cv, "peer_multiples",
                        lambda method, exclude="": {"n": 3, "pe_median": 24.0, "pb_median": 2.0,
                                                    "ev_ebitda_median": 9.0,
                                                    "ev_sales_median": 2.5, "source": "test peers"})
    monkeypatch.setattr(cv, "peer_table_rows", lambda: dict(PEER_ROWS))
    monkeypatch.setattr(cv, "_macro_feeds", lambda: None)
    monkeypatch.setattr(cv, "_held_names", lambda p: [])       # reviewer defect 10
    yield docs
    vv.LAST["targets"], vv.LAST["assumptions"] = {}, {}
    cv.LAST_ASSUMPTIONS.clear()
    cv.LAST_INPUTS.clear()
    W.LAST_BRANCHES.clear()
    cv.clear_feed_cache()


# ============================================================ defect 1

def test_reliance_segments_print_as_filed_with_typed_and_stray_keys_named(stored):
    c = _owner()
    html = c.get("/den?chain=RELIANCE").text
    # the inputs help lists the real names on file
    assert 'data-den-inputs-segments="RELIANCE"' in html
    page = _flat(html)
    assert "Oil to Chemicals (O2C) · Oil and Gas · Retail · Digital Services · Others" in page
    assert "its bracketed short name, or its group" in page
    row = _chain_row(html, "RELIANCE")
    assert "den_chain_segments_marker" in row and "den_chain_segments_note_marker" in row
    t = _flat(row)
    assert "segments: Oil to Chemicals (O2C) 8.50× EV/EBIT" in t          # IOC 9 / BPCL 8
    assert "segment multiples are yours to type" in t                      # lead's (b)
    assert "den_chain_segments_unmatched_marker" not in row
    c.post("/den/valuation/inputs",
           data={"ticker": "RELIANCE", "segment_multiples": "O2C=20; retail=40; Nope=5"})
    assert stored["valuation_inputs"]["inputs"]["RELIANCE"]["segment_multiples"] == {
        "O2C": 20.0, "retail": 40.0, "Nope": 5.0}
    row = _chain_row(c.get("/den?chain=RELIANCE").text, "RELIANCE")
    t = _flat(row)
    assert "Oil to Chemicals (O2C) 20.00× EV/EBIT (typed)" in t
    assert "Retail 40.00× EV/EBIT (typed)" in t
    assert "den_chain_segments_unmatched_marker" in row
    assert "NOT applied — no segment named Nope" in t and "Digital Services" in t


# ============================================================ defect 5

def test_a_failed_inputs_read_refuses_the_save_and_wipes_nothing(stored, monkeypatch):
    c = _owner()
    c.post("/den/valuation/inputs", data={"ticker": "DLF", "nav_ps": "1000"})
    assert set(stored["valuation_inputs"]["inputs"]) == {"DLF"}
    cv.LAST_INPUTS.clear()                                 # a fresh process: no in-process copy

    def boom(doc_id="default"):
        raise RuntimeError("atlas down")
    saves: list = []
    monkeypatch.setattr(vv.db, "load_settings", boom)
    monkeypatch.setattr(vv.db, "save_settings", lambda d, doc_id="default": saves.append(doc_id))
    ok, err = cv.save_input_row("SBILIFE", {"ev_ps": 600.0})
    assert ok is False and err == cv.READ_FAILED and saves == []
    assert cv.remove_input_row("DLF") is False and saves == []
    r = c.post("/den/valuation/inputs", data={"ticker": "SBILIFE", "ev_ps": "600"},
               follow_redirects=False)
    assert r.status_code == 303 and "vi=invalid" in r.headers["location"]
    assert "could not be read" in unquote(r.headers["location"])
    assert saves == [] and set(stored["valuation_inputs"]["inputs"]) == {"DLF"}
    # display still works (the in-process copy is only a fallback for READING)
    assert cv.load_inputs() == {}


def test_a_failed_branches_read_refuses_save_and_remove(stored, monkeypatch):
    c = _owner()
    r = c.post("/den/sequence/branch", data={"parent": "s15", "name": "My leg", "formula": "x"},
               follow_redirects=False)
    assert "sb=saved" in r.headers["location"]
    kept = stored["sequence_branches"]["branches"]
    assert len(kept) == 1
    nid = kept[0]["id"]
    W.LAST_BRANCHES.clear()                                # a fresh process

    def boom(doc_id="default"):
        raise RuntimeError("atlas down")
    saves: list = []
    monkeypatch.setattr(vv.db, "load_settings", boom)
    monkeypatch.setattr(vv.db, "save_settings", lambda d, doc_id="default": saves.append(doc_id))
    ok, err, got = W.save_branch({"parent": "s15", "name": "Another"})
    assert (ok, err, got) == (False, W.READ_FAILED, "")
    assert W.remove_branch(nid) == -1 and saves == []
    r = c.post("/den/sequence/branch/remove", data={"id": nid}, follow_redirects=False)
    assert "sb=invalid" in r.headers["location"]
    assert "could not be read" in unquote(r.headers["location"])
    r = c.post("/den/sequence/branch", data={"parent": "s15", "name": "Another"},
               follow_redirects=False)
    assert "sb=invalid" in r.headers["location"]
    assert saves == [] and stored["sequence_branches"]["branches"] == kept


# ============================================================ defect 7

TODAY = date(2026, 10, 21)


def _macro_env(monkeypatch, stored_rows: list, rbi_rows: list) -> dict:
    state.cfg()
    box = {"stored": list(stored_rows), "saved": []}

    def save(rows):
        box["saved"].extend(rows)
        box["stored"].extend(rows)
        return len(rows)
    monkeypatch.setattr(db, "is_connected", lambda: True)
    monkeypatch.setattr(db, "load_macro_rows", lambda series=None: list(box["stored"]))
    monkeypatch.setattr(db, "save_macro_rows", save)
    monkeypatch.setattr(mf, "fetch_gsec10y", lambda client, last_iso="", **kw: [])
    monkeypatch.setattr(mf, "fetch_rbi_home", lambda timeout=30.0: list(rbi_rows))
    monkeypatch.setattr(mf, "_today_ist", lambda: TODAY)
    monkeypatch.setattr("trading.data.history_broker", lambda: object())
    return box


def _macro_lines(n0: int) -> list:
    return [e for e in state.portfolio().decision_journal[n0:]
            if str(e.get("action", "")).startswith("MACRO_FEEDS")]


def test_an_unchanged_repo_is_stored_daily_but_not_journaled(monkeypatch):
    held = [{"series": "rbi_repo", "date": "2026-10-20", "value": 5.5, "source": "RBI"},
            {"series": "rbi_usdinr", "date": "2026-10-20", "value": 94.7, "source": "RBI"},
            {"series": "gsec10y_yield", "date": "2026-10-20", "value": 6.93, "source": "Upstox",
             "bond": "7.54% GS 2036"}]
    same = [{"series": "rbi_repo", "date": "2026-10-21", "value": 5.5, "source": "RBI"},
            {"series": "rbi_usdinr", "date": "2026-10-20", "value": 94.7, "source": "RBI"}]
    box = _macro_env(monkeypatch, held, same)
    n0 = len(state.portfolio().decision_journal)
    scheduler._macro_feeds_refresh()
    assert [(r["series"], r["date"]) for r in box["saved"]] == [("rbi_repo", "2026-10-21")]
    assert _macro_lines(n0) == []                            # stored, but not news
    # the rate moves → journaled; the RBI benchmark row is named as display only
    moved = [{"series": "rbi_repo", "date": "2026-10-21", "value": 5.25, "source": "RBI"},
             {"series": "rbi_usdinr", "date": "2026-10-20", "value": 94.7, "source": "RBI"},
             {"series": "rbi_gsec10y", "date": "2026-10-16", "value": 7.3725,
              "bond": "6.94% GS 2036", "source": "RBI home page"}]
    box = _macro_env(monkeypatch, box["stored"], moved)
    n0 = len(state.portfolio().decision_journal)
    scheduler._macro_feeds_refresh()
    assert {(r["series"], r["date"]) for r in box["saved"]} == {
        ("rbi_repo", "2026-10-21"), ("rbi_gsec10y", "2026-10-16")}
    lines = _macro_lines(n0)
    assert [e["action"] for e in lines] == ["MACRO_FEEDS"]
    why = lines[0]["why"]
    assert "RBI repo 5.25 (2026-10-21)" in why
    assert "RBI 10-year benchmark 7.37% (6.94% GS 2036, 2026-10-16; display only" in why


# ============================================================ defect 8

def _feeds(with_benchmark: bool = True, with_rows: bool = True) -> SimpleNamespace:
    d = lambda n: (date.today() - timedelta(days=n)).isoformat()      # noqa: E731
    latest = {"gsec10y_yield": (6.9276, d(2), "Upstox NSE candles · 7.54% GS 2036 "
                                               f"(IN0020220029) close 104.23 on {d(2)}"),
              "rbi_repo": (5.5, d(1), "RBI home page"),
              "rbi_usdinr": (94.77, d(1), "RBI reference rate")}
    rows = {"gsec10y_yield": {"series": "gsec10y_yield", "bond": "7.54% GS 2036",
                              "value": 6.9276, "date": d(2)}}
    if with_benchmark:
        latest["rbi_gsec10y"] = (7.3725, "2026-10-16", "RBI home page, Government Securities "
                                                        "Market: 6.94% GS 2036 : 7.3725%")
        rows["rbi_gsec10y"] = {"series": "rbi_gsec10y", "bond": "6.94% GS 2036",
                               "value": 7.3725, "date": "2026-10-16"}
    ns = SimpleNamespace(latest=lambda s: latest.get(s), at=lambda s, w: None,
                         inst_holding=lambda sym, pe: None)
    if with_rows:
        ns.latest_row = lambda s: rows.get(s)
    return ns


def test_the_10y_row_names_the_bond_and_shows_rbis_benchmark_beside_it(stored, monkeypatch):
    monkeypatch.setattr(cv, "_macro_feeds", lambda: _feeds())
    m = cv._macro_inputs({"period_end": "2026-03-31"}, None, "TCS.NS")
    assert m["rf_bond"] == "7.54% GS 2036" and m["rf_pct"] == 6.9276 and m["rf_feed"] is True
    assert (m["rbi_benchmark_pct"], m["rbi_benchmark_date"], m["rbi_benchmark_bond"]) == \
        (7.3725, "2026-10-16", "6.94% GS 2036")
    c = _owner()
    row = _chain_row(c.get("/den?chain=TCS").text, "TCS")
    t = _flat(row)
    assert "data-den-chain-rf-bond" in row
    # (_flat pads the bond's own <span> with spaces, so the pieces are matched)
    assert "10y 6.93%" in t and "7.54% GS 2036" in t and "Upstox G-sec close" in t
    assert re.search(r"10y 6\.93% \( ?7\.54% GS 2036 ?, Upstox G-sec close", t)
    assert "data-den-chain-rbi-benchmark" in row
    assert "RBI benchmark 7.37% (2026-10-16, 6.94% GS 2036) — display only" in t
    # the walk's rf node carries the same two readings
    node = _flat(_node(c.get("/den?walk=TCS").text, "s13.rf"))
    assert "7.54% GS 2036" in node and "RBI benchmark 7.37% (2026-10-16, 6.94% GS 2036)" in node
    assert "display only, not used" in node


def test_bond_name_falls_back_to_the_source_text_and_no_benchmark_prints_nothing(stored,
                                                                                  monkeypatch):
    monkeypatch.setattr(cv, "_macro_feeds", lambda: _feeds(with_benchmark=False, with_rows=False))
    m = cv._macro_inputs({"period_end": "2026-03-31"}, None, "TCS.NS")
    assert m["rf_bond"] == "7.54% GS 2036"                # read off the source text
    assert m["rbi_benchmark_pct"] is None and m["rbi_benchmark_bond"] == ""
    row = _chain_row(_owner().get("/den?chain=TCS").text, "TCS")
    t = _flat(row)
    assert re.search(r"10y 6\.93% \( ?7\.54% GS 2036 ?, Upstox G-sec close", t)
    assert "data-den-chain-rbi-benchmark" not in row and "RBI benchmark" not in t
