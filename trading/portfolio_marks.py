"""Last observed prices for valuation only; never executable quotes."""
from __future__ import annotations

from datetime import datetime, timezone
import math


def positive_price(value):
    if isinstance(value, bool):
        return None
    try:
        price = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return price if math.isfinite(price) and price > 0 else None


def last_mark(position):
    record = getattr(position, "last_mark", None)
    if not isinstance(record, dict) or positive_price(record.get("price")) is None:
        return {}
    return record


def valuation_price(position, prices=None):
    """Pure read: offered valid price, retained observation, then cost basis."""
    offered = positive_price((prices or {}).get(position.ticker))
    if offered is not None:
        return offered
    retained = positive_price(last_mark(position).get("price"))
    return retained if retained is not None else float(position.entry_price)


def missing_marks(portfolio, prices=None):
    return [t for t, pos in portfolio.positions.items()
            if positive_price((prices or {}).get(t)) is None and not last_mark(pos)]


def remember(position, price, source, now, *, asof=None):
    """Record receipt time separately from provider time (not supplied yet)."""
    price = positive_price(price)
    if price is None:
        return
    when = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now
    position.last_mark = {
        "price": price, "source": source,
        "received_at": when.astimezone(timezone.utc).isoformat(),
        "asof": asof, "provider_time": None,
    }


def _date(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


def collect(portfolio, quotes, history, fallback, now):
    """Retain prices across partial responses without advancing carried dates.

    A daily row has only session precision. It cannot supersede a quote/fill
    received in the same session, and an undated scan fallback cannot replace
    an existing observation. Receipt of a quote is not proof of provider age.
    """
    result, evidence = {}, {}
    for ticker, pos in portfolio.positions.items():
        previous = last_mark(pos)
        source_this_pass = "carried" if previous else "entry_only"
        quote = positive_price((quotes or {}).get(ticker))
        if quote is not None:
            remember(pos, quote, "quote_response", now)
            source_this_pass = "quote_response"
        else:
            frame = (history or {}).get(ticker)
            close, bar_day = None, None
            if frame is not None:
                try:
                    series = frame["Close"].dropna()
                    bar_day = series.index[-1].date()
                    close = positive_price(series.iloc[-1])
                except (KeyError, IndexError, TypeError, ValueError, AttributeError):
                    pass
            entry_day = pos.entry_date.date()
            # Stored history can still contain pre-split per-share units while
            # its refresh is pending. It must not undo the adjusted mark.
            for action in getattr(pos, "ca_applied", []) or []:
                action_day = _date(str(action).partition("|")[2])
                if action_day is not None:
                    entry_day = max(entry_day, action_day)
            old_day = _date(previous.get("asof") or previous.get("received_at"))
            history_newer = (not previous or old_day is None or
                             (bar_day is not None and
                              (bar_day > old_day or
                               (bar_day == old_day and
                                previous.get("source") == "stored_close"))))
            if (close is not None and bar_day is not None
                    and entry_day <= bar_day <= now.date() and history_newer):
                # Identical history read again keeps its first receipt time.
                if not (previous.get("source") == "stored_close"
                        and previous.get("asof") == bar_day.isoformat()
                        and previous.get("price") == close):
                    remember(pos, close, "stored_close", now, asof=bar_day.isoformat())
                source_this_pass = "stored_close"
            elif not previous and frame is None:
                price = positive_price((fallback or {}).get(ticker))
                if price is not None:
                    remember(pos, price, "scan_close_undated", now)
                    source_this_pass = "scan_close_undated"
        record = last_mark(pos)
        if record:
            result[ticker] = float(record["price"])
        evidence[ticker] = {
            "price": valuation_price(pos),
            "basis": source_this_pass,
            "source": record.get("source", "entry_only"),
            "received_at": record.get("received_at"),
            "asof": record.get("asof"),
            "provider_time": record.get("provider_time"),
        }
    portfolio.mark_snapshot = {
        "at": now.isoformat(), "positions": evidence,
        "missing": missing_marks(portfolio, result),
        "quote_count": sum(r["basis"] == "quote_response" for r in evidence.values()),
        "carried_count": sum(r["basis"] == "carried" for r in evidence.values()),
        "complete": not missing_marks(portfolio, result),
        "provider_freshness_verified": False,
    }
    return result
