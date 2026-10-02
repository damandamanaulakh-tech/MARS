"""Market data loader. yfinance for v1 (~15-min delayed for NSE).

Upstox real-time wiring is a separate module (broker/upstox.py) — to be
added once OAuth handshake is implemented.
"""

from __future__ import annotations

import logging
import os
import threading
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

# ---------- store-first history (owner fault report 2026-09-11) -------------
# "why system still stucking at 49 ... when its decided that 1200 is the
# count". THE ROOT CAUSE, measured: `fetch_daily` fetched EVERY name from the
# Upstox network, one request at a time, and never read the Mongo OHLCV store
# at all. At 1.77s per name (measured live) and five modes in the 09:20
# rotation that is ~7 minutes for the owner's 49 names, ~50 minutes for 340,
# and ~3 HOURS for 1,200. The universe could not grow past a few dozen names
# no matter how full the store got — filling the store did not even help,
# because nothing read it. Memory was never the constraint either: 1,200
# two-year frames measure +15 MB, 2,648 measure +32 MB on a 512 MB box.
#
# So the store is consulted FIRST and the network only covers what the store
# lacks or holds stale. trading/ must not import web.db, so the reader is
# INJECTED at boot — the same pattern flows.set_store_reader and
# data_refresh use. Reader missing or failing = the exact pre-2026-09-11
# behaviour, network for everything.
_HISTORY_STORE = None

# A stored frame is used when its last bar is no older than this many
# CALENDAR days. ASSUMED, and chosen for the calendar rather than measured:
# the scan runs at 09:20 before the close, so the newest bar it can ever
# want is yesterday's; a Monday morning after a long weekend is 4 days
# behind. 5 gives that one day of slack. A name staler than this is fetched,
# so the tolerance can only cost network time, never correctness.
STORE_MAX_AGE_DAYS = 5

# How many names ONE fetch_daily call may fall back to the network for.
# Without this, the first scan after the universe grows would try to fetch
# every missing name and run for an hour inside the 09:20 window. Bounded,
# the scan always finishes; the names it could not cover are REPORTED, never
# silently dropped, and the off-hours top-up closes the gap.
MAX_NETWORK_FALLBACK = 120

# What the last fetch_daily actually did — read by the engine so the scan
# journal can state its own coverage instead of implying it saw everything.
LAST_FETCH_STATS: Dict[str, object] = {}

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


def set_history_store(reader) -> None:
    """Inject the batch OHLCV-store reader: (tickers, rows) -> {ticker: doc}.

    web/state wires db.history_docs at boot. trading/ never imports web.db.
    """
    global _HISTORY_STORE
    _HISTORY_STORE = reader


def history_store():
    return _HISTORY_STORE


def _from_store(tickers: list[str], period: str) -> Dict[str, pd.DataFrame]:
    """Frames the STORE can serve, fresh enough to scan on.

    A stale doc is left out rather than returned — the caller then fetches
    it, so "stale" costs a network call and never a wrong score. A doc that
    cannot be rebuilt into a frame is skipped the same way.
    """
    if _HISTORY_STORE is None or not tickers:
        return {}
    if is_backtest_mode():
        # The store holds today's bars; a backtest as-of a past date must
        # never see them. Backtests use fetch_daily_range, but the guard
        # stands so a future caller cannot leak the future in through here.
        return {}
    from .history_store import records_to_df
    from_ts = pd.Timestamp(_period_to_from_date(period))
    # One bar per calendar day at most; ask for the window plus slack so a
    # long history is trimmed server-side instead of shipped whole.
    rows = int((pd.Timestamp.today() - from_ts).days) + 10
    try:
        docs = _HISTORY_STORE(list(tickers), rows) or {}
    except Exception as e:
        log.warning("history store read failed — network for all: %s", e)
        return {}
    cutoff = (pd.Timestamp.today()
              - pd.Timedelta(days=STORE_MAX_AGE_DAYS)).strftime("%Y-%m-%d")
    out: Dict[str, pd.DataFrame] = {}
    for t, doc in docs.items():
        end = str((doc or {}).get("end") or "")
        if not end or end < cutoff:
            continue                      # stale -> let the network serve it
        try:
            df = records_to_df(doc)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        df = df[df.index >= from_ts]
        if df.empty:
            continue
        out[t] = df
    return out


