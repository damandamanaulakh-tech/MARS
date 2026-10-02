"""Audit F-5 (2026-10-02): freeze the IFR throttle's thresholds.

The IFR throttle compared today's reading with a quantile of the SAME window
it was reading, so it fired at a fixed rate whatever the market did. This
script measures, once, on the committed 191-name x 12-year panel:

  * p90 of IFR's own down-tail series   (share of names with any -3% day
                                         in the last 5 days)
  * p85 of IFR's tail-damage ratio       (down / (down + up + 0.001))

and writes them, with the reference ticker list, to
research/ifr_frozen_2026-10-02/thresholds.json, which trading/ifr.py loads at
import. It also reports how often the throttle is ACTIVE under the old
rolling rule and the new frozen rule, on the real panel and on a random-walk
panel of the same size and volatility.

Run:  python scripts/ifr_frozen_thresholds.py
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

from trading import ifr  # noqa: E402

ZIP = os.path.join(ROOT, "data", "history_upstox_export_2026-07-19.zip")
OUT_DIR = os.path.join(ROOT, "research", "ifr_frozen_2026-10-02")
SPLIT = "2020-06-30"
LIVE_WINDOW = 500          # the momentum scan pulls ~2 years of bars


def load_closes() -> pd.DataFrame:
    z = zipfile.ZipFile(ZIP)
    cols = {}
    for name in z.namelist():
        if name.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(z.read(name)), parse_dates=["Date"])
            cols[name[:-4]] = df.set_index("Date").sort_index()["Close"]
    return pd.concat(cols, axis=1).sort_index()


def series(closes: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """The PRODUCTION functions from trading/ifr.py, so the frozen number and
    the live reading are one computation (the anti-divergence rule)."""
    hist = {t: pd.DataFrame({"Close": closes[t].dropna()})
            for t in closes.columns}
    d = ifr.compute_market_tail_down3_5d(hist)
    u = ifr.compute_market_tail_up3_5d(hist)
    names = closes.pct_change().rolling(ifr.TAIL_WINDOW).count().ge(
        ifr.TAIL_WINDOW).sum(axis=1)
    keep = names[names >= ifr.MIN_REFERENCE_NAMES].index
    d = d.reindex(keep).dropna()
    u = u.reindex(d.index).dropna()
    return d.reindex(u.index), u


def active_rate(down: pd.Series, up: pd.Series, frozen: dict | None) -> float:
    """Share of days the throttle is ACTIVE (fired in the last 22 calendar
    days), replaying the live rule day by day."""
    ratio = down / (down + up + 0.001)
    dates = down.index
    last_fire = None
    active = 0
    counted = 0
    for i in range(LIVE_WINDOW if frozen is None else 40, len(dates)):
        lo = max(0, i - LIVE_WINDOW + 1)
        dw = down.iloc[lo:i + 1]
        rw = ratio.iloc[lo:i + 1]
        if frozen is None:
            t_c = float(dw.quantile(ifr.TAIL_PERCENTILE))
            t_r = float(rw.quantile(ifr.RATIO_PERCENTILE))
        else:
            t_c = frozen["cluster"]
            t_r = frozen["ratio"]
        fires = int((dw.tail(ifr.CLUSTER_WINDOW) > t_c).sum())
        fired = fires >= ifr.CLUSTER_MIN_FIRES or float(rw.iloc[-1]) > t_r
        if fired:
            last_fire = dates[i]
        counted += 1
        if last_fire is not None and (dates[i] - last_fire).days <= \
                ifr.ACTIVE_DAYS_AFTER_FIRE:
            active += 1
    return active / counted if counted else float("nan")


def main() -> None:
    closes = load_closes()
    down, up = series(closes)
    ratio = down / (down + up + 0.001)
    frozen = {
        "cluster": round(float(down.quantile(ifr.TAIL_PERCENTILE)), 6),
        "ratio": round(float(ratio.quantile(ifr.RATIO_PERCENTILE)), 6),
    }
    train = down.index <= SPLIT
    report = {
        "panel": "data/history_upstox_export_2026-07-19.zip",
        "tickers": sorted(closes.columns),
        "days": int(len(down)),
        "first": str(down.index[0].date()),
        "last": str(down.index[-1].date()),
        "cluster_threshold_p90": frozen["cluster"],
        "ratio_threshold_p85": frozen["ratio"],
        "cluster_p90_train": round(float(down[train].quantile(0.90)), 6),
        "cluster_p90_test": round(float(down[~train].quantile(0.90)), 6),
        "ratio_p85_train": round(float(ratio[train].quantile(0.85)), 6),
        "ratio_p85_test": round(float(ratio[~train].quantile(0.85)), 6),
    }

    report["active_rate_old_rolling"] = round(active_rate(down, up, None), 4)
    report["active_rate_new_frozen"] = round(active_rate(down, up, frozen), 4)

    # Random walk of the same shape and volatility: a throttle that means
    # anything should be quiet here.
    rng = np.random.default_rng(7)
    vol = float(closes.pct_change().stack().std())
    rw = pd.DataFrame(
        100 * np.cumprod(1 + rng.normal(0, vol, closes.shape), axis=0),
        index=closes.index, columns=closes.columns)
    rd, ru = series(rw)
    report["random_walk_daily_vol"] = round(vol, 5)
    report["random_walk_active_old_rolling"] = round(active_rate(rd, ru, None), 4)
    report["random_walk_active_new_frozen"] = round(active_rate(rd, ru, frozen), 4)

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "thresholds.json"), "w") as f:
        json.dump(report, f, indent=1)
    show = {k: v for k, v in report.items() if k != "tickers"}
    print(json.dumps(show, indent=1))


if __name__ == "__main__":
    main()
