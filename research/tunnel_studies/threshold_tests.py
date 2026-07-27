"""Offline threshold tests for the new-gen rows (user pick, 2026-07-24).

N5 — abs-momentum tolerance: does entering at own-6m-return > -2%
     (instead of > 0) help or hurt, portfolio-level, monthly walk?
N6 — rank near-band: do entries taken in the 50-55 rank band perform
     like entries >= 55, once other lanes agree? (event study)
N9 — exit policy: +12% target vs +30% target vs trail-only, on the
     SAME entry events across all 191 stocks, 2016-2026. (event study)

Data: the committed 191x12y Upstox store + real Nifty for regime.
Costs 0.15%/side in the monthly walk; event studies report gross moves
(costs identical across policies, so comparisons stand).
No app code touched.
"""
import io
import json
import zipfile

import numpy as np
import pandas as pd

from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parents[2]
ZIP = str(_ROOT / "data/history_upstox_export_2026-07-19.zip")
IDX = str(_ROOT / "data/index_daily_15y.csv")
COST = 0.0015
BOOK = 15

frames = {}
with zipfile.ZipFile(ZIP) as z:
    for name in z.namelist():
        if name.endswith(".csv"):
            with z.open(name) as fh:
                frames[name[:-4]] = pd.read_csv(io.TextIOWrapper(fh),
                                                parse_dates=["Date"], index_col="Date")
tickers = sorted(frames)
close = pd.DataFrame({t: frames[t]["Close"] for t in tickers}).sort_index()
high = pd.DataFrame({t: frames[t]["High"] for t in tickers}).reindex(close.index)
volume = pd.DataFrame({t: frames[t]["Volume"] for t in tickers}).reindex(close.index)

idx = pd.read_csv(IDX, dayfirst=True, parse_dates=["trade_date"],
                  index_col="trade_date").sort_index()
nifty = idx["nifty_close"].reindex(close.index).ffill()
regime_open = nifty > nifty.rolling(200).mean()

r126 = close / close.shift(126) - 1
r252 = close / close.shift(252) - 1
vol126 = close.pct_change().rolling(126).std()
rank = ((r126 / vol126).rank(axis=1, pct=True) * 0.5 +
        (r252 / vol126).rank(axis=1, pct=True) * 0.5) * 100
turnover = (close * volume).rolling(60).median()
hist_ok = close.notna().rolling(252).count() >= 200
elig = hist_ok & (turnover >= 1e7)

# ---------------- Test A: N5 monthly walk, baseline vs tolerance ----------------
month_ends = close.groupby(close.index.to_period("M")).tail(1).index
month_ends = [d for d in month_ends if d >= pd.Timestamp("2016-06-30")]
daily_ret = close.pct_change()


def run_walk(absmom_floor):
    curve = [1.0]
    w = pd.Series(0.0, index=tickers)
    yearly = {}
    for i, m in enumerate(month_ends[:-1]):
        if bool(regime_open.loc[m]):
            ok = elig.loc[m] & (r126.loc[m] > absmom_floor)
            cand = rank.loc[m].where(ok).nlargest(BOOK).dropna()
            newW = pd.Series(0.0, index=tickers)
            if len(cand):
                newW[cand.index] = 1.0 / len(cand)
        else:
            newW = pd.Series(0.0, index=tickers)
        turn = float((newW - w).abs().sum()) / 2
        w = newW
        nxt = month_ends[i + 1]
        seg = daily_ret.loc[m:nxt].iloc[1:].fillna(0.0)
        seg_r = float((1 + seg @ w).prod() - 1) - turn * 2 * COST
        curve.append(curve[-1] * (1 + seg_r))
        y = nxt.year
        yearly[y] = yearly.get(y, 1.0) * (1 + seg_r)
    arr = np.array(curve)
    dd = float(((arr - np.maximum.accumulate(arr)) / np.maximum.accumulate(arr)).min()) * 100
    return arr[-1], dd, {y: round((v - 1) * 100, 1) for y, v in yearly.items()}


