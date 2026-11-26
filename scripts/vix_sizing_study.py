"""VIX size multiplier — the test fixed in research/vix_sizing_study/PLAN.md.

The plan was committed before this script was run. The rule is the owner's
five-band ladder (trading.sizing.vix_size_multiplier — the same function the
engine would use), the population is the Momentum scan's own picks in the
committed observation table, and every figure the plan lists is written out
whatever it shows.

Usage: python scripts/vix_sizing_study.py [out_dir]
(an existing result is never overwritten: trading.validation.guard_research_output)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trading.sizing import vix_band_label, vix_size_multiplier      # noqa: E402

OBS = ROOT / "research" / "ash_formula_study" / "observations.csv.gz"
INDEX = ROOT / "data" / "index_daily_15y.csv"
SPLIT = pd.Timestamp("2020-06-30")
FLOOR = 62.0
VIX_MAX_AGE_DAYS = 5
BANDS = ["under 12", "12 to under 16", "16 to under 20", "20 to under 25", "25 and above"]


def load() -> tuple[pd.DataFrame, dict]:
    obs = pd.read_csv(OBS, parse_dates=["date"])
    picks = obs[(obs["am07"] >= FLOOR) & (obs["abs_ok"]) & obs["bracket"].notna()].copy()
    idx = pd.read_csv(INDEX)
    idx["date"] = pd.to_datetime(idx["trade_date"], format="%d-%m-%Y")
    vix = idx[["date", "india_vix_close"]].dropna().sort_values("date")
    picks = pd.merge_asof(picks.sort_values("date"), vix.rename(columns={"date": "vix_date"}),
                          left_on="date", right_on="vix_date", direction="backward")
    age = (picks["date"] - picks["vix_date"]).dt.days
    dropped = int((age.isna() | (age > VIX_MAX_AGE_DAYS)).sum())
    picks = picks[age.notna() & (age <= VIX_MAX_AGE_DAYS)].copy()
    picks["vix"] = picks["india_vix_close"].astype(float)
    picks["mult"] = picks["vix"].map(vix_size_multiplier)
    picks["band"] = picks["vix"].map(vix_band_label)
    picks["half"] = np.where(picks["date"] <= SPLIT, "to 2020-06-30", "after 2020-06-30")
    counts = {"observations": int(len(obs)),
              "picks_resolved": int(((obs["am07"] >= FLOOR) & obs["abs_ok"] & obs["bracket"].notna()).sum()),
              "picks_unresolved_left_out": int(((obs["am07"] >= FLOOR) & obs["abs_ok"] & obs["bracket"].isna()).sum()),
              "dropped_no_vix": dropped, "used": int(len(picks))}
    return picks, counts


def per_band(sub: pd.DataFrame) -> list[dict]:
    rows = []
    for band in BANDS:
        g = sub[sub["band"] == band]
        rows.append({"band": band, "buys": int(len(g)),
                     "multiplier": float(g["mult"].iloc[0]) if len(g) else None,
                     "mean_outcome_pct": round(100 * float(g["bracket"].mean()), 3) if len(g) else None,
                     "stopped_pct": round(100 * float((g["exit_why"] == "stop").mean()), 1) if len(g) else None,
                     "target_pct": round(100 * float((g["exit_why"] == "target").mean()), 1) if len(g) else None})
    return rows


def worst_runs(sub: pd.DataFrame) -> dict:
    """Monthly cohorts of summed outcomes: worst month and worst 6-month run,
    plain and with the multiplier; the multiplied figures are also shown
    divided by the average multiplier (so deploying less is not counted as
    an improvement)."""
    month = sub["date"].dt.to_period("M")
    plain = sub.groupby(month)["bracket"].sum()
    mult = (sub["bracket"] * sub["mult"]).groupby(month).sum()
    avg = float(sub["mult"].mean())
    months = pd.period_range(month.min(), month.max(), freq="M")
    plain, mult = plain.reindex(months, fill_value=0.0), mult.reindex(months, fill_value=0.0)

    def worst(series):
        return float(series.min()), float(series.rolling(6).sum().min())
    p1, p6 = worst(plain)
    m1, m6 = worst(mult)
    return {"worst_month_plain": round(p1, 3), "worst_month_mult": round(m1, 3),
            "worst_month_mult_per_unit": round(m1 / avg, 3),
            "worst_6m_plain": round(p6, 3), "worst_6m_mult": round(m6, 3),
            "worst_6m_mult_per_unit": round(m6 / avg, 3)}


def summary(sub: pd.DataFrame) -> dict:
    plain_mean = float(sub["bracket"].mean())
    weighted = float((sub["bracket"] * sub["mult"]).sum() / sub["mult"].sum())
    return {"buys": int(len(sub)),
            "mean_outcome_pct": round(100 * plain_mean, 3),
            "per_rupee_with_multiplier_pct": round(100 * weighted, 3),
            "per_rupee_difference_points": round(100 * (weighted - plain_mean), 3),
            "sum_outcome": round(float(sub["bracket"].sum()), 3),
            "sum_with_multiplier": round(float((sub["bracket"] * sub["mult"]).sum()), 3),
            "average_multiplier": round(float(sub["mult"].mean()), 4),
            **worst_runs(sub)}


def main(out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    from trading.validation import guard_research_output
    from trading import sizing
    guard_research_output(out / "vix_sizing.json",
                          parameters={"study": "vix_sizing", "floor": FLOOR, "split": str(SPLIT.date()),
                                      "bands": [list(b) for b in sizing.VIX_SIZE_BANDS],
                                      "top": sizing.VIX_SIZE_TOP, "vix_max_age_days": VIX_MAX_AGE_DAYS})
    picks, counts = load()
    result = {"counts": counts, "split": str(SPLIT.date()), "halves": {}, "bands": {}, "years": []}
    halves = {"whole period": picks, "to 2020-06-30": picks[picks["half"] == "to 2020-06-30"],
              "after 2020-06-30": picks[picks["half"] == "after 2020-06-30"]}
    band_rows = []
    for name, sub in halves.items():
        result["halves"][name] = summary(sub)
        result["bands"][name] = per_band(sub)
        band_rows += [{"period": name, **r} for r in result["bands"][name]]
    for year, sub in picks.groupby(picks["date"].dt.year):
        s = summary(sub)
        result["years"].append({"year": int(year), **{k: s[k] for k in (
            "buys", "mean_outcome_pct", "per_rupee_with_multiplier_pct", "per_rupee_difference_points",
            "sum_outcome", "sum_with_multiplier", "average_multiplier")}})
    # the reading rule fixed in the plan
    a, b = result["halves"]["to 2020-06-30"], result["halves"]["after 2020-06-30"]
    checks = {
        "per_rupee_at_least_plain_first_half": a["per_rupee_difference_points"] >= 0,
        "per_rupee_at_least_plain_second_half": b["per_rupee_difference_points"] >= 0,
        "worst_6m_less_negative_first_half": a["worst_6m_mult_per_unit"] > a["worst_6m_plain"],
        "worst_6m_less_negative_second_half": b["worst_6m_mult_per_unit"] > b["worst_6m_plain"],
    }
    result["reading_rule"] = {**checks, "supported": all(checks.values())}
    (out / "vix_sizing.json").write_text(json.dumps(result, indent=2) + "\n")
    pd.DataFrame(band_rows).to_csv(out / "vix_sizing_by_band.csv", index=False)
    pd.DataFrame(result["years"]).to_csv(out / "vix_sizing_by_year.csv", index=False)
    print(json.dumps(counts))
    print(pd.DataFrame(band_rows).to_string(index=False))
    print(pd.DataFrame([{"period": k, **v} for k, v in result["halves"].items()]).T.to_string())
    print(pd.DataFrame(result["years"]).to_string(index=False))
    print(json.dumps(result["reading_rule"]))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "research" / "vix_sizing_study" / "run_2026-11-26"))
