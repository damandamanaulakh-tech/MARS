"""The owner's Pre-Rise/Fall Trigger Layer — wired on their order.

Provenance: every trigger, family, side, weight and gate in this module comes
from the owner's own design sheets, committed at
research/chityy_archive/trigger_layer_v0_2/ (trigger_params_v0_2.csv,
trigger_score_gates_v0_2.csv, trigger_clusters_v0_2.csv,
pyramid_insert_map_v0_2.csv). The registry below is PARSED FROM that CSV at
import time, so the code can never silently drift from the owner's sheet —
a guard test cross-checks every id, family, side and weight.

Order history:
  2026-08-04 owner: "how many filtration i was looking for but u are not even
  close" → gap measured: 112 designed triggers, 0 wired. Owner then ordered
  "wire it" for the agreed scope: DISPLAY + WATCHLIST ONLY. This module and
  its callers touch no entry and no exit. GATE_RISE_ENTRY_01's verdict is
  shown, never executed — the owner's own sheet marks every trigger
  REQUIRES_15Y_BACKTEST before it earns a place in the money path
  (proof_gap_ledger GAP_TRIG_001).

Honesty rules carried from the rest of the app:
  * 79 of 112 triggers are computable from daily OHLCV. The other 33 need
    data this app does not have (sector/RS panels per-day, delivery %, F&O
    open interest, news/governance flags, bulk deals). Those return
    DATA_NEEDED — never a guessed value, never silently dropped: they are
    listed with exactly what data would unlock them.
  * Where the owner's formula names no number ("threshold", "not extended",
    "near support") the chosen constant is marked ASSUMED in the definition
    and collected in ASSUMED_CONSTANTS for the calibration backtest to fix.
  * The side scores (rise_trigger_score etc.) are weighted-percent-fired;
    the sheets never defined the score formula, so this one is ASSUMED and
    said so on the page. The gate thresholds (>=65 etc.) are the owner's.
  * A trigger whose lookback exceeds the available history returns None
    (not False) and lowers data_quality_score — thin data is visible, not
    silently bullish/bearish.

Boundary (same as lens_v2, test-enforced): trading/engine.py, risk.py and
orders.py must never import this module. The scheduler and web layer call it
after the scan, read-only.
"""

from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

PARAMS_CSV = os.path.join(
    os.path.dirname(__file__), "..", "research", "chityy_archive",
    "trigger_layer_v0_2", "trigger_params_v0_2.csv")

# Families, verbatim from the sheet
F_COMPRESSION = "Compression / Base Before Move"
F_ACCUMULATION = "Stealth Accumulation / Absorption"
F_RECLAIM = "Failed Breakdown / Reclaim"
F_RS = "Relative Strength / Sector Lead"
F_LIQUIDITY = "Liquidity / Volume Transition"
F_DISTRIBUTION = "Distribution / Pre-Fall"
F_COLLAPSE = "Collapse / Risk Tripwire"
F_EVENT = "Event / News / Institution Overlay"

# Constants the owner's formulas name but do not number — every one of these
# is a candidate for the 15Y calibration backtest (BT06 in their design).
# Constants that are no longer guesses: each was chosen by a split-sample
# sweep and carries the run that set it. Kept apart from ASSUMED_CONSTANTS so
# the page can say which numbers are measured and which are still placeholders.
MEASURED_CONSTANTS = {
    # T12W_0066: the sheet said only "gap up ... volume rejection", so the gap
    # size was originally unconstrained (any open above the prior high) and the
    # rule was REJECTED at lift 1.091. The sweep found the gap size IS the
    # information: test-half lift rose monotonically with it at every volume
    # multiple. Owner order 2026-08-13: "wire 0066 with gap 1%".
    "gap_up_min_gap": 0.01,
    # T12W_0040: the sheet said "oversold to neutral FAST" without numbers.
    # RSI below 35 was too shallow to be a real oversold — in both halves of
    # the panel, requiring a DEEPER dip beat a shallower one. Owner order
    # 2026-08-13: "wire 0040 too". The exit level stays 45: train preferred
    # 45, test preferred 50, so that axis is NOT stable and the production
    # value is kept rather than fitted.
    "oversold_entry_rsi": 30.0,
    "oversold_exit_rsi": 45.0,
}
MEASURED_CONSTANT_SOURCE = {
    "gap_up_min_gap": ("threshold sweep 2026-08-13 "
                       "(research/threshold_sweep/): chosen on 2014-2020, "
                       "confirmed on 2020-2026 — train lift 1.217, test 1.227"),
    "oversold_entry_rsi": ("threshold sweep 2026-08-13 "
                           "(research/threshold_sweep/): chosen on 2014-2020 "
                           "(lift 1.162), read on 2020-2026 (1.142) — was 35, "
                           "which scored 1.023 train / 1.094 test"),
    "oversold_exit_rsi": ("UNCHANGED production value — the sweep's two halves "
                          "disagreed on this axis (train best 45, test best "
                          "50), so it was not fitted"),
}

ASSUMED_CONSTANTS = {
    "midpoint_band_frac": 0.25,      # T12W_0001 "within midpoint band"
    "atr_shelf_slope": 0.02,         # T12W_0014 "slope small"
    "quiet_accum_fire": 1.0,         # T12W_0025 composite fire level
    "cluster_count_threshold": 4,    # T12W_0032/0081 "≥ threshold"
    "not_extended_ma20": 1.08,       # T12W_0061 "price not extended"
    "support_touch_band": 0.015,     # T12W_0074 "touches of support"
    "near_level_band": 0.03,         # generic "near support/high"
    "htf_prior_gain": 0.20,          # T12W_0085 "prior strong gain"
    "htf_flag_width": 0.10,          # T12W_0085 "tight flag"
    "score_formula": "weighted % of computable triggers fired (ASSUMED — "
                     "sheets define gates, not the score formula)",
}


# --------------------------------------------------------------------------
# Per-stock context: every shared series computed once, triggers read from it
# --------------------------------------------------------------------------

