"""Pre-entry risk controls — the 5 critical fixes every reviewer flagged.

This module is purely additive. It does NOT modify existing risk.py logic.
The engine calls these as additional gates BEFORE the existing can_open().

Critical fixes:
  1. Graduated drawdown governor — 5 levels (-5/-8/-10/-15/-20)
  2. Pre-entry correlation gate — reject if any open position correlates > 0.7
  3. Vol-adjusted position sizing — ATR-based 3-tier (low/mid/high vol)
  4. Hard liquidity filters — ADV, mcap, spread
  5. Consecutive loss halt — 2 losing trades same day -> halt entries
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


# ============================================================================
# 1. GRADUATED DRAWDOWN GOVERNOR
# ============================================================================

DRAWDOWN_LEVELS = [
    # (threshold_pct_from_peak, level_name, sizing_multiplier, action_desc)
    (-0.05, "watchful",     0.75, "Reduce new position size to 75%"),
    (-0.08, "stop_adding",  0.0,  "No new positions; existing trail"),
    (-0.10, "cut_half",     0.0,  "Cut total exposure 50%"),
    (-0.15, "mostly_cash",  0.0,  "Move to mostly cash; minimal positions"),
    (-0.20, "full_halt",    0.0,  "Full strategy halt; close everything"),
]


@dataclass
class DrawdownGovernor:
    """Tracks peak equity and current drawdown; returns the active level."""
    peak_equity: float = 0.0
    last_equity: float = 0.0
    active_level: str = "normal"
    sizing_multiplier: float = 1.0
    halt_new_entries: bool = False
    forced_cut: float = 0.0   # fraction of exposure to liquidate now (0..1)
    reason: str = ""

    def update(self, current_equity: float) -> None:
        """Call on every refresh. Mutates state with new readings."""
        self.last_equity = current_equity
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity
        if self.peak_equity <= 0:
            self.active_level = "normal"
            self.sizing_multiplier = 1.0
            self.halt_new_entries = False
            self.forced_cut = 0.0
            self.reason = "no peak yet"
            return
        dd = (current_equity - self.peak_equity) / self.peak_equity
        # Walk thresholds from least to most severe
        level_name = "normal"
        sizing_mult = 1.0
        halt_new = False
        forced_cut = 0.0
        reason = f"DD {dd*100:+.2f}% from peak ₹{self.peak_equity:,.0f}"
        for threshold, name, mult, _ in DRAWDOWN_LEVELS:
            if dd <= threshold:
                level_name = name
                sizing_mult = mult
                if name in {"stop_adding", "cut_half", "mostly_cash", "full_halt"}:
                    halt_new = True
                if name == "cut_half":
                    forced_cut = 0.50
                elif name == "mostly_cash":
                    forced_cut = 0.80
                elif name == "full_halt":
                    forced_cut = 1.0
        self.active_level = level_name
        self.sizing_multiplier = sizing_mult
        self.halt_new_entries = halt_new
        self.forced_cut = forced_cut
        self.reason = reason

    def snapshot(self) -> dict:
        return {
            "level": self.active_level,
            "peak": self.peak_equity,
            "current": self.last_equity,
            "drawdown_pct": (
                (self.last_equity - self.peak_equity) / self.peak_equity * 100
                if self.peak_equity > 0 else 0.0
            ),
            "sizing_multiplier": self.sizing_multiplier,
            "halt_new_entries": self.halt_new_entries,
            "forced_cut": self.forced_cut,
            "reason": self.reason,
        }


# ============================================================================
# 2. CORRELATION GATE
# ============================================================================

def daily_returns(close: pd.Series, n: int = 60) -> Optional[pd.Series]:
    if close is None or len(close.dropna()) < n + 1:
        return None
    return close.dropna().iloc[-(n + 1):].pct_change().dropna()


def correlation(a: pd.Series, b: pd.Series) -> Optional[float]:
    if a is None or b is None or a.empty or b.empty:
        return None
    df = pd.concat([a, b], axis=1, keys=["a", "b"]).dropna()
    if len(df) < 20:
        return None
    return float(df["a"].corr(df["b"]))


def correlation_gate_blocks(
    candidate_returns: Optional[pd.Series],
    open_position_returns: dict[str, pd.Series],
    threshold: float = 0.70,
) -> Optional[tuple[str, float]]:
    """Returns (existing_ticker, corr) if candidate correlates > threshold
    with ANY open position. Returns None if safe to add.
    """
    if candidate_returns is None or candidate_returns.empty:
        return None
    for ticker, ret in open_position_returns.items():
        c = correlation(candidate_returns, ret)
        if c is not None and c > threshold:
            return (ticker, c)
    return None


# ============================================================================
# 3. VOL-ADJUSTED POSITION SIZING (3 TIERS)
# ============================================================================

# Tier thresholds in annualised volatility (%):
#   low-vol  : < 25%  -> 18% allocation
#   mid-vol  : 25-45% -> 12% allocation
#   high-vol : > 45%  -> 6%  allocation
TIER_THRESHOLDS = [(25.0, "low", 0.18), (45.0, "mid", 0.12), (1e9, "high", 0.06)]


def annualised_vol_pct(close: pd.Series, lookback: int = 21) -> Optional[float]:
    if close is None or len(close.dropna()) < lookback + 1:
        return None
    pct = close.dropna().pct_change().dropna().iloc[-lookback:]
    if len(pct) < lookback:
        return None
    return float(pct.std() * np.sqrt(252) * 100)


def vol_tier(close: pd.Series) -> tuple[str, float]:
    """Returns (tier_name, alloc_pct). Defaults to mid if can't compute."""
    vol = annualised_vol_pct(close)
    if vol is None:
        return "mid", 0.12
    for cutoff, name, alloc in TIER_THRESHOLDS:
        if vol < cutoff:
            return name, alloc
    return "high", 0.06


