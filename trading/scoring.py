"""AM 07 scoring — full 10-component score for daily NSE swing.

Weights match what the user specified:
  momentum_1m 0.20, volume_expansion 0.15, earnings_growth 0.10,
  catalyst 0.10, relative_strength 0.10, vol_adj_upside 0.10,
  short_interest 0.05, analyst_revision 0.05, breakout_quality 0.05,
  sector_alignment 0.10

All ten components now compute (the last stub fell 2026-08-23, Node 7):
catalyst = live news sentiment (neutral 50 without the API key),
analyst_revision = consensus rating, earnings_growth = year-on-year net
income — both read the app's STORED files first since 2026-10-22 (owner card
D2; trading/stored_feeds.py) and Yahoo only second, because Yahoo blocks the
Render box and both legs sat at 50 for every name there; short_interest =
a LABELLED PROXY from the committed futures-OI panel (short build-up low /
covering high; non-F&O names and a stale panel read neutral 50). Weights
and engine code were untouched at every unstub — only the per-component
compute functions changed, as designed.
"""

from __future__ import annotations

from typing import Optional, Dict, Tuple

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


class _Sourced(float):
    """A leg's score that remembers where its number came from. Behaves as a
    plain float everywhere; compute_score reads `.source` for the reasoning
    text. A plain float (e.g. a test stub) simply has no source."""
    source: Optional[str] = None

    def __new__(cls, value: float, source: Optional[str] = None):
        obj = super().__new__(cls, value)
        obj.source = source
        return obj


def _growth_band(g: float) -> float:
    """THE earnings-growth band, one function for every source:
    +20% YoY -> 100, -20% YoY -> 0, 0% -> 50."""
    return _clip(50 + g * 2.5)


def score_earnings_growth(ticker: str) -> float:
    """OWNER CARD 2026-10-22 (D2, "Feed the two legs from stored data"):
    stored statements first (trading/stored_feeds — committed fundamentals
    file + the monthly Mongo refresh), Yahoo second (blocked on the Render
    box, so on the live app this leg was 50 for every name), neutral 50 last.
    Same band for both sources, so the score scale is unchanged."""
    try:
        from .stored_feeds import earnings_growth as _stored_growth
        st = _stored_growth(ticker)
    except Exception:                                       # noqa: BLE001
        st = None
    if st is not None:
        return _Sourced(_growth_band(st["growth_pct"]), st["source"])
    g = fetch_earnings_growth(ticker)
    if g is None:
        return 50.0  # neutral when data missing
    return _Sourced(_growth_band(g), "Yahoo, latest quarter vs a year ago")


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
    # AUDIT F-16 (2026-10-02): NOT a Sharpe ratio, and it was mislabelled as
    # one. It is the last ~1-month return (%) divided by the ANNUAL
    # volatility (%), so it reads about sqrt(12) smaller than a true monthly
    # Sharpe. Relabelled, not rescaled: the number is a consistent ranking of
    # names within one scan, and annualising it would move every swing /
    # short / positional score the owner's presets were set against.
    # Ratio 1.0 (a month's gain equal to a year's vol) -> 100.
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


def _short_interest_score(ticker: str, close) -> float:
    """Node 7: futures-OI short-interest proxy; 50.0 on any failure — the
    exact value the retired stub always returned."""
    try:
        from .signals.futures_oi import score_short_interest
        return score_short_interest(ticker, close)
    except Exception:
        return 50.0


def _analyst_revision_score(ticker: str) -> float:
    """Analyst consensus (TESTED 2026-07-16: RELIANCE 96.1 / CGCL 25.0).

    OWNER CARD 2026-10-22 (D2): the committed ratings file first — its
    mean_rating column is the same Yahoo recommendationMean the live call
    reads, mapped by the same function (signals.analysts.score_from_mean) —
    then the live Yahoo call, then neutral 50. Neutral only when no honest
    number exists (backtest, no coverage, Yahoo-blocked box)."""
    try:
        from .signals.analysts import score_from_mean
        from .stored_feeds import analyst_reading
        st = analyst_reading(ticker)
        if st is not None:
            return _Sourced(score_from_mean(st["mean"]), st["source"])
    except Exception:                                       # noqa: BLE001
        pass
    try:
        from .signals.analysts import fetch_analyst_score
        a = fetch_analyst_score(ticker)
        if a is None:
            return 50.0
        return _Sourced(a.score, f"Yahoo, {a.analysts} analysts")
    except Exception:
        return 50.0


class ScoreComponents(dict):
    """The per-component scores (a plain dict to every reader) plus
    `.sources`: where the earnings-growth and analyst legs got their number,
    so the reasoning text can name it (owner card 2026-10-22: "earnings growth
    72 (fundamentals store FY26 vs FY25)")."""
    sources: Dict[str, str]

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.sources = {}


# OWNER ORDER 2026-10-04 ("use each category's weights"). modes.WEIGHTS has
# carried a weight preset per category since the modes were built, and the
# module docstring said "the engine reads cfg.mode and applies the preset at
# scoring time" — but nothing ever did: every category scored with the global
# AM07_WEIGHTS (which equal the swing preset), so intraday, short and
# positional differed only by their pass marks. Swing is unchanged by this;
# the other three now score on their own weights. Momentum does not use the
# composite to buy (it ranks on the momentum+quality blend), so it and any
# unknown mode keep the global weights.
COMPOSITE_MODES = ("intraday", "short", "swing", "positional")


def weights_for(mode: Optional[str]) -> Dict[str, float]:
    """The composite weights a category scores with."""
    if mode in COMPOSITE_MODES:
        from .modes import WEIGHTS
        return WEIGHTS[mode]
    return AM07_WEIGHTS


