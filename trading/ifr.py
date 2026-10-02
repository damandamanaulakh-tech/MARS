"""IFR (Internal Fracture & Repair) risk overlay.

Validated signals from 28-year backtest:
  - DAMAGE_CLUSTER_5IN10:
      tail_down3_5d > 90th percentile, sustained 5+ days in a 10-day window
      => market internals are fracturing; cut position size by 50%
  - TAIL_DAMAGE_RATIO (Q2-validated, 2026-06):
      tail_down3_5d / (tail_down3_5d + tail_up3_5d) > 85th percentile
      => damage outweighs up-thrust days; independent of DAMAGE_CLUSTER (~20% overlap)

Result on 1996-2024 daily internals (28 years):
  - DAMAGE_CLUSTER alone: 561x, max DD -54% (vs 442x / -72% baseline)
  - TAIL_DAMAGE_RATIO alone: 1341x, max DD -46% (best single signal found)
  - Either-active throttle: combines both for stronger drawdown protection.

The engine throttles size by 50% whenever EITHER signal is in its active
window. Snapshot exposes which fired for the Risk Dashboard + journal.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional

import numpy as np
import pandas as pd


# Tunable constants — match the validated 28Y backtest.
TAIL_DROP_PCT = -0.03          # what counts as a "down tail" day per stock
TAIL_UP_PCT   = 0.03           # what counts as an "up tail" day per stock (mirror)
TAIL_WINDOW   = 5              # how many days back to look for the per-stock tail count
CLUSTER_WINDOW = 10            # rolling window for cluster check (days)
CLUSTER_MIN_FIRES = 5          # 5 of 10 days must exceed 90th pctile
TAIL_PERCENTILE = 0.90         # what threshold counts as "fracture day" (cluster)
RATIO_PERCENTILE = 0.85        # what threshold counts as damage-ratio fire
THROTTLE_MULTIPLIER = 0.50     # cut sizing by 50% when any signal fires

# Active-window: once a signal fires, treat the next N days as "risk-on"
# (i.e. still throttled even if a single subsequent reading dips below the threshold)
ACTIVE_DAYS_AFTER_FIRE = 22

log = logging.getLogger(__name__)

# AUDIT F-5 (owner: "go for next", 2026-10-02): FROZEN thresholds.
#
# WHAT WAS WRONG. Both thresholds were a quantile of the very window being
# read (p90 / p85 of the ~2 years the scan had loaded), so the throttle fired
# at a fixed rate whatever the market did: on a random-walk panel of the same
# size and volatility it was active 76% of days. It also read every scanned
# name, so the reading moved with the universe (49 names, then 443, then
# 1,205 small and mid caps) rather than with the market.
#
# NOW. The thresholds are measured ONCE on the committed 191-name x 12-year
# panel by scripts/ifr_frozen_thresholds.py (p90 of the down-tail share,
# p85 of the damage ratio — the same percentiles as before, frozen), and the
# live reading is taken over the SAME reference names, so the number and its
# threshold describe one panel. Measured: thresholds 0.484 / 0.631, nearly
# identical on 2014-2020 and 2020-2026; random-walk active rate 76% -> 0%.
# Missing file = the old rolling rule, logged — never a silent change.
FROZEN_FILE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "research", "ifr_frozen_2026-10-02",
    "thresholds.json")
MIN_REFERENCE_NAMES = 30


def _load_frozen() -> Optional[dict]:
    try:
        with open(FROZEN_FILE) as f:
            d = json.load(f)
        return {"cluster": float(d["cluster_threshold_p90"]),
                "ratio": float(d["ratio_threshold_p85"]),
                "tickers": frozenset(d["tickers"])}
    except Exception as e:                                # noqa: BLE001
        log.error("IFR frozen thresholds unavailable (%s) — rolling rule", e)
        return None


FROZEN = _load_frozen()


@dataclass
class IFRSnapshot:
    """Result of one IFR check at a point in time."""
    fired_today: bool
    active: bool                          # true if within ACTIVE_DAYS_AFTER_FIRE of last fire
    sizing_multiplier: float
    tail_down3_5d_today: float            # % of stocks with cluster of down-tails today
    threshold_value: float                # 90th-percentile threshold used
    fires_in_window: int                  # count of days in last 10 exceeding threshold
    last_fire_date: Optional[str]         # iso date or None
    reason: str                           # human-readable explanation
    # Q2-added: TAIL_DAMAGE_RATIO state (independent of cluster, ~20% overlap)
    ratio_today: float = 0.0
    ratio_threshold: float = 0.0
    ratio_fired_today: bool = False
    ratio_last_fire_date: Optional[str] = None
    ratio_active: bool = False
    which_fired: tuple = ()               # ("cluster",) / ("ratio",) / both / ()

    def as_dict(self) -> dict:
        return {
            "fired_today": self.fired_today,
            "active": self.active,
            "sizing_multiplier": self.sizing_multiplier,
            "tail_down3_5d_today": round(self.tail_down3_5d_today, 4),
            "threshold_value": round(self.threshold_value, 4),
            "fires_in_window": self.fires_in_window,
            "last_fire_date": self.last_fire_date,
            "reason": self.reason,
            "ratio_today": round(self.ratio_today, 4),
            "ratio_threshold": round(self.ratio_threshold, 4),
            "ratio_fired_today": self.ratio_fired_today,
            "ratio_last_fire_date": self.ratio_last_fire_date,
            "ratio_active": self.ratio_active,
            "which_fired": list(self.which_fired),
        }


def _per_stock_down_tail_count(close: pd.Series, window: int = TAIL_WINDOW) -> pd.Series:
    """For each day, count how many of the last `window` days were < TAIL_DROP_PCT.
    Returns the count series (one value per date)."""
    if close is None or len(close) < window + 1:
        return pd.Series(dtype=float)
    ret = close.pct_change()
    is_down = (ret < TAIL_DROP_PCT).astype(int)
    return is_down.rolling(window).sum()


def _per_stock_up_tail_count(close: pd.Series, window: int = TAIL_WINDOW) -> pd.Series:
    """Mirror of down-tail count, used for TAIL_DAMAGE_RATIO."""
    if close is None or len(close) < window + 1:
        return pd.Series(dtype=float)
    ret = close.pct_change()
    is_up = (ret > TAIL_UP_PCT).astype(int)
    return is_up.rolling(window).sum()


def compute_market_tail_down3_5d(history: Dict[str, pd.DataFrame]) -> pd.Series:
    """Across the universe, compute the % of stocks that have AT LEAST ONE
    down-3% day in the last 5 trading days, per date.

    Higher = more internal damage. This is the same metric the 28Y backtest
    found predictive of pre-fall regimes.
    """
    per_stock = []
    for ticker, df in history.items():
        if "Close" not in df.columns or len(df) < TAIL_WINDOW + 2:
            continue
        counts = _per_stock_down_tail_count(df["Close"])
        # boolean: did this stock have any down-tail day in last 5? A day the
        # name has no reading stays empty — it is not "undamaged".
        per_stock.append((counts > 0).astype(float)
                         .where(counts.notna()).rename(ticker))
    if not per_stock:
        return pd.Series(dtype=float)
    panel = pd.concat(per_stock, axis=1, sort=True)
    # market-wide ratio: % of stocks with at least one down-tail day in window,
    # over the names that have a reading that day
    return panel.mean(axis=1, skipna=True)


def compute_market_tail_up3_5d(history: Dict[str, pd.DataFrame]) -> pd.Series:
    """Mirror of down-tail metric for up-side: % of stocks with any +3% day in
    the last 5 trading days. Used to form TAIL_DAMAGE_RATIO."""
    per_stock = []
    for ticker, df in history.items():
        if "Close" not in df.columns or len(df) < TAIL_WINDOW + 2:
            continue
        counts = _per_stock_up_tail_count(df["Close"])
        per_stock.append((counts > 0).astype(float)
                         .where(counts.notna()).rename(ticker))
    if not per_stock:
        return pd.Series(dtype=float)
    panel = pd.concat(per_stock, axis=1, sort=True)
    return panel.mean(axis=1, skipna=True)


def compute_tail_damage_ratio(down: pd.Series, up: pd.Series) -> pd.Series:
    """TAIL_DAMAGE_RATIO = down / (down + up + epsilon). 0..1 scale.
    Higher = damage outweighs strength. Validated Q2 (2026-06)."""
    if down is None or up is None or down.empty or up.empty:
        return pd.Series(dtype=float)
    aligned = pd.concat([down, up], axis=1, join="inner")
    aligned.columns = ["down", "up"]
    return aligned["down"] / (aligned["down"] + aligned["up"] + 0.001)


def check_damage_cluster(history: Dict[str, pd.DataFrame],
                          now: Optional[datetime] = None,
                          last_fire_date: Optional[str] = None,
                          ratio_last_fire_date: Optional[str] = None) -> IFRSnapshot:
    """The full IFR check used by the engine.

    Evaluates BOTH IFR signals (validated 28Y backtest):
      - DAMAGE_CLUSTER_5IN10  (561x equity, -54% DD)
      - TAIL_DAMAGE_RATIO     (1341x equity, -46% DD, ~20% overlap with cluster)

    Returns an IFRSnapshot. If either fires today OR was within
    ACTIVE_DAYS_AFTER_FIRE, .active is True and .sizing_multiplier is 0.5.
    Engine throttles new BUY qty by sizing_multiplier.
    """
    now = now or datetime.utcnow()
    today_iso = now.date().isoformat()

    frozen = FROZEN
    if frozen is not None:
        history = {t: df for t, df in (history or {}).items()
                   if t in frozen["tickers"]}
        thin = len(history) < MIN_REFERENCE_NAMES
    else:
        thin = False
    market_series = (pd.Series(dtype=float) if thin
                     else compute_market_tail_down3_5d(history))
    if market_series.empty or len(market_series) < CLUSTER_WINDOW + 30:
        # Not enough data to make a call — assume normal (no throttle).
        return IFRSnapshot(
            fired_today=False, active=False, sizing_multiplier=1.0,
            tail_down3_5d_today=0.0, threshold_value=0.0,
            fires_in_window=0, last_fire_date=last_fire_date,
            reason=(f"insufficient reference panel ({len(history)} of the "
                    f"{len(frozen['tickers'])} reference names loaded, need "
                    f"{MIN_REFERENCE_NAMES})" if thin
                    else "insufficient history"),
            ratio_today=0.0, ratio_threshold=0.0,
            ratio_fired_today=False, ratio_last_fire_date=ratio_last_fire_date,
            ratio_active=False, which_fired=(),
        )

    # ---- Signal 1: DAMAGE_CLUSTER ----
    thresh = (frozen["cluster"] if frozen is not None
              else float(market_series.quantile(TAIL_PERCENTILE)))
    today_value = float(market_series.iloc[-1])
    is_above = (market_series > thresh).astype(int)
    fires_in_window = int(is_above.rolling(CLUSTER_WINDOW).sum().iloc[-1])
    cluster_fired_today = fires_in_window >= CLUSTER_MIN_FIRES

    # ---- Signal 2: TAIL_DAMAGE_RATIO (Q2-validated) ----
    up_series = compute_market_tail_up3_5d(history)
    ratio_series = compute_tail_damage_ratio(market_series, up_series)
    if ratio_series.empty:
        ratio_today_val = 0.0; ratio_thresh = 0.0; ratio_fired_today = False
    else:
        ratio_thresh = (frozen["ratio"] if frozen is not None
                        else float(ratio_series.quantile(RATIO_PERCENTILE)))
        ratio_today_val = float(ratio_series.iloc[-1])
        ratio_fired_today = ratio_today_val > ratio_thresh

    # ---- Active-window logic for each signal independently ----
    def _is_active(fired: bool, prev_iso: Optional[str]) -> tuple[bool, Optional[str]]:
        if fired:
            return True, today_iso
        if prev_iso:
            try:
                prev = datetime.fromisoformat(prev_iso).date()
                return (now.date() - prev).days <= ACTIVE_DAYS_AFTER_FIRE, prev_iso
            except ValueError:
                return False, prev_iso
        return False, prev_iso

    cluster_active, new_last_fire = _is_active(cluster_fired_today, last_fire_date)
    ratio_active,   new_ratio_fire = _is_active(ratio_fired_today,   ratio_last_fire_date)

    # OR-combine: throttle if either is active
    fired_today = cluster_fired_today or ratio_fired_today
    active = cluster_active or ratio_active
    sizing = THROTTLE_MULTIPLIER if active else 1.0
    which = tuple(
        x for x, on in (("cluster", cluster_active), ("ratio", ratio_active)) if on
    )

    if fired_today:
        parts = []
        if cluster_fired_today:
            parts.append(
                f"DAMAGE_CLUSTER FIRED ({fires_in_window}/{CLUSTER_WINDOW} above "
                f"90th-pctile, today {today_value:.3f}, thr {thresh:.3f})"
            )
        if ratio_fired_today:
            parts.append(
                f"TAIL_DAMAGE_RATIO FIRED (today {ratio_today_val:.3f} > "
                f"85th-pctile thr {ratio_thresh:.3f})"
            )
        reason = " | ".join(parts) + f". Throttle sizing to {sizing*100:.0f}%."
    elif active:
        active_names = ", ".join(which) if which else "?"
        reason = (
            f"Within {ACTIVE_DAYS_AFTER_FIRE}-day window after last fire "
            f"({active_names}). Throttle continues at {sizing*100:.0f}%."
        )
    else:
        reason = (
            f"Normal: DAMAGE_CLUSTER {fires_in_window}/{CLUSTER_WINDOW} "
            f"(today {today_value:.3f}), TAIL_DAMAGE_RATIO today "
            f"{ratio_today_val:.3f} <= {ratio_thresh:.3f}. No throttle."
        )

    return IFRSnapshot(
        fired_today=fired_today,
        active=active,
        sizing_multiplier=sizing,
        tail_down3_5d_today=today_value,
        threshold_value=thresh,
        fires_in_window=fires_in_window,
        last_fire_date=new_last_fire,
        reason=reason,
        ratio_today=ratio_today_val,
        ratio_threshold=ratio_thresh,
        ratio_fired_today=ratio_fired_today,
        ratio_last_fire_date=new_ratio_fire,
        ratio_active=ratio_active,
        which_fired=which,
    )
