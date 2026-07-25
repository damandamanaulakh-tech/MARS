"""MTAR reverse-engineering tunnel — user's strategy, 2026-07-24.

Runs MTAR's real daily history through the app's EXISTING parameters,
day by day, with the 191-stock cohort for honest cross-sectional rank.
Marks HIT / NEAR-MISS (within 5 points under the bar) / MISS at every
node, finds the first day the full tunnel would have lit green, and
freezes the key dates for the news step. READ-ONLY on app code.
"""
import io
import json
import sys
import zipfile

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/user/sourceborn-engine")
from trading import candles as C
from trading.config import Config
from trading.scoring import (score_breakout_quality, score_momentum_1m,
                             score_relative_strength, score_vol_adj_upside,
                             score_volume_expansion)

SCRATCH = "/tmp/claude-0/-home-user-sourceborn-engine/0cfa8919-7563-5aa0-bfc1-4dcab357369b/scratchpad"

cfg = Config()
cfg.mode = "nifty200_momentum"
cfg.apply_mode_preset()

# ---- MTAR ----
mtar = pd.read_csv(f"{SCRATCH}/mtar_daily.csv", parse_dates=["Date"], index_col="Date").sort_index()

# ---- cohort: the 191 real tickers (2021+ overlap) ----
closes = {}
with zipfile.ZipFile("/home/user/sourceborn-engine/data/history_upstox_export_2026-07-19.zip") as z:
    for name in z.namelist():
        if name.endswith(".csv"):
            with z.open(name) as fh:
                d = pd.read_csv(io.TextIOWrapper(fh), parse_dates=["Date"], index_col="Date")
            closes[name[:-4]] = d["Close"]
panel = pd.DataFrame(closes).sort_index()
panel = panel[panel.index >= "2020-06-01"]
panel["MTAR"] = mtar["Close"]

# rank-blend momentum exactly like the strategy: (6m & 12m return / vol126), pct-ranked, blended
r126 = panel / panel.shift(126) - 1
r252 = panel / panel.shift(252) - 1
vol126 = panel.pct_change().rolling(126).std()
rank6 = (r126 / vol126).rank(axis=1, pct=True)
rank12 = (r252 / vol126).rank(axis=1, pct=True)
mom_rank = (rank6 * 0.5 + rank12 * 0.5) * 100        # 0-100 like a score

# ---- Nifty benchmark for relative strength + regime ----
idx = pd.read_csv("/home/user/sourceborn-engine/data/index_daily_15y.csv",
                  dayfirst=True, parse_dates=["trade_date"], index_col="trade_date").sort_index()
nifty = idx["nifty_close"]
nifty200 = nifty.rolling(200).mean()

sma200 = mtar["Close"].rolling(200).mean()

CANDLE_FNS = [("breakout", C.breakout_candle), ("urr", C.urr_confirmation),
              ("engulfing", C.bullish_engulfing), ("hammer", C.hammer),
              ("morning_star", C.morning_star), ("piercing", C.piercing_line),
              ("tweezer_bottom", C.tweezer_bottom)]


def tunnel_day(day):
    """All node readings for one date. Returns dict of name -> value."""
    hist = mtar.loc[:day]
    if len(hist) < 60:
        return None
    close, vol = hist["Close"], hist["Volume"]
    bench = nifty.loc[:day]
    out = {}
    out["mom_rank"] = float(mom_rank.loc[:day, "MTAR"].iloc[-1]) if day in mom_rank.index or len(mom_rank.loc[:day]) else np.nan
    out["abs_mom_6m"] = float(close.iloc[-1] / close.iloc[-127] - 1) * 100 if len(close) > 127 else np.nan
    out["momentum_1m"] = score_momentum_1m(close, cfg)
    out["volume_exp"] = score_volume_expansion(vol, cfg)
    out["rel_strength"] = score_relative_strength(close, bench, cfg)
    out["vol_adj_up"] = score_vol_adj_upside(close, cfg)
    out["breakout_q"] = score_breakout_quality(close, cfg)
    pats = [n for n, fn in CANDLE_FNS if fn(hist)]
    out["candle"] = pats[0] if pats else ""
    out["above_200dma"] = float(close.iloc[-1] / sma200.loc[:day].iloc[-1] - 1) * 100 if not np.isnan(sma200.loc[:day].iloc[-1]) else np.nan
    nb = nifty.loc[:day]
    n2 = nifty200.loc[:day]
    out["regime_ok"] = bool(nb.iloc[-1] > n2.iloc[-1]) if len(n2) and not np.isnan(n2.iloc[-1]) else None
    return out


