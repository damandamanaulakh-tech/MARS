"""Audit F-8 (2026-10-02): quality's liquidity leg ranked SHARE COUNT.

`quality.liquidity_score` tiered the 20-day average share volume
(>10 lakh -> 90, >3 lakh -> 70, >1 lakh -> 55, else 30). A ten-million-share
penny stock scored 90 and THERMAX (1.4 lakh shares, Rs 48 crore a day)
scored 55 — the trap removed from the entry gate on 2026-09-08. The leg is
half of quality, and quality is 35% of the momentum entry score.

This script, on the committed 191-name x 12-year panel at month-ends:
  1. measures the share of stock-months in each OLD tier;
  2. sets RUPEE thresholds (20-day mean close x volume) at the same
     quantiles, so the score's distribution is unchanged and only the
     ORDER is fixed (no invented crore levels);
  3. reports which entry decisions flip (blend >= floor and positive 6m
     return) and the forward 126-day return of names flipped in vs out,
     train <= 2020-06-30 / test after.
Writes research/quality_liquidity_2026-10-02/result.json.
"""
from __future__ import annotations

import io
import json
import os
import sys
import zipfile

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from trading.quality import blend_scores, low_vol_score  # noqa: E402
from trading.strategies.nifty200_momentum import momentum_score  # noqa: E402

ZIP = os.path.join(ROOT, "data", "history_upstox_export_2026-07-19.zip")
OUT = os.path.join(ROOT, "research", "quality_liquidity_2026-10-02")
SPLIT = pd.Timestamp("2020-06-30")
FLOOR = 62.0
QUALITY_W = 0.35
FWD = 126
OLD_CUTS = (1_000_000, 300_000, 100_000)       # share-count tier edges
TIER_SCORES = (90.0, 70.0, 55.0, 30.0)


def load() -> dict:
    z = zipfile.ZipFile(ZIP)
    out = {}
    for name in z.namelist():
        if name.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(z.read(name)), parse_dates=["Date"])
            out[name[:-4]] = df.set_index("Date").sort_index()
    return out


def tier(x: float, cuts) -> float:
    for c, s in zip(cuts, TIER_SCORES):
        if x > c:
            return s
    return TIER_SCORES[-1]


def main() -> None:
    hist = load()
    idx = sorted(set().union(*[set(d.index) for d in hist.values()]))
    month_ends = pd.Series(idx, index=idx).groupby(
        pd.Index(idx).to_period("M")).max().tolist()

    rows = []
    for t, df in hist.items():
        c, v = df["Close"], df["Volume"]
        for me in month_ends:
            pos = df.index.searchsorted(me, side="right") - 1
            if pos < 260 or pos + FWD >= len(df):
                continue
            close = c.iloc[:pos + 1]
            vol = v.iloc[:pos + 1]
            ms = momentum_score(close)
            if ms is None:
                continue
            shares = float(vol.iloc[-20:].mean())
            rupees = float((close.iloc[-20:] * vol.iloc[-20:]).mean())
            r6 = float(close.iloc[-1] / close.iloc[-127] - 1)
            fwd = float(c.iloc[pos + FWD] / c.iloc[pos] - 1)
            rows.append((df.index[pos], t, float(ms), low_vol_score(close),
                         shares, rupees, r6, fwd))
    d = pd.DataFrame(rows, columns=["date", "ticker", "mom", "lowvol",
                                    "shares", "rupees", "r6", "fwd"])

    old_tier = d["shares"].apply(lambda x: tier(x, OLD_CUTS))
    share = {s: float((old_tier == s).mean()) for s in TIER_SCORES}
    # cumulative share at or above each tier, highest first
    above = np.cumsum([share[s] for s in TIER_SCORES[:-1]])
    cuts = tuple(float(d["rupees"].quantile(1 - a)) for a in above)
    new_tier = d["rupees"].apply(lambda x: tier(x, cuts))

    def blend(liq):
        q = (d["lowvol"] + liq) / 2.0
        return [blend_scores(m, qq, QUALITY_W) for m, qq in zip(d["mom"], q)]

    d["old"] = blend(old_tier)
    d["new"] = blend(new_tier)
    d["old_in"] = (d["old"] >= FLOOR) & (d["r6"] > 0)
    d["new_in"] = (d["new"] >= FLOOR) & (d["r6"] > 0)

    def half(mask):
        x = d[mask]
        added = x[x["new_in"] & ~x["old_in"]]
        dropped = x[x["old_in"] & ~x["new_in"]]
        kept_old = x[x["old_in"]]
        kept_new = x[x["new_in"]]
        return {
            "stock_months": int(len(x)),
            "entries_old": int(len(kept_old)),
            "entries_new": int(len(kept_new)),
            "flipped_in": int(len(added)),
            "flipped_out": int(len(dropped)),
            "fwd126_flipped_in_mean_pct": round(added["fwd"].mean() * 100, 2)
            if len(added) else None,
            "fwd126_flipped_out_mean_pct": round(dropped["fwd"].mean() * 100, 2)
            if len(dropped) else None,
            "fwd126_all_entries_old_pct": round(kept_old["fwd"].mean() * 100, 2),
            "fwd126_all_entries_new_pct": round(kept_new["fwd"].mean() * 100, 2),
        }

    res = {
        "panel": "data/history_upstox_export_2026-07-19.zip",
        "stock_months": int(len(d)),
        "old_tier_share": {str(k): round(v, 4) for k, v in share.items()},
        "rupee_cuts": [round(x, 0) for x in cuts],
        "rupee_cuts_crore": [round(x / 1e7, 2) for x in cuts],
        "tier_agreement": round(float((old_tier == new_tier).mean()), 4),
        "train": half(d["date"] <= SPLIT),
        "test": half(d["date"] > SPLIT),
    }
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "result.json"), "w") as f:
        json.dump(res, f, indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
