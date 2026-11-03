"""Read-only realized performance by entry segment, with explicit coverage.

These are raw net-P&L outcomes of retained ClosedTrade records, including
owner exits. They are not the reason-filtered Kelly estimate used for sizing.
Partial exits create their own ClosedTrade record and are counted separately.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

from trading.category_budget import LABELS
from trading.measured_kelly import trade_category


IST = ZoneInfo("Asia/Kolkata")
SEGMENTS = {**LABELS, "ocean": "Ocean"}
PERIODS = {
    "all": "All retained history",
    "30": "Last 30 days",
    "90": "Last 90 days",
    "365": "Last 365 days",
}


def _utc(value) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    # The engine stores naive UTC timestamps. Explicit offsets take priority.
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None
            else value.astimezone(timezone.utc))


def segment_of(trade) -> str:
    """Keep only proven attribution; legacy unknowns have their own row."""
    category = trade_category(trade)
    return category if category in SEGMENTS else "unknown"


def segment_label(trade) -> str:
    return SEGMENTS.get(segment_of(trade), "Unknown / legacy")


def segment_performance(trades: Iterable, period: str = "all", *,
                        now: datetime | None = None) -> dict:
    now = _utc(now) or datetime.now(timezone.utc)
    today = now.astimezone(IST).date()
    period = period if period in PERIODS else "all"
    start = None if period == "all" else today - timedelta(days=int(period) - 1)
    groups = {category: [] for category in SEGMENTS}
    invalid_records = future_exits = 0
    for trade in trades:
        exited = _utc(getattr(trade, "exit_date", None))
        try:
            net = float(trade.net_pnl)
        except (AttributeError, TypeError, ValueError):
            net = float("nan")
        if exited is None or not math.isfinite(net):
            invalid_records += 1
            continue
        if exited > now:
            future_exits += 1
            continue
        day = exited.astimezone(IST).date()
        if start is not None and day < start:
            continue
        entry = _utc(getattr(trade, "entry_date", None))
        hold = ((exited - entry).total_seconds() / 86400
                if entry is not None and exited >= entry else None)
        groups.setdefault(segment_of(trade), []).append((day, net, hold))

    rows = []
    for category, records in groups.items():
        total = len(records)
        wins = sum(net > 0 for _, net, _ in records)
        losses = sum(net < 0 for _, net, _ in records)
        breakevens = total - wins - losses
        days = [day for day, _, _ in records]
        holds = [hold for _, _, hold in records if hold is not None]
        rows.append({
            "category": category,
            "label": SEGMENTS.get(category, "Unknown / legacy"),
            "total": total, "wins": wins, "losses": losses,
            "breakevens": breakevens,
            "win_pct": wins / total * 100 if total else None,
            "loss_pct": losses / total * 100 if total else None,
            "breakeven_pct": breakevens / total * 100 if total else None,
            "net_pnl": sum(net for _, net, _ in records),
            "first_exit": min(days).isoformat() if days else None,
            "last_exit": max(days).isoformat() if days else None,
            "average_hold_days": sum(holds) / len(holds) if holds else None,
            "hold_sample": len(holds),
        })
    first = [r["first_exit"] for r in rows if r["first_exit"]]
    last = [r["last_exit"] for r in rows if r["last_exit"]]
    return {
        "rows": rows, "total": sum(r["total"] for r in rows),
        "period": period, "period_label": PERIODS[period],
        "period_options": PERIODS,
        "window_start": start.isoformat() if start else None,
        "window_end": today.isoformat(),
        "first_exit": min(first) if first else None,
        "last_exit": max(last) if last else None,
        "asof": now.astimezone(IST).strftime("%Y-%m-%d %H:%M IST"),
        "invalid_records": invalid_records, "future_exits": future_exits,
    }
