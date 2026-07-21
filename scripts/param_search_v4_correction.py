"""Search v4 — the CORRECTION playbook. What should the app DO in corrections?

The regime brake proved momentum must stop buying in corrections. This test
asks the next question the user demanded: is CASH actually the best use of a
correction, or can a correction-specific engine earn there?

Method — preregistered, NO tuning (few episodes => any grid would overfit;
every rule below is parameter-FIXED before seeing results, so every episode
is out-of-sample by construction):
  * Correction detection = the app's own rule: Nifty close < its 200-DMA
    (real Nifty from data/index_daily_15y.csv). Episodes = contiguous runs
    >= 15 trading days.
  * Inside each episode, WEEKLY rebalance, costs 0.15%/side on turnover,
    all positions liquidated (with costs) at episode end.
  * Candidates:
      CASH      — 0% (the incumbent; the one to beat)
      NIFTY     — buy the index at episode start, hold to end (dip-buying)
      OCEAN     — deep reversion: 10 deepest names below their own 200-DMA
                  with RSI14 < 35 (washout), equal-weight  [R1]
      HALF_MOM  — final-set momentum signal (6m+12m vol-adjusted), only
                  names with positive 21d return, top 10, 50% exposure [R3]
      VIX_CRUSH — cash until India VIX falls to 75% of its episode peak,
                  then full-size momentum top-10 for the remainder     [R4]
  * Universe: the 191-ticker Upstox export, liquidity floor Rs 1 Cr median
    turnover (as v3). Survivorship tag rides as ever.

Writes docs/param_search_v4_{progress.jsonl,results.json}.
"""

from __future__ import annotations

import io
import json
import time
import zipfile

import numpy as np
import pandas as pd

ZIP = "data/history_upstox_export_2026-07-19.zip"
IDX = "data/index_daily_15y.csv"
OUT_JSONL = "docs/param_search_v4_progress.jsonl"
OUT_JSON = "docs/param_search_v4_results.json"
COST = 0.0015
TURNOVER_FLOOR = 1e7
MIN_EP_DAYS = 15
N_BOOK = 10


def log(rec):
    with open(OUT_JSONL, "a") as fh:
        fh.write(json.dumps(rec) + "\n")
    print(json.dumps(rec), flush=True)


