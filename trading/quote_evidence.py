"""Prices and per-call provenance travel together, never via a shared last fetch."""
from copy import deepcopy
from datetime import datetime, timezone
import math


class QuoteBatch(dict):
    def __init__(self, *args, evidence=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.evidence = deepcopy(evidence or {})


def utc(value=None):
    if value is None:
        return datetime.now(timezone.utc)
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def make(price, source, received=None, provider_at=None, interval=None, **extra):
    received = utc(received) or utc()
    # A naive provider timestamp has no proved exchange timezone.
    try:
        raw = provider_at if isinstance(provider_at, datetime) else datetime.fromisoformat(str(provider_at).replace('Z', '+00:00'))
        provider = utc(raw) if raw.tzinfo is not None else None
    except (ValueError, TypeError):
        provider = None
    return {"price": price, "source": source, "received_at": received.isoformat(),
            "provider_at": provider.isoformat() if provider else None,
            "provider_time_status": "observed" if provider else "unavailable",
            "interval": interval, **extra}


def observation(batch, ticker, now=None):
    ev = (getattr(batch, "evidence", None) or {}).get(ticker)
    if ev:
        out = deepcopy(ev)
        clock = utc(now) or utc()
        for field, label in (("received_at", "receipt_age_seconds"), ("provider_at", "provider_age_seconds")):
            when = utc(out.get(field)) if out.get(field) else None
            out[label] = (clock - when).total_seconds() if when else None
        if out.get('source') == 'upstox_1min_delayed':
            from .intraday_bars import bar_status
            reason = (bar_status(out.get('provider_at'), 1, clock)
                      if out.get('interval') == '1m' else 'wrong_or_unproved_interval')
            out['bar_validity'] = reason or 'completed_current_session'
            out['execution_eligible'] = reason is None
        return out
    # A caller-supplied price has no proved provider or provider time.
    return make((batch or {}).get(ticker), "unknown", now,
                status="unavailable", reason="Caller supplied no quote provenance")


def merge(target, batch):
    target.update(batch)
    target.evidence.update(deepcopy(getattr(batch, "evidence", {}) or {}))


def select(batch, predicate):
    values = {t: px for t, px in (batch or {}).items() if predicate(t, px)}
    return QuoteBatch(values, evidence=batch.evidence) if isinstance(batch, QuoteBatch) else values


def executable(batch, ticker, now=None, *, live_only=False):
    """Known delayed/closing data is never relabelled as a real-time tick.

    Plain mappings retain the legacy injected-adapter contract, explicitly
    unknown in evidence. All production router batches carry metadata.
    """
    price = (batch or {}).get(ticker)
    if isinstance(price, bool):
        return False
    try:
        if not math.isfinite(float(price)) or float(price) <= 0:
            return False
    except (TypeError, ValueError):
        return False
    if not isinstance(batch, QuoteBatch):
        return not live_only
    ev = observation(batch, ticker, now)
    src = ev.get("source")
    # SEQ-04: revalidate against the execution clock, including cached quotes.
    return src == "upstox" or (not live_only and src == "upstox_1min_delayed"
                              and ev.get('execution_eligible') is True)
