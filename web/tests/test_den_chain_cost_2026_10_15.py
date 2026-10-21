"""Den §12 cost fix (owner 2026-10-15: "Mars den always go down nt
opening"). Money impact NONE — display only.

What broke: §12 loaded ~200 twelve-year price documents (the peer table)
and re-read three committed files per row on every open; on the 512 MB box,
on top of a scan, that took the app down. Pins:
  * the corporate-action / shares files are read ONCE per process (an
    index), with the same rows the old streamed read returned;
  * the index / bond series are read once per process (copies handed out);
  * the peer table's price-free base is STORED (settings doc
    valuation_peers) and read back on a fresh process instead of rebuilt;
    a base from another day, a changed sector label, a version bump or a
    change to the valuation code rebuilds it; today's multiples = that base
    priced from ONE store query, names the store lacks filled from the repo
    zip in one open (never name-by-name store reads); a base priced on
    under 90% of the names is used but not stored;
  * concurrent renders: one peer build, one computing render, a thread-
    safe row cache; the busy reading is re-taken before every new row;
  * splitting the peer table changed no number;
  * §12 stops computing new rows once its time budget is spent and says so;
    rows it computed are kept and fill in over later opens;
  * no new row is computed while a scan rotation runs or the process is
    above the memory ceiling — the page prints why, cached rows still show;
  * the production defaults (20 s, 380 MiB) and that render.yaml sets
    neither variable.
"""
from __future__ import annotations

import csv
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import web.db as db
from trading.valuation import pipeline as P
from trading.valuation import shares as SH
from web import auth as auth_mod
from web import state
from web import valuation_chain_view as cv
from web import valuation_view as vv
from web.main import app

ROOT = Path(__file__).resolve().parents[2]


def _owner() -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD},
               follow_redirects=False)
    assert r.status_code == 303
    return c


@pytest.fixture
def fresh_peers(monkeypatch):
    """An empty peer cache for this test only (the module global is
    restored afterwards)."""
    monkeypatch.setattr(cv, "_PEER_CACHE", {"day": None, "by_method": {}, "base": None,
                                            "built": None, "sig": None, "source": ""})


# --------------------------------------------------------- file indexes

def test_the_corporate_action_file_is_read_once_for_many_symbols(monkeypatch):
    SH.clear_file_index()
    opened: list = []
    real_open = open

    def counting(path, *a, **k):
        opened.append(str(path))
        return real_open(path, *a, **k)

    monkeypatch.setattr(SH, "open", counting, raising=False)
    tcs = SH.load_corporate_actions("TCS.NS")
    SH.load_corporate_actions("INFY.NS")
    again = SH.load_corporate_actions("TCS")
    assert sum(1 for p in opened if p.endswith("corporate_actions_2010_2026.csv")) == 1
    # Same rows the old streamed read returned.
    with real_open(ROOT / "data" / "corporate_actions_2010_2026.csv", newline="",
                   encoding="utf-8") as f:
        old = [dict(r) for r in csv.DictReader(f)
               if (r.get("symbol") or "").strip().upper() == "TCS"]
    assert tcs == old and again == old and len(old) > 0
    # Copies: a caller editing a row cannot change the index.
    tcs[0]["symbol"] = "CHANGED"
    assert SH.load_corporate_actions("TCS.NS")[0]["symbol"] != "CHANGED"
    SH.clear_file_index()


