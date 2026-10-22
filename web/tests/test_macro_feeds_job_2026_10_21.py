"""Macro feeds job (owner order 2026-10-21, VALFIX batch 2 item 10): the
10-year G-sec yield from Upstox candles, RBI repo + USD/INR from the RBI
home page, stored in Mongo `macro_daily` and read by the valuation chain.

Pinned here: the job stores what it fetched and journals MACRO_FEEDS once;
it is quiet when nothing new landed; a failing leg journals
MACRO_FEEDS_FAILED and never costs the other leg; the RBI leg is not
re-fetched once both of today's readings are held; Mongo down fetches
nothing; the 09:20 job and the 15:35 pass survive a crashing feed; the DB
helpers key rows series|date; and the boot injects the reader.
"""
from __future__ import annotations

from datetime import date

import pytest

from trading import macro_feeds as mf
from web import db, scheduler, state

state.cfg()          # boot BEFORE any fixture patches db (the reader is captured at boot)

TODAY = date(2026, 10, 21)

GSEC = [
    {"series": "gsec10y_yield", "date": "2026-10-16", "value": 6.9615,
     "source": "Upstox NSE candles · 7.54% GS 2036 (IN0020220029) close 103.99 on 2026-10-16",
     "bond": "7.54% GS 2036", "price": 103.99},
    {"series": "gsec10y_yield", "date": "2026-10-19", "value": 6.9276,
     "source": "Upstox NSE candles · 7.54% GS 2036 (IN0020220029) close 104.23 on 2026-10-19",
     "bond": "7.54% GS 2036", "price": 104.23},
]
RBI = [
    {"series": "rbi_repo", "date": "2026-10-21", "value": 5.5, "source": "RBI home page"},
    {"series": "rbi_usdinr", "date": "2026-10-19", "value": 94.7691, "source": "RBI home page"},
]


@pytest.fixture
def env(monkeypatch):
    """A fake store + fake fetchers; returns a dict the test can steer."""
    box = {"stored": [], "saved": [], "gsec": list(GSEC), "rbi": list(RBI),
           "rbi_calls": 0, "gsec_calls": 0}

    def fake_gsec(client, last_iso="", **kw):
        box["gsec_calls"] += 1
        g = box["gsec"]
        if isinstance(g, Exception):
            raise g
        return [r for r in g if not last_iso or r["date"] > last_iso]

    def fake_rbi(timeout=30.0):
        box["rbi_calls"] += 1
        if isinstance(box["rbi"], Exception):
            raise box["rbi"]
        return list(box["rbi"])

    def save(rows):
        box["saved"].extend(rows)
        box["stored"].extend(rows)
        return len(rows)

    monkeypatch.setattr(db, "is_connected", lambda: True)
    monkeypatch.setattr(db, "load_macro_rows", lambda series=None: list(box["stored"]))
    monkeypatch.setattr(db, "save_macro_rows", save)
    monkeypatch.setattr(mf, "fetch_gsec10y", fake_gsec)
    monkeypatch.setattr(mf, "fetch_rbi_home", fake_rbi)
    monkeypatch.setattr(mf, "_today_ist", lambda: TODAY)
    monkeypatch.setattr("trading.data.history_broker", lambda: object())
    return box


def _new_lines(n0):
    return [e for e in state.portfolio().decision_journal[n0:]
            if str(e.get("action", "")).startswith("MACRO_FEEDS")]


def test_the_job_stores_both_legs_and_journals_one_line(env):
    n0 = len(state.portfolio().decision_journal)
    scheduler._macro_feeds_refresh()
    assert {(r["series"], r["date"]) for r in env["saved"]} == {
        ("gsec10y_yield", "2026-10-16"), ("gsec10y_yield", "2026-10-19"),
        ("rbi_repo", "2026-10-21"), ("rbi_usdinr", "2026-10-19")}
    lines = _new_lines(n0)
    assert [e["action"] for e in lines] == ["MACRO_FEEDS"]
    why = lines[0]["why"]
    assert "10-year yield 6.93%" in why and "7.54% GS 2036" in why and "2026-10-19" in why
    assert "RBI repo 5.5 (2026-10-21)" in why and "USD/INR 94.7691 (2026-10-19)" in why
    assert "Display only" in why


