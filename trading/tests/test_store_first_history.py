"""The scan reads the OHLCV store first (owner fault report 2026-09-11).

"why system still stucking at 49 and rest of things when its decided that
1200 is the count why new stocks n not listed in that yet and why its follow
old".

ROOT CAUSE these tests pin: `fetch_daily` fetched EVERY name from the Upstox
network, one request at a time, and never touched the Mongo OHLCV store.
Measured live at 1.77s per name, times five modes in the 09:20 rotation:
~7 minutes for 49 names, ~50 minutes for 340, ~3 HOURS for 1,200. So the
universe could not grow past a few dozen names — and filling the store would
not have helped, because nothing read it.
"""
from __future__ import annotations

import pandas as pd
import pytest

from trading import data as d


def _doc(ticker: str, end: pd.Timestamp, n: int = 300) -> dict:
    idx = pd.date_range(end=end, periods=n, freq="D")
    return {"ticker": ticker, "end": end.strftime("%Y-%m-%d"),
            "candles": [[i.strftime("%Y-%m-%d"), 100.0, 101.0, 99.0, 100.5,
                         1_000_000.0] for i in idx]}


@pytest.fixture
def clean_data(monkeypatch):
    """Leave the module's injected globals exactly as they were."""
    old_store, old_broker = d.history_store(), d.history_broker()
    yield
    d.set_history_store(old_store)
    d.set_history_broker(old_broker)


def test_a_fresh_stored_name_is_served_without_any_network(clean_data):
    today = pd.Timestamp.today().normalize()
    store = {"TCS.NS": _doc("TCS.NS", today - pd.Timedelta(days=1))}
    d.set_history_store(lambda ts, rows: {t: store[t] for t in ts if t in store})

    called = []
    d.set_history_broker(type("B", (), {
        "get_daily_candles": lambda self, *a: called.append(a)})())

    out = d.fetch_daily(["TCS.NS"], period="2y")
    assert "TCS.NS" in out and not out["TCS.NS"].empty
    assert called == [], "a fresh stored name must not hit the network"
    assert d.LAST_FETCH_STATS["from_store"] == 1
    # the frame must be production-shaped (the 2026-08-09 lowercase lesson)
    assert list(out["TCS.NS"].columns) == ["Open", "High", "Low", "Close", "Volume"]


def test_a_stale_stored_name_falls_through_to_the_network(clean_data):
    """Stale is not "good enough" — it costs a fetch, never a wrong score."""
    today = pd.Timestamp.today().normalize()
    old = today - pd.Timedelta(days=d.STORE_MAX_AGE_DAYS + 3)
    store = {"OLD.NS": _doc("OLD.NS", old)}
    d.set_history_store(lambda ts, rows: {t: store[t] for t in ts if t in store})
    d.set_history_broker(None)

    d.fetch_daily(["OLD.NS"], period="2y")
    assert d.LAST_FETCH_STATS["from_store"] == 0, "a stale doc must not be used"


def test_the_network_fallback_is_bounded_so_the_scan_window_closes(clean_data,
                                                                   monkeypatch):
    """Without this, the first scan after the universe grows would try to
    fetch every missing name and run for an hour inside the 09:20 slot."""
    d.set_history_store(lambda ts, rows: {})          # store holds nothing
    seen = {}

    def fake_upstox(tickers, period):
        seen["n"] = len(tickers)
        return {}
    monkeypatch.setattr(d, "_fetch_daily_upstox", fake_upstox)
    monkeypatch.setattr(d, "yf", type("Y", (), {
        "download": staticmethod(lambda *a, **k: pd.DataFrame())})())

    names = [f"N{i}.NS" for i in range(d.MAX_NETWORK_FALLBACK + 75)]
    d.fetch_daily(names, period="2y")
    assert seen["n"] == d.MAX_NETWORK_FALLBACK
    assert d.LAST_FETCH_STATS["not_fetched_cap"] == 75


def test_names_the_scan_could_not_cover_are_reported_not_hidden(clean_data,
                                                               monkeypatch):
    """A 1,200-name universe reading as fully scanned when 170 names were
    seen is the lying-screen fault class."""
    d.set_history_store(lambda ts, rows: {})
    monkeypatch.setattr(d, "_fetch_daily_upstox", lambda t, p: {})
    monkeypatch.setattr(d, "yf", type("Y", (), {
        "download": staticmethod(lambda *a, **k: pd.DataFrame())})())

    d.fetch_daily(["A.NS", "B.NS"], period="2y")
    st = d.LAST_FETCH_STATS
    assert st["asked"] == 2 and st["covered"] == 0
    assert set(st["uncovered"]) == {"A.NS", "B.NS"}


