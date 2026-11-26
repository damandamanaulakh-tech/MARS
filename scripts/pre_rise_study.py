"""Pre-rise = compression (T12W_0005) + volume ignition (T12W_0057) within 5
sessions — measured the way BT01-BT08 measured each half.

Owner decision 2026-11-26: show the two as ONE signal. Each half was measured
on 2026-08-09 (research/bt_calibration/): 0005 lift 1.063 / 1.146 (main /
strict labels), 0057 lift 0.987 / 1.044. This run measures the COMBINATION,
on every trading day (not a 5-day grid — the pair is rare), with the same
labels and the same hit rule, split at 2020-06-30.

  signal day t : T12W_0057 fires on t AND T12W_0005 fired on any of t-5 .. t
  label  day d : close[d+20] / close[d] - 1 >= +10% (main) or +15% (strict)
  hit          : a label day in (t, t+10]            (the BT hit rule)
  chance       : the same hit rule over every evaluated stock-day

The two conditions are evaluated with vectorised copies of the production
formulas; the script first proves they equal trading.triggers._t0005 /
_t0057 bar for bar on a sample before using them.

Usage: python scripts/pre_rise_study.py [out_dir]
"""
from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trading import triggers as TR                                   # noqa: E402

ZIP = ROOT / "data" / "history_upstox_export_2026-07-19.zip"
MIN_BARS = 300
FWD = 20
WITHIN = 5                      # owner default: ignition within 5 sessions of the compression
SPLIT = pd.Timestamp("2020-06-30")
LABELS = {"main_10pct": 0.10, "strict_15pct": 0.15}


def load_panel() -> dict[str, pd.DataFrame]:
    out = {}
    with zipfile.ZipFile(ZIP) as z:
        for name in z.namelist():
            if not name.endswith(".csv"):
                continue
            df = pd.read_csv(io.BytesIO(z.read(name)), parse_dates=["Date"],
                             index_col="Date").sort_index()
            if len(df) >= MIN_BARS + FWD + 50:
                out[name[:-4]] = df
    return out


def compression(df: pd.DataFrame) -> np.ndarray:
    """T12W_0005 on every bar: 5 or more inside bars among the last 10."""
    h, l = df["High"].to_numpy(float), df["Low"].to_numpy(float)
    inside = np.zeros(len(df), dtype=float)
    inside[1:] = (h[1:] <= h[:-1]) & (l[1:] >= l[:-1])
    return pd.Series(inside).rolling(10).sum().to_numpy() >= 5


def ignition(df: pd.DataFrame) -> np.ndarray:
    """T12W_0057 on every bar: the 5 bars before today average under 0.75x
    the 20-bar mean (today included, as the production formula has it) and
    today is above 1.5x that mean."""
    v = pd.Series(df["Volume"].to_numpy(float))
    vol20 = v.rolling(20).mean()
    prev5 = v.shift(1).rolling(5).mean()
    return ((prev5 < 0.75 * vol20) & (v > 1.5 * vol20)).to_numpy()


def prove_equal(panel: dict, sample: int = 12, bars: int = 250) -> int:
    """The vectorised conditions must equal the production triggers."""
    checked = 0
    for tick in sorted(panel)[:sample]:
        df = panel[tick]
        comp, ign = compression(df), ignition(df)
        for i in range(len(df) - bars, len(df)):
            x = TR._Ctx(df.iloc[:i + 1].tail(400))
            assert bool(TR._t0005(x)) == bool(comp[i]), (tick, i, "0005")
            assert bool(TR._t0057(x)) == bool(ign[i]), (tick, i, "0057")
            checked += 2
    return checked


def main(out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    panel = load_panel()
    checked = prove_equal(panel)
    rows = []
    for tick, df in sorted(panel.items()):
        close = df["Close"].to_numpy(float)
        n = len(df)
        comp, ign = compression(df), ignition(df)
        comp_recent = pd.Series(comp.astype(float)).rolling(WITHIN + 1, min_periods=1).max().to_numpy() > 0
        signal = ign & comp_recent
        f20 = np.full(n, np.nan)
        f20[:-FWD] = close[FWD:] / close[:-FWD] - 1
        frame = pd.DataFrame({"ticker": tick, "date": df.index, "comp": comp, "ign": ign,
                              "signal": signal, "f20": f20})
        for name, cut in LABELS.items():
            lab = pd.Series((f20 >= cut).astype(float))
            # any label day in (t, t+10]
            frame["hit_" + name] = lab[::-1].rolling(10, min_periods=1).max()[::-1].shift(-1).to_numpy() > 0
        frame = frame.iloc[MIN_BARS:n - FWD - 10]
        rows.append(frame)
    data = pd.concat(rows, ignore_index=True)
    data["half"] = np.where(data["date"] <= SPLIT, "train", "test")

    def cell(sub: pd.DataFrame, col: str, label: str) -> dict:
        fires = sub[sub[col]]
        chance = float(sub["hit_" + label].mean())
        hit = float(fires["hit_" + label].mean()) if len(fires) else float("nan")
        episodes = int(fires.assign(m=fires["date"].dt.to_period("M")).groupby(["ticker", "m"]).ngroups) if len(fires) else 0
        return {"days": int(len(sub)), "fires": int(len(fires)), "episodes": episodes,
                "fire_pct": round(100 * len(fires) / len(sub), 3),
                "hit_pct": round(100 * hit, 2) if len(fires) else None,
                "chance_pct": round(100 * chance, 2),
                "lift": round(hit / chance, 3) if len(fires) and chance else None,
                "mean_fwd20_pct": round(100 * float(fires["f20"].mean()), 2) if len(fires) else None,
                "all_mean_fwd20_pct": round(100 * float(sub["f20"].mean()), 2)}

    result = {"panel": len(panel), "stock_days": int(len(data)), "within_sessions": WITHIN,
              "split": str(SPLIT.date()), "equality_checks": checked, "cells": {}}
    table = []
    for col, name in (("signal", "pre_rise (0057 with 0005 within 5 sessions)"),
                      ("comp", "T12W_0005 alone"), ("ign", "T12W_0057 alone")):
        for label in LABELS:
            for half in ("all", "train", "test"):
                sub = data if half == "all" else data[data["half"] == half]
                c = cell(sub, col, label)
                result["cells"][f"{col}|{label}|{half}"] = c
                table.append({"signal": name, "labels": label, "half": half, **c})
    pd.DataFrame(table).to_csv(out / "pre_rise_calibration.csv", index=False)
    (out / "pre_rise_calibration.json").write_text(json.dumps(result, indent=2) + "\n")
    data[data["signal"]][["ticker", "date", "f20"]].assign(
        f20=lambda d: (d["f20"] * 100).round(2)).to_csv(out / "pre_rise_fires.csv", index=False)
    print(json.dumps({k: v for k, v in result.items() if k != "cells"}))
    print(pd.DataFrame(table).to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "research" / "pre_rise_study" / "run_2026-11-26"))
