"""Upstox instrument master file.

Upstox publishes daily-refreshed CSVs of all NSE/BSE-listed instruments
with their canonical instrument_keys (e.g. 'NSE_EQ|INE002A01018' for
RELIANCE). The engine uses yfinance-style tickers ('RELIANCE.NS'); we
need a lookup table to convert.

This module:
  - Downloads the NSE equity CSV from Upstox's public bucket on first use
  - Caches it to disk for 24h (configurable)
  - Builds a {yfinance_ticker -> upstox_instrument_key} dict
  - Survives Mongo / network outages (returns None for unknowns)

Public URL (Upstox docs):
  https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz
  https://assets.upstox.com/market-quote/instruments/exchange/NSE.csv.gz

We use the NSE-only file (smaller, faster).
"""

from __future__ import annotations

import gzip
import io
import logging
import os
import time
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger(__name__)


INSTRUMENT_URL = (
    "https://assets.upstox.com/market-quote/instruments/exchange/NSE.csv.gz"
)
CACHE_DIR = Path(os.environ.get("INSTRUMENT_CACHE_DIR", "/tmp/sourceborn"))
CACHE_FILE = CACHE_DIR / "upstox_nse_instruments.csv"
CACHE_TTL_SECONDS = 24 * 60 * 60  # 24 hours

# In-memory lookup map, built lazily on first call
_LOOKUP: dict[str, str] | None = None
_LOOKUP_BUILT_AT: float = 0.0


def _download_csv() -> Optional[bytes]:
    """Fetch the gzipped CSV from Upstox; gunzip; return raw CSV bytes."""
    try:
        r = httpx.get(INSTRUMENT_URL, timeout=30.0, follow_redirects=True)
        r.raise_for_status()
        return gzip.decompress(r.content)
    except (httpx.HTTPError, OSError, gzip.BadGzipFile) as e:
        log.warning("Upstox instrument download failed: %s", e)
        return None


