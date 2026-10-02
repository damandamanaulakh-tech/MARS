"""Value/quality co-scorer — ported from the Chityy pyramid engine (C1/C2).

Pure momentum buys the leaders that fall hardest in a weak tape (why the book
went all-red). This adds a quality tilt — low volatility + liquidity, and
OPTIONALLY fundamentals (PE / beta / analyst rec) when a snapshot is fed — to
blend with momentum so the book holds steadier names too.

Computed from the OHLCV already in memory: no extra network calls, no extra
DataFrames (keeps Render's 512 MB budget). Fundamentals are opt-in via a
snapshot dict (e.g. from the 30k master); live scan runs without them.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(max(lo, min(hi, x)))


def _ann_vol_pct(close: pd.Series, lookback: int = 63) -> Optional[float]:
    c = close.dropna()
    if len(c) < lookback + 1:
        return None
    r = c.pct_change().dropna().iloc[-lookback:]
    if len(r) < 5:
        return None
    return float(r.std() * np.sqrt(252) * 100)


def low_vol_score(close: pd.Series, lookback: int = 63) -> float:
    """Lower realised volatility -> higher score. ~15% vol -> 92, 60% -> 15."""
    v = _ann_vol_pct(close, lookback)
    if v is None:
        return 50.0
    return _clip(100 - (v - 10) * 1.7)


def liquidity_score(volume: Optional[pd.Series], lookback: int = 20) -> float:
    """Chityy-style liquidity tiers on 20-day average volume.

    SHARE COUNT, deliberately kept (audit F-8, measured 2026-10-02 and NOT
    changed). Ranking by rupee turnover is the correct liquidity measure and
    the entry gate uses it — but here this number is a SCORE input, and
    scripts/quality_liquidity_rupee.py measured the swap on the committed
    191-name x 12-year panel with rupee tiers set at the same quantiles
    (Rs 67 / 23 / 10 crore): entries got WORSE in both halves (mean 126-day
    forward return 6.97% -> 6.45% train, 14.31% -> 13.74% test). The names
    the swap would drop went on to +12.6% / +35.9%, the names it would add
    +0.4% / +8.0%. Record: research/quality_liquidity_2026-10-02/. Do not
    re-propose without new evidence."""
    if volume is None:
        return 50.0
    v = volume.dropna()
    if v.empty:
        return 50.0
    adv = float(v.iloc[-lookback:].mean())
    if adv > 1_000_000:
        return 90.0
    if adv > 300_000:
        return 70.0
    if adv > 100_000:
        return 55.0
    return 30.0


def fundamental_score(snapshot: Optional[dict]) -> Optional[float]:
    """Optional value/quality from a snapshot dict (PE, beta, analyst_rec).
    Returns None when no snapshot is provided (live scan avoids slow .info
    fetches that 403 from cloud IPs). Mirrors Chityy's pe/beta/rec logic."""
    if not snapshot:
        return None
    vals = []
    pe = snapshot.get("pe")
    if pe is not None:
        vals.append(_clip(100 - ((pe - 20) * 2 + 40)))   # low PE -> high
    beta = snapshot.get("beta")
    if beta is not None:
        vals.append(_clip(100 - beta * 50))              # low beta -> high
    rec = str(snapshot.get("analyst_rec") or "").lower()
    if "buy" in rec:
        vals.append(90.0 if "strong" in rec else 75.0)
    elif "sell" in rec:
        vals.append(20.0)
    elif "hold" in rec:
        vals.append(50.0)
    return round(sum(vals) / len(vals), 2) if vals else None


def quality_score(close: pd.Series, volume: Optional[pd.Series] = None,
                  snapshot: Optional[dict] = None) -> float:
    """0-100 quality tilt: low-vol + liquidity (+ fundamentals if fed)."""
    parts = [low_vol_score(close), liquidity_score(volume)]
    f = fundamental_score(snapshot)
    if f is not None:
        parts.append(f)
    return round(sum(parts) / len(parts), 2)


def blend_scores(momentum: float, quality: float, quality_weight: float = 0.0) -> float:
    """Final rank score = (1-w)*momentum + w*quality."""
    w = max(0.0, min(1.0, quality_weight))
    return round((1 - w) * momentum + w * quality, 2)
