#!/usr/bin/env python3
"""Fundamentals (annual + quarterly key lines) via Yahoo's timeseries API.

REWRITTEN 2026-08-09: the yfinance wrapper (tk.balance_sheet etc.) returned
EMPTY for symbols whose data the raw endpoint serves fine (measured:
RELIANCE.NS empty via yfinance 1.5.2, 4 years of annualTotalAssets via the
raw call). So this hits the endpoint directly — one batched request per
symbol, every field at once, ~8x faster than the statement wrapper.

Also fixed from the owner's Colab original: universe = the actual Nifty-200
constituent list (the Colab version sampled 250 symbols ALPHABETICALLY).

Output: one long-format CSV (symbol, field, as_of, value) + coverage log.
Free-source ceiling (stated, not hidden): ~4 annual years + ~5 quarters.
Deeper history (8-quarter SUE etc.) needs exchange filings — DATA_NEEDED.
"""
import csv
import glob
import time

import requests

FIELDS_ANNUAL = [
    "annualTotalRevenue", "annualGrossProfit", "annualNetIncome",
    "annualOperatingIncome", "annualTotalAssets", "annualStockholdersEquity",
    "annualTotalDebt", "annualOperatingCashFlow", "annualFreeCashFlow",
    "annualBasicEPS",
]
FIELDS_QUARTERLY = [
    "quarterlyTotalRevenue", "quarterlyNetIncome", "quarterlyBasicEPS",
    "quarterlyOperatingIncome",
]


def run(list_glob: str = "data/index_lists/*ind_nifty200list.csv",
        out_csv: str = "data/fundamentals_n200.csv",
        period1: int = 1420070400):
    lists = sorted(glob.glob(list_glob))
    if not lists:
        raise SystemExit(f"No constituent list matches {list_glob} — run "
                         f"fetch_nse_public.py first.")
    with open(lists[-1]) as f:
        syms = [r["Symbol"] for r in csv.DictReader(f)]
    types = ",".join(FIELDS_ANNUAL + FIELDS_QUARTERLY)
    hdrs = {"User-Agent": "Mozilla/5.0"}
    p2 = int(time.time())
    out, cov = [], []
    for i, s in enumerate(syms, 1):
        y = s + ".NS"
        url = (f"https://query2.finance.yahoo.com/ws/fundamentals-timeseries/"
               f"v1/finance/timeseries/{y}?symbol={y}&type={types}"
               f"&period1={period1}&period2={p2}")
        try:
            j = requests.get(url, timeout=30, headers=hdrs).json()
            n = 0
            for res in j.get("timeseries", {}).get("result", []):
                typ = res.get("meta", {}).get("type", [""])[0]
                for item in (res.get(typ) or []):
                    if item and item.get("reportedValue") is not None:
                        out.append([s, typ, item["asOfDate"],
                                    item["reportedValue"]["raw"]])
                        n += 1
            cov.append([s, "OK" if n else "EMPTY", n])
        except Exception as e:
            cov.append([s, f"FAIL {str(e)[:60]}", 0])
        if i % 40 == 0:
            print(f"{i}/{len(syms)}", flush=True)
        time.sleep(0.25)
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "field", "as_of", "value"])
        w.writerows(out)
    with open(out_csv.replace(".csv", "_coverage.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "status", "datapoints"])
        w.writerows(cov)
    ok = sum(1 for c in cov if c[1] == "OK")
    print(f"FUNDAMENTALS: {len(out)} datapoints, {ok}/{len(syms)} symbols OK")


if __name__ == "__main__":
    run()
