#!/usr/bin/env python3
"""PRIMARY fetch: daily OHLCV for the NSE equity universe + key indices, Upstox.

FIXED 2026-08-09 (session review of the uploaded kit):
- Historical candles are PUBLIC — no access token needed (verified live).
  A token, if present in .env, is still sent (higher rate limits).
- V3 caps one request at ~10 YEARS of daily candles (verified: 10y OK,
  16y+ -> UDAPI1148 "Invalid date range"). The original kit asked for the
  whole range in one call, so EVERY symbol would have failed. Now chunked.
- Default START really is 2000-01-01 (the README always claimed it).
- BE (trade-to-trade) series included by default alongside EQ — the app's
  universe carries both; --series EQ to narrow.
- Concurrent workers with 429/5xx backoff; gzipped output; resumable.

Usage:
    python fetch_upstox_ohlcv.py                 # full universe, 2000->today
    python fetch_upstox_ohlcv.py --series EQ --workers 4
    python fetch_upstox_ohlcv.py --only TCS,RELIANCE,INDIA_VIX
"""
import argparse
import csv
import gzip
import io
import json
import os
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

TOKEN = os.getenv("UPSTOX_ACCESS_TOKEN", "")
H = {"Accept": "application/json"}
if TOKEN:
    H["Authorization"] = f"Bearer {TOKEN}"

OUT = os.getenv("FETCH_OUT", "data/ohlcv_upstox")
START = os.getenv("FETCH_START", "2000-01-01")
CHUNK_DAYS = 3600          # verified max ~10y of daily candles per request
MASTER = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"

INDEXES = [
    ("INDIA_VIX", "NSE_INDEX|India VIX"),
    ("NIFTY50", "NSE_INDEX|Nifty 50"),
    ("NIFTY200", "NSE_INDEX|Nifty 200"),
    ("NIFTYBANK", "NSE_INDEX|Nifty Bank"),
    ("SENSEX", "BSE_INDEX|SENSEX"),
]


def instruments(series: set) -> list:
    """NSE equity (trading_symbol, instrument_key) pairs from the JSON master.

    NOTE: the JSON master labels equities instrument_type 'EQ'/'BE'; the CSV
    master uses 'EQUITY'. Do not copy this filter onto the CSV file — that
    exact confusion once cost the live app every stock quote (2026-08-04).
    A tiny result is treated as the filter being wrong, not as truth.
    """
    raw = gzip.decompress(requests.get(MASTER, timeout=120).content)
    rows = json.loads(raw)
    eq = [r for r in rows
          if r.get("segment") == "NSE_EQ" and r.get("instrument_type") in series]
    if len(eq) < 1000:
        sys.exit(f"FILTER SUSPECT: only {len(eq)} instruments matched "
                 f"{sorted(series)} — expected thousands. Refusing to run.")
    print(f"NSE_EQ instruments matching {sorted(series)}: {len(eq)}")
    return [(r["trading_symbol"], r["instrument_key"]) for r in eq]


def _chunks(frm: str, to: str):
    d0 = date.fromisoformat(frm)
    d1 = date.fromisoformat(to)
    while d0 <= d1:
        d_end = min(d0 + timedelta(days=CHUNK_DAYS - 1), d1)
        yield d0.isoformat(), d_end.isoformat()
        d0 = d_end + timedelta(days=1)


def fetch_symbol(sym: str, key: str, frm: str, to: str):
    """All daily candles for one instrument, oldest first. Raises on failure."""
    out = []
    qkey = urllib.parse.quote(key)
    for c_frm, c_to in _chunks(frm, to):
        url = (f"https://api.upstox.com/v3/historical-candle/"
               f"{qkey}/days/1/{c_to}/{c_frm}")
        for attempt in range(5):
            r = requests.get(url, headers=H, timeout=60)
            if r.status_code == 200:
                out.extend(r.json().get("data", {}).get("candles", []))
                break
            if r.status_code in (429, 500, 502, 503):
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"{sym}: HTTP {r.status_code} {r.text[:120]}")
        else:
            raise RuntimeError(f"{sym}: gave up after retries on {c_frm}..{c_to}")
    out.sort(key=lambda c: c[0])
    # The API occasionally returns the same candle in two adjacent chunk
    # responses (identical values — measured 2026-08-09: 1 dup in ~45% of
    # symbols). Deduplicate by date, keep the first.
    seen, dedup = set(), []
    for c in out:
        d = c[0][:10]
        if d in seen:
            continue
        seen.add(d)
        dedup.append(c)
    return dedup


def write_symbol(sym: str, candles: list) -> str:
    safe = sym.replace(" ", "_").replace("|", "_").replace("/", "_")
    path = f"{OUT}/{safe}.csv.gz"
    with gzip.open(path, "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "date", "open", "high", "low", "close",
                    "volume", "oi"])
        for c in candles:
            w.writerow([sym, c[0][:10], c[1], c[2], c[3], c[4], c[5], c[6]])
    return path


def run(series: set, workers: int, only: set | None):
    os.makedirs(OUT, exist_ok=True)
    todo = instruments(series) + INDEXES
    if only:
        todo = [(s, k) for s, k in todo if s in only]
    done = {f.split(".")[0] for f in os.listdir(OUT)}
    todo = [(s, k) for s, k in todo
            if s.replace(" ", "_").replace("|", "_") not in done]
    end = date.today().isoformat()
    ok = fail = empty = 0
    failures = []

    def job(item):
        sym, key = item
        candles = fetch_symbol(sym, key, START, end)
        if not candles:
            return sym, "EMPTY", 0
        write_symbol(sym, candles)
        return sym, "OK", len(candles)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(job, it): it for it in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            sym, _key = futures[fut]
            try:
                sym, status, n = fut.result()
                if status == "OK":
                    ok += 1
                else:
                    empty += 1
                if i % 100 == 0 or status != "OK":
                    print(f"[{i}/{len(todo)}] {sym}: {status} {n}")
            except Exception as e:
                fail += 1
                failures.append((sym, str(e)[:160]))
                print(f"[{i}/{len(todo)}] {sym}: FAIL {e}")
    print(f"DONE ok={ok} empty={empty} fail={fail} (re-run resumes; "
          f"failed symbols retry automatically)")
    if failures:
        with open(f"{OUT}/_failures.csv", "w", newline="") as f:
            csv.writer(f).writerows([["symbol", "error"]] + failures)
    print("Next: python verify_intake.py")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", default="EQ,BE",
                    help="comma list of instrument_type values (default EQ,BE)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--only", default="",
                    help="comma list of trading symbols to fetch (testing)")
    args = ap.parse_args()
    run(set(args.series.split(",")), args.workers,
        set(args.only.split(",")) if args.only else None)
