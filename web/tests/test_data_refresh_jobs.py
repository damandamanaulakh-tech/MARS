"""Node 3 rest (owner order 2026-08-24: "node 3") — the auto-pull jobs for
index daily, constituents and fundamentals.

Pinned here: the index tail is shaped to the committed CSV's own columns
and starts strictly after the newest held date; Mongo-down skips journal
(never a silent no-op); the consumer merges append ONLY past the committed
frame and degrade to exactly the committed data when the store is absent
or the reader fails; the fundamentals overlay reaches load_fundamentals;
the monthly jobs run once per month and journal what they stored.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from trading import data_refresh as dr
from web import state


class _FakeBroker:
    """get_daily_candles → 3 fixed days for any instrument key."""

    def __init__(self):
        self.calls = []

    def get_daily_candles(self, key, from_date, to_date):
        self.calls.append(key)
        idx = pd.to_datetime(["2026-08-10", "2026-08-11", "2026-08-12"])
        return pd.DataFrame({"Open": [1.0, 2.0, 3.0], "High": [1, 2, 3],
                             "Low": [1, 2, 3], "Close": [10.0, 20.0, 30.0],
                             "Volume": [0, 0, 0]}, index=idx)


def _own_journal(p, n0):
    return [e.get("action") for e in p.decision_journal[n0:]]


def test_fetch_index_tail_builds_csv_shaped_rows():
    rows = dr.fetch_index_tail(_FakeBroker(), "2026-08-10")
    # strictly AFTER last_iso — the 08-10 bar must not duplicate the CSV row
    assert [r["date"] for r in rows] == ["2026-08-11", "2026-08-12"]
    for prefix in dr.INDEX_FIELDS.values():
        assert f"{prefix}_close" in rows[0]
    assert rows[0]["nifty_close"] == 20.0


def test_weekly_index_refresh_stores_and_journals(monkeypatch):
    from web import scheduler
    saved = {}
    monkeypatch.setattr("web.db.is_connected", lambda: True)
    monkeypatch.setattr("trading.data.history_broker", lambda: _FakeBroker())
    monkeypatch.setattr("trading.data_refresh.committed_index_last",
                        lambda: "2026-08-07")
    monkeypatch.setattr("trading.data_refresh.stored_index_rows", lambda: [])
    monkeypatch.setattr("web.db.save_index_rows",
                        lambda rows: saved.setdefault("rows", rows) and 0
                        or len(rows))
    p = state.portfolio()
    n0 = len(p.decision_journal)
    scheduler._weekly_index_refresh()
    assert "DATA_REFRESH_INDEX" in _own_journal(p, n0)
    assert [r["date"] for r in saved["rows"]] == [
        "2026-08-10", "2026-08-11", "2026-08-12"]


def test_index_refresh_mongo_down_journals_a_skip(monkeypatch):
    from web import scheduler

    def _boom(*a, **k):
        raise AssertionError("must not fetch with Mongo down")

    monkeypatch.setattr("web.db.is_connected", lambda: False)
    monkeypatch.setattr("trading.data_refresh.fetch_index_tail", _boom)
    p = state.portfolio()
    n0 = len(p.decision_journal)
    scheduler._weekly_index_refresh()
    assert "DATA_REFRESH_INDEX_SKIPPED" in _own_journal(p, n0)


def test_extend_index_df_appends_only_past_committed():
    df = pd.DataFrame({
        "trade_date": pd.to_datetime(["2026-08-06", "2026-08-07"]),
        "nifty_close": [100.0, 101.0]})
    old_reader = dr._index_reader
    dr.set_index_reader(lambda: [
        {"date": "2026-08-07", "nifty_close": 999.0},   # dup — must not add
        {"date": "2026-08-11", "nifty_close": 102.0},
    ])
    try:
        out, n = dr.extend_index_df(df)
        assert n == 1
        assert float(out["nifty_close"].iloc[-1]) == 102.0
        assert len(out) == 3                       # the dup date was refused
    finally:
        dr._index_reader = old_reader


def test_extend_helpers_degrade_when_the_reader_fails():
    """The world before Node 3, exactly — never something new."""
    df = pd.DataFrame({"trade_date": pd.to_datetime(["2026-08-07"]),
                       "nifty_close": [101.0]})
    s = pd.Series([101.0], index=pd.to_datetime(["2026-08-07"]))
    old_reader = dr._index_reader

    def _broken():
        raise RuntimeError("mongo died")

    dr.set_index_reader(_broken)
    try:
        out, n = dr.extend_index_df(df)
        assert n == 0 and len(out) == 1
        s2 = dr.extend_close_series(s, "nifty_close")
        assert list(s2) == [101.0]
    finally:
        dr._index_reader = old_reader


def test_fundamentals_overlay_reaches_load_fundamentals():
    import trading.register_v4 as rv
    old_reader = dr._fund_reader
    dr.set_fundamentals_reader(lambda: [
        {"symbol": "ZZTEST", "field": "annualNetIncome",
         "as_of": "2026-03-31", "value": 42.0}])
    rv.clear_fundamentals_cache()
    try:
        fund = rv.load_fundamentals("ZZTEST")
        assert fund["annualNetIncome"]["2026-03-31"] == 42.0
        # the committed CSV's own symbols must still be there
        assert rv.load_fundamentals("360ONE")
    finally:
        dr._fund_reader = old_reader
        rv.clear_fundamentals_cache()


def test_monthly_constituents_runs_once_per_month(monkeypatch):
    from web import scheduler
    month = date.today().isoformat()[:7]

    def _boom(*a, **k):
        raise AssertionError("must not refetch inside the month")

    monkeypatch.setattr("web.db.is_connected", lambda: True)
    monkeypatch.setattr(
        "web.db.load_settings",
        lambda doc_id="default": {"constituents_month": month}
        if doc_id == "data_refresh" else {})
    monkeypatch.setattr("trading.data_refresh.fetch_constituents", _boom)
    scheduler._monthly_constituents_snapshot()   # no raise = guard held


def test_monthly_fundamentals_stores_and_journals(monkeypatch):
    from web import scheduler
    stored, marks, cleared = {}, {}, []
    monkeypatch.setattr("web.db.is_connected", lambda: True)
    monkeypatch.setattr("web.db.load_settings",
                        lambda doc_id="default": {})
    monkeypatch.setattr(
        "web.db.save_settings",
        lambda d, doc_id="default": marks.update({doc_id: d}))
    # Owner order 2026-10-12: the step calls the time-budgeted fetcher; this
    # fake answers everything inside the budget (nothing remaining).
    monkeypatch.setattr(
        "trading.data_refresh.fetch_fundamentals_budgeted",
        lambda syms, **k: {"rows": [{"symbol": s, "field": "annualNetIncome",
                                     "as_of": "2026-03-31", "value": 1.0}
                                    for s in syms],
                           "n_ok": len(syms), "attempted": list(syms),
                           "remaining": [], "elapsed_s": 1.0,
                           "stopped": None})
    monkeypatch.setattr("web.db.unset_settings_keys",
                        lambda keys, doc_id="default": None)
    monkeypatch.setattr(
        "web.db.save_fundamentals_rows",
        lambda rows: stored.setdefault("rows", rows) and 0 or len(rows))
    import trading.register_v4 as rv
    monkeypatch.setattr(rv, "clear_fundamentals_cache",
                        lambda: cleared.append(True))
    p = state.portfolio()
    n0 = len(p.decision_journal)
    scheduler._monthly_fundamentals_refresh()
    assert "DATA_REFRESH_FUNDAMENTALS" in _own_journal(p, n0)
    assert stored["rows"] and cleared
    assert marks["data_refresh"]["fundamentals_month"] == \
        date.today().isoformat()[:7]
    # scope = custom universe (+held), bare symbols — no .NS suffixes
    assert all("." not in r["symbol"] or "&" in r["symbol"]
               for r in stored["rows"])


def test_external_index_overlay_reads_the_stored_tail():
    """T12W_0095's index leg (danger layer) must see past the committed
    CSV once the store holds a tail."""
    from trading.external_panels import _load_index_close
    base = _load_index_close()
    assert base is not None
    old_reader = dr._index_reader
    future = (base.index.max() + pd.Timedelta(days=1)).date().isoformat()
    dr.set_index_reader(lambda: [{"date": future, "nifty_close": 12345.0}])
    try:
        s = _load_index_close()
        assert float(s.iloc[-1]) == 12345.0
        assert s.index.max() > base.index.max()
    finally:
        dr._index_reader = old_reader
