"""Market data loader. yfinance for v1 (~15-min delayed for NSE).

Upstox real-time wiring is a separate module (broker/upstox.py) — to be
added once OAuth handshake is implemented.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Dict

import pandas as pd
import yfinance as yf


def fetch_daily(tickers: list[str], period: str = "6mo") -> Dict[str, pd.DataFrame]:
    """{ticker: daily OHLCV DataFrame} for the requested period."""
    if isinstance(tickers, str):
        tickers = [tickers]
    raw = yf.download(
        tickers, period=period, interval="1d",
        progress=False, auto_adjust=True, group_by="ticker", threads=True,
    )
    out: Dict[str, pd.DataFrame] = {}
    for t in tickers:
        if isinstance(raw.columns, pd.MultiIndex):
            if t not in raw.columns.get_level_values(0):
                continue
            df = raw[t]
        else:
            df = raw
        if "Close" not in df.columns or df.empty:
            continue
        df = df.dropna(subset=["Close"]).copy()
        if not df.empty:
            out[t] = df
    return out


def fetch_quote(ticker: str) -> float | None:
    """Latest available close price for a single ticker (delayed ~15 min on NSE)."""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="2d", interval="1d", auto_adjust=True)
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception:
        return None


def fetch_quotes(tickers: list[str]) -> Dict[str, float]:
    """Latest available close for many tickers in one call."""
    try:
        raw = yf.download(
            tickers, period="2d", interval="1d",
            progress=False, auto_adjust=True, group_by="ticker", threads=True,
        )
    except Exception:
        return {}
    out: Dict[str, float] = {}
    for t in tickers:
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                if t not in raw.columns.get_level_values(0):
                    continue
                close = raw[t]["Close"].dropna()
            else:
                close = raw["Close"].dropna()
            if not close.empty:
                out[t] = float(close.iloc[-1])
        except (KeyError, AttributeError):
            continue
    return out


_BACKTEST_MODE = False


def set_backtest_mode(enabled: bool) -> None:
    """Toggle backtest mode.

    When True, all snapshot-based feeders (earnings, news, analyst
    revisions) return neutral / None so they cannot leak future data
    into historical replay. The backtester sets this around run_backtest.
    """
    global _BACKTEST_MODE
    _BACKTEST_MODE = bool(enabled)


def is_backtest_mode() -> bool:
    return _BACKTEST_MODE


@lru_cache(maxsize=512)
def fetch_market_cap_cr(ticker: str) -> float | None:
    """Market cap in ₹ Crores (1 Cr = 1e7). Cached for the session.

    yfinance reports mcap in INR for .NS tickers, so we just divide by 1e7.
    Returns None in backtest mode (no historical snapshot — leak risk).
    """
    if _BACKTEST_MODE:
        return None
    try:
        info = yf.Ticker(ticker).info
        mc = info.get("marketCap")
        return float(mc) / 1e7 if mc else None
    except Exception:
        return None


def fetch_earnings_growth(ticker: str) -> float | None:
    """Latest quarterly earnings growth YoY%, if yfinance has it. Flaky.

    In backtest mode returns None — yfinance.info has no historical
    snapshot, so using it during replay would leak future data.
    """
    if _BACKTEST_MODE:
        return None
    try:
        info = yf.Ticker(ticker).info
        g = info.get("earningsQuarterlyGrowth")
        return float(g) * 100 if g is not None else None
    except Exception:
        return None
