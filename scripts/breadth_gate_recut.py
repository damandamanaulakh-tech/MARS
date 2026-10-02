"""Does the breadth gate block the right days?

The gate blocks MOMENTUM ENTRIES, so the honest test is the forward return
of the ENGINE'S OWN PICKS in each breadth band, not the index's.

Picks are rebuilt with the production selection: momentum_score + quality
blend 35% >= the live floor 62, own 6-month return > 0 (absolute-momentum
gate), timing gate (within 1% of the 20d high). Equal-weight forward 20d.

Band edges are chosen on TRAIN (2015-06 to 2020-06) and only then read on
TEST (2020-07 on). The 2026-08-13 lesson: never sweep a threshold without
a holdout, and never choose the setting by the holdout.
"""
import io, os, sys, warnings, zipfile
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (R-3)
import numpy as np, pandas as pd
from trading.config import Config
from trading.quality import blend_scores, quality_score
from trading.strategies.nifty200_momentum import momentum_score

OUT = os.path.dirname(os.path.abspath(__file__))
R = os.path.join(os.path.dirname(OUT), "data")   # repo-relative (R-3)
SPLIT = pd.Timestamp("2020-07-01")
H, STEP = 20, 5
cfg = Config(mode="nifty200_momentum"); cfg.apply_mode_preset()
FLOOR = cfg.min_score_to_enter
print("live floor", FLOOR, "blend", cfg.quality_blend_pct,
      "breakout thr", cfg.breakout_threshold_pct, "lookback",
      cfg.breadth_gate_enabled,
      # audit R-3 (2026-10-02): breadth_min_pct was deleted by this very
      # study's outcome (2026-09-09); the live rule is the band below.
      cfg.breadth_block_low, cfg.breadth_block_high)

z = zipfile.ZipFile(f"{R}/history_upstox_export_2026-07-19.zip")
CL, VO = {}, {}
for n in z.namelist():
    if not n.endswith(".csv"): continue
    d = pd.read_csv(io.BytesIO(z.read(n)), index_col=0, parse_dates=True)
    CL[n[:-4]], VO[n[:-4]] = d.Close, d.Volume
C = pd.DataFrame(CL).sort_index(); V = pd.DataFrame(VO).reindex(C.index)
print("panel", C.shape)

# BUG FOUND AND FIXED 2026-09-09: the denominator must count only names
# that HAVE a 200DMA. Using C.notna() puts newly-listed names (price, but
# no 200-bar mean yet) in the denominator and never in the numerator, which
# crushes breadth toward 0 in the panel's early years and invents a fake
# "washed out" regime. Same fault class as the Node 10 weak-assertion lesson.
_ma200 = C.rolling(200).mean()
_have = _ma200.notna() & C.notna()
_n200 = _have.sum(axis=1).replace(0, np.nan)
breadth = (C > _ma200).where(_have).sum(axis=1) / _n200
breadth = breadth.where(_n200 >= 100)      # thin panel = no reading
fwd = C.shift(-H) / C - 1
hi20 = C.rolling(20).max()

rows = []
dates = C.index[(C.index >= pd.Timestamp("2015-06-01"))][::STEP]
for i, d in enumerate(dates):
    if d > C.index[-(H + 1)]: break
    win = C.loc[:d]
    if len(win) < 260: continue
    picks = []
    for t in C.columns:
        c = win[t].dropna()
        if len(c) < 260: continue
        ms = momentum_score(c)
        if ms is None: continue
        q = quality_score(c, V[t].loc[:d].dropna())
        s = blend_scores(ms, q, cfg.quality_blend_pct)
        if s < FLOOR: continue
        if float(c.iloc[-1]) <= float(c.iloc[-127]): continue      # abs momentum
        h = hi20[t].loc[d]
        if not (h > 0 and (float(c.iloc[-1]) - h) / h >= -cfg.breakout_threshold_pct):
            continue                                               # timing gate
        f = fwd[t].loc[d]
        if pd.notna(f): picks.append(f)
    if not picks: continue
    rows.append({"date": d, "breadth": float(breadth.loc[d]),
                 "n_picks": len(picks), "fwd": float(np.mean(picks))})