def test_a_backtest_never_reads_the_store(clean_data, monkeypatch):
    """The store holds TODAY's bars; a backtest as-of a past date must never
    see them."""
    today = pd.Timestamp.today().normalize()
    store = {"X.NS": _doc("X.NS", today)}
    d.set_history_store(lambda ts, rows: {t: store[t] for t in ts if t in store})
    monkeypatch.setattr(d, "is_backtest_mode", lambda: True)
    monkeypatch.setattr(d, "_fetch_daily_upstox", lambda t, p: {})
    monkeypatch.setattr(d, "yf", type("Y", (), {
        "download": staticmethod(lambda *a, **k: pd.DataFrame())})())

    d.fetch_daily(["X.NS"], period="2y")
    assert d.LAST_FETCH_STATS["from_store"] == 0


def test_no_reader_behaves_exactly_as_before(clean_data, monkeypatch):
    """Reader unset (or Mongo down) must degrade to the pre-2026-09-11
    world: network for everything, and NO fallback cap, because the cap only
    makes sense when a store exists to cover the rest."""
    d.set_history_store(None)
    seen = {}
    monkeypatch.setattr(d, "_fetch_daily_upstox",
                        lambda t, p: seen.update(n=len(t)) or {})
    monkeypatch.setattr(d, "yf", type("Y", (), {
        "download": staticmethod(lambda *a, **k: pd.DataFrame())})())

    names = [f"N{i}.NS" for i in range(d.MAX_NETWORK_FALLBACK + 40)]
    d.fetch_daily(names, period="2y")
    assert seen["n"] == len(names)
    assert d.LAST_FETCH_STATS["not_fetched_cap"] == 0


def test_a_failing_store_read_does_not_break_the_scan(clean_data, monkeypatch):
    def boom(ts, rows):
        raise RuntimeError("mongo down")
    d.set_history_store(boom)
    monkeypatch.setattr(d, "_fetch_daily_upstox", lambda t, p: {})
    monkeypatch.setattr(d, "yf", type("Y", (), {
        "download": staticmethod(lambda *a, **k: pd.DataFrame())})())
    d.fetch_daily(["A.NS"], period="2y")       # must not raise
    assert d.LAST_FETCH_STATS["from_store"] == 0


def test_the_store_read_is_one_batched_call_not_one_per_name(clean_data):
    """1,200 individual Mongo round-trips would trade one slow path for
    another."""
    today = pd.Timestamp.today().normalize()
    store = {f"T{i}.NS": _doc(f"T{i}.NS", today - pd.Timedelta(days=1))
             for i in range(50)}
    calls = []

    def reader(ts, rows):
        calls.append(len(ts))
        return {t: store[t] for t in ts if t in store}
    d.set_history_store(reader)
    d.set_history_broker(None)
    d.fetch_daily(sorted(store), period="2y")
    assert calls == [50], "one query for the whole list"


# ---------------------------------------------------------------------------
# the bounded, resumable pull
# ---------------------------------------------------------------------------

def test_the_pull_stops_on_its_time_budget_and_says_what_is_left():
    """It needed ~61 uninterrupted minutes for the 2,075 names the store
    lacked, on a box that restarts on every deploy — so it never finished,
    and every job sequenced behind it never ran."""
    import time as _t
    from datetime import date

    from trading.history_store import top_up_history

    class _B:
        def get_daily_candles(self, key, start, end):
            _t.sleep(0.02)
            idx = pd.to_datetime([date.today()])
            return pd.DataFrame({"Open": [1.0], "High": [1.0], "Low": [1.0],
                                 "Close": [1.0], "Volume": [0.0]}, index=idx)

    keys = {f"T{i}.NS": f"k{i}" for i in range(200)}
    saved = []
    summ = top_up_history(_B(), keys, lambda t: None, saved.append,
                          date.today(), time_budget_s=0.15)
    assert summ["stopped"] == "time_budget"
    assert summ["remaining"] > 0
    assert summ["ok"] == len(saved) > 0, "what it fetched must be SAVED"
    assert summ["ok"] + summ["remaining"] + summ["failed"] <= summ["total"]


def test_an_unbounded_pull_still_finishes_everything():
    """The bound is opt-in; callers that pass none keep the old behaviour."""
    from datetime import date

    from trading.history_store import top_up_history

    class _B:
        def get_daily_candles(self, key, start, end):
            idx = pd.to_datetime([date.today()])
            return pd.DataFrame({"Open": [1.0], "High": [1.0], "Low": [1.0],
                                 "Close": [1.0], "Volume": [0.0]}, index=idx)

    keys = {f"T{i}.NS": f"k{i}" for i in range(20)}
    summ = top_up_history(_B(), keys, lambda t: None, lambda d: None,
                          date.today())
    assert summ["remaining"] == 0 and summ["stopped"] == ""
    assert summ["ok"] == 20
