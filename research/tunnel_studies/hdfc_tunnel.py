"""TCS through the same tunnel — the falling-giant edition.
[paths repaired 2026-07-27: repo-relative, was the retired sourceborn-engine checkout]
Window 2024-03-01 -> today: pre-peak, the -56% fall, the 3-week-old turn.
Same lanes, same bars, same near-miss band as the MTAR study."""
import io
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

import numpy as np
import pandas as pd

sys.path.insert(0, str(ROOT))
from trading import candles as C
from trading.config import Config
from trading.scoring import (score_breakout_quality, score_momentum_1m,
                             score_relative_strength, score_vol_adj_upside,
                             score_volume_expansion)

S = str(HERE)
cfg = Config(); cfg.mode = "nifty200_momentum"; cfg.apply_mode_preset()

hdfc = pd.read_csv(f"{S}/hdfc_daily.csv", parse_dates=["Date"], index_col="Date").sort_index()

closes = {}
with zipfile.ZipFile(ROOT / "data/history_upstox_export_2026-07-19.zip") as z:
    for name in z.namelist():
        if name.endswith(".csv"):
            with z.open(name) as fh:
                d = pd.read_csv(io.TextIOWrapper(fh), parse_dates=["Date"], index_col="Date")
            closes[name[:-4]] = d["Close"]
panel = pd.DataFrame(closes).sort_index()
panel = panel[panel.index >= "2022-06-01"]
panel = panel.reindex(panel.index.union(hdfc.index)).ffill(limit=5)
panel["HDFCBANK.NS"] = hdfc["Close"]

r126 = panel / panel.shift(126) - 1
r252 = panel / panel.shift(252) - 1
vol126 = panel.pct_change().rolling(126).std()
mom_rank = ((r126 / vol126).rank(axis=1, pct=True) * 0.5 +
            (r252 / vol126).rank(axis=1, pct=True) * 0.5) * 100

idx = pd.read_csv(ROOT / "data/index_daily_15y.csv",
                  dayfirst=True, parse_dates=["trade_date"], index_col="trade_date").sort_index()
nifty = idx["nifty_close"]; nifty200 = nifty.rolling(200).mean()
sma200 = hdfc["Close"].rolling(200).mean()

CANDLE_FNS = [("breakout", C.breakout_candle), ("urr", C.urr_confirmation),
              ("engulfing", C.bullish_engulfing), ("hammer", C.hammer),
              ("morning_star", C.morning_star), ("piercing", C.piercing_line),
              ("tweezer_bottom", C.tweezer_bottom)]
BARS = {"mom_rank": 55.0, "abs_mom_6m": 0.0, "momentum_1m": 60.0,
        "volume_exp": 60.0, "rel_strength": 60.0, "vol_adj_up": 60.0,
        "breakout_q": 60.0}
NEAR = 5.0


def tunnel_day(day):
    hist = hdfc.loc[:day]
    close, vol = hist["Close"], hist["Volume"]
    out = {}
    mr = mom_rank.loc[:day, "HDFCBANK.NS"]
    out["mom_rank"] = float(mr.iloc[-1]) if len(mr) else np.nan
    out["abs_mom_6m"] = float(close.iloc[-1] / close.iloc[-127] - 1) * 100
    out["momentum_1m"] = score_momentum_1m(close, cfg)
    out["volume_exp"] = score_volume_expansion(vol, cfg)
    out["rel_strength"] = score_relative_strength(close, nifty.loc[:day], cfg)
    out["vol_adj_up"] = score_vol_adj_upside(close, cfg)
    out["breakout_q"] = score_breakout_quality(close, cfg)
    pats = [n for n, fn in CANDLE_FNS if fn(hist)]
    out["candle"] = pats[0] if pats else ""
    out["above_200dma"] = float(close.iloc[-1] / sma200.loc[:day].iloc[-1] - 1) * 100
    n2 = nifty200.loc[:day]
    out["regime_ok"] = bool(nifty.loc[:day].iloc[-1] > n2.iloc[-1]) if len(n2) and not np.isnan(n2.iloc[-1]) else None
    return out


