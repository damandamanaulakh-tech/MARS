"""Full-universe OHLCV download — every stock in the fresh 2,393 list,
2019-01-01 -> today, straight from Upstox's public historical API.
Two date chunks per ticker (single requests over ~5y get rejected).
Resume-safe: skips tickers already on disk. Failures logged, never fatal.
"""
import csv
import json
import os
import sys
import time

import httpx
import pandas as pd

from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parents[2]
UNI = str(_ROOT / "data/upstox_nse_equities.csv")
OUT = str(_ROOT / "research/tunnel_studies/full_universe")   # unzip data/full_universe_2392.zip here
CHUNKS = [("2019-01-01", "2022-12-31"), ("2023-01-01", "2026-07-24")]

os.makedirs(OUT, exist_ok=True)
rows = list(csv.DictReader(open(UNI)))
print(f"universe: {len(rows)} tickers", flush=True)

client = httpx.Client(timeout=30.0, headers={"Accept": "application/json"})
done = fail = skip = 0
failures = []
t0 = time.time()

for i, r in enumerate(rows):
    tkr, key = r["ticker"], r["instrument_key"]
    path = f"{OUT}/{tkr}.csv"
    if os.path.exists(path):
        skip += 1
        continue
    candles = []
    ok = True
    for frm, to in CHUNKS:
        url = (f"https://api.upstox.com/v2/historical-candle/"
               f"{key.replace('|', '%7C')}/day/{to}/{frm}")
        for attempt in (1, 2):
            try:
                resp = client.get(url)
                if resp.status_code == 429:
                    time.sleep(3)
                    continue
                d = resp.json()
                if d.get("status") == "success":
                    candles += d["data"]["candles"]
                # non-success (e.g. listed after chunk) is fine — empty chunk
                break
            except Exception as e:
                if attempt == 2:
                    ok = False
                    failures.append(f"{tkr}: {type(e).__name__}")
                time.sleep(2)
        time.sleep(0.12)
    if not candles:
        fail += 1
        failures.append(f"{tkr}: no candles")
        continue
    df = pd.DataFrame(candles, columns=["ts", "Open", "High", "Low", "Close", "Volume", "OI"])
    df["Date"] = pd.to_datetime(df["ts"].str[:10])
    df = df.set_index("Date").sort_index()
    df = df[~df.index.duplicated()][["Open", "High", "Low", "Close", "Volume"]]
    df.to_csv(path)
    done += 1
    if (done + fail) % 100 == 0:
        el = (time.time() - t0) / 60
        print(f"progress {i+1}/{len(rows)}  saved={done} skip={skip} fail={fail}  {el:.1f} min", flush=True)

open(f"{OUT}/_failures.txt", "w").write("\n".join(failures))
summary = {"total": len(rows), "saved": done, "skipped": skip, "failed": fail,
           "minutes": round((time.time() - t0) / 60, 1)}
json.dump(summary, open(f"{OUT}/_summary.json", "w"))
print("DONE", json.dumps(summary), flush=True)
