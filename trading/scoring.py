"""AM 07 scoring — full 10-component score for daily NSE swing.

Weights match what the user specified:
  momentum_1m 0.20, volume_expansion 0.15, earnings_growth 0.10,
  catalyst 0.10, relative_strength 0.10, vol_adj_upside 0.10,
  short_interest 0.05, analyst_revision 0.05, breakout_quality 0.05,
  sector_alignment 0.10

Three components (catalyst, short_interest, analyst_revision) are stubbed
at neutral 50 because no free data source exists for NSE. When real feeds
are wired in later, only the per-component compute function changes — the
weights and engine code stay identical.
"""

from __future__ import annotations

from typing import Dict, Tuple

import numpy as np
import pandas as pd

from .config import AM07_WEIGHTS, Config, STUB_COMPONENTS
from .data import fetch_earnings_growth


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    if x != x:  # NaN
        # AUDIT FIX B9 (2026-08-09): NaN used to clip to 0 — MAXIMUM BEARISH.
        # A data hole scored as a strong sell signal is the opposite of the
        # DATA_NEEDED rule; missing input = neutral, visibly mid-scale.
        return 50.0
    return float(max(lo, min(hi, x)))


def _ret_pct(close: pd.Series, lookback: int) -> float:
    if len(close) < lookback + 1:
        return 0.0
    last = float(close.iloc[-1])
    ref = float(close.iloc[-lookback - 1])
    return (last / ref - 1) * 100 if ref else 0.0


def score_momentum_1m(close: pd.Series, cfg: Config) -> float:
    r = _ret_pct(close, cfg.momentum_lookback_days)
    # +5% over 1 month = 100; -5% = 0
    return _clip(50 + r * 10)


def score_volume_expansion(volume: pd.Series, cfg: Config,
                           close: pd.Series = None) -> float:
    """N7 fix (owner-approved 2026-07-26): volume expansion is bullish only
    on RISING days. The direction-blind version scored 100 on HDFC's -8.4%
    crash day and 84 on TCS's -7% day — panic selling read as buy fuel.
    A falling last bar now earns 0, whatever its volume."""
    if len(volume) < cfg.volume_long_days:
        return 50.0
    if close is not None and len(close) >= 2 and float(close.iloc[-1]) <= float(close.iloc[-2]):
        return 0.0
    short = float(volume.iloc[-cfg.volume_short_days:].mean())
    long_ = float(volume.iloc[-cfg.volume_long_days:].mean())
    if long_ <= 0:
        return 50.0
    ratio = short / long_
    # ratio 1.0 -> 50; ratio 2.0 -> 100; ratio 0.5 -> 0
    return _clip((ratio - 1) * 100 + 50)


def score_earnings_growth(ticker: str) -> float:
    g = fetch_earnings_growth(ticker)
    if g is None:
        return 50.0  # neutral when data missing
    # +20% YoY -> 100, -20% YoY -> 0
    return _clip(50 + g * 2.5)


def score_relative_strength(stock_close: pd.Series, bench_close: pd.Series, cfg: Config) -> float:
    if bench_close is None or len(bench_close) < cfg.medium_lookback_days + 1:
        return 50.0
    stock_r = _ret_pct(stock_close, cfg.medium_lookback_days)
    bench_r = _ret_pct(bench_close, cfg.medium_lookback_days)
    rs = stock_r - bench_r
    # +10% outperformance -> 100; -10% -> 0
    return _clip(50 + rs * 5)


def score_vol_adj_upside(close: pd.Series, cfg: Config) -> float:
    if len(close) < cfg.volatility_days + 1:
        return 50.0
    pct = close.pct_change(fill_method=None).dropna()
    if len(pct) < cfg.volatility_days:
        return 50.0
    ann_vol = float(pct.iloc[-cfg.volatility_days:].std() * np.sqrt(252) * 100)
    if ann_vol <= 0:
        return 50.0
    r = _ret_pct(close, cfg.momentum_lookback_days)
    # Sharpe-like: return / vol. 1.0 ratio -> 100.
    return _clip(50 + (r / ann_vol) * 50)


def score_breakout_quality(close: pd.Series, cfg: Config) -> float:
    if len(close) < cfg.breakout_lookback_days + 1:
        return 50.0
    window = close.iloc[-cfg.breakout_lookback_days:]
    last = float(close.iloc[-1])
    hi = float(window.max())
    pct_from_high = (last / hi - 1) * 100 if hi else -100
    return _clip(100 + pct_from_high * 20)


def score_sector_alignment(
    ticker: str,
    universe_history: Dict[str, pd.DataFrame],
    cfg: Config,
) -> float:
    """Score the stock's sector by averaging momentum across same-sector peers."""
    sector = cfg.sector_map.get(ticker)
    if sector is None:
        return 50.0
    peers = [t for t, s in cfg.sector_map.items() if s == sector and t in universe_history]
    if not peers:
        return 50.0
    sector_r = []
    for p in peers:
        df = universe_history[p]
        if "Close" in df.columns and len(df) > cfg.momentum_lookback_days:
            sector_r.append(_ret_pct(df["Close"], cfg.momentum_lookback_days))
    if not sector_r:
        return 50.0
    avg = sum(sector_r) / len(sector_r)
    # Sector up 5% -> 100; down 5% -> 0
    return _clip(50 + avg * 10)