def test_quiet_when_current(env):
    scheduler._macro_feeds_refresh()
    n0 = len(state.portfolio().decision_journal)
    saved0 = len(env["saved"])
    scheduler._macro_feeds_refresh()                 # nothing new anywhere
    assert _new_lines(n0) == []
    assert len(env["saved"]) == saved0


def test_the_rbi_page_is_not_read_again_once_both_of_todays_readings_are_held(env):
    env["stored"] = list(GSEC) + [RBI[0], {**RBI[1], "date": "2026-10-21"}]
    scheduler._macro_feeds_refresh()
    assert env["rbi_calls"] == 0


def test_the_rbi_leg_runs_again_until_todays_reference_rate_lands(env):
    scheduler._macro_feeds_refresh()                 # morning: USD/INR still 19 Oct
    env["stored"] = [r for r in env["stored"] if r["series"] != "rbi_usdinr"] + [
        {"series": "rbi_usdinr", "date": "2026-10-20", "value": 94.70, "source": "x"}]
    calls0 = env["rbi_calls"]
    env["rbi"] = [RBI[0], {**RBI[1], "date": "2026-10-21", "value": 94.81}]
    n0 = len(state.portfolio().decision_journal)
    scheduler._macro_feeds_refresh()                 # 15:35: today's rate is out
    assert env["rbi_calls"] == calls0 + 1
    why = _new_lines(n0)[0]["why"]
    assert "USD/INR 94.81 (2026-10-21)" in why and "10-year" not in why


def test_a_crashing_gsec_leg_never_costs_the_rbi_leg(env):
    env["gsec"] = RuntimeError("upstox down")
    n0 = len(state.portfolio().decision_journal)
    scheduler._macro_feeds_refresh()
    assert {r["series"] for r in env["saved"]} == {"rbi_repo", "rbi_usdinr"}
    acts = {e["action"]: e["why"] for e in _new_lines(n0)}
    assert set(acts) == {"MACRO_FEEDS", "MACRO_FEEDS_FAILED"}
    assert "10-year yield crashed: upstox down" in acts["MACRO_FEEDS_FAILED"]


def test_a_failing_rbi_page_never_costs_the_yield(env):
    env["rbi"] = RuntimeError("rbi timeout")
    n0 = len(state.portfolio().decision_journal)
    scheduler._macro_feeds_refresh()
    assert {r["series"] for r in env["saved"]} == {"gsec10y_yield"}
    acts = {e["action"]: e["why"] for e in _new_lines(n0)}
    assert "RBI home page fetch failed: rbi timeout" in acts["MACRO_FEEDS_FAILED"]


def test_an_rbi_page_without_the_values_is_named(env):
    env["rbi"] = [RBI[0]]                             # USD/INR line gone
    n0 = len(state.portfolio().decision_journal)
    scheduler._macro_feeds_refresh()
    acts = {e["action"]: e["why"] for e in _new_lines(n0)}
    assert "answered without rbi_usdinr" in acts["MACRO_FEEDS_FAILED"]


def test_no_candles_and_no_fresh_reading_is_a_failure_not_silence(env):
    env["gsec"] = []
    n0 = len(state.portfolio().decision_journal)
    scheduler._macro_feeds_refresh()
    acts = {e["action"]: e["why"] for e in _new_lines(n0)}
    assert "no Upstox candles for any 9-11-year G-sec" in acts["MACRO_FEEDS_FAILED"]


def test_no_candles_but_a_fresh_stored_yield_stays_quiet(env):
    env["stored"] = list(GSEC) + list(RBI)
    env["gsec"] = []
    n0 = len(state.portfolio().decision_journal)
    scheduler._macro_feeds_refresh()
    assert _new_lines(n0) == []