def rsi14(close: pd.DataFrame) -> pd.DataFrame:
    d = close.diff()
    up = d.clip(lower=0).rolling(14).mean()
    dn = (-d.clip(upper=0)).rolling(14).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def main():
    t0 = time.time()
    frames = {}
    with zipfile.ZipFile(ZIP) as z:
        for name in z.namelist():
            if name.endswith(".csv"):
                with z.open(name) as fh:
                    frames[name[:-4]] = pd.read_csv(
                        io.TextIOWrapper(fh), parse_dates=["Date"], index_col="Date")
    tickers = sorted(frames)
    close = pd.DataFrame({t: frames[t]["Close"] for t in tickers}).sort_index()
    volume = pd.DataFrame({t: frames[t]["Volume"] for t in tickers}).reindex(close.index)

    idx = pd.read_csv(IDX, dayfirst=True, parse_dates=["trade_date"],
                      index_col="trade_date").sort_index()
    nifty = idx["nifty_close"].reindex(close.index).ffill()
    vix = idx["india_vix_close"].reindex(close.index).ffill()

    corr = nifty < nifty.rolling(200).mean()
    # episodes: contiguous correction runs
    episodes = []
    in_ep, start = False, None
    dates = close.index
    aligned = corr.reindex(dates).fillna(False).values
    for i, flag in enumerate(aligned):
        if flag and not in_ep:
            in_ep, start = True, i
        elif not flag and in_ep:
            in_ep = False
            if i - start >= MIN_EP_DAYS:
                episodes.append((start, i))
    if in_ep and len(dates) - start >= MIN_EP_DAYS:
        episodes.append((start, len(dates)))
    log({"event": "episodes", "count": len(episodes),
         "spans": [[str(dates[a].date()), str(dates[b - 1].date()), b - a]
                   for a, b in episodes]})

    # signals (daily, computed once)
    sma200 = close.rolling(200).mean()
    depth = close / sma200 - 1                    # most negative = deepest
    rsi = rsi14(close)
    r126 = close / close.shift(126) - 1
    r252 = close / close.shift(252) - 1
    vol126 = close.pct_change().rolling(126).std()
    mom = (r126 / vol126).rank(axis=1, pct=True) + \
          (r252 / vol126).rank(axis=1, pct=True)
    r21 = close / close.shift(21) - 1
    turnover = (close * volume).rolling(60).median()
    hist_ok = close.notna().rolling(252).count() >= 200

    daily_ret = close.pct_change()

    def run_rule(a, b, pick_fn):
        """Weekly-rebalanced book inside episode [a,b); returns (total, maxdd)."""
        week_marks = list(range(a, b, 5))
        curve = [1.0]
        weights = pd.Series(0.0, index=tickers)
        for wi, m in enumerate(week_marks):
            newW = pick_fn(m)
            turn = float((newW - weights).abs().sum()) / 2
            cost = turn * 2 * COST
            weights = newW
            end = min(m + 5, b)
            seg = daily_ret.iloc[m + 1:end + 1].fillna(0.0)
            seg_ret = float((seg @ weights).add(0).apply(lambda x: x).pipe(
                lambda s: (1 + s).prod() - 1)) if len(seg) else 0.0
            curve.append(curve[-1] * (1 + seg_ret - cost))
        curve.append(curve[-1] * (1 - float(weights.abs().sum()) / 2 * 2 * COST))
        arr = np.array(curve)
        dd = float(((arr - np.maximum.accumulate(arr)) / np.maximum.accumulate(arr)).min()) * 100
        return (arr[-1] - 1) * 100, dd

    def elig_at(m):
        return (hist_ok.iloc[m] & close.iloc[m].notna()
                & (turnover.iloc[m] >= TURNOVER_FLOOR))

    def w_ocean(m):
        e = elig_at(m)
        cand = depth.iloc[m].where(e & (rsi.iloc[m] < 35))
        top = cand.nsmallest(N_BOOK).dropna()
        w = pd.Series(0.0, index=tickers)
        if len(top):
            w[top.index] = 1.0 / len(top)
        return w

    def w_halfmom(m):
        e = elig_at(m)
        cand = mom.iloc[m].where(e & (r21.iloc[m] > 0))
        top = cand.nlargest(N_BOOK).dropna()
        w = pd.Series(0.0, index=tickers)
        if len(top):
            w[top.index] = 0.5 / len(top)
        return w

    results = {"episodes": []}
    for a, b in episodes:
        span = f"{dates[a].date()}..{dates[b-1].date()}"
        nifty_ret = float(nifty.iloc[b - 1] / nifty.iloc[a] - 1) * 100

        ocean_t, ocean_dd = run_rule(a, b, w_ocean)
        half_t, half_dd = run_rule(a, b, w_halfmom)

        vix_peak = vix.iloc[a:b].cummax()
        crushed = vix.iloc[a:b] <= 0.75 * vix_peak

        def w_vix(m):
            if not bool(crushed.iloc[min(m - a, len(crushed) - 1)]):
                return pd.Series(0.0, index=tickers)
            e = elig_at(m)
            top = mom.iloc[m].where(e).nlargest(N_BOOK).dropna()
            w = pd.Series(0.0, index=tickers)
            if len(top):
                w[top.index] = 1.0 / len(top)
            return w

        vixc_t, vixc_dd = run_rule(a, b, w_vix)

        rec = {"episode": span, "days": b - a,
               "CASH": 0.0, "NIFTY_hold": round(nifty_ret, 2),
               "OCEAN": {"ret": round(ocean_t, 2), "dd": round(ocean_dd, 2)},
               "HALF_MOM": {"ret": round(half_t, 2), "dd": round(half_dd, 2)},
               "VIX_CRUSH": {"ret": round(vixc_t, 2), "dd": round(vixc_dd, 2)}}
        results["episodes"].append(rec)
        log({"event": "episode_done", **rec})

    def agg(key):
        vals = [e[key]["ret"] if isinstance(e[key], dict) else e[key]
                for e in results["episodes"]]
        beats = sum(1 for v in vals if v > 0)
        return {"avg_ret": round(float(np.mean(vals)), 2),
                "episodes_beating_cash": f"{beats}/{len(vals)}"}

    results["summary"] = {
        "episodes": len(results["episodes"]),
        "NIFTY_hold": agg("NIFTY_hold"), "OCEAN": agg("OCEAN"),
        "HALF_MOM": agg("HALF_MOM"), "VIX_CRUSH": agg("VIX_CRUSH"),
        "runtime_min": round((time.time() - t0) / 60, 1),
        "method": "preregistered fixed rules, weekly, costs 0.15%/side, "
                  "liquidation at episode end; no tuning anywhere",
    }
    with open(OUT_JSON, "w") as fh:
        json.dump(results, fh, indent=2)
    log({"event": "done", **results["summary"]})


if __name__ == "__main__":
    main()
