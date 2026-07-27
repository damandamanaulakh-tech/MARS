"""COCHIN SHIPYARD through the same tunnel — RECONSTRUCTED 2026-07-27.

The committed cochin_tunnel_readings.csv (1028 rows, 2022-06-01 -> 2026-07-23)
had no generator script in the repo and none in the owner's Drive zip. This
script was rebuilt from the surviving tcs/hdfc/mtar tunnel scripts and is
VERIFIED by regeneration: running it reproduces the committed readings CSV
(see the reconstruction commit for the match evidence). Two properties were
inferred, not recovered: the panel start date and the narrative prints at the
bottom — the day-by-day readings themselves are exact.

Same lanes, same bars, same near-miss band as the MTAR study. Paths are
repo-relative (the originals pointed into the retired sourceborn-engine
checkout and a dead scratchpad).
"""
import io
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from trading import candles as C
from trading.config import Config
from trading.scoring import (score_breakout_quality, score_momentum_1m,
                             score_relative_strength, score_vol_adj_upside,
                             score_volume_expansion)

cfg = Config(); cfg.mode = "nifty200_momentum"; cfg.apply_mode_preset()

cochin = pd.read_csv(HERE / "cochin_daily.csv", parse_dates=["Date"],
                    index_col="Date").sort_index()

closes = {}
with zipfile.ZipFile(ROOT / "data/history_upstox_export_2026-07-19.zip") as z:
    for name in z.namelist():
        if name.endswith(".csv"):
            with z.open(name) as fh:
                d = pd.read_csv(io.TextIOWrapper(fh), parse_dates=["Date"], index_col="Date")
            closes[name[:-4]] = d["Close"]
panel = pd.DataFrame(closes).sort_index()
panel = panel[panel.index >= "2020-06-01"]
panel["COCHINSHIP.NS"] = cochin["Close"]

r126 = panel / panel.shift(126) - 1
r252 = panel / panel.shift(252) - 1
vol126 = panel.pct_change().rolling(126).std()
rank6 = (r126 / vol126).rank(axis=1, pct=True)
rank12 = (r252 / vol126).rank(axis=1, pct=True)
mom_rank = (rank6 * 0.5 + rank12 * 0.5) * 100

idx = pd.read_csv(ROOT / "data/index_daily_15y.csv", dayfirst=True,
                  parse_dates=["trade_date"], index_col="trade_date").sort_index()
nifty = idx["nifty_close"]
nifty200 = nifty.rolling(200).mean()

CANDLE_FNS = [("breakout", C.breakout_candle), ("urr", C.urr_confirmation),
              ("engulfing", C.bullish_engulfing), ("hammer", C.hammer),
              ("morning_star", C.morning_star), ("piercing", C.piercing_line),
              ("tweezer_bottom", C.tweezer_bottom)]


def tunnel_day(day):
    hist = cochin.loc[:day]
    if len(hist) < 60:
        return None
    close, vol = hist["Close"], hist["Volume"]
    out = {}
    mr = mom_rank.loc[:day, "COCHINSHIP.NS"]
    out["mom_rank"] = float(mr.iloc[-1]) if len(mr) else np.nan
    out["abs_mom_6m"] = float(close.iloc[-1] / close.iloc[-127] - 1) * 100 if len(close) > 127 else np.nan
    out["momentum_1m"] = score_momentum_1m(close, cfg)
    out["volume_exp"] = score_volume_expansion(vol, cfg)
    out["rel_strength"] = score_relative_strength(close, nifty.loc[:day], cfg)
    out["vol_adj_up"] = score_vol_adj_upside(close, cfg)
    out["breakout_q"] = score_breakout_quality(close, cfg)
    pats = [n for n, fn in CANDLE_FNS if fn(hist)]
    out["candle"] = pats[0] if pats else ""
    n2 = nifty200.loc[:day]
    out["regime_ok"] = bool(nifty.loc[:day].iloc[-1] > n2.iloc[-1]) if len(n2) and not np.isnan(n2.iloc[-1]) else None
    return out


BARS = {"mom_rank": 55.0, "abs_mom_6m": 0.0,
        "momentum_1m": 60.0, "volume_exp": 60.0, "rel_strength": 60.0,
        "vol_adj_up": 60.0, "breakout_q": 60.0}
NEAR = 5.0

days = cochin.loc["2022-06-01":].index
rows = {}
for d in days:
    r = tunnel_day(d)
    if r:
        rows[d] = r
T = pd.DataFrame(rows).T


def full_green(d):
    r = T.loc[d]
    return (all(r[k] >= b for k, b in BARS.items() if not np.isnan(r[k]))
            and r["candle"] != "" and r["regime_ok"])


first_green = next((d for d in T.index if full_green(d)), None)
print("first FULL-TUNNEL green:", first_green.date() if first_green is not None else "NEVER",
      f"₹{cochin.loc[first_green,'Close']:.0f}" if first_green is not None else "")

print(f"\n=== NODE OPEN-RATE ({len(T)} days) ===")
for k, b in BARS.items():
    vals = T[k].astype(float)
    ok = (vals >= b).sum()
    print(f"   {k:14s} open {ok:3d}/{len(T)} days ({ok/len(T)*100:.0f}%)")
print(f"   candle         confirmed {sum(1 for x in T['candle'] if x)}/{len(T)} days")
print(f"   regime         open {int(T['regime_ok'].sum())}/{len(T)} days")

T.to_csv(HERE / "cochin_tunnel_readings.csv")
print("\nsaved full day-by-day readings -> cochin_tunnel_readings.csv")
