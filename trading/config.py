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


def _owner_universe_default() -> tuple:
    """The owner's 50-name custom universe (order 2026-08-20). Lazy import
    for the same circular-import reason as _nifty_200_default; empty tuple
    (= rotating full-NSE bucket) if the constant is ever unimportable."""
    try:
        from .universes import OWNER_UNIVERSE_2026_08_20
        return OWNER_UNIVERSE_2026_08_20
    except Exception:
        return ()


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
    "short_interest":    0.05,   # LIVE 2026-08-23 (Node 7): futures-OI proxy; 50 for non-F&O/stale
    "analyst_revision":  0.05,   # LIVE 2026-07-16: yfinance consensus (50 when unfetchable)
    "breakout_quality":  0.05,
    "sector_alignment":  0.10,
}

# v4: catalyst now live via Claude API news sentiment. analyst_revision went
# LIVE 2026-07-16 (yfinance consensus). short_interest went LIVE 2026-08-23
# (Node 7): a LABELLED PROXY from the committed futures-OI panel — true
# borrowed-share short interest has no free NSE source; non-F&O names and a
# stale panel read the old neutral 50. No component is a constant any more.
STUB_COMPONENTS: set = set()


@dataclass
class Config:
    # --- Capital & sizing ---
    # Target: 60-70% weekly deployment across all 4 modes.
    # 12 positions * 5-7% avg = 60-84% gross — comfortably in band.
    # Owner order 2026-07-30 ("change the capital to 50 lack, 80 stocks,
    # roughly 1-1.25 for each trade" — then corrected: "1.25 lac, 125000,
    # not percentage"): Rs 50L paper capital, FIXED Rs 1.25L per new trade,
    # 80 positions as the ceiling. At 50L the cash funds ~40 concurrent
    # 1.25L trades; the remaining slots open as capital grows. Supersedes
    # the 25L/30/3.3% book of 2026-07-19.
    starting_capital: float = 50_00_000.0
    cash_reserve_pct: float = 0.0             # deploy 100% — no idle cash reserve (user setting)
    # Fixed rupee size per new trade (owner's number). 0 = fall back to
    # max_position_pct sizing. All sizing sites go through
    # position_notional() so the two knobs can never disagree.
    position_size_value: float = 125_000.0
    max_position_pct: float = 0.025           # fallback: 2.5% of 50L = 1.25L
    max_positions: int = 80
    # Sizing. "fixed" = the owner's own number, position_size_value above.
    #
    # REVERTED to fixed 2026-08-04 (owner: "still u r making 30000 positions").
    # ½-Kelly was live for four days; at scores just above the 62 entry floor it
    # sizes Rs 20k-60k (measured: 63 -> Rs 20,719 · 65 -> Rs 62,158), so the
    # book was opening Rs 30k trades when the owner had ordered Rs 1.25 lakh
    # each. Kelly was adopted from a question I asked, not from an instruction
    # they wrote; their number wins. "half_kelly" is still selectable in
    # Settings, and the maths stays in trading/sizing.py and the /lens page.
    sizing_mode: str = "fixed"                # "fixed" | "half_kelly"
    kelly_fraction: float = 0.5               # NEVER above 1.0 — see the doc
    kelly_ic: float = 0.05                    # ASSUMED information coefficient
    kelly_max_pct: float = 0.05               # hard ceiling: 5% of equity/name
    # P1: momentum mode scans the FULL NSE universe via rotating buckets
    # (engine.scan); set False to fall back to the Nifty-200-only scan.
    full_universe_scan: bool = True
    # OWNER ORDER 2026-08-20 ("reduce the universe to 50 share only but all
    # fresh ... rest remains same"): when non-empty, the momentum scan reads
    # EXACTLY this list every day (held names always included, blocklist
    # still applies, every gate/score/exit unchanged) instead of the
    # rotating full-NSE bucket. Editable in Settings; the owner refreshes it
    # weekly. Empty tuple = back to the rotating bucket.
    custom_universe: tuple = field(
        default_factory=lambda: _owner_universe_default())
    # User instruction 2026-07-21: names the engine must NEVER scan or buy,
    # even though they are currently listed (distress/junk the owner bans).
    # Held positions are exempt so exits still work if one is ever held.
    ticker_blocklist: tuple = ("YESBANK.NS",)
    # v4 correction playbook (user-approved 2026-07-21): momentum entries
    # re-enable inside a correction once India VIX crushes to <=75% of its
    # recent spike peak — the only correction tactic that beat cash in the
    # 10-episode preregistered test (docs/PARAM_SEARCH_V4.md).
    correction_vix_reentry: bool = True
    # Ocean (mean-reversion / knife-catch engine, trading/engines/ocean.py).
    # Docs have described it as PARKED since the v3 review (-41% drawdown
    # catching 2020's falling knives), but until 2026-07-27 there was no
    # switch in the code: engine.scan() ran it on nothing more than
    # regime.ocean_active, which regime.classify() sets True for correction,
    # panic AND neutral. In a correction Everest is off, so Ocean was the
    # only engine able to open a position — and its picks skip the score
    # floor and the absolute-momentum gate. This flag is that missing park
    # switch. Leave False until the crash guard is designed and tested.
    ocean_enabled: bool = False
    # OWNER ORDER 2026-08-09 ("i want to take risk on 200 DMA" -> picked
    # "Brake fully OFF" after being shown the v4 evidence: blind correction
    # dip-buying tested -2.2pp across 10 episodes). False = the CORRECTION
    # regime (Nifty < 200DMA) no longer blocks new momentum entries; the
    # engine buys normally below the 200DMA. Everything else stands: score
    # floor, absolute-momentum gate, breadth gate, -5% stop, kill switch,
    # regime reserve ladder — and PANIC (crash -10% + VIX>25) still blocks.
    # True restores the old brake (Settings toggle). UNTESTED live at False
    # — the 60-day window judges.
    correction_brake_enabled: bool = False
    # OWNER ORDER 2026-08-11 ("danger layer, go with c then auto with B"):
    # the 10 strict-calibration-VERIFIED danger triggers protect HELD
    # positions. First 7 days = alerts only (mode C); then automatic
    # per-stock defence (mode B): 1 fire = tighten stop to -2.5% off price,
    # 2+ fires = exit half. Sell side ONLY - never entries (F-006).
    danger_layer_enabled: bool = True
    # A3 breadth gate (owner: "wire it", 2026-07-27). New momentum entries
    # only when >50% of the scan panel's tickers close above their own
    # 200DMA. Preregistered evidence: docs/breadth_gate_results.json —
    # +17.7 vs +13.4 (2022-23), +7.5 vs +5.6 with DD -18.8 -> -15.6
    # (2024-26); costs a little in strong trends (2020-21 +37.0 vs +40.8).
    # Fail-open: too few tickers with 200d history = no block (journaled).
    # Momentum mode only — the other modes' history windows are too short
    # for a 200DMA and the study only covered momentum.
    breadth_gate_enabled: bool = True
    breadth_min_pct: float = 0.50
    breadth_min_sample: int = 30
    # F3 fix (2026-08-20): the gate PREFERS the full-store breadth reading
    # (~2,700 names, ~±1pp noise) over the scan bucket (~50 names, ±7pp)
    # while the stored reading's closes are at most this many days old.
    # Past that it falls back to the bucket sample — stale-wide loses to
    # fresh-narrow once the gap grows. ASSUMED number: the weekly top-up
    # plus weekend is 7-9 days in the worst normal case.
    breadth_store_max_age_days: int = 10

    # --- Per-trade risk ---
    stop_loss_pct: float = -0.05              # -5% hard stop
    take_profit_pct: float = 0.10             # +10% -> R:R 1:2 from -5% stop
    trailing_stop_pct: float = -0.08          # -8% from peak (chandelier-style)
    score_decay_exit: float = 50.0            # exit if AM 07 score drops below this
    max_hold_days: int = 60                   # safety cap
    # Buy-and-hold switch (user setting): when True the engine never sells —
    # no stop-loss / target / trailing / score-decay / max-hold / kill-switch exit.
    # Policy C (user-approved 2026-07-16): exits ON with per-mode stops/targets.
    # Buy-and-hold (True) was the P0/B1 experiment; score-decay stayed live then.
    disable_exits: bool = False

    # --- Portfolio risk ---
    daily_loss_halt_pct: float = -0.02        # -2% of equity -> kill switch
    max_sector_positions: int = 12            # loose — allow a broad 50-stock book across sectors
    # v7 (absorbed from G07 research framework): cap total open risk-at-stop.
    # Sum of (entry - stop) * qty across open positions must stay under this
    # fraction of equity. Prevents many small "safe" positions from summing
    # into one large hidden risk.
    max_portfolio_heat_pct: float = 0.25      # 25% of equity at stop, max
    # P2 audit fixes: sector capital cap + stale-data guard
    max_sector_exposure_pct: float = 0.25     # max 25% of capital in one sector
    max_stale_days: int = 7                   # skip buys when last candle older than this

    # --- Strategy ---
    # C1: blend a value/quality tilt into the momentum score (0 = pure momentum,
    # 1 = pure quality). Diversifies off high-beta leaders that crash together.
    quality_blend_pct: float = 0.35
    # Batch C: candle BUY-CONFIRMATION gate — a scored pick executes only when
    # the latest candle confirms (breakout / URR retest / engulfing); otherwise
    # it waits on the watchlist as candle_wait_*.
    candle_confirm_enabled: bool = True
    # user's reference-pic set + the 6 strong reversals from the owner's
    # 2026-07-28 chart ("i want more"). Only completed multi-bar reversals
    # confirm; single-bar shapes stay display-only lanes.
    candle_patterns: tuple = ("breakout", "urr", "engulfing", "hammer",
                              "morning_star", "piercing", "tweezer_bottom",
                              "three_soldiers", "three_inside_up",
                              "three_outside_up", "three_line_strike",
                              "abandoned_baby_bull", "kicker_bull",
                              "morning_doji_star")
    candle_breakout_lookback: int = 20
    candle_volume_mult: float = 1.5
    # F7 (user-approved 2026-07-11): never open dust positions — a position
    # whose notional is below this is skipped (IDEA-12-shares problem).
    min_position_value: float = 25000.0
    # F15 (Chityy pyramid maps): add to a WINNING position in tranches.
    # Default OFF — opt-in from Settings so it can't surprise the live book;
    # add pyramid_add_pct of the ORIGINAL notional each time the position is
    # up >= pyramid_trigger_pct * (adds_done+1) from entry, up to max adds.
    pyramid_enabled: bool = False
    pyramid_trigger_pct: float = 0.08      # +8% per tranche step
    pyramid_add_pct: float = 0.5           # add 50% of the original size
    max_pyramid_adds: int = 2
    # WARNING — this default is DEAD for every real mode. apply_mode_preset()
    # runs at every boot and overwrites it from trading/modes.py PRESETS, so the
    # LIVE floor is the mode's: momentum 62 (owner order 2026-07-30 "it will buy
    # above 62"), intraday 72, short 70, swing 60, positional 68. Reading this
    # line as "the engine buys at 70+" is wrong, and cost a doc error on
    # 2026-07-30. Change the PRESET, not this.
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

    # AUDIT FIX A7 (2026-08-09): per-mode owner overrides for the 8 fields
    # apply_mode_preset() writes — {mode: {field: value}}. Populated by the
    # Settings save when the owner submits a value different from the mode's
    # preset; persisted in the Mongo settings doc; re-applied at the end of
    # every apply_mode_preset() so a Settings edit finally sticks.
    preset_overrides: dict = field(default_factory=dict)

    def trail_is_reachable(self) -> tuple[bool, float]:
        """(is the trailing stop practically live?, peak % it would need).

        Surfaced 2026-07-31 because the app advertised a trailing stop that,
        at the then-live -5%/+12% bracket, COULD NOT fire (the peak had to
        clear +11.76% but the +12% target closed first — a 0.24pp window).
        Node 12 (owner order 2026-08-24) moved the bracket to -5%/+20%,
        where the -15% trail arms above +17.65% and is genuinely live; the
        Settings banner now only appears if a future edit deadens it
        again. This method just reports; it never changes an exit."""
        stop = float(self.stop_loss_pct)
        trail = float(self.trailing_stop_pct)
        target = float(self.take_profit_pct)
        if trail >= 0 or trail <= -1:
            return False, 0.0
        need_peak = (1.0 + stop) / (1.0 + trail) - 1.0
        # The window is [need_peak, target): below need_peak the trail sits
        # under the hard stop (so the stop fires first); at target the trade
        # closes. A window under 1 percentage point is dead in practice.
        window = target - need_peak
        return (window > 0.01), need_peak

    def position_notional(self, base: float | None = None) -> float:
        """Per-trade rupee target (owner order 2026-07-30: '1.25 lac,
        125000, not percentage'). A positive position_size_value wins;
        0 falls back to max_position_pct of `base` (default: starting
        capital). Every sizing site uses this one function."""
        v = float(getattr(self, "position_size_value", 0.0) or 0.0)
        if v > 0:
            return v
        b = float(base) if base is not None else float(self.starting_capital)
        return b * float(self.max_position_pct)

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
        # AUDIT FIX A7 (2026-08-09): the owner's Settings edits to these
        # preset-owned fields used to be a silent no-op — saved to Mongo,
        # then overwritten right here on the very next preset application,
        # while the page said "Saved." Owner edits are now stored PER MODE
        # in preset_overrides and re-applied after the preset, so an edit
        # sticks for the mode it was made in, survives restarts and the
        # daily mode rotation, and a mode's own preset still governs every
        # field the owner never touched.
        for k, v in dict((self.preset_overrides or {}).get(self.mode) or {}).items():
            if hasattr(self, k):
                try:
                    setattr(self, k, v)
                except Exception:
                    pass