days = hdfc.loc["2024-03-01":].index
T = pd.DataFrame({d: tunnel_day(d) for d in days}).T
T.to_csv(f"{S}/hdfc_tunnel_readings.csv")

peak = hdfc.loc["2023":]["Close"].idxmax()
trough = hdfc.loc[peak:]["Close"].idxmin()
today = T.index[-1]


def full_green(d):
    r = T.loc[d]
    return (all(r[k] >= b for k, b in BARS.items() if not np.isnan(r[k]))
            and r["candle"] != "" and r["regime_ok"])


greens = [d for d in T.index if full_green(d)]
print("=== FULL-GREEN DAYS in window:", len(greens))
if greens:
    first = greens[0]
    e_px = hdfc.loc[first, "Close"]
    print("first full green:", first.date(), f"₹{e_px:.0f}")
    # simulate the engine's exits on that entry
    stop = e_px * 0.88
    tgt12, tgt30 = e_px * 1.12, e_px * 1.30
    peak_px = e_px
    exit_d = exit_px = exit_why = None
    for d in hdfc.loc[first:].index[1:]:
        c_ = hdfc.loc[d, "Close"]
        peak_px = max(peak_px, c_)
        trail = peak_px * 0.85
        if c_ <= stop:
            exit_d, exit_px, exit_why = d, c_, "STOP -12%"; break
        if c_ <= trail:
            exit_d, exit_px, exit_why = d, c_, "TRAIL -15%"; break
        if c_ >= tgt12:
            exit_d, exit_px, exit_why = d, c_, "TARGET +12%"; break
    print("engine exit:", exit_d.date() if exit_d is not None else "still held", exit_px, exit_why,
          f"P&L {((exit_px/e_px)-1)*100:+.1f}%" if exit_px else "")
    print("last full green:", greens[-1].date())

print("\n=== FREEZE: PEAK", peak.date(), f"₹{hdfc.loc[peak,'Close']:.0f} ===")
for k in list(BARS) + ["candle", "regime_ok", "above_200dma"]:
    print(f"   {k:14s}", T.loc[peak, k] if peak in T.index else "n/a")
print("\n=== FREEZE: TROUGH", trough.date(), f"₹{hdfc.loc[trough,'Close']:.0f} ===")
for k in list(BARS) + ["candle", "regime_ok", "above_200dma"]:
    print(f"   {k:14s}", T.loc[trough, k])
print("\n=== FREEZE: TODAY", today.date(), f"₹{hdfc.loc[today,'Close']:.0f} ===")
for k in list(BARS) + ["candle", "regime_ok", "above_200dma"]:
    print(f"   {k:14s}", T.loc[today, k])

# protection metric: how dark was the tunnel during the fall?
fall = T.loc[(T.index > peak) & (T.index <= trough)]
fg_fall = sum(1 for d in fall.index if full_green(d))
print(f"\n=== FALL WINDOW ({peak.date()} -> {trough.date()}, {len(fall)} days) ===")
print("full-green days during the -56% fall:", fg_fall)
for k, b in BARS.items():
    ok = int((fall[k].astype(float) >= b).sum())
    print(f"   {k:14s} open {ok:3d}/{len(fall)} ({ok/len(fall)*100:.0f}%)")

# the bounce: near-miss ledger since trough
print(f"\n=== BOUNCE since {trough.date()} ({len(T.loc[T.index>trough])} days) ===")
for d in T.index[T.index > trough]:
    r = T.loc[d]
    hits = sum(1 for k, b in BARS.items() if not np.isnan(r[k]) and r[k] >= b)
    nears = [(k, r[k]) for k, b in BARS.items() if not np.isnan(r[k]) and b - NEAR <= r[k] < b]
    print(f"{d.date()} ₹{hdfc.loc[d,'Close']:6.0f} hits={hits}/7 "
          + (" near:" + ",".join(f"{k}={v:.0f}" for k, v in nears) if nears else "")
          + (f" candle:{r['candle']}" if r["candle"] else "")
          + ("" if r["regime_ok"] else " [BRAKE]"))