def test_mongo_down_fetches_nothing(env, monkeypatch):
    monkeypatch.setattr(db, "is_connected", lambda: False)
    n0 = len(state.portfolio().decision_journal)
    scheduler._macro_feeds_refresh()
    assert env["gsec_calls"] == 0 and env["rbi_calls"] == 0
    assert _new_lines(n0) == []


# ------------------------------------------------------------ the two slots
def _boom():
    raise RuntimeError("macro feed exploded")


def test_the_1535_pass_survives_a_crashing_macro_feed(monkeypatch):
    pushed = []
    monkeypatch.setattr("trading.calendars.is_trading_day", lambda *a, **k: True)
    monkeypatch.setattr(state.engine(), "refresh", lambda *a, **k: None)
    monkeypatch.setattr(scheduler, "_weekly_index_refresh", lambda: None)
    monkeypatch.setattr(scheduler, "_sector_index_refresh", lambda: None)
    monkeypatch.setattr(scheduler, "_macro_feeds_refresh", _boom)
    monkeypatch.setattr(scheduler, "_push_phone_alerts", lambda where: pushed.append(where))
    scheduler._eod_refresh()
    assert pushed == ["15:35"]


def test_the_0920_job_survives_a_crashing_macro_feed_and_still_rotates(monkeypatch):
    order = []
    monkeypatch.setattr("trading.calendars.is_trading_day", lambda *a, **k: True)
    monkeypatch.setattr(scheduler, "_degraded_halt", lambda where: False)
    monkeypatch.setattr(scheduler, "_topup_once_today", lambda where: None)
    monkeypatch.setattr(scheduler, "refresh_breadth_store", lambda where: None)
    monkeypatch.setattr(scheduler, "_weekly_index_refresh", lambda: order.append("index"))
    monkeypatch.setattr(scheduler, "_sector_index_refresh", lambda: order.append("sector"))

    def macro():
        order.append("macro")
        raise RuntimeError("macro feed exploded")
    monkeypatch.setattr(scheduler, "_macro_feeds_refresh", macro)
    monkeypatch.setattr(scheduler, "_run_category_rotation",
                        lambda eng, cfg, where: (order.append("rotation"), [])[1])
    monkeypatch.setattr(scheduler, "_check_pending_orders", lambda where: None)
    monkeypatch.setattr(scheduler, "run_trigger_layer", lambda where: order.append("triggers"))
    monkeypatch.setattr(scheduler, "_push_phone_alerts", lambda where: order.append("push"))
    monkeypatch.setattr("web.memory_relief.release", lambda label: None)
    scheduler._daily_multi_mode_scan()
    # reviewer defect 6 (2026-10-21): the feeds run AFTER the rotation — three
    # serial legs with 30 s timeouts each could otherwise hold the scan 4-5 min
    assert order == ["index", "sector", "rotation", "macro", "triggers", "push"]


# ------------------------------------------------------------ DB + reader
class _Res:
    """A pymongo BulkWriteResult's counted fields."""

    def __init__(self, upserted: int, matched: int, acknowledged: bool = True):
        self.upserted_count, self.matched_count = upserted, matched
        self.acknowledged = acknowledged


class _Coll:
    """macro_daily: since the 2026-10-21 review save_macro_rows writes ONE
    unordered bulk_write of ReplaceOne(upsert) per 500 rows (it used to call
    replace_one per row); every call is recorded."""

    def __init__(self):
        self.docs = {}
        self.bulk_calls: list = []

    def bulk_write(self, ops, ordered=True):
        self.bulk_calls.append((len(ops), ordered))
        up = matched = 0
        for op in ops:
            flt, doc = op._filter, op._doc
            if flt["_id"] in self.docs:
                matched += 1
            else:
                up += 1
            self.docs[flt["_id"]] = doc
        return _Res(up, matched)

    def find(self, q):
        rows = [d for d in self.docs.values()
                if all(d.get(k) == v for k, v in q.items())]

        class _C(list):
            def sort(self, key, direction):
                return sorted(self, key=lambda d: d[key])
        return _C(rows)


