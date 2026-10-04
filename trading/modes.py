"""Trade-horizon mode presets.

Same AM 07 scoring engine, four different "personalities" via weight +
exit + timing presets:

  intraday   — same day, square-off by 3:25 PM IST
  swing      — 2-4 weeks   (20 sessions)
  short      — 1-3 months  (63 sessions)
  positional — 1-12 months (252 sessions)

HORIZONS REMAPPED 2026-09-10 on the owner's order: "app should buy the stock
under there Horizon of intraday, Swing for 2-4 Weeks, Short 1-3 months, Long
1-12 Months or when it hit target, and all Horizon have target formula".
Before this, `short` held 7 days and `swing` held 60 — the app's short was
SHORTER than its swing, the opposite of what those words mean to the owner.
Targets are now k x ATR per horizon (see ATR_TARGET_K), not fixed percentages.

Each preset overrides AM07_WEIGHTS and exit thresholds. The weights are
read at scoring time by scoring.weights_for(mode) — for intraday, short,
swing and positional. That was only true on paper until 2026-10-04 (owner:
"use each category's weights"); before then every category scored with the
global weights (= the swing preset). Momentum ranks on its own blend, not
the composite, so its preset below is never used to buy.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional

import threading

# AUDIT FIX C3 (2026-08-09): the 09:20 rotation mutates the ONE shared live
# Config's mode through 4 presets while web requests execute — an owner
# action mid-scan could run under the wrong mode's stops/floor. This lock is
# held by the rotation for each mode's pass and best-effort acquired by the
# owner buy paths, so a manual buy no longer interleaves a preset swap.
MODE_LOCK = threading.RLock()

# AUDIT S-6 (2026-10-02): the comment above promised the owner paths took the
# lock; none did. Every scan pass, every exit pass and every owner action that
# swaps or reads the active mode now holds it. A web request waits at most
# this long for a running scan pass, then tells the owner it was busy instead
# of hanging the page.
MODE_LOCK_WAIT_S = 30.0


class ModeBusy(RuntimeError):
    """A scan pass held the mode lock for longer than the caller would wait."""


@contextmanager
def holding_mode_lock(timeout: Optional[float] = None):
    """Hold MODE_LOCK for the block. `timeout=None` waits as long as it
    takes (background jobs); a number raises ModeBusy if a scan pass still
    holds it after that many seconds (web requests); 0 = do not wait."""
    if timeout is None:
        got = MODE_LOCK.acquire()
    elif timeout > 0:
        got = MODE_LOCK.acquire(timeout=timeout)
    else:
        got = MODE_LOCK.acquire(blocking=False)
    if not got:
        raise ModeBusy("a scan pass is running")
    try:
        yield
    finally:
        MODE_LOCK.release()


# Top-level AM 07 weight presets per mode. All sum to 1.0.
WEIGHTS = {
    "intraday": {
        # Intraday: technical signals dominate, fundamentals irrelevant.
        "momentum_1m":       0.10,   # 1-day return momentum
        "volume_expansion":  0.20,   # 5-min volume vs 20-min baseline
        "earnings_growth":   0.00,   # ignored intraday
        "catalyst":          0.10,   # gap-up + news
        "relative_strength": 0.10,   # vs Nifty intraday
        "vol_adj_upside":    0.10,
        "short_interest":    0.00,
        "analyst_revision":  0.00,
        "breakout_quality":  0.30,   # ORB + VWAP reclaim heavily weighted
        "sector_alignment":  0.10,
    },
    "short": {
        # 1-7 day holds: momentum + breakout, light fundamentals
        "momentum_1m":       0.25,
        "volume_expansion":  0.15,
        "earnings_growth":   0.05,
        "catalyst":          0.10,
        "relative_strength": 0.10,
        "vol_adj_upside":    0.10,
        "short_interest":    0.00,
        "analyst_revision":  0.05,
        "breakout_quality":  0.15,
        "sector_alignment":  0.05,
    },
    "swing": {
        # Default — what's currently running.
        "momentum_1m":       0.20,
        "volume_expansion":  0.15,
        "earnings_growth":   0.10,
        "catalyst":          0.10,
        "relative_strength": 0.10,
        "vol_adj_upside":    0.10,
        "short_interest":    0.05,
        "analyst_revision":  0.05,
        "breakout_quality":  0.05,
        "sector_alignment":  0.10,
    },
    "positional": {
        # Multi-month holds — fundamentals + relative strength dominate.
        "momentum_1m":       0.10,
        "volume_expansion":  0.05,
        "earnings_growth":   0.25,
        "catalyst":          0.05,
        "relative_strength": 0.20,
        "vol_adj_upside":    0.15,
        "short_interest":    0.05,
        "analyst_revision":  0.10,
        "breakout_quality":  0.00,
        "sector_alignment":  0.05,
    },
    "nifty200_momentum": {
        # NSE Nifty 200 Momentum 30 methodology — pure momentum, no
        # fundamentals. Score is computed in trading/strategies/nifty200_momentum.py
        # using 6m + 12m sharpe-style momentum. These weights are placeholders;
        # the engine uses the strategy module's momentum_score() when
        # cfg.mode == "nifty200_momentum".
        "momentum_1m":       0.50,
        "volume_expansion":  0.10,
        "earnings_growth":   0.00,
        "catalyst":          0.00,
        "relative_strength": 0.20,
        "vol_adj_upside":    0.20,
        "short_interest":    0.00,
        "analyst_revision":  0.00,
        "breakout_quality":  0.00,
        "sector_alignment":  0.00,
    },
}


@dataclass
class ModePreset:
    """Holds the mode-specific exit + timing config overrides."""
    name: str
    bar_interval: str            # "5m" | "15m" | "1d" | "1wk"
    history_period: str          # yfinance period
    stop_loss_pct: float
    take_profit_pct: float
    trailing_stop_pct: float
    score_decay_exit: float
    max_hold_days: int
    min_score_to_enter: float
    breakout_threshold_pct: float
    volume_confirm_ratio: float
    square_off_time: str | None  # "15:25" IST for intraday; None otherwise
    description: str


PRESETS: dict[str, ModePreset] = {
    "intraday": ModePreset(
        name="intraday",
        bar_interval="5m",
        history_period="5d",
        stop_loss_pct=-0.015,
        take_profit_pct=0.025,
        trailing_stop_pct=-0.01,
        score_decay_exit=40.0,
        max_hold_days=1,
        min_score_to_enter=72.0,
        breakout_threshold_pct=0.005,
        volume_confirm_ratio=1.5,
        square_off_time="15:25",
        description=(
            "Tight stops, fast targets, all positions auto-square-off by "
            "3:25 PM IST. Needs Upstox real-time WebSocket + always-on host."
        ),
    ),
    "short": ModePreset(
        name="short",
        bar_interval="1d",
        history_period="3mo",
        stop_loss_pct=-0.03,
        take_profit_pct=0.06,
        trailing_stop_pct=-0.04,
        score_decay_exit=45.0,
        # HORIZON REMAP 2026-09-10 (owner: "Short 1-3 months"). Was 7 days —
        # the app's "short" was SHORTER than its own "swing" (60 days), i.e.
        # the two names were swapped against the owner's meaning, so every
        # trade ever tagged short or swing ran on the wrong horizon.
        max_hold_days=63,            # 3 months of sessions
        min_score_to_enter=70.0,
        breakout_threshold_pct=0.015,
        volume_confirm_ratio=1.3,
        square_off_time=None,
        description=(
            "1-3 month holds (owner's horizon, 2026-09-10). Daily bars. "
            "Target is k x ATR, so a quiet name gets a nearer target than a "
            "wild one."
        ),
    ),
    "swing": ModePreset(
        name="swing",
        bar_interval="1d",
        history_period="6mo",
        stop_loss_pct=-0.05,
        take_profit_pct=0.10,
        trailing_stop_pct=-0.08,
        score_decay_exit=45.0,        # loosened from 50 to match lower entry bar
        # HORIZON REMAP 2026-09-10 (owner: "Swing for 2-4 Weeks"). Was 60
        # days — see the note on `short` above; the two were swapped.
        max_hold_days=20,             # 4 weeks of sessions
        min_score_to_enter=60.0,      # was 70 — too strict, gave only 6 trades / 2yr
        breakout_threshold_pct=0.03,  # was 0.02 — allow 3% off the 20-day high
        volume_confirm_ratio=1.2,     # was 1.3 — slightly looser volume confirm
        square_off_time=None,
        description="2-4 week holds (owner's horizon, 2026-09-10). Daily bars. Target is k x ATR.",
    ),
    "positional": ModePreset(
        name="positional",
        bar_interval="1wk",
        history_period="2y",
        stop_loss_pct=-0.10,
        take_profit_pct=0.25,
        trailing_stop_pct=-0.12,
        score_decay_exit=45.0,
        # Owner's "Long 1-12 Months" horizon (2026-09-10): 12 months of
        # sessions, up from 180 calendar days.
        max_hold_days=252,
        min_score_to_enter=68.0,
        breakout_threshold_pct=0.03,
        volume_confirm_ratio=1.2,
        square_off_time=None,
        description=(
            "1-6 month holds. Weekly bars. Fundamentals weighted higher. "
            "Lower turnover, lower stress."
        ),
    ),
    "nifty200_momentum": ModePreset(
        name="nifty200_momentum",
        bar_interval="1d",
        history_period="2y",         # need 12m history for the momentum score
        stop_loss_pct=-0.05,         # user decision 2026-07-26: "stop loss 5% not
                                     # 12" — tighter protection, owner's order;
                                     # UNTESTED at this number (N9 grid used -12);
                                     # 60-day window judges
        take_profit_pct=0.20,        # OWNER ORDER 2026-08-24 (Node 12): picked
                                     # "Switch to D" from the options card after
                                     # BOTH exit studies (floor 55 committed +
                                     # fresh floor-62 live-era rerun) showed
                                     # -5/+20/trail-15 beating +12 in all four
                                     # windows (+69.9 vs +54.2 total at 62).
                                     # Supersedes 2026-07-21 "keep 12% for now".
                                     # At +20 the -15 trail ARMS above +17.6%
                                     # and becomes a real exit (was dead at +12:
                                     # a 0.24pp window).
        trailing_stop_pct=-0.15,
        score_decay_exit=40.0,
        max_hold_days=200,           # semi-annual rebalance horizon
        min_score_to_enter=62.0,     # owner order 2026-07-30: "it will buy above
                                     # 62". Raised from 55 after the ALGO_V1_V2
                                     # write-up revealed the live floor was 55 and
                                     # not the 70 every doc had claimed. Buys at
                                     # 62.0 and above. UNTESTED at this number —
                                     # the 60-day paper window judges it.
                                     # (History: P0/B3 set 55 because 50 = "no
                                     # edge"; the dataclass default 70 has always
                                     # been dead — apply_mode_preset overwrites it.)
        breakout_threshold_pct=1.0,  # timing OFF for momentum (hold leaders, don't time entries)
        volume_confirm_ratio=0.0,    # timing OFF — no volume gate
        square_off_time=None,
        description=(
            "Nifty 200 Momentum 30 — NSE's published methodology. Universe "
            "is Nifty 200; pick top 30 by 6m+12m sharpe momentum. "
            "Semi-annual rebalance. 5y CAGR ~29.7% (per NSE)."
        ),
    ),
}


def get_preset(mode: str) -> ModePreset:
    return PRESETS.get(mode, PRESETS["swing"])


def get_weights(mode: str) -> dict[str, float]:
    return WEIGHTS.get(mode, WEIGHTS["swing"])