def vol_adjusted_qty(
    portfolio_value: float,
    fill_price: float,
    close: pd.Series,
    sizing_multiplier: float = 1.0,
) -> tuple[int, str]:
    """Returns (qty, tier_name) for the new position. Multiplier applied
    on top (for drawdown-governor + regime damping)."""
    tier, alloc_pct = vol_tier(close)
    target_value = portfolio_value * alloc_pct * sizing_multiplier
    if fill_price <= 0 or target_value <= 0:
        return 0, tier
    return int(target_value // fill_price), tier


# ============================================================================
# 4. HARD LIQUIDITY FILTERS
# ============================================================================

@dataclass
class LiquidityRules:
    min_avg_daily_volume_shares: float = 200_000     # at least 2L shares/day
    min_rupee_volume_5d_cr: float = 5.0              # min ₹5 Cr daily rupee turnover (avg over 5d)
    max_spread_pct: float = 0.001                    # 0.1% bid-ask (Upstox-derived; soft when no live quote)
    min_market_cap_cr: float = 5_000.0               # min ₹5,000 Cr free-float mcap
    require_no_circuit: bool = True                  # skip if at upper/lower circuit today


def passes_liquidity(
    close: pd.Series,
    volume: pd.Series,
    last_high: Optional[float] = None,
    last_low: Optional[float] = None,
    last_open: Optional[float] = None,
    last_close: Optional[float] = None,
    market_cap_cr: Optional[float] = None,
    spread_pct: Optional[float] = None,
    rules: Optional[LiquidityRules] = None,
) -> tuple[bool, str]:
    """Returns (passes, reason). reason is empty on pass.

    market_cap_cr and spread_pct are checked when provided (caller pulls them
    from Upstox quote / yfinance fast_info). When None, the corresponding
    check is skipped — keeps the gate from blocking on missing reference data.
    """
    rules = rules or LiquidityRules()

    if close is None or volume is None or len(close.dropna()) < 20:
        return False, "insufficient_history"

    # Average daily volume (last 20 days)
    adv = float(volume.dropna().iloc[-20:].mean())
    if adv < rules.min_avg_daily_volume_shares:
        return False, f"adv {adv:,.0f} < {rules.min_avg_daily_volume_shares:,.0f}"

    # Rupee volume (price * vol, avg of 5 days, in Crores)
    rupee_vol = (close.iloc[-5:] * volume.iloc[-5:]).dropna().mean()
    rupee_vol_cr = float(rupee_vol) / 1e7
    if rupee_vol_cr < rules.min_rupee_volume_5d_cr:
        return False, f"rupee_vol {rupee_vol_cr:.2f}Cr < {rules.min_rupee_volume_5d_cr}Cr"

    # Market cap hard floor (only when provided)
    if market_cap_cr is not None and market_cap_cr < rules.min_market_cap_cr:
        return False, f"mcap {market_cap_cr:.0f}Cr < {rules.min_market_cap_cr:.0f}Cr"

    # Spread hard ceiling (only when provided — live quote required)
    if spread_pct is not None and spread_pct > rules.max_spread_pct:
        return False, f"spread {spread_pct*100:.3f}% > {rules.max_spread_pct*100:.2f}%"

    # Circuit-filter check: open == high == low == close means stuck at circuit
    if rules.require_no_circuit and None not in (last_open, last_high, last_low, last_close):
        if last_open == last_high == last_low == last_close:
            return False, "stuck_at_circuit"

    return True, ""


# ============================================================================
# 5a. DYNAMIC CASH RESERVE (regime-aware)
# ============================================================================

# P2/B2: bull/neutral deploy fully (user: 100% in good markets); genuine
# stress regimes force protective cash back in.
DYNAMIC_CASH_RESERVE = {
    "bull_extension": 0.00,
    "neutral":        0.00,
    "correction":     0.20,
    "panic":          0.40,
}


def dynamic_cash_reserve_pct(regime_name: Optional[str], floor: float = 0.20) -> float:
    """Returns the cash reserve fraction (0..1) for the given regime.

    Defaults to `floor` when regime is unknown — protects against under-
    reserving when the classifier hasn't run yet.
    """
    if not regime_name:
        return floor
    return DYNAMIC_CASH_RESERVE.get(regime_name, floor)


# ============================================================================
# 6. CONSECUTIVE LOSS HALT
# ============================================================================

@dataclass
class ConsecutiveLossTracker:
    today: Optional[date] = None
    losses_today: int = 0
    max_consecutive: int = 2  # halt at 2 losers in a day

    def record_close(self, exit_date: date, net_pnl: float) -> None:
        """Call when a position closes. Resets count on a new day."""
        if self.today != exit_date:
            self.today = exit_date
            self.losses_today = 0
        if net_pnl < 0:
            self.losses_today += 1

    def halted(self, today: Optional[date] = None) -> bool:
        today = today or self.today
        if today != self.today:
            return False
        return self.losses_today >= self.max_consecutive

    def snapshot(self) -> dict:
        return {
            "today": self.today.isoformat() if self.today else None,
            "losses_today": self.losses_today,
            "max_allowed": self.max_consecutive,
            "halted": self.halted(),
        }
