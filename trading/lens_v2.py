"""LENS V2 — the growth-first ("aggressive") lens. READ-ONLY.

Owner ask 2026-07-30: score the universe with a second, bolder point of view
next to the live engine — "v1 for current, v2 for what we r mixing" — so the
owner can SEE what a compound-growth-maximising agent would shortlist,
without the engine ever acting on it.

  v1 (live engine)  maximises: return SUBJECT TO not blowing up.
  v2 (this module)  maximises: E[log W] — the long-run compound growth rate.

That one flip is the whole difference. v1 sizes every trade the same
(Rs 1.25L), spreads across 80 names, caps risk. v2 sizes by edge (Kelly),
concentrates into conviction, and hunts asymmetry. This module computes the
v2 view and NOTHING else: no order, no position, no config mutation. The
engine does not import it (guarded by a test).

HONEST LIMITS — stated, not hidden:
  * mu (expected return) is an ESTIMATE built from a momentum z-score via the
    Grinold relation alpha = IC * sigma * z. IC is an ASSUMPTION (default
    0.05, a realistic single-factor cross-sectional IC). Kelly is famously
    fragile to mu error, so kelly_fraction() returns FRACTIONAL Kelly.
  * No options chain, no order-book depth, no live short interest, no
    intraday microstructure exist in this app. So true convexity pricing,
    real impact measurement and borrow-cost shorting are NOT implementable
    here honestly — the convexity and capacity numbers below are proxies
    computed from daily OHLCV only, and are labelled as proxies.
  * Every function is a pure function of a DataFrame. No network.
"""

from __future__ import annotations

import math
from typing import Optional

import pandas as pd

# The Kelly maths is SHARED with the live engine (trading/sizing.py) so the
# lens can never drift from the sizing that actually trades. It lives there
# and not here because the engine is forbidden by test from importing this
# module — v2 stays a viewer.
from .sizing import annual_vol, edge_estimate, kelly_fraction  # noqa: F401

# ---- assumptions, named so they can be argued with -----------------------
DEFAULT_IC = 0.05          # cross-sectional information coefficient (assumed)
KELLY_FRACTION = 0.25      # quarter-Kelly for the DISPLAY lens (the live
                           # engine runs cfg.kelly_fraction — ½ since the
                           # owner's 2026-07-30 order)
IMPACT_Y = 0.75            # square-root-law coefficient (literature ~0.5-1)
TRADING_DAYS = 252
_EPS = 1e-12


def _closes(df: pd.DataFrame) -> Optional[pd.Series]:
    if df is None or df.empty or "Close" not in df.columns:
        return None
    c = df["Close"].dropna()
    return c if len(c) >= 60 else None


# ---- 1. edge + Kelly: imported from trading/sizing.py (shared with the
#         live engine so the lens and the book can never disagree)

# ---- 2. convexity: is the payoff asymmetric? ----------------------------

def atr(df: pd.DataFrame, n: int = 14) -> float:
    """Average True Range — the honest volatility unit for stop distance."""
    if df is None or len(df) < n + 1:
        return 0.0
    h, l, c = df["High"].astype(float), df["Low"].astype(float), df["Close"].astype(float)
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return float(tr.tail(n).mean())


def return_skew(close: pd.Series, days: int = 126) -> float:
    """Skew of daily returns. A compounder PREFERS positive skew: many small
    losses, occasional large win, because log-wealth rewards the tail."""
    r = close.pct_change().dropna().tail(days)
    if len(r) < 30:
        return 0.0
    return float(r.skew())


def convexity(df: pd.DataFrame, stop_pct: float = 0.05) -> dict:
    """Proxy payoff asymmetry from daily bars only (no options chain here).

    room  = distance to the 252-day high in ATR units (open upside)
    R     = payoff ratio: that room vs the stop distance
    breakeven hit-rate = 1/(1+R) — the win rate this bet needs to be +EV
    """
    c = _closes(df)
    if c is None:
        return {"atr": 0.0, "room_atr": 0.0, "payoff_R": 0.0,
                "breakeven_hit": 1.0, "skew": 0.0}
    a = atr(df)
    last = float(c.iloc[-1])
    hi = float(c.tail(TRADING_DAYS).max())
    room = (hi - last) / a if a > 0 else 0.0
    stop_rupees = last * stop_pct
    R = ((hi - last) / stop_rupees) if stop_rupees > 0 else 0.0
    return {
        "atr": round(a, 2),
        "room_atr": round(room, 2),
        "payoff_R": round(R, 2),
        "breakeven_hit": round(1.0 / (1.0 + R), 3) if R > 0 else 1.0,
        "skew": round(return_skew(c), 2),
    }


