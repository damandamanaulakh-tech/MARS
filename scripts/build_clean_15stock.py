"""Rebuild data/history_15stock/ (clean per-ticker OHLCV) from the raw NSE mirror.

Input : data/history_15stock_raw/  — 30 NSE security-wise archive CSVs
        (UTF-8 BOM, quoted headers with trailing spaces, Indian digit
        grouping, dates 01-Feb-2021, reverse-chronological, Series col).
Output: data/history_15stock/<TICKER>.csv  — Date,Open,High,Low,Close,Volume
        ascending, corporate-action back-adjusted; plus
        adjustments_manifest.csv recording every adjustment applied.

Corporate-action detection: verified against this archive's ground truth
(RELIANCE 1:1 bonus ex-date 2017-09-07 — Prev Close stays 1,645.40, Close
drops to 818.10), NSE does NOT restate Prev Close in these files, so the
overnight ratio IS the action:
    factor = Open[t] / PrevClose[t]      (fallback Close[t]/Close[t-1])
A factor below 0.75 is a split/bonus — the deepest one-day circuit-limit
crash is -20% (factor 0.80), while the smallest common action (1:2 bonus)
is 0.667. Measured factors within 3% of a standard ratio (1/2, 2/3, 1/4,
1/5, 1/8, 1/10) snap to it; both values land in the manifest. All prices
before t are multiplied by the factor (volume divided) — back-adjustment.
Joints across the ~4-month window gap (Feb-2021 -> Jun-2021) are excluded:
a >30-day date jump is logged GAP_SKIPPED and never adjusted, so the gap is
preserved honestly instead of being smoothed into fake continuity.

Run from repo root:  python scripts/build_clean_15stock.py
Deterministic — same raw input, same output, byte for byte.
"""

from __future__ import annotations

import csv
import glob
import os
import re

import pandas as pd

RAW = "data/history_15stock_raw"
OUT = "data/history_15stock"
ADJ_THRESHOLD = 0.75      # overnight ratio below this = corporate action
GAP_DAYS = 30             # date jumps beyond this are window gaps, never adjusted


