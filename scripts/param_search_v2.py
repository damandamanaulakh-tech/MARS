"""Walk-forward momentum search v2 — the breadth referendum.

Data: data/nse_all_stock_data.csv.zip — adjusted-CLOSE panel, 1,417 symbols,
1991-01 -> 2024-07 (687 symbols >= 15y). Close-only, so this tests the
MOMENTUM CORE exactly (the app's 6m/12m ranking family); candle/volume legs
are out of scope until OHLCV lands.

Method — vectorized cross-sectional momentum, monthly rebalance:
  * Investable set per rebalance: >=252 prior sessions of data, price >= 20
    (penny filter), non-null. No volume/mcap in the panel, so no honest
    liquidity screen exists — stated limitation.
  * Signal grid (96): lookback {6m, 12m, 6+12 blend} x signal {plain return,
    vol-adjusted (the app's sharpe-style)} x skip-last-month {0, 1}
    x portfolio size {20, 50} x absolute-momentum gate {on, off}
    x regime brake {off, on = all-cash when EW index < its 200DMA}.
  * Costs: 0.15% per side on turnover (STT + slippage, the app's model).
  * Walk-forward: each config's full monthly series is computed once; per
    window the winner is chosen by Sharpe on the OPTIMIZE slice only, then
    judged on the unseen VALIDATE slice. OOS numbers are the only verdict.
  * Benchmarks: (B1) equal-weight investable universe, monthly rebalanced —
    the exact opportunity set; (B2) equal-weight of the 76 oldest (>=25y)
    names — a mega-cap proxy. Alpha reported vs both.
  * Survivorship: the panel skews to names alive in 2024 — results are
    optimistic; the tag rides in the output.

Run: python scripts/param_search_v2.py   (writes docs/param_search_v2_*.{jsonl,json})
"""

from __future__ import annotations

import itertools
import json
import time
import zipfile

import numpy as np
import pandas as pd

ZIP = "data/nse_all_stock_data.csv.zip"
OUT_JSONL = "docs/param_search_v2_progress.jsonl"
OUT_JSON = "docs/param_search_v2_results.json"
COST_PER_SIDE = 0.0015
PENNY = 20.0
MIN_HIST = 252

WINDOWS = [
    {"name": "W1_2004_07", "opt": ("1996-01-01", "2003-12-31"), "val": ("2004-01-01", "2007-12-31")},
    {"name": "W2_GFC",     "opt": ("1996-01-01", "2007-12-31"), "val": ("2008-01-01", "2010-12-31")},
    {"name": "W3_2011_15", "opt": ("1996-01-01", "2010-12-31"), "val": ("2011-01-01", "2015-12-31")},
    {"name": "W4_2016_19", "opt": ("1996-01-01", "2015-12-31"), "val": ("2016-01-01", "2019-12-31")},
    {"name": "W5_2020_24", "opt": ("1996-01-01", "2019-12-31"), "val": ("2020-01-01", "2024-07-05")},
]


def log(rec: dict) -> None:
    with open(OUT_JSONL, "a") as fh:
        fh.write(json.dumps(rec) + "\n")
    print(json.dumps(rec), flush=True)


def load_panel() -> pd.DataFrame:
    with zipfile.ZipFile(ZIP) as z:
        name = z.namelist()[0]
        with z.open(name) as fh:
            df = pd.read_csv(fh, parse_dates=["Date"], index_col="Date")
    return df.astype("float32")


def month_ends(idx: pd.DatetimeIndex) -> np.ndarray:
    s = pd.Series(np.arange(len(idx)), index=idx)
    return s.groupby([idx.year, idx.month]).last().values


def stats(monthly: pd.Series) -> dict:
    if len(monthly) < 6:
        return {"cagr": None}
    curve = (1 + monthly).cumprod()
    yrs = len(monthly) / 12
    cagr = float(curve.iloc[-1] ** (1 / yrs) - 1) * 100
    sharpe = float(monthly.mean() / monthly.std() * np.sqrt(12)) if monthly.std() > 0 else 0.0
    dd = float(((curve - curve.cummax()) / curve.cummax()).min()) * 100
    total = float(curve.iloc[-1] - 1) * 100
    return {"cagr": round(cagr, 2), "sharpe": round(sharpe, 3),
            "max_dd": round(dd, 2), "total": round(total, 2)}


