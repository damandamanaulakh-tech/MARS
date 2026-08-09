#!/usr/bin/env python3
"""CROSS-CHECK fetch: sample symbols via Kite Connect, compared against Upstox.

Requires the historical-data add-on on your Kite Connect app. Never the
primary source. The actual close-by-close comparison lives in
verify_intake.py (FIXED 2026-08-09 — the original kit claimed the compare
existed but never implemented it).
"""
import csv
import datetime as dt
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()
from kiteconnect import KiteConnect  # noqa: E402

kite = KiteConnect(api_key=os.getenv("KITE_API_KEY", ""))


def auth(request_token):
    s = kite.generate_session(request_token,
                              api_secret=os.getenv("KITE_API_SECRET"))
    print("ACCESS TOKEN:", s["access_token"], "-> put in .env KITE_ACCESS_TOKEN")


def run(sample_n=25):
    kite.set_access_token(os.getenv("KITE_ACCESS_TOKEN"))
    ins = [i for i in kite.instruments("NSE")
           if i["instrument_type"] == "EQ" and i["segment"] == "NSE"]
    updir = os.getenv("FETCH_OUT", "data/ohlcv_upstox")
    ups = os.listdir(updir) if os.path.isdir(updir) else []
    have = {f.split(".")[0] for f in ups}          # .csv and .csv.gz alike
    picks = [i for i in ins if i["tradingsymbol"] in have][:sample_n]
    os.makedirs("data/ohlcv_kite", exist_ok=True)
    frm, to = dt.date(2015, 1, 1), dt.date.today()
    for i, inst in enumerate(picks, 1):
        rows, cur = [], frm
        while cur < to:                            # Kite day candles: <=2000d/req
            chunk_end = min(cur + dt.timedelta(days=1800), to)
            rows += kite.historical_data(inst["instrument_token"], cur,
                                         chunk_end, "day")
            cur = chunk_end + dt.timedelta(days=1)
            time.sleep(0.4)
        with open(f"data/ohlcv_kite/{inst['tradingsymbol']}.csv", "w",
                  newline="") as f:
            w = csv.writer(f)
            w.writerow(["symbol", "date", "open", "high", "low", "close",
                        "volume"])
            for r in rows:
                w.writerow([inst["tradingsymbol"], r["date"].date(), r["open"],
                            r["high"], r["low"], r["close"], r["volume"]])
        print(f"[{i}/{len(picks)}] {inst['tradingsymbol']}: {len(rows)} rows")
    print("Cross-check data written. Now run verify_intake.py — it compares "
          "these closes against the Upstox files on shared dates.")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--auth":
        auth(sys.argv[2])
    else:
        run(int(sys.argv[sys.argv.index("--sample") + 1])
            if "--sample" in sys.argv else 25)
