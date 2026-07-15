"""Defaults for the live paper-trading engine.

Rules locked in with the user:
  - 20% cash reserve at all times (max 80% deployed)
  - Max 10% capital per single position
  - Risk:Reward minimum 1:2
  - Mandatory stop-loss + trailing stop
  - Daily loss limit -> kill switch
  - AM 07 scoring with the exact 10-component weights
"""

from dataclasses import dataclass, field
from typing import Tuple


def _nifty_200_default() -> Tuple[list, dict]:
    """Default universe + sector map = Nifty 200. Imported here to avoid
    a circular import at module load if universes.py grows engine deps."""
    try:
        from .universes import get_nifty_200_universe
        return get_nifty_200_universe()
    except Exception:
        # Hard fallback to the legacy 30-stock list so Config never crashes.
        legacy = [
            "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
            "ITC.NS", "SBIN.NS", "BHARTIARTL.NS", "KOTAKBANK.NS", "LT.NS",
        ]
        return legacy, {}


AM07_WEIGHTS = {
    "momentum_1m":       0.20,
    "volume_expansion":  0.15,
    "earnings_growth":   0.10,
    "catalyst":          0.10,   # stubbed at 50 until news API wired
    "relative_strength": 0.10,
    "vol_adj_upside":    0.10,
    "short_interest":    0.05,   # stubbed at 50 (no free NSE source)
    "analyst_revision":  0.05,   # stubbed at 50 (paid data)
    "breakout_quality":  0.05,
    "sector_alignment":  0.10,
}

# v4: catalyst now live via Claude API news sentiment.
# short_interest and analyst_revision remain stubbed (no clean free source).
STUB_COMPONENTS = {"short_interest", "analyst_revision"}


@dataclass
class Config:
    # --- Capital & sizing ---
    # Target: 60-70% weekly deployment across all 4 modes.
    # 12 positions * 5-7% avg = 60-84% gross — comfortably in band.
    starting_capital: float = 25_00_000.0
    cash_reserve_pct: float = 0.25            # 25% reserve (regime-adjusted at scan)
    max_position_pct: float = 0.07            # max 7% per single stock (was 10%)
    max_positions: int = 12                   # was 10; allows 4 modes x ~3 fills

    # --- Per-trade risk ---
    stop_loss_pct: float = -0.05              # -5% hard stop
    take_profit_pct: float = 0.10             # +10% -> R:R 1:2 from -5% stop
    trailing_stop_pct: float = -0.08          # -8% from peak (chandelier-style)
    score_decay_exit: float = 50.0            # exit if AM 07 score drops below this
    max_hold_days: int = 60                   # safety cap

    # --- Portfolio risk ---
    daily_loss_halt_pct: float = -0.02        # -2% of equity -> kill switch
    max_sector_positions: int = 3

    # --- Strategy ---
    min_score_to_enter: float = 70.0
    momentum_lookback_days: int = 21          # 1 month
    medium_lookback_days: int = 63            # 3 months
    breakout_lookback_days: int = 20
    volume_short_days: int = 5
    volume_long_days: int = 20
    volatility_days: int = 21

    # --- Entry overlay ---
    breakout_threshold_pct: float = 0.02      # within 2% of 20-day high
    volume_confirm_ratio: float = 1.3         # 5d vol >= 1.3x 20d vol

    # --- Costs (Upstox CNC equity, approximate) ---
    brokerage_per_order: float = 0.0          # CNC = zero brokerage on Upstox/Zerodha
    stt_buy_pct: float = 0.001                # 0.1% STT delivery buy
    stt_sell_pct: float = 0.001               # 0.1% STT delivery sell
    exchange_pct: float = 0.0000345
    sebi_per_cr: float = 10.0
    stamp_buy_pct: float = 0.00015            # 0.015% delivery
    gst_pct: float = 0.18
    dp_charges: float = 15.93                 # per scrip per day sold (delivery)
    slippage_bps: float = 5.0                 # 5 bps per side

    # --- Universe (Nifty 200) ---
    universe: list = field(default_factory=lambda: _nifty_200_default()[0])
    benchmark: str = "^NSEI"
    sector_map: dict = field(default_factory=lambda: _nifty_200_default()[1])

    # --- Sector blacklist (empty by default) ---
    # No sectors blacklisted. The user runs this as a real app, not a
    # filtered toy. Re-enable specific sectors here only with strong evidence.
    blacklisted_sectors: list = field(default_factory=list)

    # --- Nifty trend filter (v4, disabled by default) ---
    # Setting to ADX>=20 was too aggressive in backtest — killed winning
    # entries in mildly-trending Nifty regimes. Default 0 (disabled).
    # Enable from Settings if your testing shows it helps your window.
    nifty_min_adx: float = 0.0

    # --- Persistence ---
    state_dir: str = "/tmp/sourceborn_paper"

    # --- Mode (v2 — trade horizon) ---
    # "intraday" | "short" | "swing" | "positional" | "nifty200_momentum"
    # Default: NSE Nifty 200 Momentum 30 methodology (published 5y CAGR ~29.7%).
    mode: str = "nifty200_momentum"

    def apply_mode_preset(self) -> None:
        """Mutate this config in place using the active mode preset.

        Safe to call multiple times — idempotent because each call writes
        the same values. Routes call this after the user changes mode in
        Settings.
        """
        try:
            from .modes import get_preset
        except ImportError:
            return
        p = get_preset(self.mode)
        self.stop_loss_pct = p.stop_loss_pct
        self.take_profit_pct = p.take_profit_pct
        self.trailing_stop_pct = p.trailing_stop_pct
        self.score_decay_exit = p.score_decay_exit
        self.max_hold_days = p.max_hold_days
        self.min_score_to_enter = p.min_score_to_enter
        self.breakout_threshold_pct = p.breakout_threshold_pct
        self.volume_confirm_ratio = p.volume_confirm_ratio
