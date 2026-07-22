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