base_eq, base_dd, base_y = run_walk(0.0)
tol_eq, tol_dd, tol_y = run_walk(-0.02)
print("=== TEST A (N5): monthly walk 2016-2026, top-15, regime brake, costs ===")
print(f"BASELINE  absmom>0   : {base_eq:6.2f}x  maxDD {base_dd:6.1f}%")
print(f"N5 tol    absmom>-2% : {tol_eq:6.2f}x  maxDD {tol_dd:6.1f}%")
print("per-year (base | N5):")
for y in sorted(base_y):
    print(f"  {y}: {base_y[y]:+7.1f}% | {tol_y.get(y, 0):+7.1f}%")

# ---------------- Test B: N6 rank near-band event study ----------------
# entries: fresh 20d-high day (candle proxy) + absmom>0 + regime open,
# compare forward returns for rank band [50,55) vs [55,100]
hi20 = close >= close.rolling(20).max()
fwd60 = close.shift(-60) / close - 1
fwd120 = close.shift(-120) / close - 1
sig = hi20 & (r126 > 0) & elig
sig = sig[sig.index >= "2016-06-30"]
band_lo, band_hi = [], []
for t in tickers:
    s = sig[t]
    days = s[s].index
    for d in days:
        rk = rank.loc[d, t]
        if pd.isna(rk) or not bool(regime_open.loc[d]):
            continue
        f60, f120 = fwd60.loc[d, t], fwd120.loc[d, t]
        if pd.isna(f60):
            continue
        rec = (f60, f120)
        if 50 <= rk < 55:
            band_lo.append(rec)
        elif rk >= 55:
            band_hi.append(rec)
lo60 = np.array([a for a, b in band_lo]) * 100
hi60 = np.array([a for a, b in band_hi]) * 100
lo120 = np.array([b for a, b in band_lo if not np.isnan(b)]) * 100
hi120 = np.array([b for a, b in band_hi if not np.isnan(b)]) * 100
print("\n=== TEST B (N6): forward returns of entries by rank band ===")
print(f"band [50,55): n={len(lo60):5d}  fwd60 mean {lo60.mean():+6.2f}%  median {np.median(lo60):+6.2f}%  fwd120 mean {lo120.mean():+6.2f}%")
print(f"band [55+ ) : n={len(hi60):5d}  fwd60 mean {hi60.mean():+6.2f}%  median {np.median(hi60):+6.2f}%  fwd120 mean {hi120.mean():+6.2f}%")

# ---------------- Test C: N9 exit-policy on identical entries ----------------
# entries: rank>=55 & absmom>0 & fresh 20d-high & regime open, one open
# position per ticker at a time; walk each to exit under 3 policies.
def walk_exits(tgt):
    """tgt None = trail-only. stop -12%, trail -15% always."""
    res = []
    for t in tickers:
        s = sig[t]
        days = list(s[s].index)
        pos_until = None
        cl = close[t]
        for d in days:
            rk = rank.loc[d, t]
            if pd.isna(rk) or rk < 55 or not bool(regime_open.loc[d]):
                continue
            if pos_until is not None and d <= pos_until:
                continue
            e = cl.loc[d]
            path = cl.loc[d:].iloc[1:253]          # up to ~1y walk
            pk = e
            out = None
            for dd_, c_ in path.items():
                if np.isnan(c_):
                    continue
                pk = max(pk, c_)
                if c_ <= e * 0.88:
                    out = (dd_, c_ / e - 1); break
                if c_ <= pk * 0.85:
                    out = (dd_, c_ / e - 1); break
                if tgt and c_ >= e * (1 + tgt):
                    out = (dd_, c_ / e - 1); break
            if out is None and len(path):
                out = (path.index[-1], path.iloc[-1] / e - 1)
            if out is None:
                continue
            res.append(out[1])
            pos_until = out[0]
    return np.array(res) * 100


for name, tgt in (("TARGET +12%", 0.12), ("TARGET +30%", 0.30), ("TRAIL-ONLY", None)):
    r = walk_exits(tgt)
    wins = (r > 0).mean() * 100
    print(f"\n=== TEST C (N9): {name} ===  trades {len(r)}")
    print(f"  mean {r.mean():+6.2f}%  median {np.median(r):+6.2f}%  win-rate {wins:4.1f}%")
    print(f"  avg log-growth per trade {np.mean(np.log1p(r/100))*100:+6.2f}%  "
          f"best {r.max():+.0f}%  worst {r.min():+.0f}%")

