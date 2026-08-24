"""Node 9 steps 1-2 (owner: "node 9 go", 2026-08-24) — ONE canonical
market-damage series.

Three systems each measured "how damaged is the tape" with private
arithmetic (docs/DESIGN_ONE_DAMAGE_NUMBER.md): the IFR throttle counts
stocks with any single −3% DAY in the last 5 days; the /register FII-08/09
stacks recompute the owner's formula on the page's own panel; the danger
layer reads per-stock signatures. This module is the single computation
site for the OWNER'S formula — the one recovered from their
market_internals_1996_2024.csv and verified 6,808/7,162 rows exact:

    tail_down3_5d = share of panel members whose WORST 3-DAY return
                    within the last 5 days is <= -3%   (fraction, 0..1)

Per the approved design, day-one behaviour changes NOWHERE:
  * FII-08/09 read the stored reading instead of recomputing (display).
  * IFR keeps its own fire rule and merely JOURNALS this number beside its
    own on every fire, so 60 days of live data show whether they agree.
  * The danger layer is untouched — its 12 tripwires are
    calibration-VERIFIED per-stock rules; welding them to a panel gauge
    would discard measured evidence for tidiness.
Step 3 (IFR reading the store) happens only after the 60-day window and
only on the owner's explicit word.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# p90 of tail_down3_5d frozen over 1996-2024 — the owner's own
# thresholds.csv (p90(tail_down3_5d) = 0.212987); "static 0.213" and
# "rolling q90" were always the same rule, one frozen (2026-08-11 find).
STATIC_THRESHOLD = 0.213
CLUSTER_WINDOW = 10          # cluster = >= CLUSTER_MIN fires in this many days
CLUSTER_MIN = 5
MIN_MEMBERS = 10             # fewer names = no reading, never a guess
MIN_DAYS = 70                # need a window to take q90 over (60 obs + warmup)
MAX_AGE_DAYS = 5             # consumers trust a stored reading this long


def tail_down3_5d(closes: pd.DataFrame) -> pd.Series:
    """The owner's canonical damage series over a closes panel
    (columns = tickers). Fraction 0..1 per date."""
    return (closes.pct_change(3).rolling(5).min() <= -0.03).mean(axis=1)


def damage_reading(history: Dict[str, pd.DataFrame]) -> Optional[dict]:
    """One dated reading over a {ticker: OHLCV df} panel — the shape the
    engine's scan already holds. None when the panel is too thin to speak
    (DATA_NEEDED, never a fabricated zero).

    Returns {asof, value, sample, window_days, q90, fires10_static,
    fires10_q90, static_cluster, q90_cluster}: `value` is today's
    tail_down3_5d; the cluster verdicts apply the owner's >= 5-in-10 rule
    against the frozen 0.213 threshold and the rolling q90 of the loaded
    window, side by side (owner 2026-08-09: "why not both")."""
    cols = {}
    for t, df in (history or {}).items():
        if df is not None and "Close" in df.columns and len(df) >= MIN_DAYS:
            cols[t] = df["Close"]
    if len(cols) < MIN_MEMBERS:
        return None
    closes = pd.concat(cols, axis=1)
    series = tail_down3_5d(closes).dropna()
    if len(series) < 60:
        return None
    q90 = float(np.percentile(series, 90))
    last10 = series.tail(CLUSTER_WINDOW)
    fires_static = int((last10 > STATIC_THRESHOLD).sum())
    fires_q90 = int((last10 > q90).sum())
    asof = series.index[-1]
    try:
        asof = asof.date().isoformat()
    except AttributeError:
        asof = str(asof)[:10]
    return {
        "asof": asof,
        "value": float(series.iloc[-1]),
        "sample": len(cols),
        "window_days": int(len(series)),
        "q90": q90,
        "fires10_static": fires_static,
        "fires10_q90": fires_q90,
        "static_cluster": fires_static >= CLUSTER_MIN,
        "q90_cluster": fires_q90 >= CLUSTER_MIN,
    }


def is_fresh(reading: Optional[dict], today: Optional[str] = None) -> bool:
    """A stored reading is trusted MAX_AGE_DAYS from its asof; anything
    older and every consumer falls back to what it did before Node 9."""
    if not reading or not reading.get("asof"):
        return False
    from datetime import date, timedelta
    try:
        ref = date.fromisoformat(str(today)) if today else date.today()
        return (ref - date.fromisoformat(str(reading["asof"])[:10])
                ) <= timedelta(days=MAX_AGE_DAYS)
    except ValueError:
        return False
