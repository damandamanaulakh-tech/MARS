"""Walk-forward search v3 — the deciding run, on the app's REAL tradable data.

Data: data/history_upstox_export_2026-07-19.zip — 191 Nifty-200 tickers,
12y daily OHLCV+Volume (2014-06-30 -> 2026-07-17), exported straight from
the app's own Upstox-fed Mongo store. This is the exact universe the live
engine trades, with the columns the research panel never had.

New vs v2.1 (only possible with real OHLCV):
  * LIQUIDITY: eligible = 60d median rupee-turnover >= Rs 1 Cr/day.
  * CANDLE GATE testable: entry only if the app's own candle_confirmation
    (trading/candles.py, all 8 patterns, default cfg) confirms on the
    rebalance day's frame.
  * VOLUME-EXPANSION testable: entry only if 20d avg volume > 90d avg.
Grid: {6,12,6+12} x voladj x skip x N{15,25} x absgate x regime
      x candle x volx = 384 configs.
Walk-forward: 4 unseen windows (2019 / 2020-21 / 2022-23 / 2024-26H1),
expanding optimization, Sharpe-selected on PAST data only. Costs
0.15%/side on turnover. Benchmark = EW of the eligible universe.
Survivorship: current N200 membership backfilled — tag rides until PIT
snapshots land.

Run: python scripts/param_search_v3.py
Writes docs/param_search_v3_{progress.jsonl,results.json}.
"""

from __future__ import annotations

import io
import json
import sys
import time
import zipfile

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from trading.candles import candle_confirmation
from trading.config import Config

ZIP = "data/history_upstox_export_2026-07-19.zip"
OUT_JSONL = "docs/param_search_v3_progress.jsonl"
OUT_JSON = "docs/param_search_v3_results.json"
COST_PER_SIDE = 0.0015
TURNOVER_FLOOR = 1e7          # Rs 1 Cr median daily turnover
MIN_HIST = 252

WINDOWS = [
    {"name": "W1_2019",    "opt": ("2015-08-01", "2018-12-31"), "val": ("2019-01-01", "2019-12-31")},
    {"name": "W2_2020_21", "opt": ("2015-08-01", "2019-12-31"), "val": ("2020-01-01", "2021-12-31")},
    {"name": "W3_2022_23", "opt": ("2015-08-01", "2021-12-31"), "val": ("2022-01-01", "2023-12-31")},
    {"name": "W4_2024_26", "opt": ("2015-08-01", "2023-12-31"), "val": ("2024-01-01", "2026-07-17")},
]


def log(rec):
    with open(OUT_JSONL, "a") as fh:
        fh.write(json.dumps(rec) + "\n")
    print(json.dumps(rec), flush=True)


def load_frames() -> dict[str, pd.DataFrame]:
    frames = {}
    with zipfile.ZipFile(ZIP) as z:
        for name in z.namelist():
            if not name.endswith(".csv"):
                continue
            with z.open(name) as fh:
                df = pd.read_csv(io.TextIOWrapper(fh), parse_dates=["Date"],
                                 index_col="Date")
            frames[name[:-4]] = df.sort_index()
    return frames


def stats(monthly: pd.Series) -> dict:
    if len(monthly) < 6:
        return {"cagr": None}
    curve = (1 + monthly).cumprod()
    yrs = len(monthly) / 12
    cagr = float(curve.iloc[-1] ** (1 / yrs) - 1) * 100
    sharpe = float(monthly.mean() / monthly.std() * np.sqrt(12)) if monthly.std() > 0 else 0.0
    dd = float(((curve - curve.cummax()) / curve.cummax()).min()) * 100
    return {"cagr": round(cagr, 2), "sharpe": round(sharpe, 3), "max_dd": round(dd, 2)}