def main() -> None:
    t0 = time.time()
    px = load_panel()
    me = month_ends(px.index)
    mp = px.iloc[me]                                    # month-end prices
    mdates = mp.index
    fwd = mp.shift(-1) / mp - 1                         # next-month holding return
    hist_len = px.notna().cumsum().iloc[me]             # sessions of history so far

    # eligibility per month-end
    elig = (hist_len >= MIN_HIST) & (mp >= PENNY) & mp.notna()

    # oldest-76 mega-cap proxy (fixed membership, stated crudeness)
    first_valid = px.apply(lambda s: s.first_valid_index())
    oldest76 = list(first_valid.sort_values().index[:76])

    # equal-weight benchmark series
    def ew_series(mask: pd.DataFrame) -> pd.Series:
        r = fwd.where(mask)
        return r.mean(axis=1).dropna()

    b1 = ew_series(elig)                                 # investable universe
    b2 = ew_series(elig[oldest76].reindex(columns=oldest76)
                   if False else elig[oldest76])         # mega-cap proxy
    b2 = fwd[oldest76].where(elig[oldest76]).mean(axis=1).dropna()

    # regime state: EW investable index vs its 200DMA (daily, sampled at me)
    ew_daily = px.where(px >= PENNY).pct_change().mean(axis=1)
    ew_curve = (1 + ew_daily.fillna(0)).cumprod()
    regime_ok_daily = ew_curve > ew_curve.rolling(200).mean()
    regime_ok = regime_ok_daily.iloc[me].values

    def momentum(look_m: int, skip: int, voladj: bool) -> pd.DataFrame:
        past = mp.shift(skip)
        sig = past / past.shift(look_m) - 1
        if voladj:
            vol = mp.pct_change().rolling(look_m).std()
            sig = sig / vol.replace(0, np.nan)
        return sig

    GRID = [{"look": lk, "voladj": va, "skip": sk, "N": n, "absgate": ag, "regime": rg}
            for lk in ("6", "12", "6+12") for va in (False, True)
            for sk in (0, 1) for n in (20, 50) for ag in (True, False)
            for rg in (False, True)]
    log({"event": "start", "grid": len(GRID), "symbols": px.shape[1],
         "months": len(mdates)})

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

    series: dict[int, pd.Series] = {}
    for gi, g in enumerate(GRID):
        sig = sigs[(g["look"], g["voladj"], g["skip"])]
        ranks = sig.rank(axis=1, ascending=False)
        member = ranks <= g["N"]
        if g["absgate"]:
            plain6 = sigs[(("6", False, g["skip"]))]
            member = member & (plain6 > 0)
        w = member.astype("float32")
        rowsum = w.sum(axis=1)
        w = w.div(rowsum.replace(0, np.nan), axis=0).fillna(0.0)
        if g["regime"]:
            w = w.mul(pd.Series(regime_ok, index=mdates).astype("float32"), axis=0)
        port = (w * fwd.fillna(0.0)).sum(axis=1)
        turnover = (w - w.shift(1).fillna(0.0)).abs().sum(axis=1) / 2
        net = port - turnover * 2 * COST_PER_SIDE
        series[gi] = net.iloc[:-1]                      # last month has no fwd
        if gi % 16 == 0:
            log({"event": "cfg", "i": gi, "elapsed_s": round(time.time() - t0, 1)})

    results = {"windows": [], "grid_size": len(GRID)}
    for wdef in WINDOWS:
        o0, o1 = pd.Timestamp(wdef["opt"][0]), pd.Timestamp(wdef["opt"][1])
        v0, v1 = pd.Timestamp(wdef["val"][0]), pd.Timestamp(wdef["val"][1])
        scored = []
        for gi, g in enumerate(GRID):
            s = series[gi]
            st = stats(s[(s.index >= o0) & (s.index <= o1)])
            if st.get("cagr") is None:
                continue
            scored.append({"i": gi, "cfg": g, **st})
        scored.sort(key=lambda r: (-r["sharpe"], -(r["cagr"] or 0)))
        win = scored[0]
        sv = series[win["i"]]
        val = stats(sv[(sv.index >= v0) & (sv.index <= v1)])
        bb1 = stats(b1[(b1.index >= v0) & (b1.index <= v1)])
        bb2 = stats(b2[(b2.index >= v0) & (b2.index <= v1)])
        wrec = {"window": wdef["name"], "opt_span": wdef["opt"], "val_span": wdef["val"],
                "winner_cfg": win["cfg"],
                "winner_opt": {k: win[k] for k in ("cagr", "sharpe", "max_dd")},
                "validation": val,
                "bench_universe": bb1, "bench_megacap": bb2,
                "oos_alpha_vs_universe": (round(val["cagr"] - bb1["cagr"], 2)
                                          if val.get("cagr") is not None and bb1.get("cagr") is not None else None),
                "oos_alpha_vs_megacap": (round(val["cagr"] - bb2["cagr"], 2)
                                         if val.get("cagr") is not None and bb2.get("cagr") is not None else None),
                "top3_opt": scored[:3]}
        results["windows"].append(wrec)
        log({"event": "window_done", **{k: wrec[k] for k in
             ("window", "winner_cfg", "validation", "bench_universe",
              "bench_megacap", "oos_alpha_vs_universe", "oos_alpha_vs_megacap")}})

    a1 = [w["oos_alpha_vs_universe"] for w in results["windows"] if w["oos_alpha_vs_universe"] is not None]
    a2 = [w["oos_alpha_vs_megacap"] for w in results["windows"] if w["oos_alpha_vs_megacap"] is not None]
    results["summary"] = {
        "windows_run": len(results["windows"]),
        "positive_oos_vs_universe": sum(1 for a in a1 if a > 0),
        "positive_oos_vs_megacap": sum(1 for a in a2 if a > 0),
        "avg_oos_alpha_vs_universe": round(float(np.mean(a1)), 2) if a1 else None,
        "avg_oos_alpha_vs_megacap": round(float(np.mean(a2)), 2) if a2 else None,
        "runtime_min": round((time.time() - t0) / 60, 1),
        "caveats": [
            "close-only panel: momentum core only (no candle/volume legs)",
            "no volume/mcap in panel -> no honest liquidity screen; penny filter >=20 only",
            "SURVIVORSHIP-BIASED: panel skews to symbols alive in 2024",
            "costs 0.15%/side on turnover (app's model)",
            "mega-cap benchmark = 76 oldest names (crude proxy, fixed membership)",
        ],
    }
    with open(OUT_JSON, "w") as fh:
        json.dump(results, fh, indent=2)
    log({"event": "done", **results["summary"]})


if __name__ == "__main__":
    main()
