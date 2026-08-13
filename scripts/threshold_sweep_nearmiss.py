"""Threshold sweep for the 6 near-miss triggers (owner order 2026-08-13).

The 2026-08-09 calibration rejected 64 of 79 triggers at lift < 1.10. Six of
them missed by less than 0.04, and their numeric thresholds were ASSUMED by
Claude where the owner's sheet said only "threshold" / "fast" / "cluster". So
the rejection might be a bad constant rather than a bad idea. This sweeps each
one's constants over a grid and reports what the best setting actually buys.

HONESTY DESIGN — a sweep without a holdout is curve-fitting, and would hand
the owner a fitted number dressed as evidence:
  * The panel is split in half by date. TRAIN = 2014-06-30 -> 2020-06-30,
    TEST = 2020-07-01 -> 2026-07-17. A setting is CHOSEN on train only, then
    read on test. The test number is the honest one.
  * The engine is validated first: it recomputes each trigger at its
    PRODUCTION setting and must reproduce the committed calibration lift.
    If it cannot, the sweep is not trusted and says so.
  * Every setting tried is written out, not just the winner, so the owner can
    see how many were tried (the multiple-comparison cost) and whether the
    surface is smooth (a real effect) or spiky (noise).
  * Baseline is recomputed inside each half — a rise baseline differs between
    a bull half and a chop half, and comparing to the wrong one would invent
    an edge.

This is measurement only. Nothing here changes a live threshold; the owner's
constants stay as they are until they say otherwise.

Usage: python scripts/threshold_sweep_nearmiss.py out_dir/
"""
from __future__ import annotations

import io
import sys
import zipfile
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ZIP = ROOT / "data" / "history_upstox_export_2026-07-19.zip"
CAL = ROOT / "research" / "bt_calibration" / "run_2026-08-09" / \
    "trigger_calibration.csv"

STEP, MIN_BARS, FWD = 5, 300, 20          # the calibration's own grid
SPLIT = pd.Timestamp("2020-06-30")
MIN_FIRES = 100                            # a setting needs a real sample
RISE_T, FALL_T = 0.10, -0.10

# The production setting of each swept constant, and the committed lift it
# produced — the engine must reproduce these before any sweep is believed.
PROD = {
    "T12W_0005": {"win": 10, "n": 5},
    "T12W_0033": {"lb": 20, "vmult": 0.0},
    "T12W_0034": {"lb": 25, "bwin": 4},
    "T12W_0040": {"lo": 35, "hi": 45},
    "T12W_0066": {"vmult": 1.5, "gap": 0.0},
    "T12W_0086": {"lb": 20, "rwin": 3},
}
SIDE = {"T12W_0005": "RISE", "T12W_0033": "REVERSAL_UP",
        "T12W_0034": "REVERSAL_UP", "T12W_0040": "REVERSAL_UP",
        "T12W_0066": "FALL", "T12W_0086": "FALL"}
GRID = {
    "T12W_0005": {"win": [10, 15, 20], "n": [3, 4, 5, 6, 7]},
    "T12W_0033": {"lb": [10, 15, 20, 30, 40, 60], "vmult": [0.0, 1.0, 1.5]},
    "T12W_0034": {"lb": [15, 20, 25, 40], "bwin": [2, 3, 4, 6]},
    "T12W_0040": {"lo": [25, 30, 35, 40], "hi": [45, 50, 55, 60]},
    "T12W_0066": {"vmult": [1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0],
                  "gap": [0.0, 0.005, 0.01, 0.02]},
    "T12W_0086": {"lb": [10, 15, 20, 30, 40], "rwin": [2, 3, 5]},
}


# ---------------------------------------------------------------------------
# Per-ticker series, then one boolean array per (trigger, setting)
# ---------------------------------------------------------------------------

