"""Full-market backtest — the complete fresh universe (2,392 NSE stocks,
7.5y daily from Upstox), old rules vs the new-gen candidates.

Result (Jun-2020 -> Jul-2026, monthly walk, 0.15%/side, regime brake):
  complete market, current rules : 4.66x   maxDD -29.7%
  1,527-stock slice, current     : 5.19x   maxDD -19.5%
  1,527-stock slice, N5 alone    : 5.19x   (identical - never binds)
  1,527-stock slice, full new set: 4.91x   maxDD -21.8%
  Nifty same period              : 2.27x
Verdict: entry rules stay. Breadth was the upgrade, already shipped.

Data: data/full_universe_2392.zip (unzip to full_universe/ first).
"""
import glob
import os

import numpy as np
import pandas as pd

DIR = "full_universe"
IDX = "data/index_daily_15y.csv"
COST, BOOK = 0.0015, 15

files = [f for f in sorted(glob.glob(f"{DIR}/*.csv")) if not os.path.basename(f).startswith("_")]
closes, vols = {}, {}
for f in files:
    t = os.path.basename(f)[:-4]
    d = pd.read_csv(f, parse_dates=["Date"], index_col="Date")
    closes[t], vols[t] = d["Close"].astype("float32"), d["Volume"].astype("float32")
close = pd.DataFrame(closes).sort_index()
volume = pd.DataFrame(vols).reindex(close.index)

idx = pd.read_csv(IDX, dayfirst=True, parse_dates=["trade_date"], index_col="trade_date").sort_index()
nifty = idx["nifty_close"].reindex(close.index).ffill()
regime_open = nifty > nifty.rolling(200).mean()

r126 = close / close.shift(126) - 1
r252 = close / close.shift(252) - 1
vol126 = close.pct_change().rolling(126).std()
rank = ((r126 / vol126).rank(axis=1, pct=True) * 0.5 +
        (r252 / vol126).rank(axis=1, pct=True) * 0.5) * 100
turnover = (close * volume).rolling(60).median()
elig = (close.notna().rolling(252).count() >= 200) & (turnover >= 1e7)

month_ends = [d for d in close.groupby(close.index.to_period("M")).tail(1).index
              if d >= pd.Timestamp("2020-06-30")]
daily_ret = close.pct_change()
tickers = close.columns

curve, w, yearly = [1.0], pd.Series(0.0, index=tickers), {}
for i, m in enumerate(month_ends[:-1]):
    if bool(regime_open.loc[m]):
        cand = rank.loc[m].where(elig.loc[m] & (r126.loc[m] > 0)).nlargest(BOOK).dropna()
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
    yearly[nxt.year] = yearly.get(nxt.year, 1.0) * (1 + seg_r)

arr = np.array(curve)
dd = float(((arr - np.maximum.accumulate(arr)) / np.maximum.accumulate(arr)).min()) * 100
print(f"{close.shape[1]} stocks: {arr[-1]:.2f}x  maxDD {dd:.1f}%  | "
      f"Nifty {(nifty.loc[month_ends[-1]]/nifty.loc[month_ends[0]]-1)*100:+.0f}%")
print({y: f"{(v-1)*100:+.1f}%" for y, v in sorted(yearly.items())})