def _ensure_cached(force: bool = False) -> Optional[bytes]:
    """Read from cache if fresh; otherwise download + refresh cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not force and CACHE_FILE.exists():
        age = time.time() - CACHE_FILE.stat().st_mtime
        if age < CACHE_TTL_SECONDS:
            try:
                return CACHE_FILE.read_bytes()
            except OSError:
                pass
    raw = _download_csv()
    if raw is None:
        # Fall back to whatever's on disk even if stale
        if CACHE_FILE.exists():
            try:
                return CACHE_FILE.read_bytes()
            except OSError:
                return None
        return None
    try:
        CACHE_FILE.write_bytes(raw)
    except OSError as e:
        log.warning("Could not write instrument cache to %s: %s", CACHE_FILE, e)
    return raw


def _build_lookup(csv_bytes: bytes) -> dict[str, str]:
    """Parse the CSV and build a {yfinance_ticker -> instrument_key} dict.

    Upstox CSV columns (as of 2026):
      instrument_key, exchange_token, tradingsymbol, name, last_price,
      expiry, strike, tick_size, lot_size, instrument_type, option_type,
      exchange

    We only care about NSE_EQ (delivery-eligible cash-segment stocks):
      yfinance ticker = tradingsymbol + ".NS"
      Upstox instrument_key = "NSE_EQ|<ISIN>"  (already in the CSV)
    """
    import csv

    lookup: dict[str, str] = {}
    try:
        reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8")))
        for row in reader:
            instrument_key = row.get("instrument_key") or ""
            tradingsymbol = row.get("tradingsymbol") or ""
            instrument_type = (row.get("instrument_type") or "").upper()
            # Only NSE equities in cash segment.
            #
            # THE PRICE BUG, root cause (owner 2026-08-04: "prices are not live
            # at all"). This filter used to accept only {"EQ", ""}. Upstox
            # labels cash equities **EQUITY** in this file — all 9,467 NSE_EQ
            # rows were thrown away and the lookup came out EMPTY (measured: 0
            # entries). With no instrument keys, get_ltp() was handed an empty
            # list, so EVERY stock quote failed — silently, because an empty
            # lookup is indistinguishable from "ticker not listed". The only
            # live numbers on the whole app were the 4 indices, which come from
            # the hardcoded INDEX_KEYS constants and never touch this table.
            # That is exactly the "only 3 are updated" the owner reported.
            if not instrument_key.startswith("NSE_EQ"):
                continue
            if instrument_type not in {"EQ", "EQUITY", ""}:
                continue
            if not tradingsymbol:
                continue
            lookup[f"{tradingsymbol}.NS"] = instrument_key
    except (csv.Error, UnicodeDecodeError, AttributeError) as e:
        log.warning("Failed to parse instrument CSV: %s", e)
    return lookup


def committed_lookup() -> dict[str, str]:
    """{ticker -> instrument_key} from the repo's own universe CSV.

    data/upstox_nse_equities.csv already carries a real instrument_key for
    every one of its ~2,393 names. It ships with the app, so it works with no
    network at all. Nothing used it, which is why one failed download could
    take live prices to zero for the whole book (owner 2026-08-04).
    """
    import csv
    path = os.path.join(os.path.dirname(__file__), "..", "..",
                        "data", "upstox_nse_equities.csv")
    out: dict[str, str] = {}
    try:
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                tkr = (row.get("ticker") or "").strip()
                key = (row.get("instrument_key") or "").strip()
                if tkr and key:
                    out[tkr] = key
    except (OSError, csv.Error) as e:
        log.warning("Committed instrument fallback unreadable: %s", e)
    return out


# Set when the live master could not be used and the shipped CSV is standing
# in. Surfaced on /healthz so a silent degradation is impossible.
_LOOKUP_SOURCE: str = "none"


def get_lookup(force_refresh: bool = False) -> dict[str, str]:
    """Returns the {yfinance_ticker -> instrument_key} lookup. Caches in memory.

    An EMPTY lookup means no stock can be priced, so it is never accepted as an
    answer: the shipped CSV takes over and the degradation is logged loudly.
    """
    global _LOOKUP, _LOOKUP_BUILT_AT, _LOOKUP_SOURCE

    now = time.time()
    if (
        _LOOKUP
        and not force_refresh
        and (now - _LOOKUP_BUILT_AT) < CACHE_TTL_SECONDS
    ):
        return _LOOKUP

    csv_bytes = _ensure_cached(force=force_refresh)
    built = _build_lookup(csv_bytes) if csv_bytes else {}
    if built:
        _LOOKUP, _LOOKUP_SOURCE = built, "upstox_master"
        _LOOKUP_BUILT_AT = now
        log.info("Upstox instrument lookup built: %d NSE equities", len(_LOOKUP))
        return _LOOKUP

    fallback = committed_lookup()
    if fallback:
        log.error("Upstox instrument master unusable (%s) — falling back to the "
                  "committed CSV: %d tickers. LIVE PRICES ARE DEGRADED TO THE "
                  "SHIPPED UNIVERSE.",
                  "download failed" if not csv_bytes else "0 rows parsed",
                  len(fallback))
        _LOOKUP, _LOOKUP_SOURCE = fallback, "committed_csv"
        _LOOKUP_BUILT_AT = now
        return _LOOKUP

    log.error("NO instrument keys available at all — every stock quote will "
              "fail. Prices will show as unavailable, never as stale.")
    if _LOOKUP is None:
        _LOOKUP = {}
    _LOOKUP_SOURCE = "none"
    return _LOOKUP


def to_instrument_key(yf_ticker: str) -> Optional[str]:
    """Convert 'RELIANCE.NS' -> 'NSE_EQ|INE002A01018'. Returns None if unknown."""
    if not yf_ticker:
        return None
    lookup = get_lookup()
    return lookup.get(yf_ticker)


# Indices are NOT in the NSE_EQ instrument file, so they never resolved and
# every index quote silently fell through to the (Render-blocked) Yahoo path.
# That is why the dashboard was showing stored history instead of live index
# levels (owner caught it 2026-07-31). These keys are Upstox constants.
INDEX_KEYS: dict[str, str] = {
    "^NSEI": "NSE_INDEX|Nifty 50",
    "^NSEBANK": "NSE_INDEX|Nifty Bank",
    "^CNX200": "NSE_INDEX|Nifty 200",
    "^INDIAVIX": "NSE_INDEX|India VIX",
    "^BSESN": "BSE_INDEX|SENSEX",
}


def to_instrument_keys(yf_tickers: list[str]) -> dict[str, str]:
    """Batch convert. Returns {yf_ticker: instrument_key} for known tickers only."""
    lookup = get_lookup()
    out = {t: lookup[t] for t in yf_tickers if t in lookup}
    for t in yf_tickers:                      # indices come from the constants
        if t not in out and t in INDEX_KEYS:
            out[t] = INDEX_KEYS[t]
    return out


def status() -> dict:
    return {
        "lookup_size": len(_LOOKUP) if _LOOKUP is not None else 0,
        "lookup_source": _LOOKUP_SOURCE,
        "lookup_built_at": _LOOKUP_BUILT_AT,
        "cache_file_exists": CACHE_FILE.exists(),
        "cache_age_sec": (
            time.time() - CACHE_FILE.stat().st_mtime
            if CACHE_FILE.exists() else None
        ),
    }