def test_the_shares_row_matches_the_old_streamed_read():
    SH.clear_file_index()
    with open(ROOT / "data" / "shares_outstanding.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    sym = (rows[0].get("symbol") or "").strip().upper()
    first = next(dict(r) for r in rows if (r.get("symbol") or "").strip().upper() == sym)
    assert SH.load_shares_row(f"{sym}.NS") == first
    assert SH.load_shares_row("NOSUCHNAME.NS") is None


def test_a_replaced_file_is_read_again(tmp_path):
    SH.clear_file_index()
    f = tmp_path / "ca.csv"
    f.write_text("symbol,subject\nAAA,Bonus 1:1\n", encoding="utf-8")
    assert len(SH.load_corporate_actions("AAA", path=f)) == 1
    time.sleep(0.01)
    f.write_text("symbol,subject\nAAA,Bonus 1:1\nAAA,Split\n", encoding="utf-8")
    assert len(SH.load_corporate_actions("AAA", path=f)) == 2
    SH.clear_file_index()


# --------------------------------------------------------- series cache

def test_index_and_bond_series_are_read_once_per_process(monkeypatch):
    vv.clear_series_cache()
    calls = {"index": 0, "bond": 0}
    real_index, real_bond = vv._read_index_field, vv._read_bond_series

    def idx(field):
        calls["index"] += 1
        return real_index(field)

    def bond():
        calls["bond"] += 1
        return real_bond()

    monkeypatch.setattr(vv, "_read_index_field", idx)
    monkeypatch.setattr(vv, "_read_bond_series", bond)
    a = vv._index_field("nifty_close")
    for _ in range(3):
        vv._index_field("nifty_close")
    vv._bond_series()
    vv._bond_series()
    assert calls == {"index": 1, "bond": 1}
    assert a is not None and len(a) > 1000
    a.iloc[-1] = -1.0                                  # a copy was handed out
    assert vv._index_field("nifty_close").iloc[-1] != -1.0
    monkeypatch.setattr(vv, "SERIES_TTL_S", 0.0)       # expiry re-reads
    vv._index_field("nifty_close")
    assert calls["index"] == 2
    vv.clear_series_cache()


# ---------------------------------------------------------- stored peers

def _base_rows():
    return [{"ticker": "AAA.NS", "method": "m1", "basic_eps": 10.0,
             "shares_outstanding": 100.0, "ebitda": 500.0, "revenue": 2000.0,
             "total_debt": 100.0, "cash": 50.0, "equity": 1000.0},
            {"ticker": "BBB.NS", "method": "m1", "basic_eps": 20.0,
             "shares_outstanding": 100.0, "ebitda": 900.0, "revenue": 3000.0,
             "total_debt": 0.0, "cash": 0.0, "equity": 2000.0}]


def _tails():
    return [{"ticker": "AAA.NS", "candles": [["2026-10-14", 0, 0, 0, 200.0, 0]]},
            {"ticker": "BBB.NS", "candles": [["2026-10-14", 0, 0, 0, 300.0, 0]]}]


def _two_names(monkeypatch):
    """The statement set = the two fake names; the zip is never read."""
    monkeypatch.setattr(P, "statement_symbols", lambda: ["AAA", "BBB"])
    zips = []
    monkeypatch.setattr(cv, "_zip_frames", lambda ts: zips.append(list(ts)) or {})
    return zips


def test_peers_come_from_the_stored_base_and_one_price_query(monkeypatch, fresh_peers):
    zips = _two_names(monkeypatch)
    doc = {"base": _base_rows(), "built": date.today().isoformat(),
           "sig": cv._peer_sig(cv._sector_map()), "version": cv.PEER_BASE_VERSION}
    monkeypatch.setattr(db, "load_settings",
                        lambda doc_id="default": dict(doc) if doc_id == cv.PEERS_DOC_ID else {})
    queries = []
    monkeypatch.setattr(db, "history_tails", lambda rows: queries.append(rows) or _tails())

    def no_rebuild(*a, **k):
        raise AssertionError("the stored base must be read, not rebuilt")

    monkeypatch.setattr(P, "peer_base", no_rebuild)
    m = cv.peer_multiples("m1")
    assert m["n"] == 2
    assert m["pe_median"] == pytest.approx((200 / 10 + 300 / 20) / 2)   # 20 and 15
    assert queries == [1]                                # ONE query, last candle only
    assert zips == [[]]                                  # nothing missing → no zip read
    cv.peer_multiples("m1")
    assert queries == [1]                                # priced once per day


def test_a_fresh_build_is_stored_and_not_repeated(monkeypatch, fresh_peers):
    _two_names(monkeypatch)
    saved: dict = {}
    monkeypatch.setattr(db, "load_settings", lambda doc_id="default": {})
    monkeypatch.setattr(db, "save_settings",
                        lambda d, doc_id="default": saved.setdefault(doc_id, {}).update(d))
    monkeypatch.setattr(db, "history_tails", lambda rows: _tails())
    builds = []
    monkeypatch.setattr(P, "peer_base",
                        lambda price_of, sector_map, company_names=None:
                        builds.append(price_of("AAA.NS")) or _base_rows())
    base, built, src = cv.peer_base()
    assert src == "built" and len(base) == 2 and builds == [200.0]
    doc = saved[cv.PEERS_DOC_ID]
    assert doc["base"] == _base_rows() and doc["built"] == date.today().isoformat()
    assert doc["version"] == cv.PEER_BASE_VERSION and doc["sig"]
    cv.peer_base()
    assert len(builds) == 1                              # the process keeps it


@pytest.mark.parametrize("stale", ["old", "sig", "version"])
def test_a_stale_or_mismatched_base_is_rebuilt(monkeypatch, fresh_peers, stale):
    _two_names(monkeypatch)
    doc = {"base": _base_rows(), "built": date.today().isoformat(),
           "sig": cv._peer_sig(cv._sector_map()), "version": cv.PEER_BASE_VERSION}
    if stale == "old":
        doc["built"] = (date.today() - timedelta(days=cv.PEER_BASE_MAX_AGE_DAYS + 1)).isoformat()
    elif stale == "sig":
        doc["sig"] = "different"
    else:
        doc["version"] = cv.PEER_BASE_VERSION - 1
    monkeypatch.setattr(db, "load_settings", lambda doc_id="default": dict(doc))
    monkeypatch.setattr(db, "save_settings", lambda d, doc_id="default": None)
    monkeypatch.setattr(db, "history_tails", lambda rows: _tails())
    builds = []
    monkeypatch.setattr(P, "peer_base",
                        lambda price_of, sector_map, company_names=None:
                        builds.append(1) or _base_rows())
    cv.peer_base()
    assert builds == [1]


def test_splitting_the_peer_table_changed_no_number(monkeypatch):
    """The old one-step peer table, re-implemented inline, against the new
    base + pricing on six real statement names."""
    from trading.valuation import route as R
    from trading.valuation import snapshot as S
    syms = list(P.statement_symbols())[:6]
    monkeypatch.setattr(P, "statement_symbols", lambda: syms)
    prices = {f"{s}.NS": 100.0 + 37.0 * i for i, s in enumerate(syms)}
    sm = dict(state.cfg().sector_map)
    old: dict = {}
    # 2026-10-21 (valuation batch 2, owner choices): the one-step reference
    # below follows the NEW rules — peer EV = price × shares + debt − cash −
    # short-term investments + minorities (VAL-12a, the stock's own rule), a
    # lender's P/B on its VAL-02 book, peers grouped by the route's
    # sector_method. The split (base + pricing) must still change no number;
    # only the keys this reference builds are compared (the priced rows now
    # also carry price and EV/EBIT).
    for sym in syms:
        t = f"{sym}.NS"
        px = prices[t]
        snap = S.snapshot(sym, price=px)
        rt = R.route_for(t, sm, "")
        method = rt.get("sector_method") or rt.get("method")
        if not method or snap.get("source") == "none":
            continue
        f = lambda k: P._f(snap, k)                      # noqa: E731
        row = {"ticker": t, "pe": None, "ev_ebitda": None, "pb": None, "ev_sales": None}
        eps, sh, eq = f("basic_eps"), f("shares_outstanding"), f("equity")
        debt, cash, ebitda, rev = f("total_debt"), f("cash"), f("ebitda"), f("revenue")
        sti = P._sti(snap) or 0.0
        mi = f("minority_interest_bs") or 0.0
        book = eq
        if method in P.LENDER_METHODS:
            b, _basis = P._lender_book(sym, snap, snap.get("period_end"))
            book = b * P.CR if b is not None else None
        if eps and eps > 0:
            row["pe"] = px / eps
        if sh and book and book > 0:
            row["pb"] = px / (book / sh)
        if sh and debt is not None and cash is not None:
            ev = px * sh + debt - cash - sti + mi
            if ebitda and ebitda > 0:
                row["ev_ebitda"] = ev / ebitda
            if rev and rev > 0:
                row["ev_sales"] = ev / rev
        old.setdefault(method, []).append(row)
    new = P.peer_table(lambda t: prices.get(t), sm, {})
    assert old and set(new) == set(old)
    for m, rows in old.items():
        assert len(new[m]) == len(rows), m
        for a, b in zip(rows, new[m]):
            assert {k: b.get(k) for k in a} == a, (m, a["ticker"])


# ---------------------------------------------------- budget and guard

def _fake_rows(monkeypatch, names, sleep_s=0.0):
    calls = []

    def fake_row(t, cfg, p, **k):
        calls.append(t)
        if sleep_s:
            time.sleep(sleep_s)
        rec = P.failed_record(t, "fake computed row")
        rec["fake"] = True
        return rec

    monkeypatch.setattr(cv, "names_to_show", lambda p, typed="": list(names))
    monkeypatch.setattr(cv, "row_for", fake_row)
    monkeypatch.setattr(cv, "load_assumptions", lambda: {})
    return calls


def test_the_time_budget_stops_new_rows_and_later_opens_fill_them(monkeypatch):
    names = [f"N{i}.NS" for i in range(6)]
    calls = _fake_rows(monkeypatch, names, sleep_s=0.4)
    monkeypatch.setenv("SB_DEN_CHAIN_BUDGET_S", "0.6")
    cfg, p = state.cfg(), state.portfolio()
    a = cv.section(cfg, p, "")
    assert (a["computed"], a["skipped_budget"], a["cached"]) == (2, 4, 0)
    assert [r.get("not_computed", False) for r in a["rows"]] == [False] * 2 + [True] * 4
    assert "time budget" in a["rows"][-1]["route"]["rule"]
    b = cv.section(cfg, p, "")
    assert (b["cached"], b["computed"]) == (2, 2)
    c = cv.section(cfg, p, "")
    assert (c["cached"], c["computed"], c["skipped_budget"]) == (4, 2, 0)
    assert sorted(calls) == sorted(names)              # each name computed once
    assert all(r.get("fake") for r in c["rows"])


def test_no_row_is_computed_while_a_scan_rotation_runs(monkeypatch):
    from web import scheduler
    names = ["A1.NS", "A2.NS", "A3.NS"]
    calls = _fake_rows(monkeypatch, names)
    kept = P.failed_record("A1.NS", "kept from an earlier open")
    cv._row_cache_put(cv._row_key("A1.NS", {}, False), kept)
    assert scheduler._ROTATION_LOCK.acquire(blocking=False)
    try:
        out = cv.section(state.cfg(), state.portfolio(), "")
    finally:
        scheduler._ROTATION_LOCK.release()
    assert calls == []
    assert out["rows"][0] is kept and out["cached"] == 1
    assert out["skipped_busy"] == 2 and "scan rotation" in out["busy"]
    assert all(r.get("not_computed") for r in out["rows"][1:])


def test_the_memory_ceiling_reads_the_process_and_the_env(monkeypatch):
    import web.memory_relief as mr
    monkeypatch.setenv("SB_DEN_CHAIN_RSS_CEILING_MB", "380")
    monkeypatch.setattr(mr, "rss_mb", lambda: 450.0)
    why = cv.busy_reason()
    assert "450 MiB" in why and "380" in why
    monkeypatch.setattr(mr, "rss_mb", lambda: 300.0)
    assert cv.busy_reason() == ""
    monkeypatch.setenv("SB_DEN_CHAIN_RSS_CEILING_MB", "0")   # 0 = off
    monkeypatch.setattr(mr, "rss_mb", lambda: 999.0)
    assert cv.busy_reason() == ""


def test_the_production_defaults_and_render_yaml(monkeypatch):
    monkeypatch.delenv("SB_DEN_CHAIN_BUDGET_S", raising=False)
    monkeypatch.delenv("SB_DEN_CHAIN_RSS_CEILING_MB", raising=False)
    assert cv.time_budget_s() == 20.0
    assert cv.rss_ceiling_mb() == 380.0
    y = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert "SB_DEN_CHAIN_BUDGET_S" not in y and "SB_DEN_CHAIN_RSS_CEILING_MB" not in y


def test_row_for_uses_handed_closes_without_another_read(monkeypatch):
    def no_read(t):
        raise AssertionError("closes were handed in")

    monkeypatch.setattr(cv, "_closes", no_read)
    monkeypatch.setattr(cv, "peer_multiples",
                        lambda method, exclude="": {"n": 0, "pe_median": None,
                                                    "pb_median": None, "ev_ebitda_median": None,
                                                    "ev_sales_median": None, "source": "t"})
    idx = pd.bdate_range("2020-01-01", periods=1500)
    closes = pd.Series(range(1000, 2500), index=idx, dtype=float)
    rec = cv.row_for("TCS.NS", state.cfg(), state.portfolio(), closes=closes)
    assert rec["ticker"] == "TCS.NS" and rec["price"] == 2499.0


# --------------------------------------------------------------- page

def test_the_den_prints_why_rows_were_not_computed(monkeypatch):
    # Seam moved 2026-10-15: section() reads busy_status() (kind, text) —
    # busy_reason() returned text only, so the page could not print the
    # remedy for the KIND of busy box (test_den_chain_clarity_2026_10_15).
    monkeypatch.setattr(cv, "busy_status",
                        lambda: ("rotation", "a scan rotation is running on the box"))
    r = _owner().get("/den?chain=TCS")
    assert r.status_code == 200
    html = r.text
    assert "den_chain_busy_marker" in html and "den_chain_not_computed_marker" in html
    assert "NOT COMPUTED" in html and "scan rotation is running" in html


def test_the_den_prints_the_budget_line(monkeypatch):
    _fake_rows(monkeypatch, ["TCS.NS", "INFY.NS"], sleep_s=0.3)
    monkeypatch.setenv("SB_DEN_CHAIN_BUDGET_S", "0.1")
    html = _owner().get("/den").text
    assert "den_chain_budget_marker" in html and "den_chain_cost_marker" in html
    assert "1 row(s) NOT computed" in " ".join(html.split())


# ------------------------------------------------ review fixes 2026-10-15

def test_mongo_down_prices_come_from_one_zip_open_never_per_name(monkeypatch):
    monkeypatch.setattr(P, "statement_symbols", lambda: ["TCS", "INFY"])
    monkeypatch.setattr(db, "history_tails", lambda rows: [])

    def no_store(*a, **k):
        raise AssertionError("no per-name store read when the store answered nothing")

    monkeypatch.setattr(db, "history_docs", no_store)
    monkeypatch.setattr(db, "load_history_doc", no_store)
    calls = []
    real = cv._zip_frames
    monkeypatch.setattr(cv, "_zip_frames", lambda ts: calls.append(list(ts)) or real(ts))
    prices = cv._peer_prices()
    assert calls == [["TCS.NS", "INFY.NS"]]
    assert prices["TCS.NS"] == pytest.approx(2269.0)      # the zip's last close (2026-07-17)
    assert prices["INFY.NS"] > 0


def test_a_partial_store_is_filled_from_the_zip(monkeypatch):
    monkeypatch.setattr(P, "statement_symbols", lambda: ["TCS", "INFY"])
    monkeypatch.setattr(db, "history_tails", lambda rows: [
        {"ticker": "TCS.NS", "candles": [["2026-10-14", 0, 0, 0, 3000.0, 0]]}])
    calls = []
    real = cv._zip_frames
    monkeypatch.setattr(cv, "_zip_frames", lambda ts: calls.append(list(ts)) or real(ts))
    prices = cv._peer_prices()
    assert calls == [["INFY.NS"]]
    assert prices["TCS.NS"] == 3000.0 and prices["INFY.NS"] > 0


def test_a_thinly_priced_base_is_used_but_not_stored(monkeypatch, fresh_peers):
    names = [f"N{i}" for i in range(10)]
    monkeypatch.setattr(P, "statement_symbols", lambda: names)
    monkeypatch.setattr(db, "load_settings", lambda doc_id="default": {})
    saved = []
    monkeypatch.setattr(db, "save_settings", lambda d, doc_id="default": saved.append(doc_id))
    monkeypatch.setattr(P, "peer_base", lambda price_of, sector_map, company_names=None: _base_rows())
    prices = {f"N{i}.NS": 100.0 for i in range(5)}        # 5 of 10 priced
    base, built, src = cv.peer_base(prices)
    assert src == "built_thin" and base == _base_rows()
    assert cv.PEERS_DOC_ID not in saved
    full = {f"N{i}.NS": 100.0 for i in range(10)}
    cv._PEER_CACHE.update({"base": None})
    _b, _d, src2 = cv.peer_base(full)
    assert src2 == "built" and cv.PEERS_DOC_ID in saved


def test_the_signature_moves_with_the_valuation_code(monkeypatch):
    smap = cv._sector_map()
    a = cv._peer_sig(smap)
    monkeypatch.setattr(P, "VERSION", "a-different-version", raising=False)
    assert cv._peer_sig(smap) != a


def test_concurrent_first_opens_build_the_peer_table_once(monkeypatch, fresh_peers):
    import threading
    _two_names(monkeypatch)
    monkeypatch.setattr(db, "load_settings", lambda doc_id="default": {})
    monkeypatch.setattr(db, "save_settings", lambda d, doc_id="default": None)
    monkeypatch.setattr(db, "history_tails", lambda rows: _tails())
    builds = []

    def slow_base(price_of, sector_map, company_names=None):
        builds.append(1)
        time.sleep(0.4)
        return _base_rows()

    monkeypatch.setattr(P, "peer_base", slow_base)
    got = []
    ts = [threading.Thread(target=lambda: got.append(cv.peer_multiples("m1")["n"]))
          for _ in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(5)
    assert builds == [1]
    assert got == [2, 2, 2]                              # nobody read an empty table


def test_a_second_open_while_one_computes_serves_kept_rows_only(monkeypatch):
    calls = _fake_rows(monkeypatch, ["A1.NS", "A2.NS"])
    kept = P.failed_record("A1.NS", "kept")
    cv._row_cache_put(cv._row_key("A1.NS", {}, False), kept)
    assert cv._SECTION_LOCK.acquire(blocking=False)
    try:
        out = cv.section(state.cfg(), state.portfolio(), "")
    finally:
        cv._SECTION_LOCK.release()
    assert calls == [] and out["rows"][0] is kept
    assert out["skipped_busy"] == 1 and "another open" in out["busy"]


def test_a_rotation_starting_mid_render_stops_new_rows(monkeypatch):
    from web import scheduler
    names = ["B1.NS", "B2.NS", "B3.NS"]
    held = []

    def row(t, cfg, p, **k):
        if not held:                                     # the scan starts after row 1
            assert scheduler._ROTATION_LOCK.acquire(blocking=False)
            held.append(1)
        return P.failed_record(t, "computed")

    monkeypatch.setattr(cv, "names_to_show", lambda p, typed="": list(names))
    monkeypatch.setattr(cv, "row_for", row)
    monkeypatch.setattr(cv, "load_assumptions", lambda: {})
    try:
        out = cv.section(state.cfg(), state.portfolio(), "")
    finally:
        if held:
            scheduler._ROTATION_LOCK.release()
    assert out["computed"] == 1 and out["skipped_busy"] == 2


def test_the_row_cache_survives_concurrent_writers(monkeypatch):
    import threading
    monkeypatch.setattr(cv, "ROW_CACHE_MAX", 20)
    errors = []

    def writer(n):
        try:
            for i in range(200):
                cv._row_cache_put((f"T{n}-{i}", "d", "{}", False), {"i": i})
                cv._row_cache_get((f"T{n}-{i - 3}", "d", "{}", False))
        except Exception as e:                           # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    assert errors == [] and len(cv._ROW_CACHE) <= 20


def test_an_index_reading_the_store_did_not_extend_is_asked_again(monkeypatch):
    from trading import data_refresh as dr
    vv.clear_series_cache()
    monkeypatch.setattr(db, "is_connected", lambda: True)
    monkeypatch.setattr(dr, "extend_close_series", lambda s, field: s)   # store added nothing
    reads = []
    real = vv._read_index_field
    monkeypatch.setattr(vv, "_read_index_field", lambda f: reads.append(f) or real(f))
    vv._index_field("nifty_close")
    vv._index_field("nifty_close")
    assert len(reads) == 2                               # not kept: the next call asks again

    def extended(s, field):
        return pd.concat([s, pd.Series({s.index.max() + pd.Timedelta(days=1): 1.0})])

    monkeypatch.setattr(dr, "extend_close_series", extended)
    vv.clear_series_cache()
    reads.clear()
    vv._index_field("nifty_close")
    vv._index_field("nifty_close")
    assert len(reads) == 1
    vv.clear_series_cache()


def test_a_not_computed_row_is_one_plain_line_not_a_verdict(monkeypatch):
    # seam: busy_status(), see test_the_den_prints_why_rows_were_not_computed
    monkeypatch.setattr(cv, "busy_status",
                        lambda: ("rotation", "a scan rotation is running on the box"))
    html = _owner().get("/den?chain=TCS").text
    i = html.find("den_chain_rows_marker")
    j = html.find("</table>", i)
    rows = html[i:j]
    assert "NOT COMPUTED on this open" in rows
    assert "no stored price" not in rows and "insufficient" not in rows.lower()