def parse_raw(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8-sig", thousands=",")
    df.columns = [re.sub(r"\s+", " ", c).strip().rstrip("₹").strip() for c in df.columns]
    df = df[df["Series"].astype(str).str.strip() == "EQ"].copy()
    df["Date"] = pd.to_datetime(df["Date"].astype(str).str.strip(), format="%d-%b-%Y")
    for c in ("Prev Close", "Open Price", "High Price", "Low Price", "Close Price",
              "Total Traded Quantity"):
        df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", ""), errors="coerce")
    df = df.dropna(subset=["Date", "Close Price"]).sort_values("Date")
    df = df.drop_duplicates(subset="Date", keep="last").set_index("Date")
    return df[["Prev Close", "Open Price", "High Price", "Low Price",
               "Close Price", "Total Traded Quantity"]]


def ticker_of(fname: str) -> str:
    m = re.search(r"-TO-\d{1,2}-\d{2}-\d{4}-(.+?)-ALL-N", os.path.basename(fname))
    return m.group(1) if m else os.path.basename(fname)


STANDARD_RATIOS = (0.5, 2 / 3, 0.25, 0.2, 0.125, 0.1)


def snap_ratio(measured: float) -> float:
    for r in STANDARD_RATIOS:
        if abs(measured - r) / r <= 0.03:
            return r
    return measured


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    files = sorted(f for f in glob.glob(f"{RAW}/*.csv") if "(1)" not in f)
    # RELIANCE/SBIN 2021-26 exist only as "(1)" copies — pull those back in
    have = {(ticker_of(f), "9-06-2021" in f) for f in files}
    for f in sorted(glob.glob(f"{RAW}/*(1)*.csv")):
        key = (ticker_of(f), "9-06-2021" in f)
        if key not in have:
            files.append(f)
            have.add(key)

    by_ticker: dict[str, list[pd.DataFrame]] = {}
    for f in sorted(files):
        by_ticker.setdefault(ticker_of(f), []).append(parse_raw(f))

    manifest: list[dict] = []
    for tkr, frames in sorted(by_ticker.items()):
        df = pd.concat(frames).sort_index()
        df = df[~df.index.duplicated(keep="last")]

        # walk ascending; collect (position, factor) for every true ex-date
        closes = df["Close Price"].values
        opens = df["Open Price"].values
        prevs = df["Prev Close"].values
        dates = df.index
        factor_from = []                       # (idx, snapped factor)
        for i in range(1, len(df)):
            day_gap = (dates[i] - dates[i - 1]).days
            if closes[i - 1] <= 0:
                continue
            measured = (opens[i] / prevs[i]) if (opens[i] > 0 and prevs[i] > 0) \
                else (closes[i] / closes[i - 1])
            if measured >= ADJ_THRESHOLD:      # normal day
                continue
            if day_gap > GAP_DAYS:
                manifest.append({"ticker": tkr, "date": dates[i].date().isoformat(),
                                 "measured": round(measured, 6), "factor": "",
                                 "action": "GAP_SKIPPED",
                                 "note": f"{day_gap}-day window gap — not an adjustment"})
                continue
            factor = snap_ratio(measured)
            factor_from.append((i, factor))
            manifest.append({"ticker": tkr, "date": dates[i].date().isoformat(),
                             "measured": round(measured, 6), "factor": round(factor, 6),
                             "action": "ADJUSTED",
                             "note": f"~1:{max(1, round(1 / factor - 1))} bonus/split "
                                     f"({'snapped' if factor != measured else 'measured'})"})

        adj = pd.Series(1.0, index=df.index)
        for i, factor in factor_from:
            adj.iloc[:i] *= factor             # back-adjust everything before the ex-date

        out = pd.DataFrame({
            "Open": (df["Open Price"] * adj).round(2),
            "High": (df["High Price"] * adj).round(2),
            "Low": (df["Low Price"] * adj).round(2),
            "Close": (df["Close Price"] * adj).round(2),
            "Volume": (df["Total Traded Quantity"] / adj).round(0),
        })
        out.index.name = "Date"
        # self-audit: any remaining close-to-close drop beyond -25% on adjacent
        # days is a genuine crash day (e.g. COVID 2020-03-23) — record it so a
        # later reader doesn't mistake it for a missed corporate action.
        cc = out["Close"] / out["Close"].shift(1)
        day_ok = out.index.to_series().diff().dt.days <= GAP_DAYS
        for ts in out.index[(cc < 0.75) & day_ok.fillna(False)]:
            manifest.append({"ticker": tkr, "date": ts.date().isoformat(),
                             "measured": round(float(cc.loc[ts]), 6), "factor": "",
                             "action": "CRASH_DAY_LEFT_UNADJUSTED",
                             "note": "real market move (overnight gap normal) — not a split"})
        out.to_csv(f"{OUT}/{tkr}.csv", date_format="%Y-%m-%d")
        print(f"{tkr:12s} rows={len(out):5d}  {out.index[0].date()} -> {out.index[-1].date()}"
              f"  adjustments={sum(1 for m in manifest if m['ticker'] == tkr and m['action'] == 'ADJUSTED')}")

    with open(f"{OUT}/adjustments_manifest.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["ticker", "date", "measured", "factor", "action", "note"])
        w.writeheader()
        w.writerows(manifest)
    n_adj = sum(1 for m in manifest if m["action"] == "ADJUSTED")
    n_gap = sum(1 for m in manifest if m["action"] == "GAP_SKIPPED")
    print(f"\ntickers={len(by_ticker)}  adjustments={n_adj}  gap-joints skipped={n_gap}")
    print(f"manifest -> {OUT}/adjustments_manifest.csv")


if __name__ == "__main__":
    main()
