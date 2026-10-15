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
    a week-old base or a changed sector label rebuilds it; today's
    multiples = that base priced from ONE store query;
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


def test_peers_come_from_the_stored_base_and_one_price_query(monkeypatch, fresh_peers):
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
    cv.peer_multiples("m1")
    assert queries == [1]                                # priced once per day


def test_a_fresh_build_is_stored_and_not_repeated(monkeypatch, fresh_peers):
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
def test_a_week_old_or_mismatched_base_is_rebuilt(monkeypatch, fresh_peers, stale):
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
    for sym in syms:
        t = f"{sym}.NS"
        px = prices[t]
        snap = S.snapshot(sym, price=px)
        method = R.route_for(t, sm, "").get("method")
        if not method or snap.get("source") == "none":
            continue
        f = lambda k: P._f(snap, k)                      # noqa: E731
        row = {"ticker": t, "pe": None, "ev_ebitda": None, "pb": None, "ev_sales": None}
        eps, sh, eq = f("basic_eps"), f("shares_outstanding"), f("equity")
        debt, cash, ebitda, rev = f("total_debt"), f("cash"), f("ebitda"), f("revenue")
        if eps and eps > 0:
            row["pe"] = px / eps
        if sh and eq and eq > 0:
            row["pb"] = px / (eq / sh)
        if sh and debt is not None and cash is not None:
            ev = px * sh + debt - cash
            if ebitda and ebitda > 0:
                row["ev_ebitda"] = ev / ebitda
            if rev and rev > 0:
                row["ev_sales"] = ev / rev
        old.setdefault(method, []).append(row)
    new = P.peer_table(lambda t: prices.get(t), sm, {})
    assert old and new == old


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
    monkeypatch.setattr(cv, "busy_reason", lambda: "a scan rotation is running on the box")
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
