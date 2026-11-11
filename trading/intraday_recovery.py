"""Per-holding square-off deadline and evidence. Engine clocks are UTC."""
from datetime import datetime, time, timezone
import os
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def utc(value):
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None
            else value.astimezone(timezone.utc))


def session_deadline(entry):
    from .modes import PRESETS
    hour, minute = map(int, PRESETS["intraday"].square_off_time.split(":"))
    day = utc(entry).astimezone(IST).date()
    return datetime.combine(day, time(hour, minute), IST).astimezone(timezone.utc)


def deadline(position):
    if getattr(position, "mode", "") != "intraday":
        return None
    derived = session_deadline(position.entry_date)
    record = getattr(position, "intraday_close", None)
    try:
        stored = utc(datetime.fromisoformat(record["deadline"]))
        # A restart or changed entry date must never postpone saved work.
        return min(derived, stored)
    except (KeyError, TypeError, ValueError):
        return derived


def ensure(position, now):
    due = deadline(position)
    if due is None:
        return False
    record = getattr(position, "intraday_close", None)
    if not isinstance(record, dict):
        record = {}
    new = {"deadline": due.isoformat(), "status": "scheduled", "attempts": 0,
           "created_at": utc(now).isoformat(), **record}
    new["deadline"] = due.isoformat()
    if new == record:
        return False
    position.intraday_close = new
    return True


def ensure_book(portfolio, now):
    changed = False
    for pos in portfolio.positions.values():
        changed = ensure(pos, now) or changed
    return changed


def is_due(position, now):
    due = deadline(position)
    return due is not None and utc(now) >= due


def entry_allowed(mode, now):
    # Existing replay/test override; never set in the shipped Render config.
    if mode != "intraday" or os.environ.get("SB_IGNORE_MARKET_HOURS") == "1":
        return True
    return utc(now) < session_deadline(now)


def in_session(now):
    from .calendars import is_market_open, MARKET_OPEN_IST, MARKET_CLOSE_IST
    if os.environ.get("SB_IGNORE_MARKET_HOURS") == "1":
        return True
    instant = utc(now).astimezone(IST)
    # The shared calendar has minute precision; recovery must not fill at
    # 15:30:59 merely because its minute still reads 15:30.
    return (is_market_open(instant)
            and time(*MARKET_OPEN_IST) <= instant.time() <= time(*MARKET_CLOSE_IST))


def view(position, now):
    """Read-only UI: show overdue work even if its scheduled job never ran."""
    due = deadline(position)
    if due is None:
        return {}
    record = getattr(position, "intraday_close", None) or {}
    overdue = utc(now) >= due
    return {"overdue": overdue,
            "deadline": due.astimezone(IST).strftime("%d %b %Y %H:%M IST"),
            "status": record.get("status", "scheduled"),
            "attempts": record.get("attempts", 0),
            "last_attempt": record.get("last_attempt"),
            "last_error": record.get("last_error"),
            "label": ("Closing overdue — retry pending" if overdue
                      else "Closes by " + due.astimezone(IST).strftime("%H:%M IST"))}
