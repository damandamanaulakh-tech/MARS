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


def fetch_quotes(
    tickers: list[str],
    upstox_client: Optional[UpstoxClient] = None,
) -> Dict[str, float]:
    """Returns {ticker: last_price} using Upstox if available, else yfinance."""
    if not tickers:
        return {}

    if upstox_client is None or not upstox_client.is_connected():
        return fetch_quotes_yf(tickers)

    # Try Upstox first; collect any tickers it couldn't handle for yfinance fallback
    keymap = to_instrument_keys(tickers)
    out: Dict[str, float] = {}
    missing: list[str] = []

    for ticker in tickers:
        ikey = keymap.get(ticker)
        if not ikey:
            missing.append(ticker)
            continue
        try:
            q = upstox_client.get_quote(ikey)
            if q is None:
                missing.append(ticker)
                continue
            # Upstox payload commonly has 'last_price' or nested 'ohlc.close'
            price = q.get("last_price")
            if price is None and isinstance(q.get("ohlc"), dict):
                price = q["ohlc"].get("close")
            if price is None:
                missing.append(ticker)
                continue
            out[ticker] = float(price)
        except Exception as e:  # noqa: BLE001
            log.warning("Upstox quote failed for %s: %s", ticker, e)
            missing.append(ticker)

    # Fallback for anything Upstox couldn't supply
    if missing:
        yf_fallback = fetch_quotes_yf(missing)
        out.update(yf_fallback)

    return out


def status(upstox_client: Optional[UpstoxClient]) -> dict:
    """Returns which source the router will use right now."""
    if upstox_client is None or not upstox_client.is_connected():
        return {"source": "yfinance", "reason": "no_upstox_token"}
    return {"source": "upstox", "reason": "ok"}
