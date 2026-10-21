"""Den §12 clarity fixes (reviewers on the live Den, 2026-10-15). Money
impact NONE — display only; no gate, stop or size reads §12.

Pins (each verified to FAIL on the 2026-10-15 main before the fix):
  1. a dead database (Atlas unreachable while db._CONNECTED stays True,
     XR-071): the first store call that waits and answers nothing is the
     LAST store call of that render; the rest reads the repo files and the
     page names the database as the cause; a name the batched price read
     did not find goes straight to the repo zip (never a second store read);
     the budget banner never says "spent on the 0 row(s) above";
  2. a busy box prints the remedy for its KIND (scan / memory / another
     open) and the memory kind never says "once it finishes";
  3. held names come before §11 targets and a cut by the 25-row cap is
     printed;
  4. a crashed row prints one FAILED line and is counted;
  5. a name with no statements (or summary figures only) prints one NO
     STATEMENTS line, not ten DATA_NEEDED cells ending in INSUFFICIENT;
  6. the "use as §11 target" button shows only on a judged verdict;
  7. the peer-only band text is rounded to 2 decimals;
  8. Verdict / Justified range / Cases sit right after Stock · price, and
     the all-terms panel is outside the sideways-scrolling table box.
"""
from __future__ import annotations

import re
import time

import pytest
from fastapi.testclient import TestClient

import web.db as db
from trading import data_refresh as dr
from trading import register_v4 as rv
from trading.valuation import pipeline as P
from trading.valuation import scenarios as SC
from trading.valuation import snapshot as S
from web import auth as auth_mod
from web import state
from web import valuation_chain_view as cv
from web import valuation_view as vv
from web.main import app
from web.templating import templates

FIXED_PEERS = {"n": 3, "pe_median": 24.0, "pb_median": 2.0, "ev_ebitda_median": 9.0,
               "ev_sales_median": 2.5, "source": "test peers"}


def _render(chain: dict, is_owner: bool = True) -> str:
    return templates.env.get_template("_den_valuation_chain.html").render(
        chain=chain, is_owner=is_owner)


