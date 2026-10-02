"""Durable OHLCV history store — the 15-year-proof unlock.

The engine already routes live/history through Upstox (data.py). This module
does a ONE-TIME bulk pull of many years of daily candles per ticker via the
user's logged-in Upstox token, and persists it to Mongo so it survives Render
restarts and feeds walk_forward() without re-downloading.

Why in-app (not from a Claude session): the Upstox token lives in the user's
app DB, tied to their OAuth. Only the running app — where the user is logged
in — can authenticate as them. So the download runs as a background job the
user triggers from /backtest, using the already-wired get_daily_candles.

Depth: Upstox daily candles reach back ~10-15y for currently-listed names.
Honest limit: only TODAY's listings (survivorship). NSE bhavcopy archives can
add delisted names later to kill that bias.
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Callable, Optional

import pandas as pd

log = logging.getLogger(__name__)


def download_ticker_history(broker, instrument_key: str, years: int,
                            today: date) -> Optional[pd.DataFrame]:
    """Pull `years` of daily candles for one instrument, paginating 365d at a
    time (Upstox caps the range per call). Returns a deduped ascending frame."""
    if broker is None or not instrument_key:
        return None
    frames = []
    end = today
    for _ in range(years):
        start = end - timedelta(days=366)
        # AUDIT FIX C8 (2026-08-09): a transient network failure used to be
        # indistinguishable from "ran past the listing's start" — one failed
        # window silently truncated the saved history, or (if the FIRST
        # window failed) returned older data missing the latest year, served
        # as complete. One retry; a second failure RAISES so the caller
        # counts this ticker as failed instead of saving a hole.
        try:
            df = broker.get_daily_candles(
                instrument_key, start.isoformat(), end.isoformat())
        except Exception:
            df = broker.get_daily_candles(
                instrument_key, start.isoformat(), end.isoformat())
        if df is not None and not df.empty:
            frames.append(df)
        elif frames:
            break                       # ran past the listing's start
        end = start - timedelta(days=1)
    if not frames:
        return None
    out = pd.concat(frames)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def download_history(broker, ticker_to_key: dict[str, str], years: int,
                     today: date, save_one: Callable[[str, pd.DataFrame], None] = None,
                     progress: Callable[[int, int, str], None] = None) -> dict:
    """Bulk pull for a whole universe. `ticker_to_key` maps yfinance-style
    tickers to Upstox instrument keys (from data/upstox_nse_equities.csv).
    Calls save_one(ticker, df) per success so the caller persists incrementally
    (keeps RAM low on 512 MB). Returns a summary."""
    total = len(ticker_to_key)
    ok = rows = 0
    done = 0
    for ticker, key in ticker_to_key.items():
        done += 1
        try:
            df = download_ticker_history(broker, key, years, today)
        except Exception as e:
            log.warning("history download failed for %s: %s", ticker, e)
            df = None
        if df is not None and not df.empty:
            ok += 1
            rows += len(df)
            if save_one is not None:
                try:
                    save_one(ticker, df)
                except Exception as e:
                    log.warning("history save failed for %s: %s", ticker, e)
        if progress is not None:
            progress(done, total, ticker)
    return {"tickers_ok": ok, "tickers_total": total, "rows": rows,
            "years": years}


def df_to_records(ticker: str, df: pd.DataFrame) -> dict:
    """Compact Mongo doc for one ticker's history (float32 rounded)."""
    recs = [
        [ts.strftime("%Y-%m-%d"), round(float(r.Open), 2), round(float(r.High), 2),
         round(float(r.Low), 2), round(float(r.Close), 2), float(r.Volume)]
        for ts, r in df.iterrows()
    ]
    return {"_id": ticker, "ticker": ticker, "candles": recs,
            "n": len(recs),
            "start": recs[0][0] if recs else None,
            "end": recs[-1][0] if recs else None}


def records_to_df(doc: dict) -> Optional[pd.DataFrame]:
    """Rebuild an OHLCV frame from a stored doc."""
    if not doc or not doc.get("candles"):
        return None
    df = pd.DataFrame(doc["candles"],
                      columns=["ts", "Open", "High", "Low", "Close", "Volume"])
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.set_index("ts").sort_index()
    for c in df.columns:
        df[c] = df[c].astype("float32")
    return df


# Fewer stored bars than this = the doc is still too short for the momentum
# score's 252-bar look-back plus a margin, so the next refresh pulls two years.
SHORT_DOC_BARS = 300


def top_up_history(broker, ticker_to_key: dict[str, str],
                   load_doc: Callable[[str], Optional[dict]],
                   save_doc: Callable[[dict], None],
                   today: date, fresh_within_days: int = 4,
                   max_fetches: int = 0,
                   time_budget_s: float = 0.0) -> dict:
    """Weekly freshness pass (owner order 2026-07-27: "renewed every week").

    For each ticker, download only the RECENT candles (1 paginated year is
    plenty for a weekly gap) and MERGE them into the stored doc — never
    replace it. A naive weekly re-save with years=1 would silently truncate
    the 12-year research store to 12 months; this merge is the reason this
    helper exists. Skips tickers already fresh (stored end >= today −
    `fresh_within_days`; 4 is the weekly job's Friday-to-Monday tolerance,
    the daily custom-universe pass passes 1 — owner order 2026-08-20 "all
    fresh").
    """
    ok = skipped = failed = 0
    remaining = 0
    fresh_cutoff = (today - timedelta(days=fresh_within_days)).isoformat()
    started = time.monotonic()
    stopped = ""
    items = list(ticker_to_key.items())
    for i, (ticker, key) in enumerate(items):
        # BOUNDED RUNS (2026-09-11, owner: "why system still stucking at 49").
        # The pull needed ~61 uninterrupted minutes for the 2,075 names the
        # store lacked, on a box that restarts on every deploy — so it never
        # finished, and everything sequenced behind it never ran. A bounded
        # run always FINISHES: it saves each name as it goes, so the next run
        # skips what is already fresh and carries on from there. Progress
        # becomes incremental instead of all-or-nothing.
        if max_fetches and ok + failed >= max_fetches:
            stopped, remaining = "fetch_cap", len(items) - i
            break
        if time_budget_s and (time.monotonic() - started) >= time_budget_s:
            stopped, remaining = "time_budget", len(items) - i
            break
        try:
            doc = load_doc(ticker)
            if doc and doc.get("end") and doc["end"] >= fresh_cutoff:
                skipped += 1
                continue
            # AUDIT S-10 (2026-10-02): a NEW name used to get one 366-day
            # window (~248 bars) while the momentum score needs 252, so it
            # sat out of the momentum pass until weeks of top-ups had
            # accumulated. A new or still-short doc gets two windows; an
            # established one keeps the cheap one-window refresh.
            short = (not doc) or len(doc.get("candles") or []) < SHORT_DOC_BARS
            new = download_ticker_history(broker, key,
                                          years=2 if short else 1, today=today)
            if new is None or new.empty:
                failed += 1
                continue
            old = records_to_df(doc) if doc else None
            merged = new if old is None else pd.concat([old, new])
            merged = merged[~merged.index.duplicated(keep="last")].sort_index()
            save_doc(df_to_records(ticker, merged))
            ok += 1
        except Exception as e:
            log.warning("weekly top-up failed for %s: %s", ticker, e)
            failed += 1
    return {"ok": ok, "skipped_fresh": skipped, "failed": failed,
            "total": len(ticker_to_key), "remaining": remaining,
            "stopped": stopped,
            "elapsed_s": round(time.monotonic() - started, 1)}
