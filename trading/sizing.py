"""Position sizing — fixed rupees or ½-Kelly (owner order 2026-07-30).

The owner ran the numbers in docs/ALGO_V1_V2.md and chose **½-Kelly**:

    growth rate     P(book ever halves)
    ¼-Kelly   43.8%          0.78%
    ½-Kelly   75.0%         12.5%      <-- chosen
    1x Kelly 100.0%         50.0%
    2x Kelly    0.0%        100.0%

½-Kelly buys three-quarters of the maximum compound growth rate for a 12.5%
chance of ever halving the book, instead of full Kelly's 50%. Never go above
1x: 1.5x earns the same 75% as ½ while carrying 6x the ruin risk.

WHY THIS LIVES HERE AND NOT IN lens_v2.py: the engine is forbidden by test
from importing trading/lens_v2.py (the read-only research lens). The maths is
identical, so it lives in this module and BOTH import it — the engine to size
real paper trades, lens_v2 to display the v2 view.

THE HONEST CAVEAT, unchanged from the doc: Kelly needs an expected return, and
the expected return is built from an ASSUMED information coefficient
(cfg.kelly_ic, default 0.05). Kelly is famously fragile to that estimate —
which is exactly why the owner is at ½ and not 1x, and why every size is
clamped by cfg.kelly_max_pct and cfg.min_position_value before it can trade.
"""

from __future__ import annotations

import math
from typing import Optional

import pandas as pd

TRADING_DAYS = 252


def annual_vol(close: pd.Series, days: int = 63) -> float:
    """Realised annualised volatility from daily returns. 0 when too short."""
    if close is None:
        return 0.0
    r = close.pct_change().dropna().tail(days)
    if len(r) < 20:
        return 0.0
    return float(r.std() * math.sqrt(TRADING_DAYS))


def edge_estimate(score: float, close: pd.Series, ic: float = 0.05,
                  floor: float = 62.0) -> tuple[float, float]:
    """(mu, sigma): expected annual excess return and annual vol.

    Grinold: alpha = IC * sigma * z. The engine's 0-100 score becomes a
    z-score anchored on the LIVE entry floor, so a stock sitting exactly at
    the floor carries zero estimated edge — and therefore zero bet."""
    sig = annual_vol(close)
    if sig <= 0:
        return 0.0, 0.0
    z = (float(score) - float(floor)) / 30.0
    return float(ic * sig * z), sig


def kelly_fraction(mu: float, sigma: float, fraction: float = 0.5,
                   cap: float = 0.05) -> float:
    """Fraction of equity to bet: `fraction * mu / sigma^2`, capped.

    `cap` is a hard per-position ceiling as a share of equity — the backstop
    against a bad mu estimate turning into a concentrated bet."""
    if sigma <= 0 or mu <= 0:
        return 0.0
    return float(min(cap, fraction * mu / (sigma * sigma)))


def kelly_notional(cfg, equity: float, score: float,
                   close: pd.Series) -> tuple[float, dict]:
    """Rupee size for one candidate under ½-Kelly, plus a diagnostics dict
    for the journal so every sizing decision stays auditable."""
    ic = float(getattr(cfg, "kelly_ic", 0.05))
    frac = float(getattr(cfg, "kelly_fraction", 0.5))
    cap = float(getattr(cfg, "kelly_max_pct", 0.05))
    floor = float(getattr(cfg, "min_score_to_enter", 62.0))

    mu, sigma = edge_estimate(score, close, ic=ic, floor=floor)
    f = kelly_fraction(mu, sigma, fraction=frac, cap=cap)
    notional = f * float(equity)
    return notional, {
        "mode": "half_kelly" if abs(frac - 0.5) < 1e-9 else f"kelly_{frac:g}",
        "mu_pct": round(mu * 100, 3),
        "sigma_pct": round(sigma * 100, 2),
        "kelly_pct": round(f * 100, 3),
        "notional": round(notional, 0),
        "capped_at_max": bool(f >= cap - 1e-12 and mu > 0),
        "ic_assumed": ic,
        "floor": floor,
    }


def position_notional_for(cfg, equity: float, score: Optional[float] = None,
                          close: Optional[pd.Series] = None) -> tuple[float, dict]:
    """THE one sizing entry point. Returns (rupees, diagnostics).

    Kelly when the mode says so AND we have the inputs it needs; otherwise the
    fixed rupee size. Degrading to fixed is deliberate: a path that cannot
    estimate an edge must not guess one."""
    mode = str(getattr(cfg, "sizing_mode", "fixed")).lower()
    if mode in ("kelly", "half_kelly") and score is not None and close is not None:
        notional, diag = kelly_notional(cfg, equity, score, close)
        if notional > 0:
            return notional, diag
        return 0.0, {**diag, "reason": "no_estimated_edge"}
    return cfg.position_notional(equity), {"mode": "fixed",
                                           "notional": cfg.position_notional(equity)}