def _analyst_revision_score(ticker: str) -> float:
    """Live analyst consensus (TESTED 2026-07-16: RELIANCE 96.1 / CGCL 25.0).
    Neutral 50 only when no honest number exists (backtest, no coverage,
    Yahoo-blocked box) — the fetcher logs the reason once."""
    try:
        from .signals.analysts import fetch_analyst_score
        a = fetch_analyst_score(ticker)
        return a.score if a is not None else 50.0
    except Exception:
        return 50.0


def compute_score(
    ticker: str,
    history: pd.DataFrame,
    benchmark: pd.DataFrame | None,
    universe_history: Dict[str, pd.DataFrame],
    cfg: Config,
) -> Tuple[float, Dict[str, float]]:
    """Return (total_score, per_component_scores) for one stock."""
    close = history["Close"]
    volume = history["Volume"]
    bench_close = benchmark["Close"] if benchmark is not None and not benchmark.empty else None

    # v4: catalyst wired to live news sentiment (Claude API), with graceful
    # fallback to neutral 50 when ANTHROPIC_API_KEY is missing or in backtest.
    try:
        from .signals.news import fetch_sentiment
        catalyst_score = fetch_sentiment(ticker).score
    except Exception:
        catalyst_score = 50.0

    components: Dict[str, float] = {
        "momentum_1m":       score_momentum_1m(close, cfg),
        "volume_expansion":  score_volume_expansion(volume, cfg, close),
        "earnings_growth":   score_earnings_growth(ticker),
        "catalyst":          catalyst_score,
        "relative_strength": score_relative_strength(close, bench_close, cfg),
        "vol_adj_upside":    score_vol_adj_upside(close, cfg),
        "short_interest":    50.0,   # stub (no free PCR source; NSE 403s cloud)
        "analyst_revision":  _analyst_revision_score(ticker),
        "breakout_quality":  score_breakout_quality(close, cfg),
        "sector_alignment":  score_sector_alignment(ticker, universe_history, cfg),
    }

    # v4: apply policy sector boost (PIB tracker) — capped at +30 per sector.
    # Sector_alignment gets the bump so the existing 10% weight carries it.
    try:
        from .signals.policy import active_sector_boosts
        sector = cfg.sector_map.get(ticker)
        if sector:
            boosts = active_sector_boosts()
            boost = boosts.get(sector, 0.0)
            if boost > 0:
                components["sector_alignment"] = min(
                    100.0, components["sector_alignment"] + boost,
                )
    except Exception:
        pass   # policy is bonus signal; never break scoring

    # v5: FII flow as a system-wide tide on catalyst score.
    # +10000 Cr 5d -> +20 boost; -10000 -> -20 dampen.
    try:
        from .signals.flows import fii_flow_score
        fii_score = _SCORING_CONTEXT.get("fii_score")
        if fii_score is not None:
            tide = (fii_score - 50.0) * 0.4   # ±20 swing
            components["catalyst"] = _clip(components["catalyst"] + tide)
    except Exception:
        pass

    # v5: sector rotation booster — top-3 sectors by 1mo momentum get +15
    try:
        sector = cfg.sector_map.get(ticker)
        hot = _SCORING_CONTEXT.get("hot_sectors") or set()
        if sector and sector in hot:
            components["sector_alignment"] = min(
                100.0, components["sector_alignment"] + 15.0,
            )
    except Exception:
        pass

    total = sum(components[k] * AM07_WEIGHTS[k] for k in AM07_WEIGHTS)
    return total, components


# Per-scan shared context. The engine fills this once at scan start and
# the per-stock scorer reads from it — avoids re-fetching FII or recomputing
# sector momentum 30 times per scan.
_SCORING_CONTEXT: dict = {}


def set_scoring_context(**kwargs) -> None:
    """Engine calls this once per scan with shared signals."""
    _SCORING_CONTEXT.clear()
    _SCORING_CONTEXT.update(kwargs)


def compute_hot_sectors(
    universe_history: Dict[str, pd.DataFrame],
    sector_map: Dict[str, str],
    cfg: Config,
    top_n: int = 3,
) -> set[str]:
    """Returns the set of top_n sectors by average 1-month return.

    Used to give the sector_alignment component an extra +15 boost when the
    stock is in a hot sector (sector rotation).
    """
    sector_returns: Dict[str, list] = {}
    for ticker, hist in universe_history.items():
        sector = sector_map.get(ticker)
        if not sector or "Close" not in hist.columns:
            continue
        if len(hist) <= cfg.momentum_lookback_days:
            continue
        r = _ret_pct(hist["Close"], cfg.momentum_lookback_days)
        sector_returns.setdefault(sector, []).append(r)
    if not sector_returns:
        return set()
    sector_avg = {s: sum(rs) / len(rs) for s, rs in sector_returns.items()}
    top = sorted(sector_avg.items(), key=lambda x: -x[1])[:top_n]
    return {s for s, _ in top}


def reasoning_text(components: Dict[str, float], total: float) -> str:
    """One-line explainability for a pick.

    AUDIT FIX B9: components sitting exactly at neutral 50 are excluded from
    "Top drivers" — a factor whose feed fell back to its neutral default
    (catalyst without a key, analyst unfetchable) must not be presented as a
    reason the stock was picked. A neutral factor drives nothing anyway."""
    top = sorted(
        [(k, v * AM07_WEIGHTS[k]) for k, v in components.items()
         if k not in STUB_COMPONENTS and v != 50.0],
        key=lambda x: -x[1],
    )[:3]
    drivers = ", ".join(f"{k}={components[k]:.0f}" for k, _ in top)
    if not drivers:
        return f"Score {total:.1f}/100. All factors at/near neutral."
    return f"Score {total:.1f}/100. Top drivers: {drivers}."
