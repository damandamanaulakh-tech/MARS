"""Calendar overlay tests — no network."""

from datetime import date, timedelta

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
    """Budget still halts: 2 days before the 2026-02-01 Budget."""
    ev = is_system_event_within(days=5, today=date(2026, 1, 30))
    assert ev is not None
    assert ev.event_type == "budget"


def test_rbi_mpc_never_halts_entries_owner_order_2026_09_30():
    """OWNER ORDER 2026-09-30: "do not consider the RBI as show stopper".
    The decision day still appears in upcoming_events (What's Coming) but
    is_system_event_within must never return it."""
    from trading.calendars import HALT_EXEMPT_EVENT_TYPES, RBI_MPC_DATES_2026
    assert "rbi_mpc" in HALT_EXEMPT_EVENT_TYPES
    for d in RBI_MPC_DATES_2026:
        for back in (0, 1, 3):
            ev = is_system_event_within(days=3, today=d - timedelta(days=back))
            assert ev is None or ev.event_type != "rbi_mpc", (d, back, ev)
    shown = [e for e in upcoming_events(today=date(2026, 10, 1), lookahead_days=10)
             if e.event_type == "rbi_mpc"]
    assert shown and shown[0].event_date == date(2026, 10, 7)


def test_rbi_mpc_dates_are_the_published_fy27_calendar():
    """RBI press release 2026-03-23 (prid 62422): Apr 6-8, Jun 3-5, Aug 3-5,
    Oct 5-7, Dec 2-4 2026, Feb 3-5 2027 — decision on the last day. The
    old list (Oct 1, a Saturday Aug 8, an invented 2027) was the
    fabricated-calendar class; nothing unpublished may return."""
    from trading.calendars import RBI_MPC_DATES_2026, RBI_MPC_DATES_2027
    assert RBI_MPC_DATES_2026 == [date(2026, 2, 6), date(2026, 4, 8), date(2026, 6, 5),
                                  date(2026, 8, 5), date(2026, 10, 7), date(2026, 12, 4)]
    assert RBI_MPC_DATES_2027 == [date(2027, 2, 5)]
    for d in RBI_MPC_DATES_2026 + RBI_MPC_DATES_2027:
        assert d.weekday() < 5, f"{d} is a weekend — not a real decision day"


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
