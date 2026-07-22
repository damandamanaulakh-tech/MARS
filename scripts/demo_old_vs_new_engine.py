"""Sample demonstration: OLD engine rules vs NEW engine (final set + v4).

Same real data (191-ticker Upstox export + real Nifty/VIX), same costs,
monthly machinery as searches v3/v4. Differences under test:
  OLD: top-40 book (the ~50-position era), hard regime brake (all-cash in
       correction, no re-entry).
  NEW: top-25 concentrated (P3/final set), VIX-crush re-entry inside
       corrections (v4) — everything else identical, so the delta IS the
       approved changes.
Outputs summary stats + equity-curve PNG + what NEW held at its most
recent re-entry (the tangible sample).
"""

from __future__ import annotations

import io
import json
import zipfile

import numpy as np
import pandas as pd

ZIP = "data/history_upstox_export_2026-07-19.zip"
IDX = "data/index_daily_15y.csv"
COST = 0.0015
TURNOVER_FLOOR = 1e7

frames = {}
with zipfile.ZipFile(ZIP) as z:
    for name in z.namelist():
        if name.endswith(".csv"):
            with z.open(name) as fh:
                frames[name[:-4]] = pd.read_csv(io.TextIOWrapper(fh),
                                                parse_dates=["Date"], index_col="Date")
tickers = sorted(frames)
close = pd.DataFrame({t: frames[t]["Close"] for t in tickers}).sort_index()
volume = pd.DataFrame({t: frames[t]["Volume"] for t in tickers}).reindex(close.index)
idx = pd.read_csv(IDX, dayfirst=True, parse_dates=["trade_date"],
                  index_col="trade_date").sort_index()
nifty = idx["nifty_close"].reindex(close.index).ffill()
vix = idx["india_vix_close"].reindex(close.index).ffill()

di = close.index
me = pd.Series(np.arange(len(di)), index=di).groupby([di.year, di.month]).last().values
mp = close.iloc[me]
mdates = mp.index
fwd = mp.shift(-1) / mp - 1

hist_ok = close.notna().cumsum().iloc[me] >= 252
turn_ok = (close * volume).rolling(60).median().iloc[me] >= TURNOVER_FLOOR
elig = hist_ok & mp.notna() & turn_ok

r126 = mp / mp.shift(6) - 1
r252 = mp / mp.shift(12) - 1
vol6 = mp.pct_change().rolling(6).std()
mom = (r126 / vol6).rank(axis=1, pct=True) + (r252 / vol6).rank(axis=1, pct=True)
mom = mom.where(elig)
abs6 = (mp / mp.shift(6) - 1) > 0

corr_m = (nifty < nifty.rolling(200).mean()).iloc[me].values
vixm = vix.iloc[me]
vix_peak120 = vix.rolling(120).max().iloc[me]
crush_m = (vixm <= 0.75 * vix_peak120).values


def run(N, use_reentry):
    weights = pd.Series(0.0, index=tickers)
    curve, held_log = [1.0], []
    for i in range(13, len(mdates) - 1):
        blocked = corr_m[i] and not (use_reentry and crush_m[i])
        if blocked:
            newW = pd.Series(0.0, index=tickers)
        else:
            cand = mom.iloc[i].where(abs6.iloc[i])
            top = cand.nlargest(N).dropna()
            newW = pd.Series(0.0, index=tickers)
            if len(top):
                newW[top.index] = 1.0 / len(top)
            if corr_m[i]:
                held_log.append((str(mdates[i].date()), list(top.index[:6])))
        turn = float((newW - weights).abs().sum()) / 2
        weights = newW
        ret = float((fwd.iloc[i].fillna(0.0) * weights).sum()) - turn * 2 * COST
        curve.append(curve[-1] * (1 + ret))
    s = pd.Series(curve, index=mdates[12:len(curve) + 12])
    yrs = (len(s) - 1) / 12
    m = s.pct_change().dropna()
    return {"curve": s,
            "total": round(float(s.iloc[-1] - 1) * 100, 1),
            "cagr": round(float(s.iloc[-1] ** (1 / yrs) - 1) * 100, 2),
            "sharpe": round(float(m.mean() / m.std() * np.sqrt(12)), 2),
            "maxdd": round(float(((s - s.cummax()) / s.cummax()).min()) * 100, 1),
            "reentry_holds": held_log}


old = run(40, use_reentry=False)
new = run(25, use_reentry=True)

print(json.dumps({
    "period": f"{old['curve'].index[0].date()} -> {old['curve'].index[-1].date()}",
    "OLD_engine": {k: old[k] for k in ("total", "cagr", "sharpe", "maxdd")},
    "NEW_engine": {k: new[k] for k in ("total", "cagr", "sharpe", "maxdd")},
    "reentry_months_in_corrections": len(new["reentry_holds"]),
    "latest_reentry_sample": new["reentry_holds"][-3:] if new["reentry_holds"] else [],
}, indent=1))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(12, 6))
ax.plot(old["curve"].index, old["curve"].values, label=f"OLD engine  (total +{old['total']}%)",
        color="#94a3b8", lw=1.8)
ax.plot(new["curve"].index, new["curve"].values, label=f"NEW engine  (total +{new['total']}%)",
        color="#059669", lw=2.2)
in_corr = pd.Series(corr_m, index=mdates)
for i in range(12, len(mdates) - 1):
    if corr_m[i]:
        ax.axvspan(mdates[i], mdates[min(i + 1, len(mdates) - 1)],
                   color="#dc2626", alpha=0.07, lw=0)
for d, names in new["reentry_holds"]:
    ax.axvline(pd.Timestamp(d), color="#059669", alpha=0.25, lw=0.8, ls=":")
ax.set_title("Sourceborn engine — OLD rules vs NEW (final set + VIX-crush re-entry)\n"
             "191 real tickers, monthly, costs 0.15%/side · red bands = corrections · "
             "green dots = re-entry active months", fontsize=11)
ax.set_ylabel("Growth of ₹1")
ax.legend(loc="upper left")
ax.grid(alpha=0.25)
fig.tight_layout()
out = "/tmp/claude-0/-home-user-sourceborn-engine/0cfa8919-7563-5aa0-bfc1-4dcab357369b/scratchpad/old_vs_new_engine.png"
fig.savefig(out, dpi=110)
print("chart:", out)
