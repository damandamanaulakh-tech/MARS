"""Owner order 2026-10-02 ("go for next"): audit Mongo rows M-3, M-4, M-5,
M-9, M-10 (and T-2's guard lives in conftest.py).

M-3   the Upstox FII/DII mapper has never produced a row; /healthz now shows
      the STRUCTURE of the stored payloads (no numbers) so it can be fixed.
M-4   the `trades` collection code had no caller.
M-5   the journal-archive reader had no caller.
M-9   the price-store query filtered an unindexed field.
M-10  closed trades were the only uncapped list in the 16 MB portfolio doc.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from trading.paper import ClosedTrade, Portfolio
from web import db, state


class _Col:
    def __init__(self, docs=None, fail=False):
        self.docs = list(docs or [])
        self.fail = fail
        self.last_filter = None
        self.writes = []

    # --- reads -------------------------------------------------------
    def find(self, flt=None, proj=None):
        self.last_filter = flt
        out = []
        for d in self.docs:
            if flt and "_id" in flt:
                if d.get("_id") not in flt["_id"]["$in"]:
                    continue
            out.append(dict(d))
        return _Cur(out)

    def find_one(self, flt=None, sort=None):
        rows = [d for d in self.docs
                if all(d.get(k) == v for k, v in (flt or {}).items())]
        if sort:
            key, direction = sort[0]
            rows.sort(key=lambda d: d.get(key) or "", reverse=direction < 0)
        return dict(rows[0]) if rows else None

    def count_documents(self, flt, **k):
        return len(self.docs)

    # --- writes ------------------------------------------------------
    def bulk_write(self, ops, ordered=True):
        if self.fail:
            raise RuntimeError("quota exceeded")
        for op in ops:
            doc = op._doc["$setOnInsert"]
            if not any(d["_id"] == doc["_id"] for d in self.docs):
                self.docs.append(dict(doc))
        self.writes.append(len(ops))


class _Cur(list):
    def sort(self, key, direction):
        return _Cur(sorted(self, key=lambda d: d.get(key) or "",
                           reverse=direction < 0))

    def limit(self, n):
        return _Cur(self[:n])


class _DB:
    def __init__(self, **cols):
        for k, v in cols.items():
            setattr(self, k, v)


@pytest.fixture
def fake_db(monkeypatch):
    def _make(**cols):
        d = _DB(**cols)
        monkeypatch.setattr(db, "_DB", d)
        monkeypatch.setattr(db, "_CONNECTED", True)
        return d
    return _make


def _trade(i: int) -> ClosedTrade:
    return ClosedTrade(
        ticker=f"T{i}.NS", qty=1, entry_date=datetime(2026, 1, 1),
        exit_date=datetime(2026, 2, 1, 0, 0, i % 60), entry_price=100.0,
        exit_price=101.0, gross_pnl=1.0, costs=0.0, net_pnl=1.0,
        reason="TAKE_PROFIT", sector="IT", entry_score=70.0,
        reasoning="fixture")


# --------------------------------------------------------------------- M-9

def test_the_store_query_uses_the_indexed_id(fake_db):
    col = _Col([{"_id": "A.NS", "ticker": "A.NS", "candles": []},
                {"_id": "B.NS", "ticker": "B.NS", "candles": []}])
    fake_db(ohlcv=col)
    out = db.history_docs(["A.NS"])
    assert set(out) == {"A.NS"}
    assert "_id" in col.last_filter and "ticker" not in col.last_filter


# ------------------------------------------------------------ M-4 / M-10

def test_archive_is_idempotent(fake_db):
    col = _Col()
    fake_db(trades=col)
    rows = [_trade(i).to_dict() for i in range(3)]
    assert db.archive_trades(rows)
    assert db.archive_trades(rows)              # a re-run after a crash
    assert len(col.docs) == 3


def test_the_book_is_trimmed_only_after_the_archive_landed(fake_db, monkeypatch):
    import trading.paper as paper
    monkeypatch.setattr(paper, "TRADES_DOC_CAP", 5)
    monkeypatch.setattr(db, "save_portfolio", lambda doc, doc_id="default": None)
    p = state.portfolio()
    saved = list(p.trades)
    try:
        # archive refuses (quota) -> nothing is trimmed, nothing is lost
        col = _Col(fail=True)
        fake_db(trades=col)
        p.trades = [_trade(i) for i in range(8)]
        p.save()
        assert len(p.trades) == 8 and col.docs == []
        # archive works -> the 3 oldest move out, the newest 5 stay
        col.fail = False
        p.save()
        assert len(p.trades) == 5
        assert [d["ticker"] for d in col.docs] == ["T0.NS", "T1.NS", "T2.NS"]
        assert p.trades[0].ticker == "T3.NS"
    finally:
        p.trades = saved
        monkeypatch.setattr(db, "_CONNECTED", False)
        p.save()


def test_the_trades_csv_carries_the_archived_trades(tmp_path):
    from trading.config import Config
    from trading.reports import write_trades_csv
    p = Portfolio.fresh(Config(state_dir=str(tmp_path)))
    p.trades = [_trade(9)]
    path = write_trades_csv(p, archived=[_trade(1).to_dict(), _trade(2).to_dict()])
    lines = open(path).read().splitlines()
    assert [ln.split(",")[0] for ln in lines[1:]] == ["T1.NS", "T2.NS", "T9.NS"]


def test_the_trades_page_says_when_stats_cover_only_the_newest(monkeypatch):
    import trading.paper as paper
    from fastapi.testclient import TestClient
    from web import auth as auth_mod
    from web.main import app
    monkeypatch.setattr(paper, "TRADES_DOC_CAP", 2)
    monkeypatch.setattr(db, "count_archived_trades", lambda: 7)
    p = state.portfolio()
    saved = list(p.trades)
    p.trades = [_trade(1), _trade(2)]
    try:
        c = TestClient(app)
        c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD})
        html = c.get("/trades").text
        assert "trades_archived_marker" in html and "7 older ones" in html
    finally:
        p.trades = saved


# --------------------------------------------------------------------- M-5

def test_the_journal_csv_carries_the_archived_lines(tmp_path):
    from trading.config import Config
    from trading.reports import write_journal_csv
    p = Portfolio.fresh(Config(state_dir=str(tmp_path)))
    p.decision_journal = [{"ts": "2026-10-02T04:00:00", "action": "HOLD"}]
    path = write_journal_csv(
        p, archived=[{"ts": "2026-01-01T04:00:00", "action": "BUY"}])
    rows = open(path).read().splitlines()[1:]
    assert rows[0].startswith("2026-01-01") and rows[1].startswith("2026-10-02")


def test_the_reports_route_reads_both_archives():
    import inspect
    from web.routes import reports_routes
    src = inspect.getsource(reports_routes._generate_all)
    code = "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith("#"))
    assert "db.list_trades()" in code and "db.list_journal_archive()" in code


# --------------------------------------------------------------------- M-3

def test_the_payload_shape_carries_no_numbers(fake_db):
    col = _Col([{"_id": "fii:2026-10-01", "kind": "fii", "day": "2026-10-01",
                 "payload": {"status": "success", "data": [
                     {"date": "2026-10-01", "long": 123456.0, "short": 7.0}]}}])
    fake_db(market_info_raw=col)
    shape = db.market_info_shape("fii")
    assert shape["day"] == "2026-10-01"
    text = repr(shape["shape"])
    assert "123456" not in text and "success" not in text
    assert shape["shape"]["data"]["list_len"] == 1
    assert shape["shape"]["data"]["first"]["long"] == "float"


def test_healthz_publishes_the_shape(monkeypatch):
    from web import health_stats
    monkeypatch.setattr(db, "list_history_meta", lambda: [])
    monkeypatch.setattr(db, "storage_stats", lambda: None)
    monkeypatch.setattr(db, "market_info_shape",
                        lambda k: {"day": "d", "shape": {"k": "str"}})
    health_stats.refresh()
    snap = health_stats.snapshot()
    assert snap["market_info_shape"]["fii"]["shape"] == {"k": "str"}
