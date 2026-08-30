"""Calendar overlay tests — no network."""

from datetime import date

from trading.calendars import (
    CalendarEvent, fno_monthly_expiry,
    is_system_event_within, is_ticker_event_within,
    upcoming_events,
)


def test_fno_expiry_is_last_tuesday():
    """AUDIT FIX B10: NSE F&O expiry moved to TUESDAY (SEBI expiry-day
    standardization, ~Sep 2025). Feb 2026's last Tuesday is 2026-02-24."""
    d = fno_monthly_expiry(2026, 2)
    assert d.weekday() == 1
    assert d == date(2026, 2, 24)


def test_fno_expiry_december_rollover():
    d = fno_monthly_expiry(2026, 12)
    assert d.weekday() == 1
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
        # Anything here must be system-wide, not a per-ticker event
        assert ev.impact == "system_wide"


def test_the_usfda_calendar_is_gone_entirely():
    """The shipped USFDA entries were invented "Examples" that BLOCKED real
    entries (A5, 2026-08-09: DRREDDY was blackout-blocked on a made-up
    Aug-22 event). They were emptied then, and on the owner's order of
    2026-08-30 ("forget about FDA as well") the whole mechanism is deleted —
    a stronger guarantee than an empty dict, since there is no longer a
    container for a fabricated date to be dropped into.
    """
    from trading import calendars as cal
    assert not hasattr(cal, "USFDA_DECISIONS_2026")
    assert is_ticker_event_within("DRREDDY.NS", days=5,
                                  today=date(2026, 8, 20)) is None
    # the per-ticker gate itself still works, on earnings
    for ev in cal.upcoming_events(today=date(2026, 1, 1), lookahead_days=400):
        assert ev.event_type != "usfda"


def test_is_ticker_event_returns_none_for_unaffected():
    ev = is_ticker_event_within("RELIANCE.NS", days=5, today=date(2026, 7, 12))
    assert ev is None


def test_calendar_event_days_until():
    ev = CalendarEvent("test", date(2026, 1, 10), "test", "system_wide")
    assert ev.days_until(today=date(2026, 1, 1)) == 9
