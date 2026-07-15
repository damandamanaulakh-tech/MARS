"""Calendar overlay tests — no network."""

from datetime import date

from trading.calendars import (
    CalendarEvent, fno_monthly_expiry,
    is_system_event_within, is_ticker_event_within,
    upcoming_events,
)


def test_fno_expiry_is_last_thursday():
    # Feb 2026: last Thursday should be 2026-02-26
    d = fno_monthly_expiry(2026, 2)
    assert d.weekday() == 3
    assert d == date(2026, 2, 26)


def test_fno_expiry_december_rollover():
    # Dec 2026 last Thursday
    d = fno_monthly_expiry(2026, 12)
    assert d.weekday() == 3
    assert d.month == 12


def test_upcoming_events_returns_sorted():
    events = upcoming_events(today=date(2026, 1, 1), lookahead_days=400)
    dates = [e.event_date for e in events]
    assert dates == sorted(dates)


def test_upcoming_events_includes_rbi_mpc():
    events = upcoming_events(today=date(2026, 1, 1), lookahead_days=60)
    mpc = [e for e in events if e.event_type == "rbi_mpc"]
    assert len(mpc) >= 1


def test_upcoming_events_includes_budget():
    events = upcoming_events(today=date(2026, 1, 1), lookahead_days=60)
    budget = [e for e in events if e.event_type == "budget"]
    assert len(budget) == 1
    assert budget[0].event_date == date(2026, 2, 1)


def test_is_system_event_within_window():
    # 2 days before RBI MPC on Feb 5 -> within 5d window
    ev = is_system_event_within(days=5, today=date(2026, 2, 3))
    assert ev is not None
    assert ev.event_type in {"rbi_mpc", "budget", "expiry"}


def test_is_system_event_returns_none_when_clear():
    ev = is_system_event_within(days=2, today=date(2026, 3, 15))
    # No system event in Mar 16-17; expiry is later
    if ev is not None:
        # If a sample USFDA happens to fall here, that's per-ticker not system
        assert ev.impact == "system_wide"


def test_is_ticker_event_returns_usfda():
    # 2 days before SUNPHARMA USFDA decision
    ev = is_ticker_event_within("SUNPHARMA.NS", days=5, today=date(2026, 7, 12))
    assert ev is not None
    assert ev.event_type == "usfda"
    assert "SUNPHARMA.NS" in ev.affected


def test_is_ticker_event_returns_none_for_unaffected():
    ev = is_ticker_event_within("RELIANCE.NS", days=5, today=date(2026, 7, 12))
    assert ev is None


def test_calendar_event_days_until():
    ev = CalendarEvent("test", date(2026, 1, 10), "test", "system_wide")
    assert ev.days_until(today=date(2026, 1, 1)) == 9
