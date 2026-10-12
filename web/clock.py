"""One IST clock for every screen stamp.

OWNER 2026-10-11, item 37: "real time date issue still not fixed on
dashboard". FOUND: web/routes/dashboard.py built its date from
datetime.utcnow(), and the engine page printed its reading "as of … UTC" —
so between 00:00 and 05:30 IST the dashboard showed YESTERDAY's date, and
every page that stamped a time stamped it five and a half hours early for
an owner who reads in India. Same clock family as the 2026-09-16 exit
drain and the 2026-09-28 startup-catch-up miss, on the display side.

Journal and Mongo timestamps stay UTC inside (they always were); this
module only decides what a HUMAN sees. Templates get `ist_now_str()` as a
Jinja global and `ist` as a filter for stored UTC stamps.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def ist_now() -> datetime:
    return datetime.now(IST)


def ist_now_str(fmt: str = "%a, %d %b %Y · %H:%M IST") -> str:
    return ist_now().strftime(fmt)


def ist_today_iso() -> str:
    """The IST calendar date, for 'today' filters on UTC-stamped journals.
    A UTC filter at 02:00 IST still shows yesterday's lines; this does not."""
    return ist_now().date().isoformat()


def to_ist(value, fmt: str = "%Y-%m-%d %H:%M IST") -> str:
    """Render a stored stamp (naive UTC datetime, aware datetime, or ISO
    string as the journal writes it) in IST. Unknown input -> '—', never a
    guess and never the raw UTC text wearing an IST label."""
    if value is None or value == "":
        return "—"
    dt = value
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return "—"
    if not isinstance(dt, datetime):
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).strftime(fmt)
