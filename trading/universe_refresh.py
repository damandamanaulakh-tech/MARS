"""Daily NSE universe auto-refresh.

User ask (2026-07-21): the 2,376 list must update itself every day —
"or else app keep working on that stocks only". This module downloads
Upstox's public instrument master (no login needed), filters the
EQ-series cash equities — the exact rule the committed list was born
from — and atomically rewrites data/upstox_nse_equities.csv, which
load_full_universe() reads. Effect:
  * new listings enter the scan rotation the day Upstox lists them,
  * delisted/suspended names drop out,
  * mis_allowed (intraday-marginable flag) is carried over from the
    previous file for known tickers; NEW tickers default to 0 so a
    fresh listing never sneaks into intraday sizing unvetted.

Memory: the master is ~88k instruments (~25 MB of JSON). A plain
json.load peaks ~180 MB (measured) — dangerous beside the live app on
the 512 MB instance — so parse_eq_rows() stream-decodes one object at
a time (~40 MB peak). If the download or parse fails in ANY way the
old file stays untouched and the caller journals the failure: a stale
universe is safer than an empty one.
"""

from __future__ import annotations

import csv
import gzip
import json
import logging
import os
import tempfile
from datetime import date, datetime

log = logging.getLogger(__name__)

MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"

# The NSE cash market carries ~2,400 EQ-series names. A parse far below
# that means a malformed/partial master — refuse to clobber the good file.
MIN_ROWS = 1500


def default_universe_path() -> str:
    return os.path.join(os.path.dirname(__file__), "..", "data",
                        "upstox_nse_equities.csv")


def fetch_master_text(url: str = MASTER_URL, timeout: float = 120.0) -> str:
    """Download the gz master and return the decompressed JSON text."""
    import httpx
    r = httpx.get(url, timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    return gzip.decompress(r.content).decode("utf-8")


def parse_eq_rows(text: str) -> list[dict]:
    """Stream-decode the master JSON array; keep NSE_EQ segment, EQ series.

    Returns rows shaped like the committed CSV (minus mis_allowed):
    {ticker, trading_symbol, name, isin, instrument_key}, sorted by ticker.
    """
    dec = json.JSONDecoder()
    i = text.index("[") + 1
    n = len(text)
    out = []
    while i < n:
        while i < n and text[i] in ", \t\r\n":
            i += 1
        if i >= n or text[i] == "]":
            break
        obj, i = dec.raw_decode(text, i)
        if (obj.get("segment") == "NSE_EQ"
                and obj.get("instrument_type") == "EQ"
                and obj.get("trading_symbol")):
            out.append({
                "ticker": obj["trading_symbol"] + ".NS",
                "trading_symbol": obj["trading_symbol"],
                "name": obj.get("name", ""),
                "isin": obj.get("isin", ""),
                "instrument_key": obj.get("instrument_key", ""),
            })
    out.sort(key=lambda r: r["ticker"])
    return out


def read_existing(path: str) -> dict[str, dict]:
    """Current file as {ticker: row}; {} if missing/unreadable."""
    try:
        with open(path, newline="") as fh:
            return {row["ticker"]: row for row in csv.DictReader(fh)}
    except OSError:
        return {}


def refresh_universe(path: str = None, url: str = MASTER_URL) -> dict:
    """Fetch → filter → merge mis_allowed → atomic replace. Returns a
    summary dict for journaling. Raises on failure (old file untouched)."""
    path = path or default_universe_path()
    rows = parse_eq_rows(fetch_master_text(url))
    if len(rows) < MIN_ROWS:
        raise ValueError(f"master parse returned only {len(rows)} equities")

    old = read_existing(path)
    for r in rows:
        prev = old.get(r["ticker"])
        r["mis_allowed"] = (prev or {}).get("mis_allowed", "0") or "0"

    fresh = {r["ticker"] for r in rows}
    added = sorted(fresh - set(old)) if old else []
    removed = sorted(set(old) - fresh) if old else []

    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)),
                               suffix=".csv.tmp")
    try:
        with os.fdopen(fd, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=[
                "ticker", "trading_symbol", "name", "isin",
                "instrument_key", "mis_allowed"])
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    summary = {"total": len(rows), "added": len(added), "removed": len(removed),
               "added_names": added[:15], "removed_names": removed[:15],
               "was": len(old)}
    log.info("Universe refreshed: %s", summary)
    return summary


def refreshed_today(path: str = None) -> bool:
    """True if the universe file was already (re)written today — lets the
    boot catch-up skip a duplicate download after the 08:50 job ran."""
    path = path or default_universe_path()
    try:
        return date.fromtimestamp(os.path.getmtime(path)) == datetime.now().date()
    except OSError:
        return False