# ---- 3. capacity: the wall the "buy a data centre" plan hits ------------

def adv_rupees(df: pd.DataFrame, days: int = 60) -> float:
    """Median daily rupee turnover — the real capacity constraint."""
    if df is None or df.empty or "Volume" not in df.columns:
        return 0.0
    v = (df["Close"].astype(float) * df["Volume"].astype(float)).dropna().tail(days)
    return float(v.median()) if len(v) else 0.0


def impact_bps(df: pd.DataFrame, notional: float, y: float = IMPACT_Y) -> float:
    """Square-root law: impact ~ Y * sigma_daily * sqrt(Q / ADV), in bps.

    THIS is why 'scale until you own data centres' fails: cost grows with the
    SQUARE ROOT of your size, so every rupee you add makes every rupee you
    already have worse. Doubling size does not double the edge — it taxes it."""
    c = _closes(df)
    adv = adv_rupees(df)
    if c is None or adv <= 0 or notional <= 0:
        return 0.0
    r = c.pct_change().dropna().tail(63)
    sig_d = float(r.std()) if len(r) >= 20 else 0.0
    return float(y * sig_d * math.sqrt(notional / adv) * 10_000.0)


def capacity_notional(df: pd.DataFrame, max_impact_bps: float = 25.0,
                      y: float = IMPACT_Y) -> float:
    """Largest position (Rs) whose modelled impact stays under a bps budget.
    Invert the square-root law: Q = ADV * (bps / (1e4 * Y * sigma))^2."""
    c = _closes(df)
    adv = adv_rupees(df)
    if c is None or adv <= 0:
        return 0.0
    r = c.pct_change().dropna().tail(63)
    sig_d = float(r.std()) if len(r) >= 20 else 0.0
    if sig_d <= 0:
        return 0.0
    ratio = max_impact_bps / (10_000.0 * y * sig_d)
    return float(adv * ratio * ratio)


# ---- 4. velocity: how fast does the edge rot? --------------------------

def momentum_halflife(close: pd.Series, max_lag: int = 60) -> Optional[int]:
    """Crude alpha-decay proxy: lag at which return autocorrelation first
    falls below half its lag-1 value. Sets the natural holding period —
    hold past it and you are paying costs for a dead signal."""
    r = close.pct_change().dropna()
    if len(r) < 120:
        return None
    base = r.autocorr(lag=1)
    if base is None or abs(base) < 1e-6:
        return None
    target = abs(base) / 2.0
    for lag in range(2, max_lag + 1):
        a = r.autocorr(lag=lag)
        if a is None:
            continue
        if abs(a) <= target:
            return lag
    return None


# ---- 5. the v2 composite ------------------------------------------------