class _DB:
    def __init__(self):
        self.macro_daily = _Coll()


def test_db_rows_are_keyed_series_and_date(monkeypatch):
    fake = _DB()
    monkeypatch.setattr(db, "_CONNECTED", True)
    monkeypatch.setattr(db, "_DB", fake)
    assert db.save_macro_rows(GSEC + RBI + [{"series": "rbi_repo", "date": "2026-10-21",
                                             "value": 5.5, "source": "re-read"}]) == 5
    assert set(fake.macro_daily.docs) == {
        "gsec10y_yield|2026-10-16", "gsec10y_yield|2026-10-19",
        "rbi_repo|2026-10-21", "rbi_usdinr|2026-10-19"}
    assert fake.macro_daily.docs["rbi_repo|2026-10-21"]["source"] == "re-read"
    assert [r["date"] for r in db.load_macro_rows("gsec10y_yield")] == ["2026-10-16", "2026-10-19"]
    assert len(db.load_macro_rows()) == 4
    monkeypatch.setattr(db, "_CONNECTED", False)
    assert db.load_macro_rows() == [] and db.save_macro_rows(GSEC) == 0


def _rows(n: int, series: str = "fred_sp500") -> list:
    from datetime import timedelta
    d0 = date(2021, 1, 1)
    return [{"series": series, "date": (d0 + timedelta(days=i)).isoformat(), "value": float(i)}
            for i in range(n)]


def test_save_macro_rows_bulk_writes_500_a_time_and_returns_what_was_acknowledged(monkeypatch):
    fake = _DB()
    monkeypatch.setattr(db, "_CONNECTED", True)
    monkeypatch.setattr(db, "_DB", fake)
    rows = _rows(1201) + [{"series": "", "date": "2026-01-01", "value": 1.0}]   # keyless: skipped
    assert db.save_macro_rows(rows) == 1201
    assert fake.macro_daily.bulk_calls == [(500, False), (500, False), (201, False)]
    assert "fred_sp500|2021-01-01" in fake.macro_daily.docs
    assert fake.macro_daily.docs["fred_sp500|2021-01-01"]["_id"] == "fred_sp500|2021-01-01"
    # a re-write of stored days is matched, and matched counts as acknowledged
    assert db.save_macro_rows(_rows(3)) == 3


def test_save_macro_rows_counts_only_what_a_refused_or_broken_write_landed(monkeypatch):
    from pymongo.errors import BulkWriteError
    fake = _DB()
    monkeypatch.setattr(db, "_CONNECTED", True)
    monkeypatch.setattr(db, "_DB", fake)
    calls = []

    def quota(ops, ordered=True):
        calls.append(len(ops))
        if len(calls) == 1:                      # first chunk: 120 landed, the rest refused
            raise BulkWriteError({"nUpserted": 100, "nMatched": 20, "writeErrors": [
                {"index": 120, "code": 8000, "errmsg": "you are over your space quota"}]})
        raise ConnectionError("store gone")      # second chunk: nothing, stop
    fake.macro_daily.bulk_write = quota
    assert db.save_macro_rows(_rows(1100)) == 120
    assert calls == [500, 500]                    # stopped after the broken chunk
    unack = _DB()
    unack.macro_daily.bulk_write = lambda ops, ordered=True: _Res(len(ops), 0, acknowledged=False)
    monkeypatch.setattr(db, "_DB", unack)
    assert db.save_macro_rows(_rows(5)) == 0      # nothing proves an unacknowledged write


def test_the_boot_injects_the_macro_reader():
    state.cfg()                                       # boot has run
    assert mf._reader is db.load_macro_rows
