"""Analyst-consensus scorer — the analyst_revision factor, live.

Method (validated 2026-07-16 against real data — see TESTED_TONIGHT sheet in
data/SOURCEBORN_STOCK_PARAMETER_MASTER_CLUBBED_v1_1.xlsx): yfinance
`Ticker.info` carries `recommendationMean` (1 = Strong Buy .. 5 = Sell) and
`numberOfAnalystOpinions` for NSE names (31-42 analysts on large caps).

    score = (5 - recommendationMean) / 4 * 100
    RELIANCE 96.1 · TCS 77.0 · CHOLAFIN 76.7 · CGCL 25.0  <- bearish flag

Honest failure ladder — this module never invents a number:
  - backtest mode        -> None (no historical snapshots; caller keeps 50)
  - no coverage / no data-> None
  - fetch error          -> None, logged once; circuit-breaker stops hammering
    a blocked endpoint (Yahoo 429s cloud IPs, so on Render this often stays
    at the neutral-50 fallback — real scores need a box Yahoo answers).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, Optional["AnalystScore"]]] = {}
_CACHE_TTL = 6 * 3600.0          # analyst consensus moves slowly
_FAIL_STREAK = 0
_BREAKER_UNTIL = 0.0             # epoch until which we skip network entirely
_BREAKER_AFTER = 3               # consecutive failures that open the breaker
_BREAKER_SECS = 1800.0
_WARNED = False


@dataclass
class AnalystScore:
    score: float      # 0..100, higher = more bullish consensus
    analysts: int     # numberOfAnalystOpinions
    mean: float       # raw recommendationMean (1..5)


def fetch_analyst_score(ticker: str) -> Optional[AnalystScore]:
    """Consensus score for one ticker, or None when no honest number exists."""
    global _FAIL_STREAK, _BREAKER_UNTIL, _WARNED

    try:
        from trading.data import is_backtest_mode
        if is_backtest_mode():
            return None
    except ImportError:
        pass

    now = time.time()
    hit = _CACHE.get(ticker)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    if now < _BREAKER_UNTIL:
        return None

    result: Optional[AnalystScore] = None
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        mean = info.get("recommendationMean")
        n = int(info.get("numberOfAnalystOpinions") or 0)
        if mean is not None and n > 0:
            mean = float(mean)
            score = max(0.0, min(100.0, (5.0 - mean) / 4.0 * 100.0))
            result = AnalystScore(round(score, 1), n, mean)
        _FAIL_STREAK = 0
        _CACHE[ticker] = (now, result)
    except Exception as e:
        _FAIL_STREAK += 1
        if _FAIL_STREAK >= _BREAKER_AFTER:
            _BREAKER_UNTIL = now + _BREAKER_SECS
            _FAIL_STREAK = 0
        if not _WARNED:
            log.warning(
                "analyst_revision fetch failed (%s: %s) — neutral-50 fallback. "
                "Yahoo blocks cloud IPs, so this is expected on Render.",
                type(e).__name__, str(e)[:80])
            _WARNED = True
    if len(_CACHE) > 600:
        _CACHE.clear()
    return result