P = pd.DataFrame(rows).set_index("date")
P.to_csv(f"{OUT}/breadth_pick_returns.csv")
print("scan points with picks:", len(P), P.index[0].date(), "->", P.index[-1].date())

tr, te = P[P.index < SPLIT], P[P.index >= SPLIT]
print(f"\nBASELINE pick fwd20: train {tr.fwd.mean()*100:+.2f}% (n={len(tr)})  "
      f"test {te.fwd.mean()*100:+.2f}% (n={len(te)})")

# --- what the LIVE gate does (block at <= 50%) ---
for nm, half in (("train", tr), ("test", te)):
    blk = half[half.breadth <= 0.50]; allw = half[half.breadth > 0.50]
    print(f"LIVE GATE {nm}: blocked days pick fwd {blk.fwd.mean()*100:+.2f}% (n={len(blk)}) | "
          f"allowed {allw.fwd.mean()*100:+.2f}% (n={len(allw)})  "
          f"-> gate {'HELPS' if blk.fwd.mean() < allw.fwd.mean() else 'HURTS'}")

# --- deciles, both halves, to see the shape ---
print("\nPick fwd20 by breadth decile (train | test):")
edges = np.arange(0, 1.01, 0.1)
for lo, hi in zip(edges[:-1], edges[1:]):
    a = tr[(tr.breadth >= lo) & (tr.breadth < hi)]; b = te[(te.breadth >= lo) & (te.breadth < hi)]
    if not len(a) and not len(b): continue
    print(f"  {lo:.0%}-{hi:.0%}  train {a.fwd.mean()*100:+6.2f}% (n={len(a):3d}) | "
          f"test {b.fwd.mean()*100:+6.2f}% (n={len(b):3d})")

# --- choose the block band on TRAIN only ---
best = None
for lo in np.arange(0.20, 0.61, 0.05):
    for hi in np.arange(lo + 0.05, 0.81, 0.05):
        m = (tr.breadth >= lo) & (tr.breadth < hi)
        if m.sum() < 30 or (~m).sum() < 30: continue
        edge = tr.fwd[~m].mean() - tr.fwd[m].mean()      # gain from blocking the band
        if best is None or edge > best[0]: best = (edge, lo, hi, int(m.sum()))
edge, LO, HI, n = best
print(f"\nTRAIN-CHOSEN block band: {LO:.0%} to {HI:.0%}  "
      f"(train blocked n={n}, train edge {edge*100:+.2f}pp)")
for nm, half in (("train", tr), ("test", te)):
    m = (half.breadth >= LO) & (half.breadth < HI)
    print(f"  {nm}: blocked {half.fwd[m].mean()*100:+.2f}% (n={int(m.sum())}) | "
          f"allowed {half.fwd[~m].mean()*100:+.2f}% (n={int((~m).sum())}) | "
          f"edge {(half.fwd[~m].mean()-half.fwd[m].mean())*100:+.2f}pp")

# --- head to head: live gate vs recut vs no gate, same days ---
print("\nAverage pick fwd20 under each rule (days the rule ALLOWS):")
for nm, half in (("train", tr), ("test", te)):
    no_gate = half.fwd.mean()
    live = half.fwd[half.breadth > 0.50].mean()
    recut = half.fwd[~((half.breadth >= LO) & (half.breadth < HI))].mean()
    print(f"  {nm}: no gate {no_gate*100:+.2f}%  live gate {live*100:+.2f}%  "
          f"recut {recut*100:+.2f}%   (days allowed: "
          f"{len(half)}, {(half.breadth>0.50).sum()}, "
          f"{(~((half.breadth>=LO)&(half.breadth<HI))).sum()})")
print(f"\nToday's live breadth 48.2% -> live gate BLOCKS, recut "
      f"{'BLOCKS' if LO <= 0.482 < HI else 'ALLOWS'}")