# thresholds of the existing tunnel (the bars each node must clear)
BARS = {"mom_rank": 55.0,        # score floor for the momentum signal
        "abs_mom_6m": 0.0,       # own 6m return must be positive
        "momentum_1m": 60.0, "volume_exp": 60.0, "rel_strength": 60.0,
        "vol_adj_up": 60.0, "breakout_q": 60.0}
NEAR = 5.0


def judge(row):
    marks = {}
    for k, bar in BARS.items():
        v = row.get(k)
        if v is None or (isinstance(v, float) and np.isnan(v)):
            marks[k] = "n/a"
        elif v >= bar:
            marks[k] = "HIT"
        elif v >= bar - NEAR:
            marks[k] = f"NEAR({v:.0f}/{bar:.0f})"
        else:
            marks[k] = f"miss({v:.0f})"
    marks["candle"] = row["candle"] or "none"
    marks["regime"] = "open" if row["regime_ok"] else "BRAKE"
    return marks


# ---- run the window: approach + trough + full rise ----
days = mtar.loc["2024-09-01":].index
rows = {}
for d in days:
    r = tunnel_day(d)
    if r:
        rows[d] = r
T = pd.DataFrame(rows).T

# key dates
trough = mtar.loc["2023-09-11":]["Close"].idxmin()
reclaim = None
for d in T.index:
    if d > trough and T.loc[d, "above_200dma"] > 0:
        reclaim = d
        break
p_tr = mtar.loc[trough, "Close"]
plus50 = next((d for d in T.index if d > trough and mtar.loc[d, "Close"] >= p_tr * 1.5), None)

# first day the FULL tunnel lights green (all bars + candle + regime)
def full_green(d):
    r = T.loc[d]
    return (all(r[k] >= b for k, b in BARS.items() if not np.isnan(r[k]))
            and r["candle"] != "" and r["regime_ok"])

first_green = next((d for d in T.index if d > trough and full_green(d)), None)

print("=== KEY DATES ===")
print("trough:", trough.date(), f"₹{p_tr:.0f}")
print("200DMA reclaim:", reclaim.date() if reclaim is not None else None,
      f"₹{mtar.loc[reclaim,'Close']:.0f}" if reclaim is not None else "")
print("+50% from trough:", plus50.date() if plus50 is not None else None,
      f"₹{mtar.loc[plus50,'Close']:.0f}" if plus50 is not None else "")
print("first FULL-TUNNEL green:", first_green.date() if first_green is not None else "NEVER",
      f"₹{mtar.loc[first_green,'Close']:.0f}" if first_green is not None else "")

print("\n=== TUNNEL READINGS AT THE KEY DATES ===")
for label, d in [("trough", trough), ("200DMA reclaim", reclaim),
                 ("+50%", plus50), ("first full green", first_green)]:
    if d is None or d not in T.index:
        continue
    m = judge(T.loc[d])
    print(f"\n{label} — {d.date()} ₹{mtar.loc[d,'Close']:.0f}")
    for k, v in m.items():
        print(f"   {k:14s} {v}")

# near-miss ledger: days in the rise where >=5 bars HIT but >=1 NEAR blocked full green
print("\n=== NEAR-MISS LEDGER (rise window, tunnel almost open) ===")
count = 0
for d in T.index:
    if d <= trough:
        continue
    r = T.loc[d]
    hits = sum(1 for k, b in BARS.items() if not np.isnan(r[k]) and r[k] >= b)
    nears = [(k, r[k]) for k, b in BARS.items() if not np.isnan(r[k]) and b - NEAR <= r[k] < b]
    if hits >= 5 and nears and count < 12:
        count += 1
        px = mtar.loc[d, "Close"]
        print(f"{d.date()} ₹{px:6.0f}  hits={hits}/7  near: " +
              ", ".join(f"{k}={v:.0f}" for k, v in nears) +
              ("  [candle:" + (r['candle'] or 'none') + "]") +
              ("  [regime BRAKE]" if not r["regime_ok"] else ""))

# how many days in the rise was each node open?
rise = T.loc[T.index > trough]
print(f"\n=== NODE OPEN-RATE DURING THE RISE ({len(rise)} days, trough → now) ===")
for k, b in BARS.items():
    vals = rise[k].astype(float)
    ok = (vals >= b).sum()
    print(f"   {k:14s} open {ok:3d}/{len(rise)} days ({ok/len(rise)*100:.0f}%)")
print(f"   candle         confirmed {sum(1 for x in rise['candle'] if x)}/{len(rise)} days")
print(f"   regime         open {int(rise['regime_ok'].sum())}/{len(rise)} days")

T.to_csv(f"{SCRATCH}/mtar_tunnel_readings.csv")
print("\nsaved full day-by-day readings -> mtar_tunnel_readings.csv")
