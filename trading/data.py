"""Market data loader. yfinance for v1 (~15-min delayed for NSE).

Upstox real-time wiring is a separate module (broker/upstox.py) — to be
added once OAuth handshake is implemented.
"""

from __future__ import annotations

import logging
import os
import threading
from functools import lru_cache
from typing import Dict

log = logging.getLogger(__name__)

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

# AUDIT FIX B12 (2026-08-09): this module kept its own PRIVATE copy of the
# index-key map and it had already drifted — ^CNX200 and ^BSESN were missing,
# which is the structural reason the Nifty-200 backtest benchmark could never
# load ("did the N200 line render?" — it never could). One source of truth
# now: the brokers module's constant.
try:
    from .brokers.upstox_instruments import INDEX_KEYS as _INDEX_KEYS
except ImportError:                                    # pragma: no cover
    _INDEX_KEYS = {
        "^NSEI": "NSE_INDEX|Nifty 50",
        "^NSEBANK": "NSE_INDEX|Nifty Bank",
        "^INDIAVIX": "NSE_INDEX|India VIX",
        "^CNX200": "NSE_INDEX|Nifty 200",
        "^BSESN": "BSE_INDEX|SENSEX",
    }
_IK_MAP: Dict[str, str] | None = None


def set_history_broker(client) -> None:
    """Attach/detach the Upstox client used for historical candles."""
    global _HISTORY_BROKER
    _HISTORY_BROKER = client


def _instrument_key(ticker: str) -> str | None:
    """AUDIT FIX C10 (2026-08-09): this loaded the committed CSV once per
    boot, swallowed OSError into a permanently-EMPTY map (all history routing
    dead, indistinguishable from "not listed"), and never learned new
    listings. It now rides the shared brokers lookup — daily-refreshed, loud
    ERROR + committed-CSV fallback when empty — the same table quotes use."""
    if ticker in _INDEX_KEYS:
        return _INDEX_KEYS[ticker]
    try:
        from .brokers.upstox_instruments import get_lookup
        key = get_lookup().get(ticker)
        if key:
            return key
    except Exception as e:
        log.warning("shared instrument lookup unavailable: %s", e)
    global _IK_MAP
    if _IK_MAP is None:
        import csv
        path = os.path.join(os.path.dirname(__file__), "..", "data",
                            "upstox_nse_equities.csv")
        m: Dict[str, str] = {}
        try:
            with open(path, newline="") as f:
                for row in csv.DictReader(f):
                    m[row["ticker"]] = row["instrument_key"]
        except OSError as e:
            log.error("instrument CSV unreadable — history routing dead: %s", e)
        _IK_MAP = m
        if not m:
            log.error("instrument map EMPTY — every Upstox history fetch "
                      "will fail visibly, not silently")
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


def committed_nifty200(start: str, end: str) -> pd.DataFrame | None:
    """The Nifty 200 benchmark from the COMMITTED data/nifty200_daily.csv
    (2004→2026-08-07, fetched 2026-08-09), clipped to [start, end].

    AUDIT F2 FIX (2026-08-20, owner: "fix F2"): this file sat in the repo
    with ZERO code consumers while every backtest network-fetched ^CNX200 —
    so the honest ruler for a N200-momentum strategy depended on Yahoo not
    rate-limiting a datacenter IP that day. The committed series is now the
    deterministic base; the network is only asked for the tail beyond the
    file's last row (see fetch_daily_range).

    Returns None (never a guess) when the file is unreadable or the window
    doesn't touch it. Volume is set to 0.0 — the source has none, and the
    benchmark maths reads only Close; a NaN column would poison float casts.
    """
    import pathlib
    path = pathlib.Path(__file__).resolve().parents[1] / "data" / "nifty200_daily.csv"
    try:
        raw = pd.read_csv(path, parse_dates=["date"], index_col="date")
    except (OSError, ValueError, KeyError) as e:
        log.warning("committed nifty200 csv unreadable: %s", e)
        return None
    raw = raw.rename(columns={"open": "Open", "high": "High",
                              "low": "Low", "close": "Close"})
    raw = raw[(raw.index >= pd.Timestamp(start)) & (raw.index <= pd.Timestamp(end))]
    if raw.empty:
        return None
    raw["Volume"] = 0.0
    return raw[["Open", "High", "Low", "Close", "Volume"]].astype("float32").sort_index()


