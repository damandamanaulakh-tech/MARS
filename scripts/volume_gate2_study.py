"""Volume Gate 2 outside Intraday — the test fixed in
research/volume_gate2_study/PLAN.md.

The plan was committed before this script was run. The rule is the owner's
spike mark read by the production function (trading.timing.passes_volume_spike
— the same one the scan calls), the population is the Momentum scan's own
picks in the committed observation table, and every figure the plan lists is
written out whatever it shows.

The ratio is computed vectorised for speed; the script first proves the
vectorised column equals passes_volume_spike bar for bar on a sample of real
(ticker, date) pairs before any of it is used.

Usage: python scripts/volume_gate2_study.py [out_dir]
(an existing result is never overwritten: trading.validation.guard_research_output)
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

from trading.config import Config                                    # noqa: E402
from trading.timing import passes_volume_spike                       # noqa: E402

OBS = ROOT / "research" / "ash_formula_study" / "observations.csv.gz"
ZIP = ROOT / "data" / "history_upstox_export_2026-07-19.zip"
SPLIT = pd.Timestamp("2020-06-30")
FLOOR = 62.0
BUCKETS = [(0.0, 0.75, "under 0.75"), (0.75, 1.0, "0.75 to 1"), (1.0, 1.25, "1 to 1.25"),
           (1.25, 1.5, "1.25 to 1.5"), (1.5, 2.0, "1.5 to 2"), (2.0, 3.0, "2 to 3"),
           (3.0, np.inf, "3 and above")]
MARKS = [1.2, 1.3, 1.5, 2.0]


def _cfg(ratio: float) -> Config:
    cfg = Config()
    cfg.volume_gate2_ratio = float(ratio)
    return cfg


def ratios(volume: pd.Series, lookback: int) -> pd.Series:
    """volume[t] / mean(volume[t-lookback .. t-1]); the bar is not in its own base."""
    base = volume.shift(1).rolling(lookback).mean()
    out = volume / base
    return out.where(base > 0)


def load() -> tuple[pd.DataFrame, dict]:
    cfg = Config()
    lookback = int(cfg.volume_gate2_lookback)
    obs = pd.read_csv(OBS, parse_dates=["date"])
    wanted = (obs["am07"] >= FLOOR) & obs["abs_ok"]
    picks = obs[wanted & obs["bracket"].notna()].copy()
    frames, checked = [], []
    with zipfile.ZipFile(ZIP) as z:
        for name in z.namelist():
            if not name.endswith(".csv"):
                continue
            ticker = name[:-4]
            df = pd.read_csv(io.BytesIO(z.read(name)), parse_dates=["Date"],
                             index_col="Date").sort_index()
            series = ratios(df["Volume"].astype(float), lookback)
            frames.append(pd.DataFrame({"ticker": ticker, "date": series.index,
                                        "ratio": series.to_numpy()}))
            # prove the vectorised column against the production function on
            # this stock's own observation dates (the first two that resolve)
            dates = picks.loc[picks["ticker"] == ticker, "date"]
            for when in dates.head(2):
                if when not in df.index:
                    continue
                tail = df.loc[:when, "Volume"].astype(float)
                ok, prod = passes_volume_spike(tail, cfg)
                mine = series.get(when)
                checked.append({"ticker": ticker, "date": str(when.date()),
                                "production": prod, "vectorised": None if pd.isna(mine) else float(mine),
                                "production_pass": ok})
    ratio = pd.concat(frames, ignore_index=True)
    mismatch = [c for c in checked
                if (c["production"] is None) != (c["vectorised"] is None)
                or (c["production"] is not None
                    and abs(c["production"] - c["vectorised"]) > 1e-9)]
    if mismatch:
        raise SystemExit(f"vectorised ratio disagrees with passes_volume_spike: {mismatch[:3]}")
    merged = picks.merge(ratio, on=["ticker", "date"], how="left")
    dropped = int(merged["ratio"].isna().sum())
    merged = merged[merged["ratio"].notna()].copy()
    merged["pass"] = merged["ratio"] >= float(cfg.volume_gate2_ratio)
    merged["half"] = np.where(merged["date"] <= SPLIT, "to 2020-06-30", "after 2020-06-30")
    counts = {"observations": int(len(obs)),
              "picks_resolved": int(len(picks)),
              "picks_unresolved_left_out": int((wanted & obs["bracket"].isna()).sum()),
              "dropped_no_ratio": dropped, "used": int(len(merged)),
              "production_checks": len(checked), "mark": float(cfg.volume_gate2_ratio),
              "lookback": lookback}
    return merged, counts


def figures(sub: pd.DataFrame) -> dict:
    if not len(sub):
        return {"buys": 0, "mean_outcome_pct": None, "stopped_pct": None, "target_pct": None}
    return {"buys": int(len(sub)),
            "mean_outcome_pct": round(float(sub["bracket"].mean()) * 100, 3),
            "stopped_pct": round(float((sub["exit_why"] == "stop").mean()) * 100, 1),
            "target_pct": round(float((sub["exit_why"] == "target").mean()) * 100, 1)}


def summary(sub: pd.DataFrame) -> dict:
    kept, refused = sub[sub["pass"]], sub[~sub["pass"]]
    all_f, keep_f, ref_f = figures(sub), figures(kept), figures(refused)
    return {"all_picks": all_f, "gate_passes": keep_f, "gate_refuses": ref_f,
            "pass_rate_pct": round(float(sub["pass"].mean()) * 100, 1),
            "kept_minus_refused_points": (None if not (len(kept) and len(refused)) else
                                          round(keep_f["mean_outcome_pct"] - ref_f["mean_outcome_pct"], 3)),
            "kept_minus_all_points": (None if not len(kept) else
                                      round(keep_f["mean_outcome_pct"] - all_f["mean_outcome_pct"], 3))}


def per_bucket(sub: pd.DataFrame) -> list[dict]:
    rows = []
    for low, high, label in BUCKETS:
        g = sub[(sub["ratio"] >= low) & (sub["ratio"] < high)]
        rows.append({"bucket": label, **figures(g)})
    return rows


def per_mark(sub: pd.DataFrame) -> list[dict]:
    rows = []
    for mark in MARKS:
        kept = sub[sub["ratio"] >= mark]
        rows.append({"mark": mark, "pass_rate_pct": round(float((sub["ratio"] >= mark).mean()) * 100, 1),
                     **figures(kept)})
    return rows


def main(out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    from trading.validation import guard_research_output
    cfg = Config()
    guard_research_output(out / "volume_gate2.json",
                          parameters={"study": "volume_gate2_momentum", "floor": FLOOR,
                                      "split": str(SPLIT.date()),
                                      "mark": float(cfg.volume_gate2_ratio),
                                      "lookback": int(cfg.volume_gate2_lookback),
                                      "marks": MARKS,
                                      "buckets": [[b[0], None if b[1] == np.inf else b[1]]
                                                  for b in BUCKETS]})
    picks, counts = load()
    halves = {"whole period": picks,
              "to 2020-06-30": picks[picks["half"] == "to 2020-06-30"],
              "after 2020-06-30": picks[picks["half"] == "after 2020-06-30"]}
    result = {"counts": counts, "split": str(SPLIT.date()),
              "halves": {k: summary(v) for k, v in halves.items()},
              "buckets": {k: per_bucket(v) for k, v in halves.items()},
              "marks": {k: per_mark(v) for k, v in halves.items()},
              "years": []}
    for year, sub in picks.groupby(picks["date"].dt.year):
        s = summary(sub)
        result["years"].append({"year": int(year), "buys": s["all_picks"]["buys"],
                                "pass_rate_pct": s["pass_rate_pct"],
                                "all_mean_pct": s["all_picks"]["mean_outcome_pct"],
                                "kept_mean_pct": s["gate_passes"]["mean_outcome_pct"],
                                "kept_minus_all_points": s["kept_minus_all_points"]})
    a, b = result["halves"]["to 2020-06-30"], result["halves"]["after 2020-06-30"]
    checks = {
        "kept_beats_refused_first_half": (a["kept_minus_refused_points"] or 0) > 0,
        "kept_beats_refused_second_half": (b["kept_minus_refused_points"] or 0) > 0,
        "kept_beats_all_first_half": (a["kept_minus_all_points"] or 0) > 0,
        "kept_beats_all_second_half": (b["kept_minus_all_points"] or 0) > 0,
    }
    result["reading_rule"] = {**checks, "supported": all(checks.values())}
    (out / "volume_gate2.json").write_text(json.dumps(result, indent=2) + "\n")
    bucket_rows = [{"period": k, **r} for k, rows in result["buckets"].items() for r in rows]
    pd.DataFrame(bucket_rows).to_csv(out / "volume_gate2_by_bucket.csv", index=False)
    pd.DataFrame(result["years"]).to_csv(out / "volume_gate2_by_year.csv", index=False)
    print(json.dumps(counts))
    for name, s in result["halves"].items():
        print(f"\n== {name} ==")
        print(pd.DataFrame([{"set": k, **s[k]} for k in ("all_picks", "gate_passes", "gate_refuses")]
                           ).to_string(index=False))
        print("pass rate %", s["pass_rate_pct"], "| kept-refused pts", s["kept_minus_refused_points"],
              "| kept-all pts", s["kept_minus_all_points"])
        print(pd.DataFrame(result["buckets"][name]).to_string(index=False))
        print(pd.DataFrame(result["marks"][name]).to_string(index=False))
    print()
    print(pd.DataFrame(result["years"]).to_string(index=False))
    print(json.dumps(result["reading_rule"]))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1
         else str(ROOT / "research" / "volume_gate2_study" / "run_2026-11-27"))
