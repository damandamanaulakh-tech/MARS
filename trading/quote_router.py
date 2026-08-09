"""Quote router: choose between Upstox (real-time) and yfinance (delayed).

When an Upstox access token is provided, route quote requests through the
Upstox REST quotes endpoint. Falls back to yfinance silently if:
  - No token is provided
  - Instrument key is unknown for that ticker
  - Upstox API returns an error or times out

This module is the single seam between the engine and the data source.
Engine code calls `fetch_quotes(tickers, upstox_client=...)` and doesn't
care which source produced the price.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

from .brokers.upstox import UpstoxClient
from .brokers.upstox_instruments import to_instrument_keys
from .data import fetch_quotes as fetch_quotes_yf


log = logging.getLogger(__name__)


# Codex review on PR#40: per-ticker provenance so an export can never label
# a yfinance daily close as "live". Updated on every fetch; read-only for
# callers.
LAST_SOURCES: dict = {}


def fetch_quotes(
    tickers: list[str],
    upstox_client: Optional[UpstoxClient] = None,
) -> Dict[str, float]:
    """Returns {ticker: last_price} using Upstox if available, else yfinance."""
    if not tickers:
        return {}

    if upstox_client is None or not upstox_client.is_connected():
        return fetch_quotes_yf(tickers)

    # BATCHED (2026-07-31): one HTTP call per 250 instruments instead of one
    # per stock. A 43-name book previously fired 43 sequential requests, slow
    # enough to time out and look like "no live prices".
    keymap = to_instrument_keys(tickers)
    out: Dict[str, float] = {}
    missing: list[str] = [t for t in tickers if t not in keymap]

    keys = [keymap[t] for t in tickers if t in keymap]
    prices_by_key: Dict[str, float] = {}
    if keys:
        try:
            prices_by_key = upstox_client.get_ltp(keys) or {}
        except Exception as e:  # noqa: BLE001
            log.warning("Upstox batch LTP failed (%d keys): %s", len(keys), e)

    for ticker in tickers:
        ikey = keymap.get(ticker)
        if not ikey:
            continue
        px = prices_by_key.get(ikey)
        if px and px > 0:
            out[ticker] = float(px)
            LAST_SOURCES[ticker] = "upstox"
        else:
            missing.append(ticker)

    # Fallback for anything Upstox couldn't supply
    if missing:
        yf_fallback = fetch_quotes_yf(missing)
        out.update(yf_fallback)
        for t in yf_fallback:
            LAST_SOURCES[t] = "yf_close"     # daily close, NOT a live tick

    return out


def status(upstox_client: Optional[UpstoxClient]) -> dict:
    """Returns which source the router will use right now, plus a health flag.

    FIX (2026-07-12): datacenter Yahoo is 429-blocked, so 'yfinance' really
    means 'degraded — prices may be stale/empty and scans can starve'. Surface
    that loudly + show hours until the Upstox token expires (03:30 IST daily)."""
    if upstox_client is None or not upstox_client.is_connected():
        return {
            "source": "yfinance", "reason": "no_upstox_token", "healthy": False,
            "hours_left": None,
            "message": "Upstox not connected — live prices unavailable and "
                       "scans fall back to blocked Yahoo. Reconnect Upstox.",
        }
    hours = None
    try:
        hours = upstox_client.hours_until_expiry()
    except Exception:
        pass
    if hours is not None and hours <= 0:
        return {
            "source": "yfinance", "reason": "token_expired", "healthy": False,
            "hours_left": 0,
            "message": "Upstox token EXPIRED (they die 03:30 IST daily). "
                       "Reconnect now or today's scan uses blocked Yahoo.",
        }
    return {
        "source": "upstox", "reason": "ok", "healthy": True,
        "hours_left": round(hours, 1) if hours is not None else None,
        "message": "Upstox live" + (f" · token ~{hours:.0f}h left" if hours else ""),
    }