def history_broker():
    """The client history fetches route through — attached with or WITHOUT
    a login token since 2026-08-20 (candles are a public endpoint; a dead
    bearer is ignored, verified live). The top-up jobs read this instead of
    engine.broker so a login-less morning still refreshes the store."""
    return _HISTORY_BROKER


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

    ORDER since 2026-09-11 (owner: "why system still stucking at 49"):
      1. the Mongo OHLCV store, for every name it holds fresh enough — ONE
         batched query, no network, milliseconds;
      2. Upstox for what the store lacks or holds stale, BOUNDED to
         MAX_NETWORK_FALLBACK names so the 09:20 window always closes;
      3. yfinance for whatever Upstox could not serve (429-blocked on
         Render, so this is a last resort, not a plan).

    Names beyond the fallback bound are LEFT OUT and counted in
    LAST_FETCH_STATS — the caller reports them. Returning a half-covered
    universe silently would be the lying-screen fault class: the scan would
    look like it had seen 1,200 names when it had seen 170.
    """
    global LAST_FETCH_STATS
    if isinstance(tickers, str):
        tickers = [tickers]
    asked = list(dict.fromkeys(tickers))

    out: Dict[str, pd.DataFrame] = _from_store(asked, period)
    from_store = len(out)

    missing = [t for t in asked if t not in out]
    capped = 0
    if missing and _HISTORY_STORE is not None and len(missing) > MAX_NETWORK_FALLBACK:
        capped = len(missing) - MAX_NETWORK_FALLBACK
        missing = missing[:MAX_NETWORK_FALLBACK]

    from_upstox = from_yahoo = 0
    if missing:
        up = _fetch_daily_upstox(missing, period)
        from_upstox = len(up)
        out.update(up)
        rest = [t for t in missing if t not in up]
        if rest:
            raw = yf.download(
                rest, period=period, interval="1d", progress=False,
                auto_adjust=True, group_by="ticker", threads=YF_THREADS,
            )
            yh = _parse_yf_multi(raw, rest)
            from_yahoo = len(yh)
            out.update(yh)
            del raw

    LAST_FETCH_STATS = {
        "asked": len(asked), "covered": len(out),
        "from_store": from_store, "from_upstox": from_upstox,
        "from_yahoo": from_yahoo, "not_fetched_cap": capped,
        "uncovered": [t for t in asked if t not in out][:50],
    }
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


# AUDIT S-15 (2026-10-02): Yahoo `.info` calls had NO timeout. The live exit
# pass scores every swing/short/positional position on the composite, which
# asks Yahoo for the analyst rating and the earnings growth of each held name;
# from this sandbox both calls hang indefinitely, and if Yahoo ever hangs
# instead of refusing on Render the 30-minute exit pass stalls with them.
# Every `.info` call now runs through run_with_timeout; a timeout is a failure
# like any other (neutral-50 fallback, counted by the circuit breaker).
YAHOO_INFO_TIMEOUT_S = 8.0
_INFO_POOL = None


def run_with_timeout(fn, timeout: float = YAHOO_INFO_TIMEOUT_S):
    """Run fn() on a small worker pool and wait at most `timeout` seconds.
    Raises TimeoutError past it. A hung call keeps its worker (Python cannot
    kill a thread), which is why callers also keep a circuit breaker: after a
    few failures nothing new is submitted at all."""
    global _INFO_POOL
    if _INFO_POOL is None:
        from concurrent.futures import ThreadPoolExecutor
        _INFO_POOL = ThreadPoolExecutor(max_workers=4,
                                        thread_name_prefix="yahoo-info")
    from concurrent.futures import TimeoutError as _FutTimeout
    fut = _INFO_POOL.submit(fn)
    try:
        return fut.result(timeout=timeout)
    except _FutTimeout:
        fut.cancel()
        raise TimeoutError(f"Yahoo info call exceeded {timeout:.0f}s")


_EG_CACHE: dict = {}
_EG_TTL = 6 * 3600.0
_EG_FAILS = 0
_EG_BREAKER_UNTIL = 0.0
_EG_BREAKER_AFTER = 3
_EG_BREAKER_SECS = 1800.0


def fetch_earnings_growth(ticker: str) -> float | None:
    """Latest quarterly earnings growth YoY%, if yfinance has it. Flaky.

    In backtest mode returns None — yfinance.info has no historical
    snapshot, so using it during replay would leak future data.
    """
    global _EG_FAILS, _EG_BREAKER_UNTIL
    if is_backtest_mode():
        return None
    import time as _time
    now = _time.time()
    hit = _EG_CACHE.get(ticker)
    if hit and now - hit[0] < _EG_TTL:
        return hit[1]
    if now < _EG_BREAKER_UNTIL:
        return None
    try:
        info = run_with_timeout(lambda: yf.Ticker(ticker).info) or {}
        g = info.get("earningsQuarterlyGrowth")
        out = float(g) * 100 if g is not None else None
        _EG_FAILS = 0
        _EG_CACHE[ticker] = (now, out)
        if len(_EG_CACHE) > 600:
            _EG_CACHE.clear()
        return out
    except Exception:
        _EG_FAILS += 1
        if _EG_FAILS >= _EG_BREAKER_AFTER:
            _EG_BREAKER_UNTIL = now + _EG_BREAKER_SECS
            _EG_FAILS = 0
        return None