def compute_score(
    ticker: str,
    history: pd.DataFrame,
    benchmark: pd.DataFrame | None,
    universe_history: Dict[str, pd.DataFrame],
    cfg: Config,
    mode: Optional[str] = None,
) -> Tuple[float, Dict[str, float]]:
    """Return (total_score, per_component_scores) for one stock, weighted
    by `mode`'s preset (default: cfg.mode)."""
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

    earnings = score_earnings_growth(ticker)
    analyst = _analyst_revision_score(ticker)
    components = ScoreComponents({
        "momentum_1m":       score_momentum_1m(close, cfg),
        "volume_expansion":  score_volume_expansion(volume, cfg, close),
        "earnings_growth":   float(earnings),
        "catalyst":          catalyst_score,
        "relative_strength": score_relative_strength(close, bench_close, cfg),
        "vol_adj_upside":    score_vol_adj_upside(close, cfg),
        # Node 7 (2026-08-23): was a CONSTANT 50 for months. Now a labelled
        # PROXY from the committed futures-OI panel — short build-up (OI up,
        # price down) scores low, covering scores high; non-F&O names and a
        # stale panel return the old neutral 50, so the failure mode of this
        # fix is exactly the world before it.
        "short_interest":    _short_interest_score(ticker, close),
        "analyst_revision":  float(analyst),
        "breakout_quality":  score_breakout_quality(close, cfg),
        "sector_alignment":  score_sector_alignment(ticker, universe_history, cfg),
    })
    for key, leg in (("earnings_growth", earnings),
                     ("analyst_revision", analyst)):
        src = getattr(leg, "source", None)
        if src:
            components.sources[key] = src

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
    # fii_flow_score runs 10..90, so the tide is +-16 at the +-5,000 Cr ends
    # (the old "+-20 at +-10,000 Cr" note was never reachable).
    try:
        from .signals.flows import fii_flow_score
        fii_score = _SCORING_CONTEXT.get("fii_score")
        if fii_score is not None:
            tide = (fii_score - 50.0) * 0.4   # ±16 swing
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

    weights = weights_for(mode if mode is not None else getattr(cfg, "mode", None))
    total = sum(components[k] * weights[k] for k in weights)
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


def reasoning_text(components: Dict[str, float], total: float,
                   weights: Optional[Dict[str, float]] = None) -> str:
    """One-line explainability for a pick.

    AUDIT FIX B9: components sitting exactly at neutral 50 are excluded from
    "Top drivers" — a factor whose feed fell back to its neutral default
    (catalyst without a key, analyst unfetchable) must not be presented as a
    reason the stock was picked. A neutral factor drives nothing anyway."""
    w = weights or AM07_WEIGHTS
    top = sorted(
        [(k, v * w.get(k, 0.0)) for k, v in components.items()
         if k not in STUB_COMPONENTS and v != 50.0],
        key=lambda x: -x[1],
    )[:3]
    drivers = ", ".join(f"{k}={components[k]:.0f}" for k, _ in top)
    if not drivers:
        text = f"Score {total:.1f}/100. All factors at/near neutral."
    else:
        text = f"Score {total:.1f}/100. Top drivers: {drivers}."
    # Owner card 2026-10-22 (D2): name where the two fundamental legs got
    # their number — only for legs this category actually weighs.
    sources = getattr(components, "sources", None) or {}
    notes = []
    for key, label in (("earnings_growth", "earnings growth"),
                       ("analyst_revision", "analyst rating")):
        if sources.get(key) and w.get(key, 0.0) > 0 and key in components:
            notes.append(f"{label} {components[key]:.0f} ({sources[key]})")
    if notes:
        text += " Data: " + "; ".join(notes) + "."
    return text


# The trade category a position was BOUGHT under decides which ruler judges it
# later. A blank position mode is a legacy position from before modes were
# recorded; exits.retag_positions_per_mode treats those as momentum, and the
# engine's exit pass passes them in as momentum. This function itself treats
# only an exact match as momentum, so the backtest's behaviour is unchanged.
MOMENTUM_MODE = "nifty200_momentum"


def score_for_mode(mode: str, ticker: str, hist: pd.DataFrame,
                   bench, history: Dict, cfg: Config,
                   missing=0.0) -> Tuple[float, Dict]:
    """Score a name with the SAME ruler its trade category buys on.

    nifty200_momentum -> momentum_score blended with quality (the entry
    score the 09:20 scan ranks on); every other category -> the AM07
    composite. Shared by the backtest and by the live exit pass.

    OWNER ORDER 2026-10-02 ("fix 1 to 3"): the live exit pass used to judge
    EVERY position on the composite, so a momentum position bought at a
    blend of 62+ was then sold for score decay when a different number
    fell below 40. Measured on the committed 191-name panel: 26.5% of the
    2,981 stock-dates good enough to BUY already read composite < 40. The
    backtest — and therefore every exit study, Node 12 included — has
    always re-scored momentum positions on the blend; live now matches it.
    `missing` is returned when momentum cannot be computed (under ~1 year of
    bars). The backtest keeps its historical 0.0; the live exit pass passes
    None so a short history can never read as a collapsed score and sell.
    Returns (score, components)."""
    if mode == MOMENTUM_MODE:
        try:
            from .strategies.nifty200_momentum import momentum_score
            from .quality import quality_score, blend_scores
        except ImportError:
            return compute_score(ticker, hist, bench, history, cfg, mode=mode)
        s = momentum_score(hist["Close"])
        if s is None:
            return missing, {MOMENTUM_MODE: missing}
        q = quality_score(hist["Close"], hist.get("Volume"))
        blended = blend_scores(float(s), q, getattr(cfg, "quality_blend_pct", 0.0))
        return blended, {"momentum": float(s), "quality": q}
    return compute_score(ticker, hist, bench, history, cfg, mode=mode)