def _series(df: pd.DataFrame) -> dict:
    o = df["Open"].to_numpy(float)
    h = df["High"].to_numpy(float)
    lo = df["Low"].to_numpy(float)
    c = df["Close"].to_numpy(float)
    v = df["Volume"].to_numpy(float)
    s = pd.Series
    # rsi14 exactly as trading.triggers._Ctx builds it
    delta = np.diff(c, prepend=c[0])
    gain = s(np.where(delta > 0, delta, 0.0)).rolling(14).mean()
    loss = s(np.where(delta < 0, -delta, 0.0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = (100 - 100 / (1 + rs)).to_numpy()
    inside = np.zeros(len(c), bool)
    inside[1:] = (h[1:] <= h[:-1]) & (lo[1:] >= lo[:-1])
    return {"o": o, "h": h, "l": lo, "c": c, "v": v, "rsi": rsi,
            "vol20": s(v).rolling(20).mean().to_numpy(),
            "inside": inside, "dates": df.index}


def _rolling_min_prior(arr: np.ndarray, window: int, skip: int) -> np.ndarray:
    """min(arr[t-window-skip+1 : t-skip+1]) — the production slices are all of
    the form min(l[-lb:-skip]), i.e. a window that ENDS `skip` bars back."""
    s = pd.Series(arr).shift(skip).rolling(window - skip).min()
    return s.to_numpy()


def fire_array(tid: str, d: dict, p: dict) -> np.ndarray:
    """Boolean fire series for one trigger at one setting, all bars at once."""
    c, o, h, l, v = d["c"], d["o"], d["h"], d["l"], d["v"]
    n = len(c)
    out = np.zeros(n, bool)

    if tid == "T12W_0005":                      # inside-bar cluster
        cnt = pd.Series(d["inside"]).rolling(p["win"]).sum().to_numpy()
        out = cnt >= p["n"]

    elif tid == "T12W_0033":                    # support sweep reclaim
        s = _rolling_min_prior(l, p["lb"], 1)
        out = (l < s) & (c > s)
        if p["vmult"] > 0:
            out &= v > p["vmult"] * d["vol20"]

    elif tid == "T12W_0034":                    # failed breakdown reclaim
        s = _rolling_min_prior(l, p["lb"], 5)
        broke = (pd.Series(c).shift(1).rolling(p["bwin"]).min().to_numpy() < s)
        out = broke & (c > s)

    elif tid == "T12W_0040":                    # oversold to neutral
        r = d["rsi"]
        prior_min = pd.Series(r).shift(1).rolling(5).min().to_numpy()
        out = (prior_min < p["lo"]) & (r > p["hi"])

    elif tid == "T12W_0066":                    # gap-up volume rejection
        prev_h = np.concatenate(([np.nan], h[:-1]))
        with np.errstate(invalid="ignore"):
            gap = o / prev_h - 1.0
        out = ((o > prev_h) & (gap >= p["gap"]) & (c < o) &
               (v > p["vmult"] * d["vol20"]))

    elif tid == "T12W_0086":                    # false reclaim trap
        s = _rolling_min_prior(l, p["lb"], 5)
        above = (pd.Series(c).shift(1).rolling(p["rwin"]).max().to_numpy() > s)
        out = above & (c < s)

    return np.nan_to_num(out.astype(float), nan=0.0).astype(bool)


def labels(c: np.ndarray) -> dict[str, np.ndarray]:
    n = len(c)
    f10 = np.full(n, np.nan)
    f20 = np.full(n, np.nan)
    f10[:-10] = c[10:] / c[:-10] - 1
    f20[:-20] = c[20:] / c[:-20] - 1
    return {"RISE": f20 >= RISE_T, "FALL": f20 <= FALL_T,
            "REVERSAL_UP": (f10 <= -0.05) & (f20 >= 0.05)}


def event_soon(lab: np.ndarray) -> np.ndarray:
    """Event label set anywhere in (t, t+10] — the calibration's hit rule."""
    n = len(lab)
    out = np.zeros(n, bool)
    for k in range(1, 11):
        out[:n - k] |= lab[k:]
    return out


def main(out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    committed = pd.read_csv(CAL).set_index("trigger_id")

    tickers = {}
    with zipfile.ZipFile(ZIP) as z:
        for name in z.namelist():
            if not name.endswith(".csv"):
                continue
            df = pd.read_csv(io.BytesIO(z.read(name)), parse_dates=["Date"],
                             index_col="Date").sort_index()
            if len(df) >= MIN_BARS + FWD + 50:
                tickers[name[:-4]] = df
    print(f"panel: {len(tickers)} tickers", flush=True)

    # accumulators: (tid, setting, half) -> [fires, hits]; half -> [win, base]
    acc: dict = {}
    base: dict = {"TRAIN": [0, 0, 0, 0], "TEST": [0, 0, 0, 0], "ALL": [0, 0, 0, 0]}
    # base holds [n_windows, rise_ev, fall_ev, rev_ev]
    ev_idx = {"RISE": 1, "FALL": 2, "REVERSAL_UP": 3}

    for k, (tick, df) in enumerate(sorted(tickers.items())):
        d = _series(df)
        lab = labels(d["c"])
        soon = {e: event_soon(v) for e, v in lab.items()}
        n = len(d["c"])
        grid = np.zeros(n, bool)
        grid[np.arange(MIN_BARS, n - FWD, STEP)] = True
        is_train = np.asarray(pd.DatetimeIndex(d["dates"]) <= SPLIT)
        halves = {"TRAIN": grid & is_train, "TEST": grid & ~is_train,
                  "ALL": grid}
        for half, mask in halves.items():
            base[half][0] += int(mask.sum())
            for e, i in ev_idx.items():
                base[half][i] += int((mask & soon[e]).sum())
        for tid, gr in GRID.items():
            keys = list(gr)
            for combo in product(*(gr[kk] for kk in keys)):
                p = dict(zip(keys, combo))
                fired = fire_array(tid, d, p)
                hit = fired & soon[SIDE[tid]]
                for half, mask in halves.items():
                    key = (tid, tuple(sorted(p.items())), half)
                    a = acc.setdefault(key, [0, 0])
                    a[0] += int((fired & mask).sum())
                    a[1] += int((hit & mask).sum())
        if (k + 1) % 50 == 0:
            print(f"{k + 1}/{len(tickers)}", flush=True)

    base_rate = {}
    for half, b in base.items():
        base_rate[half] = {e: (b[i] / b[0] if b[0] else 0.0)
                           for e, i in ev_idx.items()}
    rows = []
    for (tid, pt, half), (f, hh) in acc.items():
        p = dict(pt)
        br = base_rate[half][SIDE[tid]]
        rows.append({
            "trigger_id": tid, "side": SIDE[tid], "half": half,
            "setting": ", ".join(f"{a}={b}" for a, b in pt),
            "is_production": p == PROD[tid],
            "fires": f, "hits": hh,
            "hit_pct": round(100 * hh / f, 2) if f else None,
            "base_pct": round(100 * br, 2),
            "lift": round((hh / f) / br, 3) if f and br else None,
        })
    res = pd.DataFrame(rows)
    res.to_csv(out / "threshold_sweep_all_settings.csv", index=False)

    # --- engine validation: production setting on ALL must match the run ----
    print("\nENGINE CHECK (recomputed vs committed calibration):")
    ok = True
    for tid in GRID:
        r = res[(res.trigger_id == tid) & res.is_production & (res.half == "ALL")]
        mine = float(r.lift.iloc[0]) if len(r) and r.lift.iloc[0] else float("nan")
        theirs = float(committed.loc[tid, "lift"])
        good = abs(mine - theirs) <= 0.05
        ok &= good
        print(f"  {tid}: recomputed {mine:.3f} vs committed {theirs:.3f} "
              f"{'OK' if good else 'MISMATCH'}")
    print(f"engine {'TRUSTED' if ok else 'NOT TRUSTED — sweep is unreliable'}")

    # --- pick on TRAIN, read on TEST ---------------------------------------
    print("\nCHOSEN ON TRAIN, READ ON TEST (the honest number):")
    summary = []
    for tid in GRID:
        tr = res[(res.trigger_id == tid) & (res.half == "TRAIN") &
                 (res.fires >= MIN_FIRES)].dropna(subset=["lift"])
        if tr.empty:
            print(f"  {tid}: no setting reached {MIN_FIRES} fires in train")
            continue
        best = tr.loc[tr.lift.idxmax()]
        te = res[(res.trigger_id == tid) & (res.half == "TEST") &
                 (res.setting == best.setting)]
        prod_all = res[(res.trigger_id == tid) & res.is_production &
                       (res.half == "ALL")].iloc[0]
        prod_te = res[(res.trigger_id == tid) & res.is_production &
                      (res.half == "TEST")].iloc[0]
        te_lift = float(te.lift.iloc[0]) if len(te) and te.lift.iloc[0] else None
        summary.append({
            "trigger_id": tid, "side": SIDE[tid],
            "production_setting": ", ".join(f"{a}={b}" for a, b in
                                            sorted(PROD[tid].items())),
            "production_lift_all": prod_all.lift,
            "production_lift_test": prod_te.lift,
            "best_train_setting": best.setting,
            "best_train_lift": best.lift, "best_train_fires": int(best.fires),
            "same_setting_test_lift": te_lift,
            "same_setting_test_fires": int(te.fires.iloc[0]) if len(te) else 0,
            "settings_tried": int(len(tr)),
            "holds_up": bool(te_lift is not None and te_lift >= 1.10),
        })
        print(f"  {tid} [{SIDE[tid]}] prod {prod_all.lift} (all) / "
              f"{prod_te.lift} (test) → best train '{best.setting}' "
              f"lift {best.lift} on {int(best.fires)} fires → "
              f"TEST lift {te_lift} on "
              f"{int(te.fires.iloc[0]) if len(te) else 0} fires "
              f"{'HOLDS' if (te_lift or 0) >= 1.10 else 'FAILS OUT OF SAMPLE'}")
    pd.DataFrame(summary).to_csv(out / "threshold_sweep_summary.csv",
                                 index=False)
    print("\nDONE — all settings in threshold_sweep_all_settings.csv")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "research/threshold_sweep")