def _flat(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _row_html(html: str, ticker: str) -> str:
    i = html.find("den_chain_rows_marker")
    j = html.find("</table>", i)
    for row in re.findall(r"<tr\b.*?</tr>", html[i:j], re.S):
        cell = re.search(r"<td[^>]*>(.*?)</td>", row, re.S)
        if cell and re.sub(r"<[^>]+>|\s", "", cell.group(1)).startswith(
                ticker.replace(".NS", "")):
            return row
    raise AssertionError(f"no §12 row for {ticker}")


class _Book:
    def __init__(self, held=()):
        self.positions = {t: object() for t in held}


@pytest.fixture
def no_targets(monkeypatch):
    monkeypatch.setattr(vv, "load_doc", lambda: {"targets": {}, "assumptions": {}})
    monkeypatch.setattr(cv, "load_assumptions", lambda: {})


def _fake_rows(monkeypatch, names):
    calls = []

    def fake_row(t, cfg, p, **k):
        calls.append(t)
        return P.failed_record(t, "fake computed row")

    shown = getattr(cv, "ShownNames", list)              # (the pre-fix module lacks it)
    monkeypatch.setattr(cv, "names_to_show", lambda p, typed="": shown(names))
    monkeypatch.setattr(cv, "row_for", fake_row)
    monkeypatch.setattr(cv, "load_assumptions", lambda: {})
    return calls


# ------------------------------------------------- 1. a dead database

@pytest.fixture
def dead_store(monkeypatch):
    """Every store call waits a little and answers nothing — the shape
    web/db.py gives a dead Atlas (pymongo's 3 s timeout, swallowed). The
    gate's thresholds are scaled down with it so the test stays fast."""
    state.cfg(), state.portfolio()              # booted BEFORE the store goes dead
    calls: list = []

    def slow(name, answer):
        def f(*a, **k):
            calls.append(name)
            time.sleep(0.06)
            return answer() if callable(answer) else answer
        return f

    def tails(rows):
        calls.append("history_tails")
        time.sleep(0.06)
        return iter(())

    monkeypatch.setattr(cv, "SLOW_STORE_S", 0.05, raising=False)
    monkeypatch.setattr(db, "is_connected", lambda: True)
    monkeypatch.setattr(db, "load_settings", slow("load_settings", dict))
    monkeypatch.setattr(db, "save_settings", slow("save_settings", None))
    monkeypatch.setattr(db, "history_docs", slow("history_docs", dict))
    monkeypatch.setattr(db, "history_tails", tails)
    monkeypatch.setattr(db, "load_history_doc", slow("load_history_doc", None))
    monkeypatch.setattr(dr, "_index_reader", slow("index_rows", list))
    rv.load_fundamentals("TCS")                 # the one-per-process overlay read, done
    monkeypatch.setattr(P, "statement_symbols", lambda: ["TCS", "INFY"])
    monkeypatch.setattr(cv, "_PEER_CACHE", {"day": None, "by_method": {}, "base": None,
                                            "built": None, "sig": None, "source": ""})
    vv.clear_series_cache()
    return calls


def test_a_dead_database_is_asked_once_per_render(dead_store, monkeypatch):
    monkeypatch.setattr(vv, "LAST", {"targets": {}, "assumptions": {}})
    out = cv.section(state.cfg(), _Book(["TCS.NS", "ZZSMALL.NS"]), "")
    assert dead_store == ["load_settings"], dead_store     # the first call was the last
    assert "got no answer" in out["store_down"]
    assert out["computed"] == 2 and out["skipped_budget"] == 0
    tcs = next(r for r in out["rows"] if r["ticker"] == "TCS.NS")
    assert tcs["price_source"] == "repo copy" and tcs["price_date"] == "2026-07-17"
    html = _render(out)
    assert "den_chain_store_marker" in html
    assert "THE DATABASE DID NOT ANSWER IN TIME" in _flat(html)


def test_the_budget_banner_names_the_database_when_it_was_the_cause(dead_store, monkeypatch):
    monkeypatch.setattr(vv, "LAST", {"targets": {}, "assumptions": {}})
    monkeypatch.setenv("SB_DEN_CHAIN_BUDGET_S", "0.04")     # the dead read alone spends it
    out = cv.section(state.cfg(), _Book(["TCS.NS", "INFY.NS"]), "")
    assert out["computed"] == 0 and out["skipped_budget"] == 2
    assert all("database did not answer" in r["route"]["rule"] for r in out["rows"])
    text = _flat(_render(out))
    assert "the database was slow or unreachable" in text
    assert "before any row was computed" in text
    assert "spent on the" not in text


def test_a_name_the_price_read_missed_goes_to_the_zip_not_the_store(monkeypatch):
    """A healthy store that holds TCS but not INFY: ONE batched price read,
    INFY's closes from the repo zip — the old code asked the store again."""
    zip_tcs = cv._zip_close_series("TCS.NS")
    doc = {"ticker": "TCS.NS", "candles": [[d.strftime("%Y-%m-%d"), v, v, v, v, 1.0]
                                           for d, v in zip_tcs.items()]}
    reads = []
    monkeypatch.setattr(db, "is_connected", lambda: True)
    monkeypatch.setattr(db, "history_docs",
                        lambda tickers, rows=0: reads.append(list(tickers)) or {"TCS.NS": doc})
    monkeypatch.setattr(cv, "peer_multiples", lambda method, exclude="": dict(FIXED_PEERS))
    monkeypatch.setattr(cv, "load_assumptions", lambda: {})
    monkeypatch.setattr(vv, "load_doc", lambda: {"targets": {}, "assumptions": {}})
    out = cv.section(state.cfg(), _Book(["TCS.NS", "INFY.NS"]), "")
    assert reads == [["TCS.NS", "INFY.NS"]]                 # one read, never per name
    src = {r["ticker"]: r.get("price_source") for r in out["rows"]}
    assert src == {"TCS.NS": "store", "INFY.NS": "repo copy"}
    assert out["store_down"] == ""


def test_the_budget_banner_never_says_spent_on_zero_rows(monkeypatch):
    _fake_rows(monkeypatch, ["A1.NS", "A2.NS"])
    monkeypatch.setenv("SB_DEN_CHAIN_BUDGET_S", "0.000001")
    out = cv.section(state.cfg(), state.portfolio(), "")
    assert out["computed"] == 0 and out["skipped_budget"] == 2
    text = _flat(_render(out))
    assert "spent on the 0 row" not in text and "spent on the" not in text
    assert "which includes loading the rows' prices" in text
    assert "before any row was computed" in text


# ----------------------------------------------------- 2. busy kinds

def test_a_scan_rotation_says_open_again_when_the_scan_finishes(monkeypatch):
    from web import scheduler
    _fake_rows(monkeypatch, ["B1.NS"])
    assert scheduler._ROTATION_LOCK.acquire(blocking=False)
    try:
        out = cv.section(state.cfg(), state.portfolio(), "")
    finally:
        scheduler._ROTATION_LOCK.release()
    assert out["busy_kind"] == "rotation"
    html = _render(out)
    assert 'data-den-chain-busy-kind="rotation"' in html
    assert "when the scan finishes" in _flat(html)


def test_memory_held_after_a_scan_never_says_once_it_finishes(monkeypatch):
    import web.memory_relief as mr
    _fake_rows(monkeypatch, ["C1.NS", "C2.NS"])
    monkeypatch.setenv("SB_DEN_CHAIN_RSS_CEILING_MB", "380")
    monkeypatch.setattr(mr, "rss_mb", lambda: 450.0)
    out = cv.section(state.cfg(), state.portfolio(), "")
    assert out["busy_kind"] == "memory" and out["skipped_busy"] == 2
    html = _render(out)
    text = _flat(html)
    assert 'data-den-chain-busy-kind="memory"' in html
    assert "once it finishes" not in text and "finishes" not in text
    assert "15 minutes" not in text and "no visits" not in text
    assert "after the next restart" in text
    for r in out["rows"]:
        assert "after the next restart" in r["route"]["rule"]


def test_another_open_computing_says_reload_in_a_few_seconds(monkeypatch):
    _fake_rows(monkeypatch, ["D1.NS"])
    assert cv._SECTION_LOCK.acquire(blocking=False)
    try:
        out = cv.section(state.cfg(), state.portfolio(), "")
    finally:
        cv._SECTION_LOCK.release()
    assert out["busy_kind"] == "other_tab"
    html = _render(out)
    assert 'data-den-chain-busy-kind="other_tab"' in html
    assert "reload in a few seconds" in _flat(html)


# ------------------------------------------- 3. held names and the cap

def test_held_names_survive_25_targets_and_the_cut_is_printed(monkeypatch):
    targets = {f"T{i:02d}.NS": {"target": 1.0} for i in range(30)}
    monkeypatch.setattr(vv, "load_doc", lambda: {"targets": dict(targets), "assumptions": {}})
    names = cv.names_to_show(_Book(["SAIL.NS", "HAL.NS"]), "TCS.NS")
    assert names[:3] == ["TCS.NS", "SAIL.NS", "HAL.NS"]
    assert len(names) == cv.MAX_ROWS
    assert names.cut == [f"T{i:02d}.NS" for i in range(22, 30)]

    monkeypatch.setattr(cv, "row_for", lambda t, cfg, p, **k: P.failed_record(t, "x"))
    monkeypatch.setattr(cv, "load_assumptions", lambda: {})
    out = cv.section(state.cfg(), _Book(["SAIL.NS", "HAL.NS"]), "TCS")
    assert out["n_cut"] == 8 and out["cut_names"][0] == "T22.NS"
    text = _flat(_render(out))
    assert "8 name(s) not shown (cap 25)" in text
    assert "then held names, then your §11 targets A-Z" in text


# ----------------------------------------------------- 4. crashed rows

def test_a_crashing_row_prints_failed_and_is_counted(monkeypatch):
    def row(t, cfg, p, **k):
        if t == "BAD.NS":
            raise ZeroDivisionError("float division by zero")
        return P.failed_record(t, "fake computed row")

    monkeypatch.setattr(cv, "names_to_show",
                        lambda p, typed="": getattr(cv, "ShownNames", list)(["OK1.NS", "BAD.NS"]))
    monkeypatch.setattr(cv, "row_for", row)
    monkeypatch.setattr(cv, "load_assumptions", lambda: {})
    out = cv.section(state.cfg(), state.portfolio(), "")
    assert out["failed"] == 1 and out["computed"] == 1
    bad = next(r for r in out["rows"] if r["ticker"] == "BAD.NS")
    assert bad["failed"] and bad["row_kind"] == "failed"
    html = _render(out)
    row_txt = _flat(_row_html(html, "BAD.NS"))
    assert "FAILED on this open — ZeroDivisionError: float division by zero" in row_txt
    assert "UNDECLARED" not in row_txt and "INSUFFICIENT" not in row_txt
    assert "1 FAILED" in _flat(html)                       # counted on the "This open" line


# ------------------------------------------------ 5. no statements

def test_a_name_with_no_statements_prints_one_line_not_insufficient(monkeypatch, no_targets):
    monkeypatch.setattr(cv, "peer_multiples", lambda method, exclude="": dict(FIXED_PEERS))
    out = cv.section(state.cfg(), _Book(), "ZZNOSUCH")
    rec = out["rows"][0]
    assert rec["row_kind"] == "no_statements"
    row_txt = _flat(_row_html(_render(out), "ZZNOSUCH"))
    assert row_txt.count("NO STATEMENTS") == 1
    assert "outside the 200 names with statements" in row_txt
    assert "needs the batch fetch (your approval pending)" in row_txt
    assert "INSUFFICIENT" not in row_txt and "DATA_NEEDED" not in row_txt


def test_a_summary_only_name_also_prints_no_statements(monkeypatch, no_targets):
    """A name the monthly refresh holds only summary figures for (the
    fundamentals file, no statements zip) — e.g. a held small-cap."""
    tcs_fund = rv.load_fundamentals("TCS")
    real_stm, real_fund = S._default_statements, S._default_fundamentals
    monkeypatch.setattr(S, "_default_statements",
                        lambda sym: {} if sym == "SMALLCO" else real_stm(sym))
    monkeypatch.setattr(S, "_default_fundamentals",
                        lambda sym: tcs_fund if sym == "SMALLCO" else real_fund(sym))
    monkeypatch.setattr(cv, "peer_multiples", lambda method, exclude="": dict(FIXED_PEERS))
    out = cv.section(state.cfg(), _Book(), "SMALLCO")
    rec = out["rows"][0]
    assert rec["snapshot"]["source"] == "fundamentals_csv"
    assert rec["row_kind"] == "no_statements"
    row_txt = _flat(_row_html(_render(out), "SMALLCO"))
    assert "NO STATEMENTS" in row_txt and "only summary figures" in row_txt
    assert "INSUFFICIENT" not in row_txt


def test_statements_but_no_range_prints_no_range_not_insufficient(monkeypatch, no_targets):
    monkeypatch.setattr(cv, "peer_multiples", lambda method, exclude="": dict(FIXED_PEERS))
    out = cv.section(state.cfg(), _Book(), "DLF")           # NAV route: no project NAV stored
    rec = out["rows"][0]
    assert rec["row_kind"] == "full" and rec["range"]["status"] != "OK"
    # 2026-10-21 (VAL-01 / VAL-14b): a NAV route with nothing typed is "no
    # target by design — type NAV per share in the Den inputs table", not a
    # fault; any other missing range still prints NO RANGE — both, never an
    # INSUFFICIENT verdict
    row = _row_html(_render(out), "DLF.NS")
    if "no target by design" in (rec["range"].get("why") or ""):
        assert "den_chain_by_design_marker" in row and "NO TARGET BY DESIGN" in _flat(row)
        assert "inputs table" in _flat(row)
    else:
        assert "den_chain_no_range_marker" in row and "NO RANGE — " in _flat(row)
    assert "INSUFFICIENT" not in _flat(row)
    rec["range"]["why"] = "range not computable"
    row = _row_html(_render(out), "DLF.NS")
    assert "den_chain_no_range_marker" in row and "NO RANGE — range not computable" in _flat(row)
    assert "INSUFFICIENT" not in _flat(row)


# --------------------------------------------- 6. the §11 target button

def test_the_target_button_shows_only_on_a_judged_verdict(monkeypatch, no_targets):
    monkeypatch.setattr(cv, "peer_multiples", lambda method, exclude="": dict(FIXED_PEERS))
    hdfc = cv.row_for("HDFCBANK.NS", state.cfg(), _Book())
    sail = cv.row_for("SAIL.NS", state.cfg(), _Book())
    assert hdfc["verdict"]["verdict"] == "insufficient" and hdfc["range"]["base"]
    assert sail["verdict"]["verdict"] in ("attractive", "watch", "unattractive")
    mark = getattr(cv, "_marked", lambda r: r)
    chain = cv.empty_section()
    chain.update({"rows": [mark(hdfc), mark(sail)], "n": 2})
    html = _render(chain)
    h_row, s_row = _row_html(html, "HDFCBANK.NS"), _row_html(html, "SAIL.NS")
    assert "den_chain_use_marker" not in h_row and 'name="target"' not in h_row
    assert "too uncertain to use as a target" in _flat(h_row)
    assert "den_chain_use_marker" in s_row and 'name="target"' in s_row
    viewer = _render(chain, is_owner=False)               # never for a non-owner
    assert "den_chain_use_marker" not in viewer
    assert "too uncertain to use as a target" not in viewer


# ------------------------------------------------ 7. the peer-only text

def test_the_peer_only_band_text_is_rounded():
    band = SC.multiple_band(7.684150, [], peer_only_point=True)
    assert band["source"].startswith("peer 7.68 only")
    assert band["low"] == band["high"] == pytest.approx(7.68415)   # the number is untouched


# ----------------------------------------------------------- 8. layout

def test_the_answer_columns_come_right_after_stock_price(monkeypatch, no_targets):
    monkeypatch.setattr(cv, "peer_multiples", lambda method, exclude="": dict(FIXED_PEERS))
    out = cv.section(state.cfg(), _Book(), "TCS", show_terms=True)
    html = _render(out)
    cols = re.findall(r'<th[^>]*data-den-chain-col="([a-z]+)"', html)
    assert cols[:4] == ["stock", "verdict", "range", "cases"], cols
    assert len(cols) == 10
    heads = [_flat(h) for h in re.findall(r"<th\b.*?</th>", html, re.S)]
    assert heads[0].startswith("Stock · price") and heads[1].startswith("Verdict (18)")
    # the cells follow the headers: the 2nd cell of TCS's row is the verdict
    cells = re.findall(r"<td\b.*?</td>", _row_html(html, "TCS.NS"), re.S)
    assert len(cells) == 10
    assert re.search(r"INSUFFICIENT|ATTRACTIVE|WATCH|UNATTRACTIVE|NO RANGE", _flat(cells[1]))
    assert re.search(r"₹[\d,]+ / ₹[\d,]+ / ₹[\d,]+", _flat(cells[2]))
    # Earnings: no nowrap on the cell (only on its two number lines)
    earn = cells[7]
    assert "EPS " in _flat(earn)
    assert "whitespace-nowrap" not in earn.split(">", 1)[0]
    assert earn.count("whitespace-nowrap") == 2
    # the all-terms panel is OUTSIDE the sideways-scrolling table box
    assert html.index("den_chain_terms_marker") > html.index("</table>")
    box_end = html.index("</div>", html.index("</table>"))
    assert html.index("den_chain_terms_marker") > box_end
    assert "All 52 finance terms for TCS" in _flat(html)


def test_the_live_den_page_renders_the_new_layout(monkeypatch):
    monkeypatch.setattr(cv, "peer_multiples", lambda method, exclude="": dict(FIXED_PEERS))
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD}, follow_redirects=False)
    assert r.status_code == 303
    page = c.get("/den?chain=ZZNOSUCH")
    assert page.status_code == 200
    assert "den_chain_no_statements_marker" in page.text
    cols = re.findall(r'data-den-chain-col="([a-z]+)"', page.text)
    assert cols[:2] == ["stock", "verdict"]
