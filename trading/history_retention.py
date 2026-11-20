"""Exact, owner-approved closed-history removals; never a rolling date purge.

Selection is offline. Runtime only honors the explicit fingerprints recorded
after an export and restore check. Open positions and later exits are untouched.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import re
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')


def record(trade):
    return trade if isinstance(trade, dict) else trade.to_dict()


def fingerprint(trade) -> str:
    d = record(trade)
    key = '|'.join(str(d.get(k)) for k in ('ticker', 'entry_date', 'exit_date', 'qty', 'reason'))
    return sha256(key.encode()).hexdigest()


def approved_ids(policy: dict | None) -> set[str]:
    if not policy:
        return set()
    ids = policy.get('removed_fingerprints')
    if policy.get('version') != 1 or not isinstance(ids, list) or len(ids) > 100000:
        raise ValueError('closed_history_policy_invalid')
    if any(not isinstance(k, str) or not re.fullmatch(r'[0-9a-f]{64}', k) for k in ids):
        raise ValueError('closed_history_fingerprint_invalid')
    return set(ids)


def retain(trades, policy):
    ids = approved_ids(policy)
    return [t for t in trades if fingerprint(t) not in ids] if ids else list(trades)


def ist_time(value):
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)  # engine's documented naive UTC ledger
        return dt.astimezone(IST)
    except (AttributeError, TypeError, ValueError):
        return None


def selection_reason(trade, *, entry_before: datetime, intraday_closed_before: datetime):
    """None means retain, unknown means hold for review; partial closes are rows."""
    d = record(trade)
    entry, exited = ist_time(d.get('entry_date')), ist_time(d.get('exit_date'))
    if not entry or not exited or exited < entry:
        return 'unknown_dates'
    if entry < entry_before:
        return 'entry_through_2026_09_30'
    # Legacy missing modes are unknown; never guess intraday from duration.
    if d.get('mode') == 'intraday' and exited < intraday_closed_before:
        return 'closed_intraday_through_2026_11_17'
    return None