def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Accept either column case. FIXED 2026-08-09: the production loaders
    (history_store / the repo zip via candle_chart.load_ohlcv) return
    capitalized columns — Open/High/Low/Close/Volume — while this module was
    written against lowercase. Every real run raised KeyError('open'), the
    daily pass journaled TRIGGER_LAYER_FAILED, and the layer never produced a
    single row against real data. The tests passed because their fixtures were
    hand-built lowercase. Normalizing at the boundary makes both shapes work
    and the guard tests now use production-shaped frames."""
    cols = {c.lower(): c for c in df.columns}
    need = ("open", "high", "low", "close", "volume")
    missing = [n for n in need if n not in cols]
    if missing:
        raise KeyError(f"OHLCV columns missing: {missing} (have {list(df.columns)})")
    return df.rename(columns={cols[n]: n for n in need})


class _Ctx:
    """Precomputed arrays for one stock's daily OHLCV (oldest → newest)."""

    def __init__(self, df: pd.DataFrame):
        df = _normalize_ohlcv(df)
        self.n = len(df)
        self.o = df["open"].to_numpy(dtype=float)
        self.h = df["high"].to_numpy(dtype=float)
        self.l = df["low"].to_numpy(dtype=float)
        self.c = df["close"].to_numpy(dtype=float)
        self.v = df["volume"].to_numpy(dtype=float)
        self.rng = self.h - self.l
        safe_rng = np.where(self.rng > 0, self.rng, np.nan)
        self.clv = np.where(self.rng > 0, (self.c - self.l) / safe_rng, 0.5)
        self.upper_wick = np.where(
            self.rng > 0, (self.h - np.maximum(self.o, self.c)) / safe_rng, 0.0)
        self.lower_wick = np.where(
            self.rng > 0, (np.minimum(self.o, self.c) - self.l) / safe_rng, 0.0)
        cs = pd.Series(self.c)
        vs = pd.Series(self.v)
        self.ma10 = cs.rolling(10).mean().to_numpy()
        self.ma20 = cs.rolling(20).mean().to_numpy()
        self.ma50 = cs.rolling(50).mean().to_numpy()
        self.vol20 = vs.rolling(20).mean().to_numpy()
        sd20 = cs.rolling(20).std().to_numpy()
        with np.errstate(invalid="ignore", divide="ignore"):
            self.bandwidth = 4.0 * sd20 / self.ma20
        prev_c = np.concatenate(([np.nan], self.c[:-1]))
        tr = np.nanmax(np.vstack([
            self.rng,
            np.abs(self.h - prev_c),
            np.abs(self.l - prev_c),
        ]), axis=0)
        self.tr = tr
        self.atr14 = pd.Series(tr).rolling(14).mean().to_numpy()
        delta = np.diff(self.c, prepend=self.c[0])
        gain = pd.Series(np.where(delta > 0, delta, 0.0)).rolling(14).mean()
        loss = pd.Series(np.where(delta < 0, -delta, 0.0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        self.rsi14 = (100 - 100 / (1 + rs)).to_numpy()
        self.obv = np.cumsum(np.sign(delta) * self.v)
        self.up_day = delta > 0
        self.down_day = delta < 0
        self.prev_c = prev_c

    # -- small helpers -----------------------------------------------------
    def ret(self, days: int) -> float:
        return self.c[-1] / self.c[-1 - days] - 1.0

    def slope(self, arr: np.ndarray, n: int) -> float:
        seg = arr[-n:]
        if len(seg) < n or np.any(~np.isfinite(seg)):
            return np.nan
        return np.polyfit(np.arange(n), seg, 1)[0]

    def pctile(self, arr: np.ndarray, window: int) -> float:
        """Percentile rank (0-100) of arr[-1] within its last `window`."""
        seg = arr[-window:]
        seg = seg[np.isfinite(seg)]
        if len(seg) < max(30, window // 2):
            return np.nan
        return float((seg <= arr[-1]).mean() * 100.0)

    def swing_lows(self, lookback: int = 40, wing: int = 2) -> list[int]:
        idx = []
        start = max(wing, self.n - lookback)
        for i in range(start, self.n - wing):
            w = self.l[i - wing:i + wing + 1]
            if self.l[i] == w.min():
                idx.append(i)
        return idx

    def swing_highs(self, lookback: int = 40, wing: int = 2) -> list[int]:
        idx = []
        start = max(wing, self.n - lookback)
        for i in range(start, self.n - wing):
            w = self.h[i - wing:i + wing + 1]
            if self.h[i] == w.max():
                idx.append(i)
        return idx


# --------------------------------------------------------------------------
# The 79 OHLCV-computable trigger implementations (id → fn(ctx) -> bool)
# Each implements the sheet's formula_or_logic column; ASSUMED constants noted.
# --------------------------------------------------------------------------

def _t0001(x):  # NR7 day inside 10D base
    if x.rng[-1] > np.min(x.rng[-8:-1]):
        return False
    hi10, lo10 = np.max(x.h[-10:]), np.min(x.l[-10:])
    mid, width = (hi10 + lo10) / 2, hi10 - lo10
    return width > 0 and abs(x.c[-1] - mid) <= ASSUMED_CONSTANTS["midpoint_band_frac"] * width


def _t0002(x):  # NR14 compression
    return x.rng[-1] <= np.min(x.rng[-15:-1])


def _t0003(x):  # Bollinger bandwidth squeeze <=15th pctile over 6M
    p = x.pctile(x.bandwidth, 126)
    return None if np.isnan(p) else p <= 15


def _t0004(x):  # ATR percentile bottom 20 over 252d, price above 20D low
    p = x.pctile(x.atr14, 252)
    if np.isnan(p):
        return None
    return p <= 20 and x.c[-1] > np.min(x.l[-20:])


def _t0005(x):  # inside-bar cluster 5 of 10
    cnt = sum(1 for i in range(x.n - 10, x.n)
              if x.h[i] <= x.h[i - 1] and x.l[i] >= x.l[i - 1])
    return cnt >= 5


def _t0006(x):  # base width under 8%
    return (np.max(x.h[-10:]) - np.min(x.l[-10:])) / x.c[-1] <= 0.08


def _t0007(x):  # close above base midline 3 of last 5
    mid = (np.max(x.h[-10:]) + np.min(x.l[-10:])) / 2
    return int(np.sum(x.c[-5:] > mid)) >= 3


def _t0008(x):  # ATR down 3 sessions + rising lows
    a = x.atr14
    if np.any(~np.isfinite(a[-3:])):
        return None
    return a[-1] < a[-2] < a[-3] and x.slope(x.l, 5) > 0


def _t0009(x):  # squeeze + volume dry-up
    p = x.pctile(x.bandwidth, 126)
    if np.isnan(p):
        return None
    return p <= 25 and np.mean(x.v[-5:]) < 0.75 * x.vol20[-1]


def _t0010(x):  # 3-step range contraction, 10D low intact
    return (x.rng[-1] < x.rng[-2] < x.rng[-3]
            and x.c[-1] > np.min(x.l[-10:]))


def _t0011(x):  # tight close cluster
    m = np.mean(x.c[-5:])
    return m > 0 and np.std(x.c[-5:]) / m <= 0.015


def _t0012(x):  # compression with positive drift
    p = x.pctile(x.bandwidth, 126)
    if np.isnan(p):
        return None
    return p <= 25 and x.slope(x.c, 10) > 0


def _t0013(x):  # near 20D high, volume not panic (ASSUMED <2x vol20)
    return (x.c[-1] >= 0.97 * np.max(x.h[-20:])
            and x.v[-1] <= 2.0 * x.vol20[-1])


def _t0014(x):  # volatility shelf (ASSUMED slope band)
    s = x.slope(x.atr14, 5)
    p = x.pctile(x.atr14, 252)
    if np.isnan(s) or np.isnan(p):
        return None
    return abs(s) <= ASSUMED_CONSTANTS["atr_shelf_slope"] * x.atr14[-1] and p <= 30


def _t0015(x):  # micro pullback in base
    dd = x.c[-1] / np.max(x.h[-10:]) - 1.0
    return -0.06 <= dd <= -0.02 and x.l[-1] > np.min(x.l[-10:-1])


def _t0016(x):  # CLV last5 stronger than prior5
    return np.mean(x.clv[-5:]) > np.mean(x.clv[-10:-5])


def _t0017(x):  # OBV rising, price flat
    return x.slope(x.obv, 10) > 0 and abs(x.ret(10)) <= 0.03


def _t0018(x):  # up-volume dominance 7D
    tot = np.sum(x.v[-7:])
    return tot > 0 and np.sum(x.v[-7:][x.up_day[-7:]]) / tot >= 0.62


def _t0019(x):  # positive CLV accumulation
    return np.mean(x.clv[-7:]) >= 0.65


def _t0020(x):  # absorption at resistance
    return (x.c[-1] >= 0.98 * np.max(x.h[-20:])
            and x.v[-1] > 1.3 * x.vol20[-1] and x.clv[-1] >= 0.5)


def _t0021(x):  # volume up, price flat
    return np.mean(x.v[-5:]) > 1.25 * x.vol20[-1] and abs(x.ret(10)) < 0.03


def _t0022(x):  # higher low with volume support
    sl = x.swing_lows()
    if len(sl) < 2:
        return False
    a, b = sl[-2], sl[-1]
    return x.l[b] > x.l[a] and x.v[b] <= x.vol20[b]


def _t0023(x):  # demand wick repetition
    return int(np.sum(x.lower_wick[-7:] > 0.45)) >= 3


def _t0024(x):  # close recovery same day, >=2 of last 7
    cnt = 0
    for i in range(x.n - 7, x.n):
        if x.l[i] < x.l[i - 1] and (x.c[i] > x.o[i] or x.clv[i] > 0.5):
            cnt += 1
    return cnt >= 2


def _t0025(x):  # quiet accumulation composite (ASSUMED fire level)
    slopes = np.array([x.slope(x.obv[:i + 1], 10) for i in range(x.n - 60, x.n)])
    clvs = pd.Series(x.clv).rolling(7).mean().to_numpy()[-60:]
    if np.any(~np.isfinite(slopes)) or np.any(~np.isfinite(clvs)):
        return None
    zs = (slopes[-1] - slopes.mean()) / (slopes.std() or 1)
    zc = (clvs[-1] - clvs.mean()) / (clvs.std() or 1)
    return zs + zc - abs(x.ret(10)) * 100 / 3 >= ASSUMED_CONSTANTS["quiet_accum_fire"]


def _t0026(x):  # base-break volume preview
    r = np.mean(x.v[-3:]) / x.vol20[-1] if x.vol20[-1] else 0
    return 1.15 <= r <= 1.6 and x.c[-1] < np.max(x.h[-20:])


def _t0027(x):  # accumulation without gap
    gaps = np.abs(x.o[-10:] / x.prev_c[-10:] - 1) > 0.03
    return (np.mean(x.clv[-7:]) >= 0.6 and x.slope(x.obv, 10) > 0
            and not np.any(gaps))


def _t0028(x):  # last red-day volume shrink
    recent = x.v[-5:][x.down_day[-5:]]
    prior = x.v[-25:-5][x.down_day[-25:-5]]
    if len(recent) == 0 or len(prior) == 0:
        return False
    return np.mean(recent) < np.mean(prior)


def _t0029(x):  # two green high-CLV days inside base
    greens = np.sum((x.c[-5:] > x.o[-5:]) & (x.clv[-5:] > 0.7))
    return int(greens) >= 2 and x.c[-1] < np.max(x.h[-20:])


def _t0030(x):  # support touch, volume dry
    return (x.l[-1] <= 1.02 * np.min(x.l[-20:-1])
            and x.v[-1] < 0.8 * x.vol20[-1])


def _t0031(x):  # hidden demand delta proxy
    cv = x.clv * x.v
    return np.sum(cv[-5:]) > np.sum(cv[-10:-5]) and abs(x.ret(10)) <= 0.03


def _cluster_fired(x, fam: list) -> int:
    """AUDIT E (2026-08-09): cluster triggers used to call member functions
    directly, bypassing each member's own _MIN_BARS guard — a thin history
    could silently count members the standalone evaluation reports as n/a."""
    n = 0
    for t in fam:
        if x.n < _MIN_BARS.get(t, _MIN_BARS_DEFAULT):
            continue
        if _FNS[t](x) is True:
            n += 1
    return n


def _t0032(x):  # accumulation cluster count (ASSUMED: >=4 of the family today)
    fam = ["T12W_0017", "T12W_0018", "T12W_0019", "T12W_0020", "T12W_0021",
           "T12W_0022", "T12W_0023", "T12W_0024", "T12W_0025", "T12W_0026",
           "T12W_0027", "T12W_0028", "T12W_0029", "T12W_0030", "T12W_0031"]
    return _cluster_fired(x, fam) >= ASSUMED_CONSTANTS["cluster_count_threshold"]


def _support20(x):
    return np.min(x.l[-20:-1])


def _t0033(x):  # support sweep reclaim
    s = _support20(x)
    return x.l[-1] < s and x.c[-1] > s


def _t0034(x):  # failed breakdown, 3D reclaim
    s = np.min(x.l[-25:-5])
    broke = np.any(x.c[-5:-1] < s)
    return bool(broke and x.c[-1] > s)


def _t0035(x):  # bear trap volume reversal
    s = np.min(x.l[-25:-5])
    for i in range(x.n - 5, x.n - 1):
        if x.c[i] < s and x.v[i] > 1.5 * x.vol20[i]:
            return bool(x.c[-1] > x.h[i])
    return False


def _t0036(x):  # low undercut, close high
    sl = x.swing_lows(lookback=20)
    sl = [i for i in sl if i < x.n - 1]
    if not sl:
        return False
    return x.l[-1] < x.l[sl[-1]] and x.clv[-1] >= 0.65


def _t0037(x):  # gap down reclaim
    return x.o[-1] < x.l[-2] and x.c[-1] >= x.c[-2]


def _t0038(x):  # double bottom, stronger right side
    sl = x.swing_lows()
    if len(sl) < 2:
        return False
    a, b = sl[-2], sl[-1]
    near = abs(x.l[b] / x.l[a] - 1) <= 0.02
    return near and x.clv[b] > x.clv[a] and x.v[b] <= x.v[a]


def _t0040(x):  # oversold to neutral fast
    # Entry depth measured 2026-08-13 (MEASURED_CONSTANTS): a dip under 35 was
    # too shallow to mean anything. Exit level left at the production 45.
    r = x.rsi14
    if np.any(~np.isfinite(r[-6:])):
        return None
    return (np.min(r[-6:-1]) < MEASURED_CONSTANTS["oversold_entry_rsi"]
            and r[-1] > MEASURED_CONSTANTS["oversold_exit_rsi"])


def _t0041(x):  # reclaim with volume confirmation
    s = np.min(x.l[-20:-3])
    return x.c[-2] < s and x.c[-1] > s and x.v[-1] > x.vol20[-1]


def _t0042(x):  # three-day failed selloff
    return np.min(x.l[-3:]) < np.min(x.l[-10:-3]) and x.c[-1] > x.c[-3]


def _t0043(x):  # lower-wick cluster at support
    s = np.min(x.l[-20:])
    band = ASSUMED_CONSTANTS["near_level_band"]
    cnt = int(np.sum((x.lower_wick[-10:] > 0.5)
                     & (x.l[-10:] <= s * (1 + band))))
    return cnt >= 3


def _t0044(x):  # panic exhaustion reclaim
    for i in range(x.n - 5, x.n - 2):
        if (np.isfinite(x.atr14[i]) and x.rng[i] > 1.8 * x.atr14[i]
                and x.v[i] > 1.8 * x.vol20[i]):
            mid = (x.h[i] + x.l[i]) / 2
            if x.c[-1] > mid and x.c[-2] > mid:
                return True
    return False


def _t0057(x):  # volume wakeup after dry base
    return (np.mean(x.v[-6:-1]) < 0.75 * x.vol20[-1]
            and x.v[-1] > 1.5 * x.vol20[-1])


def _t0058(x):  # turnover expansion, no price move
    to = x.v * x.c
    return (np.mean(to[-5:]) > 1.5 * np.mean(to[-20:])
            and abs(x.ret(5)) < 0.03)


def _t0061(x):  # volume rate of change (ASSUMED "not extended" = c/ma20<=1.08)
    return (np.mean(x.v[-5:]) / x.vol20[-1] > 1.4
            and x.c[-1] / x.ma20[-1] <= ASSUMED_CONSTANTS["not_extended_ma20"])


def _t0062(x):  # dry-up to expansion ratio
    v5 = pd.Series(x.v).rolling(5).mean().to_numpy()
    ratios = v5[-10:] / x.vol20[-10:]
    return np.nanmin(ratios) < 0.75 and x.v[-1] / x.vol20[-1] > 1.4


def _t0063(x):  # liquidity fade near high
    return (np.mean(x.v[-5:]) / x.vol20[-1] < 0.7
            and x.c[-1] >= 0.97 * np.max(x.h[-20:]))


def _t0064(x):  # distribution volume spike
    tot = np.sum(x.v[-7:])
    down = np.sum(x.v[-7:][x.down_day[-7:]])
    return (tot > 0 and down / tot > 0.58
            and np.mean(x.v[-7:]) > x.vol20[-1])


def _t0065(x):  # high volume, no progress
    return (x.v[-1] > 1.8 * x.vol20[-1]
            and (x.c[-1] <= x.c[-2] or x.clv[-1] < 0.35))


def _t0066(x):  # gap-up volume rejection
    # The gap must be a REAL gap, not merely an open a paisa above yesterday's
    # high: measured 2026-08-13, see MEASURED_CONSTANTS. Volume multiple
    # unchanged at 1.5x (the owner's order named the gap only).
    if x.h[-2] <= 0:
        return None
    gap = x.o[-1] / x.h[-2] - 1.0
    return (gap >= MEASURED_CONSTANTS["gap_up_min_gap"]
            and x.c[-1] < x.o[-1]
            and x.v[-1] > 1.5 * x.vol20[-1])


def _t0067(x):  # liquidity vacuum near support
    band = ASSUMED_CONSTANTS["near_level_band"]
    return (np.mean(x.v[-10:]) / np.mean(x.v[-60:]) < 0.55
            and x.c[-1] <= np.min(x.l[-20:]) * (1 + band))


def _t0068(x):  # panic expansion early
    return (np.isfinite(x.atr14[-1]) and x.tr[-1] > 2 * x.atr14[-1]
            and x.v[-1] > 2 * x.vol20[-1] and x.clv[-1] <= 0.25)


def _t0069(x):  # upthrust above resistance
    res = np.max(x.h[-20:-1])
    return x.h[-1] > res and x.c[-1] < res and x.clv[-1] < 0.4


def _t0070(x):  # failed breakout within 5D
    res = np.max(x.h[-25:-6])
    return bool(np.any(x.c[-6:-1] > res) and x.c[-1] < res)


def _t0071(x):  # new high, negative CLV
    return x.h[-1] >= np.max(x.h[-20:]) and x.clv[-1] < 0.45


def _t0072(x):  # bearish wide-range close
    return (np.isfinite(x.atr14[-1]) and x.rng[-1] > 1.5 * x.atr14[-1]
            and x.c[-1] < x.o[-1] and x.clv[-1] < 0.3)


def _t0073(x):  # lower high on higher volume
    sh = x.swing_highs()
    if len(sh) < 2:
        return False
    a, b = sh[-2], sh[-1]
    return x.h[b] < x.h[a] and x.v[b] > x.v[a]


def _t0074(x):  # support test count overload (ASSUMED touch band + shrinking CLV)
    s = np.min(x.l[-15:])
    band = ASSUMED_CONSTANTS["support_touch_band"]
    touch = np.where(x.l[-15:] <= s * (1 + band))[0]
    if len(touch) < 3:
        return False
    clvs = x.clv[-15:][touch]
    return clvs[-1] < clvs[0]


def _t0075(x):  # breakdown pressure ratio
    return int(np.sum(x.clv[-7:] < 0.35)) >= 4


def _t0076(x):  # momentum divergence 14D
    r = x.rsi14
    if np.any(~np.isfinite(r[-19:])):
        return None
    return (np.max(x.h[-5:]) > np.max(x.h[-19:-5])
            and np.max(r[-5:]) < np.max(r[-19:-5]))


def _t0077(x):  # 50DMA rejection twice in 10 days
    if np.any(~np.isfinite(x.ma50[-10:])):
        return None
    cnt = int(np.sum((x.h[-10:] > x.ma50[-10:]) & (x.c[-10:] < x.ma50[-10:])))
    return cnt >= 2


def _t0078(x):  # gap fill failure
    for i in range(x.n - 20, x.n - 1):
        if x.o[i] > x.h[i - 1]:                      # gap up day
            level = x.h[i - 1]                       # gap-fill level
            touched = np.any(x.l[i:] <= level)
            if touched and x.c[-1] < level:
                return True
    return False


def _t0079(x):  # exhaustion: 3 green days, big gain, weak close
    up3 = x.c[-1] > x.c[-2] > x.c[-3] > x.c[-4]
    return bool(up3 and x.c[-1] / x.c[-4] - 1 > 0.08 and x.clv[-1] < 0.45)


def _t0080(x):  # extended from mean warning
    return x.c[-1] / x.ma20[-1] > 1.12 and x.clv[-1] < 0.4


def _t0081(x):  # distribution cluster count (ASSUMED: >=4 of the family today)
    fam = ["T12W_0069", "T12W_0070", "T12W_0071", "T12W_0072", "T12W_0073",
           "T12W_0074", "T12W_0075", "T12W_0076", "T12W_0077", "T12W_0078",
           "T12W_0079", "T12W_0080", "T12W_0082", "T12W_0083", "T12W_0084",
           "T12W_0085", "T12W_0086", "T12W_0087", "T12W_0088"]
    return _cluster_fired(x, fam) >= ASSUMED_CONSTANTS["cluster_count_threshold"]


def _t0082(x):  # bearish OBV divergence near high
    return (x.c[-1] >= 0.97 * np.max(x.h[-20:])
            and x.slope(x.obv, 10) < 0)


def _t0083(x):  # down-volume ratio spike
    tot = np.sum(x.v[-5:])
    return tot > 0 and np.sum(x.v[-5:][x.down_day[-5:]]) / tot >= 0.65


def _t0084(x):  # volatility expansion downside
    a = x.atr14
    if np.any(~np.isfinite(a[-3:])) or not np.isfinite(x.ma10[-1]):
        return None
    return a[-1] > a[-2] > a[-3] and x.c[-1] < x.ma10[-1]


def _t0085(x):  # high tight flag failure (ASSUMED gain/width numbers)
    gain = x.c[-10] / x.c[-30] - 1
    width = (np.max(x.h[-10:]) - np.min(x.l[-10:])) / x.c[-1]
    return (gain >= ASSUMED_CONSTANTS["htf_prior_gain"]
            and width <= ASSUMED_CONSTANTS["htf_flag_width"]
            and x.c[-1] < np.min(x.l[-10:-1]))


def _t0086(x):  # false reclaim trap
    s = np.min(x.l[-20:-5])
    return bool(np.any(x.c[-4:-1] > s) and x.c[-1] < s)


def _t0087(x):  # opening strength, closing weakness
    cnt = int(np.sum((x.o[-7:] > x.prev_c[-7:]) & (x.c[-7:] < x.o[-7:])))
    return cnt >= 3


def _t0088(x):  # supply wick cluster near high
    res = np.max(x.h[-20:])
    cnt = int(np.sum((x.upper_wick[-10:] > 0.45)
                     & (x.h[-10:] >= 0.97 * res)))
    return cnt >= 3


def _t0089(x):  # support break with volume spike
    return (x.c[-1] < np.min(x.l[-20:-1])
            and x.v[-1] > 1.8 * x.vol20[-1])


def _t0090(x):  # two gap-down sequence
    cnt = int(np.sum(x.o[-10:] < np.concatenate(([x.l[-11]], x.l[-10:-1]))))
    return cnt >= 2


def _t0091(x):  # ATR shock + close at low
    return (np.isfinite(x.atr14[-1]) and x.tr[-1] > 2 * x.atr14[-1]
            and x.clv[-1] < 0.2)


def _t0092(x):  # volatility tripwire
    a = x.atr14
    if not (np.isfinite(a[-1]) and np.isfinite(a[-11])) or a[-11] == 0:
        return None
    return a[-1] / a[-11] > 1.35 and x.ret(5) < 0


def _t0093(x):  # liquidity evaporation + support break
    return (np.mean(x.v[-10:]) / np.mean(x.v[-60:]) < 0.6
            and x.c[-1] < np.min(x.l[-20:-1]))


def _t0096(x):  # crash candle after failed breakout
    return bool(_t0070(x) and np.isfinite(x.atr14[-1])
                and x.rng[-1] > 1.5 * x.atr14[-1] and x.c[-1] < x.o[-1])


_FNS: dict[str, Callable] = {
    "T12W_0001": _t0001, "T12W_0002": _t0002, "T12W_0003": _t0003,
    "T12W_0004": _t0004, "T12W_0005": _t0005, "T12W_0006": _t0006,
    "T12W_0007": _t0007, "T12W_0008": _t0008, "T12W_0009": _t0009,
    "T12W_0010": _t0010, "T12W_0011": _t0011, "T12W_0012": _t0012,
    "T12W_0013": _t0013, "T12W_0014": _t0014, "T12W_0015": _t0015,
    "T12W_0016": _t0016, "T12W_0017": _t0017, "T12W_0018": _t0018,
    "T12W_0019": _t0019, "T12W_0020": _t0020, "T12W_0021": _t0021,
    "T12W_0022": _t0022, "T12W_0023": _t0023, "T12W_0024": _t0024,
    "T12W_0025": _t0025, "T12W_0026": _t0026, "T12W_0027": _t0027,
    "T12W_0028": _t0028, "T12W_0029": _t0029, "T12W_0030": _t0030,
    "T12W_0031": _t0031, "T12W_0032": _t0032, "T12W_0033": _t0033,
    "T12W_0034": _t0034, "T12W_0035": _t0035, "T12W_0036": _t0036,
    "T12W_0037": _t0037, "T12W_0038": _t0038, "T12W_0040": _t0040,
    "T12W_0041": _t0041, "T12W_0042": _t0042, "T12W_0043": _t0043,
    "T12W_0044": _t0044, "T12W_0057": _t0057, "T12W_0058": _t0058,
    "T12W_0061": _t0061, "T12W_0062": _t0062, "T12W_0063": _t0063,
    "T12W_0064": _t0064, "T12W_0065": _t0065, "T12W_0066": _t0066,
    "T12W_0067": _t0067, "T12W_0068": _t0068, "T12W_0069": _t0069,
    "T12W_0070": _t0070, "T12W_0071": _t0071, "T12W_0072": _t0072,
    "T12W_0073": _t0073, "T12W_0074": _t0074, "T12W_0075": _t0075,
    "T12W_0076": _t0076, "T12W_0077": _t0077, "T12W_0078": _t0078,
    "T12W_0079": _t0079, "T12W_0080": _t0080, "T12W_0081": _t0081,
    "T12W_0082": _t0082, "T12W_0083": _t0083, "T12W_0084": _t0084,
    "T12W_0085": _t0085, "T12W_0086": _t0086, "T12W_0087": _t0087,
    "T12W_0088": _t0088, "T12W_0089": _t0089, "T12W_0090": _t0090,
    "T12W_0091": _t0091, "T12W_0092": _t0092, "T12W_0093": _t0093,
    "T12W_0096": _t0096,
}


# --------------------------------------------------------------------------
# The 21 EXTERNAL-DATA trigger implementations (owner order 2026-08-13:
# "why u r waiting for 28 which u can wire, do that").
#
# These were DATA_NEEDED until the sector panel, delivery %, F&O open interest
# and bulk/block deals landed in the repo. Each takes (x, ext, fam):
#   x    = the stock's own OHLCV context (same as every other trigger)
#   ext  = external_panels.StockExternals — None-safe: a missing panel field
#          returns None, which stays DATA_NEEDED and never becomes a quiet
#          False
#   fam  = family counts from the OHLCV pass, for the two deal triggers whose
#          formula is "deal flag + accumulation/distribution cluster"
#
# Every formula is the owner's own string from trigger_params_v0_2.csv; where
# their sheet says "threshold" or "improves" the number lives in
# external_panels.ASSUMED_EXTERNAL_CONSTANTS and is shown on the page as
# ASSUMED, exactly like the OHLCV side.
# --------------------------------------------------------------------------

def _ext_const(name: str) -> float:
    from .external_panels import ASSUMED_EXTERNAL_CONSTANTS
    return ASSUMED_EXTERNAL_CONSTANTS[name]


def _in_base(x) -> bool:
    """Price still inside a base: 20d range <= 15% wide and not at its high.

    ASSUMED reading of the sheet's "while price in base" / "base not broken".
    """
    if x.n < 21:
        return False
    hi, lo = float(np.max(x.h[-20:])), float(np.min(x.l[-20:]))
    if not np.isfinite(hi) or not np.isfinite(lo) or x.c[-1] <= 0:
        return False
    return (hi - lo) / x.c[-1] <= 0.15 and x.c[-1] < hi


def _near_support(x) -> bool:
    """Within 3% of the 20d low (ASSUMED reading of "near support")."""
    if x.n < 21:
        return False
    lo = float(np.min(x.l[-20:]))
    return np.isfinite(lo) and lo > 0 and (x.c[-1] / lo - 1.0) <= _ext_const(
        "near_support_frac")


def _support_broken(x) -> bool:
    """Today's close at/below the prior 20 sessions' low."""
    if x.n < 22:
        return False
    return float(x.c[-1]) <= float(np.min(x.l[-21:-1]))


def _e0039(x, e, fam):        # RS line 10D high while price below 10D high
    if e.rs_line is None or len(e.rs_line) < 11:
        return None
    rs = e.rs_line
    return bool(rs[-1] >= np.nanmax(rs[-10:]) and
                x.c[-1] < float(np.nanmax(x.c[-10:])))


def _e0045(x, e, fam):        # stock-sector 10d spread > threshold, in base
    if e.ret_10d is None or e.sector_ret_10d is None:
        return None
    return bool(e.ret_10d - e.sector_ret_10d >
                _ext_const("rs_outperformance_10d") and _in_base(x))


def _e0046(x, e, fam):        # rank percentile improves >= 20 points in 10D
    if e.rs_rank_now is None or e.rs_rank_prev is None:
        return None
    return bool(e.rs_rank_now - e.rs_rank_prev >= _ext_const("rank_jump_points"))


def _e0047(x, e, fam):        # >=3 of top 5 peers carry pre-rise flags
    if e.peer_prerise_flags is None:
        return None
    return bool(e.peer_prerise_flags >= _ext_const("peer_flag_min_count"))


def _e0048(x, e, fam):        # % peers above 20DMA rises over 10 days
    b = e.peer_pct_above_ma20
    if b is None or len(b) < 11:
        return None
    return bool(b[-1] > b[-11])


def _e0049(x, e, fam):        # sector 5d down, stock 5d flat-or-up
    if e.ret_5d is None or e.sector_ret_5d is None:
        return None
    return bool(e.sector_ret_5d < 0 and e.ret_5d >= 0)


def _e0050(x, e, fam):        # top decile inside own industry, base not broken
    if e.own_sector_rank is None:
        return None
    top = 100.0 - _ext_const("top_decile_pct")
    return bool(e.own_sector_rank >= top and not _support_broken(x))


def _e0051(x, e, fam):        # stock-sector 10d spread < -threshold
    if e.ret_10d is None or e.sector_ret_10d is None:
        return None
    return bool(e.ret_10d - e.sector_ret_10d <
                -_ext_const("rs_underperformance_10d"))


def _e0052(x, e, fam):        # >=3 peers break 20D low before target breaks
    if e.peer_break_20d_low is None:
        return None
    return bool(e.peer_break_20d_low >= _ext_const("peer_break_min_count") and
                not _support_broken(x))


def _e0053(x, e, fam):        # sector breadth declining, target near support
    b = e.peer_pct_above_ma20
    if b is None or len(b) < 6:
        return None
    return bool(b[-1] < b[-6] and _near_support(x))


def _e0054(x, e, fam):        # price at 20D high, RS line below its prior high
    if e.rs_line is None or len(e.rs_line) < 40:
        return None
    at_high = float(x.c[-1]) >= float(np.nanmax(x.c[-20:]))
    return bool(at_high and e.rs_line[-1] < np.nanmax(e.rs_line[-40:-1]))


def _e0055(x, e, fam):        # industry rank worsens >= 20 points in 10D
    if e.sector_rank_now is None or e.sector_rank_prev is None:
        return None
    return bool(e.sector_rank_prev - e.sector_rank_now >=
                _ext_const("rank_jump_points"))


def _e0056(x, e, fam):        # 20D high + peer breadth down + RS slope flat
    b, rs = e.peer_pct_above_ma20, e.rs_line
    if b is None or len(b) < 6 or rs is None or len(rs) < 10:
        return None
    at_high = float(x.c[-1]) >= float(np.nanmax(x.c[-20:]))
    slope = np.polyfit(np.arange(10), rs[-10:], 1)[0] if np.all(
        np.isfinite(rs[-10:])) else np.nan
    if not np.isfinite(slope):
        return None
    return bool(at_high and b[-1] < b[-6] and slope <= _ext_const(
        "rs_slope_flat"))


def _e0059(x, e, fam):        # turnover rank improves >= 25 percentile points
    if e.turnover_rank_now is None or e.turnover_rank_prev is None:
        return None
    return bool(e.turnover_rank_now - e.turnover_rank_prev >=
                _ext_const("turnover_rank_jump_points"))


def _e0060(x, e, fam):        # delivery ratio above its 80th percentile
    d = e.delivery_ratio
    if d is None or len(d) < 30:
        return None
    seg = d[np.isfinite(d)]
    if len(seg) < 30:
        return None
    return bool(seg[-1] >= np.percentile(seg, _ext_const("delivery_pctile")))


def _e0094(x, e, fam):        # >=30% of industry broke down before target
    if e.peer_break_20d_low is None or not e.peers:
        return None
    frac = e.peer_break_20d_low / float(e.peers)
    return bool(frac >= _ext_const("peer_breakdown_frac") and
                not _support_broken(x))


def _e0095(x, e, fam):        # index below its 20DMA and target support break
    ic = e.index_close
    if ic is None or len(ic) < 20:
        return None
    ma20 = float(np.nanmean(ic[-20:]))
    if not np.isfinite(ma20):
        return None
    return bool(ic[-1] < ma20 and _support_broken(x))


def _e0100(x, e, fam):        # price down + OI up over 5D
    if e.fut_oi is None or len(e.fut_oi) < 6:
        return None
    r5 = x.ret(5) if x.n > 5 else None
    if r5 is None or not np.isfinite(r5):
        return None
    return bool(r5 < 0 and e.fut_oi[-1] > e.fut_oi[-6])


def _e0101(x, e, fam):        # price down + OI down after a prior uptrend
    if e.fut_oi is None or len(e.fut_oi) < 6 or x.n < 27:
        return None
    r5 = x.ret(5)
    prior = x.c[-6] / x.c[-26] - 1.0
    if not (np.isfinite(r5) and np.isfinite(prior)):
        return None
    return bool(r5 < 0 and e.fut_oi[-1] < e.fut_oi[-6] and prior > 0)


def _e0111(x, e, fam):        # bulk/block BUY + accumulation cluster
    if e.deal_buy_recent is None:
        return None
    return bool(e.deal_buy_recent and fam.get(F_ACCUMULATION, 0) >= 2)


def _e0112(x, e, fam):        # bulk/block SELL + distribution cluster
    if e.deal_sell_recent is None:
        return None
    return bool(e.deal_sell_recent and fam.get(F_DISTRIBUTION, 0) >= 2)


_EXT_FNS = {
    "T12W_0039": _e0039, "T12W_0045": _e0045, "T12W_0046": _e0046,
    "T12W_0047": _e0047, "T12W_0048": _e0048, "T12W_0049": _e0049,
    "T12W_0050": _e0050, "T12W_0051": _e0051, "T12W_0052": _e0052,
    "T12W_0053": _e0053, "T12W_0054": _e0054, "T12W_0055": _e0055,
    "T12W_0056": _e0056, "T12W_0059": _e0059, "T12W_0060": _e0060,
    "T12W_0094": _e0094, "T12W_0095": _e0095, "T12W_0100": _e0100,
    "T12W_0101": _e0101, "T12W_0111": _e0111, "T12W_0112": _e0112,
}

# Minimum stock-side history for the external triggers.
_EXT_MIN_BARS = 30


# Minimum history a trigger needs; the evaluator returns None below this.
_MIN_BARS_DEFAULT = 70
_MIN_BARS = {
    "T12W_0003": 130, "T12W_0009": 130, "T12W_0012": 130,   # 6M percentile
    "T12W_0004": 252, "T12W_0014": 252,                       # 252d percentile
    "T12W_0025": 130,
}


# --------------------------------------------------------------------------
# Registry, parsed from the owner's sheet
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Trigger:
    id: str
    name: str
    family: str
    side: str                 # RISE / FALL / REVERSAL_UP / COLLAPSE
    weight: float
    definition: str
    formula: str
    ohlcv_ready: bool
    needs: str                # what external data unlocks a DATA_NEEDED trigger
    fn: Optional[Callable] = field(default=None, compare=False)
    # Set for the 21 external triggers whose data now lives in the repo. They
    # run only when a panel batch is supplied; without one they stay None
    # (DATA_NEEDED), so nothing about the no-panel path changed.
    ext_fn: Optional[Callable] = field(default=None, compare=False)


def _load_registry() -> list[Trigger]:
    out: list[Trigger] = []
    with open(PARAMS_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            tid = row["parameter_id"].strip()
            ready = row["can_be_calculated_from_ohlcv"].strip().upper() == "YES"
            out.append(Trigger(
                id=tid,
                name=row["parameter_name"].strip(),
                family=row["trigger_family"].strip(),
                side=row["rise_fall_side"].strip(),
                weight=float(row["default_weight"] or 1.0),
                definition=row["definition"].strip(),
                formula=row["formula_or_logic"].strip(),
                ohlcv_ready=ready,
                needs="" if ready else row["required_columns"].strip(),
                fn=_FNS.get(tid) if ready else None,
                ext_fn=None if ready else _EXT_FNS.get(tid),
            ))
    missing = [t.id for t in out if t.ohlcv_ready and t.fn is None]
    if missing:
        raise RuntimeError(f"OHLCV-ready triggers without implementation: {missing}")
    stray = set(_EXT_FNS) - {t.id for t in out if not t.ohlcv_ready}
    if stray:
        raise RuntimeError(f"external impls for non-external ids: {sorted(stray)}")
    return out


TRIGGERS: list[Trigger] = _load_registry()
COMPUTABLE = [t for t in TRIGGERS if t.ohlcv_ready]
EXTERNAL = [t for t in TRIGGERS if not t.ohlcv_ready]
# Of the 33 external triggers: those whose data is now in the repo, and those
# still genuinely waiting on a feed (news/analyst/governance, options chain).
EXTERNAL_WIRED = [t for t in EXTERNAL if t.ext_fn is not None]
EXTERNAL_PENDING = [t for t in EXTERNAL if t.ext_fn is None]


# --------------------------------------------------------------------------
# Calibration (BT01-BT08, owner order 2026-08-10 "sequence must import"):
# the measured verdicts ride WITH the layer. Loaded from the committed run
# so display always shows each trigger's evidence — lift vs chance, proof
# tag, and MEDIAN LEAD DAYS (the sequencing: how many days before its event
# a trigger historically fires). Missing file = empty dict, fail-visible on
# the page, never fabricated.
# --------------------------------------------------------------------------

_CAL_CSV = (Path(__file__).resolve().parents[1] / "research" /
            "bt_calibration" / "run_2026-08-09" / "trigger_calibration.csv")


def _load_calibration() -> dict[str, dict]:
    out: dict[str, dict] = {}
    try:
        with open(_CAL_CSV, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                out[row["trigger_id"]] = {
                    "proof_tag": row["proof_tag"],
                    "lift": float(row["lift"]) if row["lift"] else None,
                    "median_lead_days": (float(row["median_lead_days"])
                                         if row["median_lead_days"] else None),
                    "fires": int(row["fires"]),
                    "false_positive_rate_pct":
                        (float(row["false_positive_rate_pct"])
                         if row["false_positive_rate_pct"] else None),
                }
    except OSError:
        pass
    return out


CALIBRATION: dict[str, dict] = _load_calibration()

# When a rule's constant changes, the verdict stored from the earlier run
# describes the rule as it WAS. Leaving it on screen would misreport the live
# rule, so a recalibrated trigger carries its own measured numbers and names
# the run they came from.
RECALIBRATED: dict[str, dict] = {
    "T12W_0040": {
        "proof_tag": "SUPPORTED",       # lift >= 1.1 with fires >= 30
        "lift": 1.161,                  # 2,985 fires, 133 hits, 4.46% vs 3.84%
        "median_lead_days": 4.0,
        "fires": 2985,
        "false_positive_rate_pct": 31.86,
        "recalibrated": "2026-08-13 RSI<30 sweep — train 1.162 / test 1.142",
        "superseded": "1.065 REJECTED (2026-08-09 run, RSI<35)",
    },
    "T12W_0066": {
        "proof_tag": "SUPPORTED",       # lift >= 1.1 with fires >= 30
        "lift": 1.213,                  # 347 fires, 82 hits, 23.63% vs 19.49%
        "median_lead_days": 2.0,
        "fires": 347,
        "false_positive_rate_pct": 30.55,
        "recalibrated": "2026-08-13 gap>=1% sweep — train 1.217 / test 1.227",
        "superseded": "1.091 REJECTED (2026-08-09 run, gap unconstrained)",
    },
}
for _tid, _row in RECALIBRATED.items():
    CALIBRATION[_tid] = _row


# --------------------------------------------------------------------------
# Evaluation: one stock → hits, family counts, side scores, gates
# --------------------------------------------------------------------------

def evaluate(df: pd.DataFrame, ext=None) -> dict[str, Optional[bool]]:
    """The trigger_hit_matrix row for one stock (owner's L04A).

    {trigger_id: True fired / False computed-quiet / None not computable}.

    `ext` is an external_panels.StockExternals for this stock. Without it
    every external trigger stays None (DATA_NEEDED) — the original behaviour.
    With it, the 21 triggers whose data now lives in the repo compute too; the
    12 that still need an outside feed stay None either way.
    """
    hits: dict[str, Optional[bool]] = {}
    if df is None or len(df) < 25:
        return {t.id: None for t in TRIGGERS}
    x = _Ctx(df)
    for t in TRIGGERS:
        if t.fn is None:
            hits[t.id] = None
            continue
        if x.n < _MIN_BARS.get(t.id, _MIN_BARS_DEFAULT):
            hits[t.id] = None
            continue
        try:
            r = t.fn(x)
            hits[t.id] = None if r is None else bool(r)
        except Exception as e:                        # a broken trigger is
            log.warning("trigger %s crashed: %s", t.id, e)   # visible, not a 0
            hits[t.id] = None
        # --- external pass: needs the family counts from the OHLCV pass above,
    # because two of the owner's formulas are "deal flag + cluster".
    if ext is not None:
        fam = family_counts(hits)
        for t in EXTERNAL_WIRED:
            if x.n < _EXT_MIN_BARS:
                continue
            try:
                r = t.ext_fn(x, ext, fam)
                hits[t.id] = None if r is None else bool(r)
            except Exception as e:
                log.warning("external trigger %s crashed: %s", t.id, e)
                hits[t.id] = None
    return hits


def family_counts(hits: dict[str, Optional[bool]]) -> dict[str, int]:
    """Fired-count per family — the numbers the owner's gates test."""
    out: dict[str, int] = {}
    for t in TRIGGERS:
        if hits.get(t.id) is True:
            out[t.family] = out.get(t.family, 0) + 1
    return out


def side_scores(hits: dict[str, Optional[bool]]) -> dict[str, float]:
    """Weighted % of computable triggers fired, per side (0-100).

    ASSUMED formula — the owner's sheets define the gate thresholds but never
    the score formula itself; calibrating this is the 15Y backtest's job
    (their BT06). Displayed with that label, never silently."""
    acc: dict[str, list[float]] = {}
    for t in TRIGGERS:
        h = hits.get(t.id)
        if h is None:
            continue
        fired, total = acc.setdefault(t.side, [0.0, 0.0])
        acc[t.side][1] = total + t.weight
        if h:
            acc[t.side][0] = fired + t.weight
    return {side: round(100.0 * f / tot, 1) if tot else 0.0
            for side, (f, tot) in acc.items()}


def data_quality_score(hits: dict[str, Optional[bool]]) -> float:
    """% of the 79 OHLCV-ready triggers that were actually computable."""
    ready = [hits.get(t.id) for t in COMPUTABLE]
    if not ready:
        return 0.0
    return round(100.0 * sum(1 for h in ready if h is not None) / len(ready), 1)


GATE_ROWS = [
    # (id, name, logic shown to the owner — their exact sheet strings)
    ("GATE_RISE_WATCH_01", "Pre-rise watchlist gate",
     "compression_count>=2 AND accumulation_count>=2 AND distribution_count=0"),
    ("GATE_RISE_ENTRY_01", "Paper entry permission gate",
     "rise_trigger_score>=65 AND fall_warning_score<35 AND data_quality_score>=80"),
    ("GATE_REVERSAL_01", "Failed breakdown reversal gate",
     "reclaim_count>=2 AND panic_exhaustion_or_support_sweep=True"),
    ("GATE_FALL_AVOID_01", "Pre-fall avoid gate",
     "distribution_count>=3 OR fall_warning_score>=55"),
    ("GATE_EXIT_WARN_01", "Paper exit warning gate",
     "failed_breakout=True OR high_volume_no_progress=True OR weak_rs=True "
     "(weak_rs leg = DATA_NEEDED: needs the sector RS panel — verdict runs "
     "on the two computable legs)"),
    ("GATE_COLLAPSE_BLOCK_01", "Collapse hard block",
     "collapse_score>=60 OR support_break_with_vol_spike=True"),
    ("GATE_SECTOR_CONFIRM_01", "Sector confirmation gate",
     "peer_confirmation_count>=3 OR sector_breadth_change passes side condition"),
    ("GATE_EVENT_OVERLAY_01", "News/event overlay gate",
     "external event flag aligns with technical trigger cluster"),
]


def evaluate_gates(hits: dict[str, Optional[bool]]) -> list[dict]:
    """The owner's 8 score gates (L07A), exact logic from their sheet.

    Result per gate: PASS / no / DATA_NEEDED. Display + watchlist only —
    GATE_RISE_ENTRY_01's verdict is shown, never executed (owner's own
    REQUIRES_15Y_BACKTEST rule)."""
    fam = family_counts(hits)
    scores = side_scores(hits)
    dq = data_quality_score(hits)
    comp = fam.get(F_COMPRESSION, 0)
    accu = fam.get(F_ACCUMULATION, 0)
    dist = fam.get(F_DISTRIBUTION, 0)
    recl = fam.get(F_RECLAIM, 0)
    rise = scores.get("RISE", 0.0)
    fall = scores.get("FALL", 0.0)
    collapse = scores.get("COLLAPSE", 0.0)
    exhaustion_or_sweep = (hits.get("T12W_0044") is True
                           or hits.get("T12W_0033") is True)
    results = {
        "GATE_RISE_WATCH_01": comp >= 2 and accu >= 2 and dist == 0,
        "GATE_RISE_ENTRY_01": rise >= 65 and fall < 35 and dq >= 80,
        "GATE_REVERSAL_01": recl >= 2 and exhaustion_or_sweep,
        "GATE_FALL_AVOID_01": dist >= 3 or fall >= 55,
        # weak_rs needs the sector panel (T12W_0051, DATA_NEEDED) — the two
        # computable legs still run; the missing leg is named on the page.
        "GATE_EXIT_WARN_01": (hits.get("T12W_0070") is True
                              or hits.get("T12W_0065") is True),
        "GATE_COLLAPSE_BLOCK_01": collapse >= 60 or hits.get("T12W_0089") is True,
        "GATE_SECTOR_CONFIRM_01": None,       # DATA_NEEDED per the owner's sheet
        "GATE_EVENT_OVERLAY_01": None,        # DATA_NEEDED per the owner's sheet
    }
    out = []
    for gid, name, logic in GATE_ROWS:
        r = results[gid]
        out.append({"id": gid, "name": name, "logic": logic,
                    "status": "DATA_NEEDED" if r is None else ("PASS" if r else "no")})
    return out


def stock_view(df: pd.DataFrame, asof: str = "", ext=None) -> dict:
    """Everything the page and the watchlist job need for one stock.

    `ext` (external_panels.StockExternals) switches on the 21 external
    triggers whose data is in the repo; without it they read DATA_NEEDED.
    """
    hits = evaluate(df, ext=ext)
    fam = family_counts(hits)
    scores = side_scores(hits)
    gates = evaluate_gates(hits)
    fired = [t for t in TRIGGERS if hits.get(t.id) is True]
    # SEQUENCE ORDER (owner 2026-08-10): earliest-warning triggers first —
    # sorted by measured median lead days from the BT01-BT08 calibration.
    # Uncalibrated triggers sort last, never guessed.
    fired.sort(key=lambda t: -(CALIBRATION.get(t.id, {})
                               .get("median_lead_days") or -1))
    return {
        "asof": asof,
        "hits": hits,
        "fired": [{"id": t.id, "name": t.name, "family": t.family,
                   "side": t.side, "weight": t.weight,
                   "definition": t.definition, "formula": t.formula,
                   "calibration": CALIBRATION.get(t.id)}
                  for t in fired],
        "family_counts": fam,
        "scores": scores,
        "data_quality": data_quality_score(hits),
        "gates": gates,
        "watch": any(g["id"] == "GATE_RISE_WATCH_01" and g["status"] == "PASS"
                     for g in gates),
        "computable": sum(1 for t in COMPUTABLE if hits.get(t.id) is not None),
        "total_computable": len(COMPUTABLE),
        "external_computed": sum(1 for t in EXTERNAL_WIRED
                                 if hits.get(t.id) is not None),
        "total_external_wired": len(EXTERNAL_WIRED),
        "external_missing": (list(ext.missing()) if ext is not None else
                             ["no panel batch supplied"]),
    }
