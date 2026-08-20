"""Full-store market breadth — the F3 fix (owner order 2026-08-20: "fix F3").

THE FAULT: the A3 breadth gate blocks new momentum entries when <=50% of the
panel is above its own 200DMA — but live breadth was measured on the scan's
OWN panel, the ~50-name rotating bucket. A 50-name sample of a ~2,400-name
universe carries ±7pp of sampling noise, and the market sat at 50.8% against
a <=50% line — so whether the owner's engine could buy on a given day was
decided by WHICH 50 NAMES the rotation happened to serve, not by the market.
The preregistered study (docs/breadth_gate_results.json) measured breadth on
the full 191-ticker panel; the live gate was reading a different, noisier
number than the one the evidence earned.

THE FIX: measure breadth over the FULL Mongo OHLCV store (~2,700 instruments,
fetched 2026-08-09, refreshed weekly — reliably so since the F1 fix made the
Friday top-up tokenless). One streamed Mongo query with a $slice tail
projection delivers each ticker's last ~200 closes; this module reduces that
stream to one number. Sampling noise at n≈2,700: ~±1pp. The engine's gate
prefers this stored reading while it is fresh and falls back to the bucket
sample only when it is missing or stale — behaviour then degrades to exactly
what it was before this fix, never to something new.

HONESTY RULES:
  * The reading carries `asof` (the newest close it saw) and the gate's
    journal prints it — store closes can be up to a week old between Friday
    top-ups, and a reading must say what day it describes.
  * Names whose own history has gone stale (no close within `dead_days` of
    the store's newest date) are EXCLUDED and counted — a name that stopped
    updating in 2024 would otherwise vote its 2024 state forever.
  * Too thin a sample returns None, never a guess.

This module is pure reduction: no Mongo import, no web import — the caller
(web/scheduler) streams the docs in. engine.py reads only the RESULT stored
on the portfolio, so the money path stays free of any new dependency.
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

log = logging.getLogger(__name__)

#: A name with no close within this many days of the store's newest date is
#: excluded from the sample (delisted / dead symbol). ASSUMED.
DEAD_NAME_DAYS = 30


def store_breadth(docs: Iterable[dict], dma: int = 200,
                  min_sample: int = 200) -> Optional[dict]:
    """Reduce streamed OHLCV tail-docs to one breadth reading.

    Each doc needs `ticker`, `end` (ISO date of its last close) and
    `candles` = the LAST `dma`(+) rows of [date, o, h, l, close, v]. Returns
    {pct, above, sample, asof, dma, excluded_stale, excluded_short} or None
    when fewer than `min_sample` names are usable — a thin store must not
    wear a confident number.
    """
    from datetime import date, timedelta

    rows = []
    newest = None
    for d in docs:
        try:
            end = str(d.get("end") or "")
            candles = d.get("candles") or []
            if not end or len(candles) < dma:
                rows.append((None, None, "short"))
                continue
            closes = [float(c[4]) for c in candles[-dma:]]
            last = closes[-1]
            sma = sum(closes) / float(dma)
            if not (last > 0 and sma > 0):
                rows.append((None, None, "short"))
                continue
            rows.append((end, last > sma, None))
            if newest is None or end > newest:
                newest = end
        except Exception:                                   # noqa: BLE001
            rows.append((None, None, "short"))
    if newest is None:
        return None
    cutoff = (date.fromisoformat(newest)
              - timedelta(days=DEAD_NAME_DAYS)).isoformat()
    above = sample = stale = short = 0
    for end, is_above, why in rows:
        if why == "short":
            short += 1
            continue
        if end < cutoff:
            stale += 1                # dead name: 2024's vote is not a vote
            continue
        sample += 1
        if is_above:
            above += 1
    if sample < min_sample:
        log.warning("store breadth: sample %d < %d — no reading", sample,
                    min_sample)
        return None
    return {
        "pct": round(above / sample, 4),
        "above": above,
        "sample": sample,
        "asof": newest,
        "dma": dma,
        "excluded_stale": stale,
        "excluded_short": short,
    }


def is_fresh(reading: Optional[dict], today, max_age_days: int) -> bool:
    """Whether a stored reading is recent enough for the gate to trust.

    `today` is a date. Past `max_age_days` the gate falls back to the live
    bucket sample — stale-wide loses to fresh-narrow once the gap grows."""
    from datetime import date, timedelta
    if not reading or not reading.get("asof") or reading.get("pct") is None:
        return False
    try:
        asof = date.fromisoformat(str(reading["asof"])[:10])
    except ValueError:
        return False
    return (today - asof) <= timedelta(days=max_age_days)
