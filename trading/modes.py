"""Trade-horizon mode presets.

Same AM 07 scoring engine, four different "personalities" via weight +
exit + timing presets:

  intraday   — 5-min bars, VWAP/ORB-driven, square-off by 3:25 PM IST
  short      — 1-7 day holds, momentum + breakout heavy
  swing      — 1-8 week holds, the default that's running today
  positional — 1-6 month holds, fundamentals weighted higher

Each preset overrides AM07_WEIGHTS and exit thresholds. The engine reads
`cfg.mode` and applies the preset at scoring time.
"""

from __future__ import annotations

from dataclasses import dataclass


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
    partial_profit_pct: float
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
        partial_profit_pct=0.012,
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
        partial_profit_pct=0.04,
        trailing_stop_pct=-0.04,
        score_decay_exit=45.0,
        max_hold_days=7,
        min_score_to_enter=70.0,
        breakout_threshold_pct=0.015,
        volume_confirm_ratio=1.3,
        square_off_time=None,
        description=(
            "1-7 day holds. Daily bars, tighter exits than swing. "
            "Captures momentum bursts that don't survive past a week."
        ),
    ),
    "swing": ModePreset(
        name="swing",
        bar_interval="1d",
        history_period="6mo",
        stop_loss_pct=-0.05,
        take_profit_pct=0.10,
        partial_profit_pct=0.06,
        trailing_stop_pct=-0.08,
        score_decay_exit=45.0,        # loosened from 50 to match lower entry bar
        max_hold_days=60,
        min_score_to_enter=60.0,      # was 70 — too strict, gave only 6 trades / 2yr
        breakout_threshold_pct=0.03,  # was 0.02 — allow 3% off the 20-day high
        volume_confirm_ratio=1.2,     # was 1.3 — slightly looser volume confirm
        square_off_time=None,
        description="Default. 1-8 week holds. Daily bars. Tuned for more entries (target 30-60 trades/yr).",
    ),
    "positional": ModePreset(
        name="positional",
        bar_interval="1wk",
        history_period="2y",
        stop_loss_pct=-0.10,
        take_profit_pct=0.25,
        partial_profit_pct=0.15,
        trailing_stop_pct=-0.12,
        score_decay_exit=45.0,
        max_hold_days=180,
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
        take_profit_pct=0.12,        # user decision 2026-07-21: "+30 too much,
                                     # keep 12% for now" (untested vs +30 — the
                                     # 60-day window is the judge; trail -15
                                     # remains the workhorse exit)
        partial_profit_pct=0.07,     # scaled below the new target (was 0.18
                                     # under +30 — would never fire above +12)
        trailing_stop_pct=-0.15,
        score_decay_exit=40.0,
        max_hold_days=200,           # semi-annual rebalance horizon
        min_score_to_enter=55.0,     # P0/B3: 50 = "no edge" — require real positive
                                     # momentum; 50 positions is a cap, not a target
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