def main():
    t0 = time.time()
    frames = load_frames()
    tickers = sorted(frames)
    close = pd.DataFrame({t: frames[t]["Close"] for t in tickers}).sort_index()
    volume = pd.DataFrame({t: frames[t]["Volume"] for t in tickers}).reindex(close.index)
    log({"event": "loaded", "tickers": len(tickers), "days": len(close)})

    idx = close.index
    me_pos = pd.Series(np.arange(len(idx)), index=idx).groupby(
        [idx.year, idx.month]).last().values
    mp = close.iloc[me_pos]
    mdates = mp.index
    fwd = mp.shift(-1) / mp - 1

    hist_len = close.notna().cumsum().iloc[me_pos]
    turnover = (close * volume).rolling(60).median().iloc[me_pos]
    elig = (hist_len >= MIN_HIST) & mp.notna() & (turnover >= TURNOVER_FLOOR)
    log({"event": "eligibility", "avg_eligible_per_month":
         round(float(elig.sum(axis=1).mean()), 1)})

    # --- candle flags: the app's own gate, evaluated per ticker per month-end
    cfg = Config()
    candle_ok = pd.DataFrame(False, index=mdates, columns=tickers)
    for t in tickers:
        df = frames[t]
        month_last = df.groupby([df.index.year, df.index.month]).tail(1).index
        for ts in month_last:
            pos = df.index.get_loc(ts)
            if pos < 25:
                continue
            try:
                ok, _ = candle_confirmation(df.iloc[max(0, pos - 39):pos + 1], cfg)
            except Exception:
                ok = False
            # align to the panel's month-end label for that month
            key = (ts.year, ts.month)
            match = [d for d in mdates if (d.year, d.month) == key]
            if match:
                candle_ok.loc[match[0], t] = bool(ok)
    log({"event": "candle_flags", "true_rate":
         round(float(candle_ok.values.mean()), 3),
         "elapsed_s": round(time.time() - t0, 1)})

    volx_ok = (volume.rolling(20).mean() > volume.rolling(90).mean()).iloc[me_pos]

    ew_daily = close.pct_change().mean(axis=1)
    ew_curve = (1 + ew_daily.fillna(0)).cumprod()
    regime_ok = (ew_curve > ew_curve.rolling(200).mean()).iloc[me_pos].values

    bench = fwd.where(elig).mean(axis=1).dropna()

    def momentum(look_m, skip, voladj):
        past = mp.shift(skip)
        sig = past / past.shift(look_m) - 1
        if voladj:
            vol = mp.pct_change().rolling(look_m).std()
            sig = sig / vol.replace(0, np.nan)
        return sig

    sigs = {}
    for lk in ("6", "12", "6+12"):
        for va in (False, True):
            for sk in (0, 1):
                if lk == "6+12":
                    s = (momentum(6, sk, va).rank(axis=1, pct=True)
                         + momentum(12, sk, va).rank(axis=1, pct=True))
                else:
                    s = momentum(int(lk), sk, va)
                sigs[(lk, va, sk)] = s.where(elig)

    GRID = [{"look": lk, "voladj": va, "skip": sk, "N": n, "absgate": ag,
             "regime": rg, "candle": cd, "volx": vx}
            for lk in ("6", "12", "6+12") for va in (False, True)
            for sk in (0, 1) for n in (15, 25) for ag in (True, False)
            for rg in (False, True) for cd in (False, True)
            for vx in (False, True)]
    log({"event": "grid", "size": len(GRID)})

    series = {}
    for gi, g in enumerate(GRID):
        sig = sigs[(g["look"], g["voladj"], g["skip"])]
        member = sig.rank(axis=1, ascending=False) <= g["N"]
        if g["absgate"]:
            member = member & (sigs[("6", False, g["skip"])] > 0)
        if g["candle"]:
            member = member & candle_ok
        if g["volx"]:
            member = member & volx_ok.fillna(False)
        w = member.astype("float32")
        w = w.div(w.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
        if g["regime"]:
            w = w.mul(pd.Series(regime_ok, index=mdates).astype("float32"), axis=0)
        port = (w * fwd.fillna(0.0)).sum(axis=1)
        turn = (w - w.shift(1).fillna(0.0)).abs().sum(axis=1) / 2
        series[gi] = (port - turn * 2 * COST_PER_SIDE).iloc[:-1]

    results = {"windows": [], "grid_size": len(GRID)}
    for wdef in WINDOWS:
        o0, o1 = map(pd.Timestamp, wdef["opt"])
        v0, v1 = map(pd.Timestamp, wdef["val"])
        scored = []
        for gi, g in enumerate(GRID):
            s = series[gi]
            st = stats(s[(s.index >= o0) & (s.index <= o1)])
            if st.get("cagr") is not None:
                scored.append({"i": gi, "cfg": g, **st})
        scored.sort(key=lambda r: (-r["sharpe"], -(r["cagr"] or 0)))
        win = scored[0]
        sv = series[win["i"]]
        val = stats(sv[(sv.index >= v0) & (sv.index <= v1)])
        bb = stats(bench[(bench.index >= v0) & (bench.index <= v1)])
        wrec = {"window": wdef["name"], "winner_cfg": win["cfg"],
                "winner_opt": {k: win[k] for k in ("cagr", "sharpe", "max_dd")},
                "validation": val, "bench_universe": bb,
                "oos_alpha": round(val["cagr"] - bb["cagr"], 2),
                "top3_opt": scored[:3]}
        results["windows"].append(wrec)
        log({"event": "window_done", **{k: wrec[k] for k in
             ("window", "winner_cfg", "validation", "bench_universe", "oos_alpha")}})

    alphas = [w["oos_alpha"] for w in results["windows"]]
    results["summary"] = {
        "positive_oos_windows": sum(1 for a in alphas if a > 0),
        "avg_oos_alpha": round(float(np.mean(alphas)), 2),
        "runtime_min": round((time.time() - t0) / 60, 1),
        "caveats": [
            "SURVIVORSHIP: current N200 membership backfilled (PIT pending)",
            "liquidity floor Rs 1 Cr median daily turnover, 60d",
            "costs 0.15%/side on turnover",
            "monthly rebalance approximation of the live engine",
        ],
    }
    with open(OUT_JSON, "w") as fh:
        json.dump(results, fh, indent=2)
    log({"event": "done", **results["summary"]})


if __name__ == "__main__":
    main()
