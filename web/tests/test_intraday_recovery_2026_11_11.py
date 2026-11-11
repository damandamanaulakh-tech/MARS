"""SEQ-02: startup/minute retries, Mongo durability and read-only visibility."""
from copy import deepcopy
from datetime import datetime, timedelta
from threading import Event, Thread
from zoneinfo import ZoneInfo

import pytest

from trading.intraday_recovery import ensure_book
from trading.paper import Portfolio
from trading.tests.test_intraday_recovery_2026_11_11 import book, ENTRY, DUE
from web import db, scheduler, state
from web.tests.test_category_budgets_page_2026_10_04 import _client


@pytest.fixture
def active_book(book, monkeypatch):
    e, clock = book
    monkeypatch.setattr(state, "engine", lambda: e)
    monkeypatch.setattr(state, "portfolio", lambda: e.portfolio)
    monkeypatch.setattr(state, "cfg", lambda: e.cfg)
    monkeypatch.setattr(state, "marks", lambda *a, **k: ({}, False))
    return e, clock


def test_mongo_round_trip_preserves_pending_deadline_and_protection(active_book, monkeypatch):
    e, _ = active_book
    stored = {}
    state._patch_portfolio_save_to_mirror_mongo()
    monkeypatch.setattr(db, "is_connected", lambda: True)
    monkeypatch.setattr(db, "save_portfolio", lambda doc: stored.update(deepcopy(doc)) or True)
    e.square_off_intraday(prices={})
    restored = Portfolio.fresh(e.cfg)
    state._hydrate_portfolio_from_mongo(restored, stored)
    a, b = e.portfolio.positions["DAY.NS"], restored.positions["DAY.NS"]
    assert a.intraday_close == b.intraday_close
    assert (b.mode, b.stop_price, b.target_price) == ("intraday", 98.5, 104.)
    assert b.intraday_close["status"] == "waiting_quote"


def test_positions_show_overdue_deadline_without_mutating_the_book(active_book, monkeypatch):
    e, _ = active_book
    e.square_off_intraday(prices={})
    before = deepcopy(e.portfolio.positions["DAY.NS"].to_dict())
    journal = deepcopy(e.portfolio.decision_journal)
    response = _client().get("/positions")
    assert response.status_code == 200
    assert "Closing overdue" in response.text
    assert "11 Nov 2026 15:25 IST" in response.text
    assert 'data-intraday-close="waiting_quote"' in response.text
    assert "original protection retained" in response.text
    assert e.portfolio.positions["DAY.NS"].to_dict() == before
    assert e.portfolio.decision_journal == journal


def test_legacy_deadline_enrollment_is_idempotent_and_does_not_trade(active_book):
    e, _ = active_book
    assert ensure_book(e.portfolio, ENTRY)
    e.portfolio.save()
    p = Portfolio.load_or_fresh(e.cfg)
    assert not ensure_book(p, DUE + timedelta(days=1))
    assert set(p.positions) == {"DAY.NS", "HOLD.NS"}
    assert not p.trades


@pytest.mark.parametrize("daily,intraday", [("1", "0"), ("0", "1")])
def test_retry_and_aware_startup_are_registered_independently_of_scan_catchup(active_book, monkeypatch, daily, intraday):
    monkeypatch.setenv("DAILY_SCHEDULER", daily)
    monkeypatch.setenv("INTRADAY_SCHEDULER", intraday)
    monkeypatch.setenv("SB_DISABLE_CATCHUP", "1")
    monkeypatch.setattr(scheduler, "_SCHED", None)
    scheduler.start_if_enabled()
    sched = scheduler._SCHED
    assert not sched.running
    job = sched.get_job("intraday_close_recovery")
    start = sched.get_job("intraday_close_startup")
    assert job and start and start.trigger.run_date.tzinfo is not None
    assert start.kwargs == {"on_startup": True}
    late = datetime(2026, 11, 11, 15, 26, tzinfo=ZoneInfo("Asia/Kolkata"))
    first = job.trigger.get_next_fire_time(None, late)
    second = job.trigger.get_next_fire_time(first, first)
    assert first == late and second-first == timedelta(minutes=1)


def test_restart_after_close_saves_pending_without_a_fabricated_fill(active_book, monkeypatch):
    e, clock = active_book
    clock[0] = datetime(2026, 11, 11, 10, 5)
    monkeypatch.setattr("trading.engine.routed_fetch_quotes", lambda *a, **k: pytest.fail("outside-session quote fetch"))
    scheduler._intraday_recovery_pass(on_startup=True)
    assert not e.portfolio.trades
    assert e.portfolio.positions["DAY.NS"].intraday_close["status"] == "waiting_market"


def test_busy_scan_defers_then_retry_closes_once(active_book, monkeypatch):
    from trading.modes import MODE_LOCK
    e, _ = active_book
    ensure_book(e.portfolio, ENTRY)
    monkeypatch.setattr("trading.calendars.is_market_open", lambda *a: True)
    acquired, release = Event(), Event()
    def hold():
        with MODE_LOCK:
            acquired.set()
            release.wait(3)
    worker = Thread(target=hold)
    worker.start()
    assert acquired.wait(2)
    try:
        scheduler._intraday_recovery_pass()
        assert "DAY.NS" in e.portfolio.positions
        assert not e.portfolio.trades
    finally:
        release.set()
        worker.join(2)
    scheduler._intraday_recovery_pass()
    scheduler._intraday_recovery_pass()
    assert [t.ticker for t in e.portfolio.trades] == ["DAY.NS"]


def test_owner_buy_receives_deadline_reason_without_mutation(active_book, monkeypatch):
    e, _ = active_book
    monkeypatch.setattr("web.market_gate.trading_blocked", lambda cfg: None)
    monkeypatch.setattr("trading.quote_router.fetch_quotes", lambda *a, **k: {"LATE.NS": 100.})
    monkeypatch.setattr("trading.intraday_recovery.entry_allowed", lambda mode, now: mode != "intraday")
    before = (e.portfolio.cash, len(e.portfolio.decision_journal))
    response = _client().post("/scan/buy", data={"ticker": "LATE", "qty": "10", "mode": "intraday"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/scan?error=intraday_entry_deadline"
    assert "LATE.NS" not in e.portfolio.positions
    assert (e.portfolio.cash, len(e.portfolio.decision_journal)) == before