def v2_view(ticker: str, df: pd.DataFrame, v1_score: float,
            equity: float, ic: float = DEFAULT_IC,
            stop_pct: float = 0.05, floor: float = 62.0,
            fraction: float = 0.5, cap: float = 0.05) -> Optional[dict]:
    """The full growth-first read on one stock. None when history is too thin
    (DATA_NEEDED — never a guessed number).

    `floor` is the zero-edge anchor for the z-score — callers pass the LIVE
    entry floor (cfg.min_score_to_enter) so v2's edge estimate moves with the
    engine instead of drifting from a hardcoded number."""
    c = _closes(df)
    if c is None:
        return None
    mu, sigma = edge_estimate(v1_score, c, ic=ic, floor=floor)
    f = kelly_fraction(mu, sigma, fraction=fraction, cap=cap)
    cx = convexity(df, stop_pct=stop_pct)
    kelly_rupees = f * equity
    cap_rupees = capacity_notional(df)
    sized = min(kelly_rupees, cap_rupees) if cap_rupees > 0 else kelly_rupees
    hl = momentum_halflife(c)
    # growth rate a Kelly bettor expects from this bet: g = f*mu - f^2 sigma^2/2
    g = (f * mu - 0.5 * f * f * sigma * sigma) if f > 0 else 0.0
    return {
        "ticker": ticker,
        "v1_score": round(float(v1_score), 1),
        "mu_pct": round(mu * 100, 2),
        "sigma_pct": round(sigma * 100, 1),
        "kelly_pct": round(f * 100, 2),
        "kelly_rupees": round(kelly_rupees, 0),
        "capacity_rupees": round(cap_rupees, 0),
        "v2_size_rupees": round(sized, 0),
        "capacity_binds": bool(cap_rupees > 0 and cap_rupees < kelly_rupees),
        "impact_bps_at_size": round(impact_bps(df, sized), 1),
        "adv_rupees": round(adv_rupees(df), 0),
        "payoff_R": cx["payoff_R"],
        "breakeven_hit": cx["breakeven_hit"],
        "room_atr": cx["room_atr"],
        "skew": cx["skew"],
        "atr": cx["atr"],
        "halflife_days": hl,
        "growth_rate_pct": round(g * 100, 2),
        "flags": manipulation_risk_flags(df),
    }


def rank_v2(rows: list[dict]) -> list[dict]:
    """Sort by expected log-growth contribution — v2's actual objective.
    v1 sorts by score; v2 sorts by what compounds fastest."""
    return sorted(rows, key=lambda r: -(r.get("growth_rate_pct") or 0.0))


# ---- 6. DEFENSIVE: do not be someone else's exit liquidity -------------
# The honest half of "look at manipulation": detect the SIGNATURE from the
# outside so the book never buys into a ramp. This protects the owner's
# money. It is not, and must not become, a manipulation recipe.

def manipulation_risk_flags(df: pd.DataFrame) -> list[str]:
    """Outside-view warning signs of an engineered move in a thin stock.

    Every flag is computed from public daily OHLCV. These are RISK FLAGS,
    not accusations — the point is that v2's small-cap/illiquidity hunting
    is exactly where pump-and-dump schemes fish for buyers, so the lens must
    be able to say 'this run does not look real, stay out'."""
    out: list[str] = []
    c = _closes(df)
    if c is None or "Volume" not in df.columns:
        return out
    close = c.astype(float)
    vol = df["Volume"].astype(float).reindex(close.index).fillna(0.0)
    if len(close) < 70:
        return out

    ret20 = float(close.iloc[-1] / close.iloc[-21] - 1.0) if len(close) > 21 else 0.0
    v20 = float(vol.tail(20).mean())
    v60 = float(vol.iloc[-60:-20].mean()) if len(vol) >= 60 else 0.0
    turnover = adv_rupees(df)

    # 1. Parabolic price on NO extra volume — nobody real is buying it up.
    if ret20 > 0.40 and v60 > 0 and v20 < 1.2 * v60:
        out.append("parabolic_without_volume")
    # 2. Big move in a stock too thin to absorb any size — easy to ramp.
    if ret20 > 0.25 and 0 < turnover < 1_00_00_000:      # < Rs 1 Cr/day
        out.append("thin_book_ramp")
    # 3. Volume explosion with price stalling — distribution into buyers.
    if v60 > 0 and v20 > 3.0 * v60 and ret20 < 0.02:
        out.append("volume_spike_price_stall")
    # 4. Repeated near-limit up days — engineered move or a real halt cascade.
    daily = close.pct_change().dropna().tail(20)
    if (daily > 0.095).sum() >= 4:
        out.append("repeated_limit_up")
    # 5. Straight line up: abnormally few down days is not natural trading.
    if len(daily) >= 20 and (daily > 0).sum() >= 18:
        out.append("no_down_days")
    return out


def is_trap(flags: list[str]) -> bool:
    """Two or more flags = v2 refuses the name. One flag = show a warning."""
    return len(flags) >= 2
