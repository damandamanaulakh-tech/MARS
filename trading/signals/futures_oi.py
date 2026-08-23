"""Short-interest proxy from futures open interest — Node 7 of the audit
work plan (owner order 2026-08-23: "start fixing ur pending and flaws one
by one").

THE FLAW THIS CLOSES (audit F6/Node 7): the AM07 composite's
`short_interest` component was a CONSTANT 50 — 5% of the swing and
positional score was a number that never moved, and both of those modes run
in the daily rotation and can buy. NSE serves no free short-interest feed,
but the committed F&O open-interest panel (data/fo_oi_daily_2024_2026.csv,
278 symbols) carries the honest cousin: futures OI read against price.

THE PROXY, and it is LABELLED a proxy: true short interest (borrowed-share
ratio) does not exist for NSE cash equities at ₹0. What futures OI gives:
  * OI RISING while price FALLS  = short build-up   → bearish → LOW score
  * OI FALLING while price RISES = short covering   → constructive → HIGH
  * OI RISING with price RISING  = long build-up    → mildly constructive
  * OI FALLING with price FALLING= long unwinding   → mildly bearish
Classic F&O read, computed over a 5-session window, scaled by the size of
the OI move (10% OI change = full tilt, capped).

HONESTY RULES:
  * STALENESS: the panel is a committed file that refreshes only when the
    owner's Colab run lands (or a future live chain job). A reading older
    than MAX_AGE_DAYS returns the NEUTRAL 50 — a 16-day-old OI signature
    must not wear today's confidence. The component self-activates
    whenever fresh data arrives, with no code change.
  * COVERAGE: only ~278 names trade F&O. Everything else returns neutral
    50 — absence of a derivatives market is not information about shorts.
  * NEVER raises: any failure returns 50.0, exactly the old stub value, so
    the failure mode of this fix is the world before it.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_CSV = Path(__file__).resolve().parents[2] / "data" / "fo_oi_daily_2024_2026.csv"

#: Sessions over which OI and price direction are compared. ASSUMED (the
#: classic build-up read uses ~a week); registry defines no window.
WINDOW = 5
#: OI %-change treated as a full-strength move. ASSUMED, capped scaling.
FULL_TILT_OI_PCT = 0.10
#: A panel older than this returns neutral — stale OI is not today's shorts.
MAX_AGE_DAYS = 10

_cache: Optional[dict] = None      # {symbol: [(iso_date, fut_oi), ...] tail}
_cache_last: str = ""


def _load() -> dict:
    """One pass over the committed panel; keep only each symbol's tail."""
    global _cache, _cache_last
    if _cache is not None:
        return _cache
    import csv
    tails: dict[str, list] = {}
    last = ""
    try:
        with open(_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                d, s = row.get("date", ""), row.get("symbol", "")
                try:
                    oi = float(row.get("fut_oi") or 0)
                except ValueError:
                    continue
                if not d or not s or oi <= 0:
                    continue
                tails.setdefault(s, []).append((d, oi))
                if d > last:
                    last = d
    except OSError as e:
        log.warning("futures OI panel unreadable: %s", e)
        _cache, _cache_last = {}, ""
        return _cache
    for s in tails:
        tails[s] = sorted(tails[s])[-(WINDOW + 5):]
    _cache, _cache_last = tails, last
    return _cache


def panel_asof() -> str:
    """Newest date in the committed panel ('' when unreadable)."""
    _load()
    return _cache_last


def score_short_interest(ticker: str, close, today: Optional[date] = None) -> float:
    """0-100 component score. 50 = neutral (non-F&O name, stale panel, or
    any failure — the exact value the old stub always returned).

    `close` is the price series the scorer already holds; its own last
    WINDOW sessions give the price direction so the two legs of the read
    come from the same era.
    """
    try:
        sym = ticker.split(".")[0].upper()
        tails = _load()
        rows = tails.get(sym)
        if not rows or len(rows) < WINDOW + 1:
            return 50.0
        newest = date.fromisoformat(rows[-1][0])
        if (today or date.today()) - newest > timedelta(days=MAX_AGE_DAYS):
            return 50.0                      # stale OI ≠ today's shorts
        oi_now, oi_then = rows[-1][1], rows[-1 - WINDOW][1]
        if oi_then <= 0:
            return 50.0
        oi_chg = oi_now / oi_then - 1.0
        c = close.dropna()
        if len(c) < WINDOW + 1:
            return 50.0
        px_chg = float(c.iloc[-1]) / float(c.iloc[-1 - WINDOW]) - 1.0
        # Strength: 10% OI move = full tilt (25 points); capped.
        tilt = max(-1.0, min(1.0, oi_chg / FULL_TILT_OI_PCT)) * 25.0
        if px_chg < 0:
            # falling price: rising OI = short build-up (bad), falling OI =
            # long unwinding (mildly bad — sellers, not shorts)
            return round(50.0 - abs(tilt) if oi_chg > 0 else 50.0 - abs(tilt) / 3, 1)
        # rising price: falling OI = short covering (good), rising OI =
        # long build-up (mildly good)
        return round(50.0 + abs(tilt) if oi_chg < 0 else 50.0 + abs(tilt) / 3, 1)
    except Exception as e:                                   # noqa: BLE001
        log.warning("short-interest proxy failed for %s: %s", ticker, e)
        return 50.0
