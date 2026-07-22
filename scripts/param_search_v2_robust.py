"""Search v2.1 — v2 re-run with surgical data-glitch repair (self-correction).

Trigger: the other AI's Data Quality Investigation found corrupt daily
returns in the same panel v2 used (HINDZINC +5,575%/day, BEL +3,200% then
-97%, CIPLA +1,2xx% ...). Those are data errors, not markets: NSE circuit
limits cap real single-day moves near ±20%.

Repair (surgical, not censorship):
  * any |daily return| > 35% (beyond every circuit, far below split echoes)
    is treated as a bad print: the price is replaced with the prior price,
    iteratively, so spike-and-revert pairs collapse to nothing and one-sided
    jumps are frozen until the series re-anchors.
  * we do NOT clip normal returns (the other AI's ±10-12% daily clipping
    destroys real crashes/rallies and still left them with impossible
    results). Real ±5..20% days survive untouched here.
Everything else — grid, windows, costs, benchmarks, walk-forward — is
identical to scripts/param_search_v2.py. If the 5/5 verdict survives this
repair, it is hardened; if it does not, v2 is retracted.

Writes docs/param_search_v2_1_*.{jsonl,json}; prints repair stats.
"""

from __future__ import annotations

import json
import time
import zipfile

import numpy as np
import pandas as pd

ZIP = "data/nse_all_stock_data.csv.zip"
OUT_JSONL = "docs/param_search_v2_1_progress.jsonl"
OUT_JSON = "docs/param_search_v2_1_results.json"
COST_PER_SIDE = 0.0015
PENNY = 20.0
MIN_HIST = 252
GLITCH = 0.35

WINDOWS = [
    {"name": "W1_2004_07", "opt": ("1996-01-01", "2003-12-31"), "val": ("2004-01-01", "2007-12-31")},
    {"name": "W2_GFC",     "opt": ("1996-01-01", "2007-12-31"), "val": ("2008-01-01", "2010-12-31")},
    {"name": "W3_2011_15", "opt": ("1996-01-01", "2010-12-31"), "val": ("2011-01-01", "2015-12-31")},
    {"name": "W4_2016_19", "opt": ("1996-01-01", "2015-12-31"), "val": ("2016-01-01", "2019-12-31")},
    {"name": "W5_2020_24", "opt": ("1996-01-01", "2019-12-31"), "val": ("2020-01-01", "2024-07-05")},
]


def log(rec):
    with open(OUT_JSONL, "a") as fh:
        fh.write(json.dumps(rec) + "\n")
    print(json.dumps(rec), flush=True)


def load_and_repair() -> tuple[pd.DataFrame, dict]:
    with zipfile.ZipFile(ZIP) as z:
        with z.open(z.namelist()[0]) as fh:
            px = pd.read_csv(fh, parse_dates=["Date"], index_col="Date").astype("float64")
    vals = px.values
    repaired = 0
    for _ in range(6):                     # iterate: chained glitches collapse
        prev = np.vstack([vals[:1], vals[:-1]])
        with np.errstate(invalid="ignore", divide="ignore"):
            r = vals / prev - 1
        bad = (np.abs(r) > GLITCH) & ~np.isnan(r) & (prev > 0)
        n = int(bad.sum())
        if n == 0:
            break
        vals[bad] = prev[bad]              # freeze bad print at prior price
        repaired += n
    px2 = pd.DataFrame(vals, index=px.index, columns=px.columns).astype("float32")
    return px2, {"glitch_prints_repaired": repaired}


def month_ends(idx):
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
    return {"cagr": round(cagr, 2), "sharpe": round(sharpe, 3), "max_dd": round(dd, 2),
            "total": round(float(curve.iloc[-1] - 1) * 100, 2)}


def main():
    t0 = time.time()
    px, repair = load_and_repair()
    log({"event": "repair", **repair})

    me = month_ends(px.index)
    mp = px.iloc[me]
    mdates = mp.index
    fwd = mp.shift(-1) / mp - 1
    hist_len = px.notna().cumsum().iloc[me]
    elig = (hist_len >= MIN_HIST) & (mp >= PENNY) & mp.notna()

    first_valid = px.apply(lambda s: s.first_valid_index())
    oldest76 = list(first_valid.sort_values().index[:76])
    b1 = fwd.where(elig).mean(axis=1).dropna()
    b2 = fwd[oldest76].where(elig[oldest76]).mean(axis=1).dropna()

    ew_daily = px.where(px >= PENNY).pct_change().mean(axis=1)
    ew_curve = (1 + ew_daily.fillna(0)).cumprod()
    regime_ok = (ew_curve > ew_curve.rolling(200).mean()).iloc[me].values

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

    GRID = [{"look": lk, "voladj": va, "skip": sk, "N": n, "absgate": ag, "regime": rg}
            for lk in ("6", "12", "6+12") for va in (False, True)
            for sk in (0, 1) for n in (20, 50) for ag in (True, False)
            for rg in (False, True)]
    log({"event": "start", "grid": len(GRID)})

    series = {}
    for gi, g in enumerate(GRID):
        sig = sigs[(g["look"], g["voladj"], g["skip"])]
        member = sig.rank(axis=1, ascending=False) <= g["N"]
        if g["absgate"]:
            member = member & (sigs[("6", False, g["skip"])] > 0)
        w = member.astype("float32")
        w = w.div(w.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
        if g["regime"]:
            w = w.mul(pd.Series(regime_ok, index=mdates).astype("float32"), axis=0)
        port = (w * fwd.fillna(0.0)).sum(axis=1)
        turnover = (w - w.shift(1).fillna(0.0)).abs().sum(axis=1) / 2
        series[gi] = (port - turnover * 2 * COST_PER_SIDE).iloc[:-1]

    results = {"windows": [], "grid_size": len(GRID), "repair": repair}
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
        bb1 = stats(b1[(b1.index >= v0) & (b1.index <= v1)])
        bb2 = stats(b2[(b2.index >= v0) & (b2.index <= v1)])
        wrec = {"window": wdef["name"], "winner_cfg": win["cfg"],
                "winner_opt": {k: win[k] for k in ("cagr", "sharpe", "max_dd")},
                "validation": val, "bench_universe": bb1, "bench_megacap": bb2,
                "oos_alpha_vs_universe": round(val["cagr"] - bb1["cagr"], 2),
                "oos_alpha_vs_megacap": round(val["cagr"] - bb2["cagr"], 2)}
        results["windows"].append(wrec)
        log({"event": "window_done", **wrec})

    a1 = [w["oos_alpha_vs_universe"] for w in results["windows"]]
    a2 = [w["oos_alpha_vs_megacap"] for w in results["windows"]]
    results["summary"] = {
        "positive_oos_vs_universe": sum(1 for a in a1 if a > 0),
        "positive_oos_vs_megacap": sum(1 for a in a2 if a > 0),
        "avg_oos_alpha_vs_universe": round(float(np.mean(a1)), 2),
        "avg_oos_alpha_vs_megacap": round(float(np.mean(a2)), 2),
        "runtime_min": round((time.time() - t0) / 60, 1),
    }
    with open(OUT_JSON, "w") as fh:
        json.dump(results, fh, indent=2)
    log({"event": "done", **results["summary"]})


if __name__ == "__main__":
    main()