def fetch_daily_range(tickers: list[str], start: str, end: str) -> Dict[str, pd.DataFrame]:
    """Date-bounded history (backtests). Upstox-first, yfinance fallback.

    ^CNX200 is special-cased (F2 fix): the committed CSV is the base, and the
    network is consulted only for dates past the file's end — so the N200
    benchmark line is deterministic offline for any window the file covers,
    and a dead network merely truncates the tail (which the backtest already
    discloses as BENCHMARK TRUNCATED)."""
    if isinstance(tickers, str):
        tickers = [tickers]
    out: Dict[str, pd.DataFrame] = {}
    if "^CNX200" in tickers:
        base = committed_nifty200(start, end)
        if base is not None:
            tail_from = base.index[-1] + pd.Timedelta(days=1)
            if pd.Timestamp(end) > base.index[-1]:
                # Ask the network for the tail only; a failure keeps the base.
                # The recursion terminates because the tail window starts
                # after the CSV's last row, so committed_nifty200 returns
                # None on re-entry and the call falls through to the
                # broker/yfinance path.
                tail = fetch_daily_range(["^CNX200"],
                                         tail_from.date().isoformat(), end)
                t = tail.get("^CNX200")
                if t is not None and not t.empty:
                    base = pd.concat([base, t])
                    base = base[~base.index.duplicated(keep="last")].sort_index()
            out["^CNX200"] = base
            tickers = [t for t in tickers if t != "^CNX200"]
            if not tickers:
                return out
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
    """Latest available close for many tickers in one call.

    AUDIT FIX B6 (2026-08-09): the Upstox daily-candle branch is GONE. It
    returned Close.iloc[-1] of a 7-day candle fetch — intraday that is
    YESTERDAY'S close, up to 7 days old across holidays — in the same dict as
    real LTPs, indistinguishable to every caller, and the marks cache then
    stamped it fresh. A missing quote must be ABSENT, not stale-dressed-as-
    live: the router's callers already handle absence honestly (entry-mark
    labels, EXIT_HELD_NO_QUOTE). The yfinance path below stays for local dev;
    from Render it is blocked and returns nothing, which is the truth.
    """
    out0: Dict[str, float] = {}
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


# AUDIT FIX A8 (2026-08-09): this flag was a process-wide global. The web
# /backtest button runs run_backtest in a background thread of the SAME
# process as the APScheduler jobs, so a backtest running through the 09:20
# scan flipped every live snapshot feeder to None/neutral mid-scan — the
# morning's picks were scored by a silently different strategy with no
# journal trace. threading.local() scopes the flag to the backtest's own
# thread; live scans in other threads are untouched. (Scripts are
# single-threaded, so their behavior is identical.)
_BT_LOCAL = threading.local()


def set_backtest_mode(enabled: bool) -> None:
    """Toggle backtest mode FOR THE CALLING THREAD ONLY.

    When True, all snapshot-based feeders (earnings, news, analyst
    revisions) return neutral / None so they cannot leak future data
    into historical replay. The backtester sets this around run_backtest.
    """
    _BT_LOCAL.on = bool(enabled)


def is_backtest_mode() -> bool:
    return bool(getattr(_BT_LOCAL, "on", False))


@lru_cache(maxsize=512)
def fetch_market_cap_cr(ticker: str) -> float | None:
    """Market cap in ₹ Crores (1 Cr = 1e7). Cached for the session.

    yfinance reports mcap in INR for .NS tickers, so we just divide by 1e7.
    Returns None in backtest mode (no historical snapshot — leak risk).
    """
    if is_backtest_mode():
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
    if is_backtest_mode():
        return None
    try:
        info = yf.Ticker(ticker).info
        g = info.get("earningsQuarterlyGrowth")
        return float(g) * 100 if g is not None else None
    except Exception:
        return None
