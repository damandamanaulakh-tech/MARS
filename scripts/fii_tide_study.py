"""FII-tide calibration (owner, 2026-08-25: "its not about getting in the
app, its about to use data for selection and scoring") — does FII
positioning data predict forward returns, and on which side?

Before FII derivatives data can touch selection or scoring, the owner's
own proof ladder applies (REQUIRES_15Y_BACKTEST; the danger layer earned
its money-path place exactly this way). This study tests the register's
own FII signals over the committed 13-year PWOI panel against forward
NIFTY returns. FII positioning is MARKET-level data — it can time or
size entries, it cannot rank one stock over another; that is a fact of
the data, not a choice.

SIGNALS (the register's own definitions + symmetric checks):
  S1 FII-04  idx-fut net < rolling 250d q10   (extreme short)
  S2 FII-05  short share > rolling 250d q80   (crowded short)
  S3 tide-   5d change in net < 0             (FII selling pressure)
  S4 long    idx-fut net > rolling 250d q90   (extreme long)
  S5 DII-    DII cash 5d net < -5000 Cr       (2007-2022 daily history)
  S6 FII-cash- FII cash 5d net < -5000 Cr     (the composite's live tide,
                                               tested at last)

METHOD: signal day -> forward 5/10/20d Nifty return; compare mean return
and up-day hit rate vs ALL-day baseline. TRAIN <= 2020-06-30, TEST after
(the threshold-sweep precedent). Every number from committed CSVs —
byte-reproducible offline.

Usage: python scripts/fii_tide_study.py research/fii_tide_study/
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SPLIT = pd.Timestamp("2020-06-30")
HORIZONS = (5, 10, 20)
Q_WIN = 250


def load_nifty() -> pd.Series:
    df = pd.read_csv(DATA / "index_daily_15y.csv",
                     usecols=["trade_date", "nifty_close"])
    df["trade_date"] = pd.to_datetime(df["trade_date"], dayfirst=True,
                                      format="mixed")
    return (df.set_index("trade_date")["nifty_close"].dropna().sort_index())


def load_pwoi() -> pd.DataFrame:
    """FII index-futures long/short 2013->2026 from BOTH committed panels
    (schemas verified against the files: 2013-2023 is long-format
    participant/product rows, 2024-2026 is one wide row per client_type)."""
    old = pd.read_csv(DATA / "fo_participant_oi_2013_2023.csv")
    old = old[(old["participant"] == "FII")
              & (old["product"] == "Future Index")]
    a = pd.DataFrame({
        "date": pd.to_datetime(old["trade_date"]),
        "long": pd.to_numeric(old["long_contracts"], errors="coerce"),
        "short": pd.to_numeric(old["short_contracts"], errors="coerce"),
    })
    new = pd.read_csv(DATA / "fo_participant_oi_2024_2026.csv")
    new = new[new["client_type"] == "FII"]
    b = pd.DataFrame({
        "date": pd.to_datetime(new["date"]),
        "long": pd.to_numeric(new["fut_idx_long"], errors="coerce"),
        "short": pd.to_numeric(new["fut_idx_short"], errors="coerce"),
    })
    out = (pd.concat([a, b], ignore_index=True).dropna()
           .drop_duplicates("date").set_index("date").sort_index())
    out["net_if"] = out["long"] - out["short"]
    out["short_share"] = out["short"] / (out["long"] + out["short"])
    return out


def load_cash() -> pd.DataFrame:
    """FII/DII cash net, daily: 2007-2022 history file + the 2024->now
    file (columns checked against the committed CSVs, not guessed)."""
    hist = pd.read_csv(DATA / "fii_dii_cash_history_2007_2022.csv",
                       usecols=["trade_date", "fii_net_cr", "dii_net_cr"])
    out_h = pd.DataFrame({
        "date": pd.to_datetime(hist["trade_date"]),
        "fii": pd.to_numeric(hist["fii_net_cr"], errors="coerce"),
        "dii": pd.to_numeric(hist["dii_net_cr"], errors="coerce"),
    })
    cur = pd.read_csv(DATA / "fii_dii_cash.csv")
    out_c = pd.DataFrame({
        "date": pd.to_datetime(cur["date"]),
        "fii": pd.to_numeric(cur["fii_cash_net"], errors="coerce"),
        "dii": pd.to_numeric(cur["dii_cash_net"], errors="coerce"),
    })
    both = pd.concat([out_h, out_c], ignore_index=True)
    return (both.dropna(subset=["date"]).drop_duplicates("date")
            .set_index("date").sort_index())


def forward_returns(nifty: pd.Series) -> dict[int, pd.Series]:
    return {h: nifty.shift(-h) / nifty - 1 for h in HORIZONS}


def evaluate(name: str, fire: pd.Series, fwd: dict[int, pd.Series],
             rows: list) -> None:
    fire = fire.dropna()
    for half, mask_dates in (
            ("TRAIN", fire.index <= SPLIT),
            ("TEST", fire.index > SPLIT)):
        sub = fire[mask_dates]
        for h, fr in fwd.items():
            f = fr.reindex(sub.index).dropna()
            sig = f[sub.reindex(f.index).fillna(False).astype(bool)]
            if len(f) < 60:
                continue
            base_mean, base_up = f.mean(), (f > 0).mean()
            rows.append({
                "signal": name, "half": half, "horizon_d": h,
                "fires": int(len(sig)),
                "mean_fwd_pct": round(float(sig.mean() * 100), 3)
                if len(sig) else None,
                "base_mean_pct": round(float(base_mean * 100), 3),
                "edge_pp": round(float((sig.mean() - base_mean) * 100), 3)
                if len(sig) else None,
                "up_rate_pct": round(float((sig > 0).mean() * 100), 1)
                if len(sig) else None,
                "base_up_pct": round(float(base_up * 100), 1),
            })


def main(out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    nifty = load_nifty()
    fwd = forward_returns(nifty)
    pwoi = load_pwoi()
    cash = load_cash()
    rows: list = []

    net = pwoi["net_if"]
    q10 = net.rolling(Q_WIN).quantile(0.10)
    q90 = net.rolling(Q_WIN).quantile(0.90)
    ss = pwoi["short_share"]
    ss_q80 = ss.rolling(Q_WIN).quantile(0.80)

    evaluate("S1_FII04_net_below_q10", net < q10, fwd, rows)
    evaluate("S2_FII05_shortshare_above_q80", ss > ss_q80, fwd, rows)
    evaluate("S3_net_5d_falling", net.diff(5) < 0, fwd, rows)
    evaluate("S4_net_above_q90_extreme_long", net > q90, fwd, rows)
    fii5 = cash["fii"].rolling(5).sum()
    dii5 = cash["dii"].rolling(5).sum()
    evaluate("S5_DII_cash_5d_below_-5000cr", dii5 < -5000, fwd, rows)
    evaluate("S6_FII_cash_5d_below_-5000cr", fii5 < -5000, fwd, rows)
    evaluate("S7_FII_cash_5d_above_+5000cr", fii5 > 5000, fwd, rows)

    df = pd.DataFrame(rows)
    df.to_csv(out / "fii_tide_results.csv", index=False)
    meta = {
        "pwoi_span": [str(pwoi.index.min().date()),
                      str(pwoi.index.max().date())],
        "cash_span": [str(cash.index.min().date()),
                      str(cash.index.max().date())],
        "nifty_span": [str(nifty.index.min().date()),
                       str(nifty.index.max().date())],
        "split": str(SPLIT.date()),
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps(meta, indent=1))
    print(df.to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1])
