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
    provider = utc(provider_at) if provider_at is not None else None
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
    # SEQ-03 is an evidence release, not approval for a new candle-age gate.
    # The existing web cache enforces its own 120-second receipt limit.
    # Provider/receipt ages are exposed for Step 4's completed-bar policy.
    return src == "upstox" or (not live_only and src == "upstox_1min_delayed")
