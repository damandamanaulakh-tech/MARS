"""Market data loader. yfinance for v1 (~15-min delayed for NSE).

Upstox real-time wiring is a separate module (broker/upstox.py) — to be
added once OAuth handshake is implemented.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Dict

import pandas as pd
import yfinance as yf

# Render 512 MB: threads=True lets yfinance size its pool from the HOST's
# core count (big on shared cloud CPUs) — dozens of parallel downloads each
# holding raw JSON + float64 frames BEFORE our float32 downcast runs. That
# transient spike, x5 modes at 09:20, is an OOM shape. Cap the pool small.
YF_THREADS: int | bool = int(os.environ.get("YF_THREADS", "4"))

# ---------- Upstox-first history routing ----------
# Yahoo 429-blocks datacenter IPs (Render, cloud runners): every scan and
# backtest starves. When the user's Upstox login is attached, ALL daily
# history is pulled from Upstox instead; yfinance stays as the fallback.

_HISTORY_BROKER = None  # UpstoxClient or None; set by web/state on attach

# NSE index keys Upstox understands (yfinance symbols -> instrument keys)
_INDEX_KEYS = {
    "^NSEI": "NSE_INDEX|Nifty 50",
    "^NSEBANK": "NSE_INDEX|Nifty Bank",
    "^INDIAVIX": "NSE_INDEX|India VIX",
}
_IK_MAP: Dict[str, str] | None = None


def set_history_broker(client) -> None:
    """Attach/detach the Upstox client used for historical candles."""
    global _HISTORY_BROKER
    _HISTORY_BROKER = client


def _instrument_key(ticker: str) -> str | None:
    global _IK_MAP
    if ticker in _INDEX_KEYS:
        return _INDEX_KEYS[ticker]
    if _IK_MAP is None:
        import csv
        path = os.path.join(os.path.dirname(__file__), "..", "data",
                            "upstox_nse_equities.csv")
        m: Dict[str, str] = {}
        try:
            with open(path, newline="") as f:
                for row in csv.DictReader(f):
                    m[row["ticker"]] = row["instrument_key"]
        except OSError:
            pass
        _IK_MAP = m
    return _IK_MAP.get(ticker)


def _period_to_from_date(period: str) -> str:
    p = (period or "6mo").strip().lower()
    if p == "max":
        days = 5500
    elif p.endswith("mo"):
        days = int(p[:-2]) * 31
    elif p.endswith("y"):
        days = int(p[:-1]) * 366 + 5
    elif p.endswith("d"):
        days = int(p[:-1])
    else:
        days = 186
    return (pd.Timestamp.today() - pd.Timedelta(days=days)).strftime("%Y-%m-%d")


def _fetch_daily_upstox(tickers: list[str], period: str) -> Dict[str, pd.DataFrame]:
    """History for every ticker Upstox can serve. Missing ones fall through."""
    if _HISTORY_BROKER is None:
        return {}
    from_date = _period_to_from_date(period)
    to_date = pd.Timestamp.today().strftime("%Y-%m-%d")
    out: Dict[str, pd.DataFrame] = {}
    for t in tickers:
        ik = _instrument_key(t)
        if not ik:
            continue
        df = _HISTORY_BROKER.get_daily_candles(ik, from_date, to_date)
        if df is not None and not df.empty:
            out[t] = df
    return out


def fetch_daily(tickers: list[str], period: str = "6mo") -> Dict[str, pd.DataFrame]:
    """{ticker: daily OHLCV DataFrame} for the requested period.

    Upstox-first when a login is attached (Yahoo blocks cloud IPs with 429);
    yfinance only for whatever Upstox couldn't serve."""
    if isinstance(tickers, str):
        tickers = [tickers]
    out: Dict[str, pd.DataFrame] = _fetch_daily_upstox(tickers, period)
    tickers = [t for t in tickers if t not in out]
    if not tickers:
        return out
    raw = yf.download(
        tickers, period=period, interval="1d",
        progress=False, auto_adjust=True, group_by="ticker", threads=YF_THREADS,
    )
    out.update(_parse_yf_multi(raw, tickers))
    del raw
    return out


def _parse_yf_multi(raw, tickers: list[str]) -> Dict[str, pd.DataFrame]:
    """Split a yf.download result into per-ticker float32 frames."""
    out: Dict[str, pd.DataFrame] = {}
    for t in tickers:
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                if t not in raw.columns.get_level_values(0):
                    continue
                df = raw[t]
            else:
                df = raw
            if "Close" not in df.columns or df.empty:
                continue
            df = df.dropna(subset=["Close"]).copy()
            if df.empty:
                continue
            # Render 512 MB: float32 halves the OHLCV panel vs float64 (prices
            # to paise + volume ratios are unaffected at this precision).
            for c in df.columns:
                if str(df[c].dtype) == "float64":
                    df[c] = df[c].astype("float32")
            out[t] = df
        except (KeyError, AttributeError):
            continue
    return out


def fetch_daily_range(tickers: list[str], start: str, end: str) -> Dict[str, pd.DataFrame]:
    """Date-bounded history (backtests). Upstox-first, yfinance fallback."""
    if isinstance(tickers, str):
        tickers = [tickers]
    out: Dict[str, pd.DataFrame] = {}
    if _HISTORY_BROKER is not None:
        for t in tickers:
            ik = _instrument_key(t)
            if not ik:
                continue
            df = _HISTORY_BROKER.get_daily_candles(ik, start, end)
            if df is not None and not df.empty:
                out[t] = df
    rest = [t for t in tickers if t not in out]
    if not rest:
        return out
    try:
        raw = yf.download(
            rest, start=start, end=end, interval="1d",
            progress=False, auto_adjust=True, group_by="ticker", threads=YF_THREADS,
        )
    except Exception:
        return out
    out.update(_parse_yf_multi(raw, rest))
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
    out0: Dict[str, float] = {}
    if _HISTORY_BROKER is not None:
        for t, df in _fetch_daily_upstox(tickers, "7d").items():
            out0[t] = float(df["Close"].iloc[-1])
        tickers = [t for t in tickers if t not in out0]
        if not tickers:
            return out0
    try:
        raw = yf.download(
            tickers, period="2d", interval="1d",
            progress=False, auto_adjust=True, group_by="ticker", threads=YF_THREADS,
        )
    except Exception:
        return out0
    out: Dict[str, float] = dict(out0)
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
