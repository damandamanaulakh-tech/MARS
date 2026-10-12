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
    # 1.25L trades; the remaining slots open as capital grows.
    # OWNER ORDER 2026-09-10: "i want to raise our app capital to 5 Cr".
    # Was Rs 50,00,000. Boot migration moves cash by the delta and
    # journals CAPITAL_ADJUST — see Portfolio.sync_capital.
    starting_capital: float = 5_00_00_000.0
    cash_reserve_pct: float = 0.0             # deploy 100% — no idle cash reserve (user setting)
    # ------------------------------------------------------------------
    # ENTRY VALUE POLICY — the whole of it, in one place.
    #
    # Written out 2026-08-30 (owner: "fix f-007") because the three numbers
    # live in three different fields and an external reviewer read them as a
    # contradiction: the owner's order was "roughly 1-1.25 for each trade",
    # yet the floor sits at Rs 25,000. They are not in conflict — they are
    # separate owner decisions that had never been stated together:
    #
    #   Everest (the momentum engine, every normal entry)
    #       Rs 1,25,000 fixed          position_size_value, below
    #       owner order 2026-07-30, corrected same day to
    #       "1.25 lac, 125000, not percentage"
    #
    #   Ocean (the dip engine)
    #       Rs 1,68,750                ocean_position_value
    #       owner order 2026-08-30: "when ocean activate it will use more
    #       in of 35%" — Everest's ticket plus 35%. This REPLACES the
    #       half-size Rs 62,500 of the 2026-08-26 approval, and it makes an
    #       Ocean trade LARGER than an Everest one. Told to the owner when
    #       they ordered it: half size was part of the risk story the crash
    #       guard was approved with; the four rails are unchanged and still
    #       gate every Ocean entry.
    #
    #   Floor, both engines
    #       Rs 25,000                  min_position_value
    #       user-approved 2026-07-11; a trade smaller than this is SKIPPED,
    #       not shrunk. It is a dust guard, never a target size.
    #       RAISED 2026-10-04 to Rs 1,50,000 (owner: "leave the 1.5 minimum
    #       value no 25 k is needed") — the slab is now the floor too.
    #
    # SUPERSEDED 2026-09-10 by the SLAB MODEL below. The three numbers above
    # are kept as the history of how the book was sized to that date.
    #
    # ---- THE SLAB MODEL (owner order 2026-09-10) ---------------------
    # Their words: "no flat size to follow / so we can put 150000 slab as
    # minimum and rest on the score basis and your arithmetic".
    #
    #   Rs 1,50,000     position_slab_value — the MINIMUM a trade opens at.
    #                   Not a target and not a fixed size: it is the floor
    #                   under a score-driven number.
    #   score-driven    above the slab the size is Grinold/Kelly on the LIVE
    #                   entry floor (see sizing.py), so a name sitting exactly
    #                   at the floor carries zero estimated edge and is
    #                   SKIPPED rather than opened at the slab.
    #   m = 0.29        kelly_fraction — owner's pick, "0.29 (prime number)".
    #                   Measured: keeps 49.6% of maximum growth for a 1.68%
    #                   risk of ever halving the book, and leaves a 6.9x
    #                   error budget before the bet reaches 2x-Kelly, where
    #                   growth is zero and ruin certain. (0.25 -> 43.8%/0.78%
    #                   /8.0x; 0.50 -> 75%/12.5%/4.0x.)
    #   Rs 25,00,000    kelly_max_pct 5% of a Rs 5 Cr book — the hard ceiling.
    #
    # Ocean keeps its own ticket (Everest + 35%). Since 2026-10-04 nothing
    # below the Rs 1,50,000 slab opens from a scan (min_position_value). Every sizing site goes through
    # sizing.position_notional_for(), so the knobs cannot disagree at runtime.
    # Guard test: test_entry_value_policy.
    # ------------------------------------------------------------------
    position_size_value: float = 125_000.0    # history; slab model supersedes
    # The minimum a trade opens at under the slab model. Score drives the
    # rest. 0 disables the slab and falls back to position_size_value.
    position_slab_value: float = 150_000.0
    max_position_pct: float = 0.025           # fallback when both are 0
    # OWNER ORDER 2026-09-10: "for 500 position doesnt mean 100000 for each
    # i given a different meaning of 500 to system have a upper ceiling we
    # can reduce to 222". A CEILING, not a target — the slab model funds far
    # fewer than 222 on a Rs 5 Cr book and that is the intent.
    # Measured: 20 sectors x max_sector_positions 12 = 240, so 222 fits under
    # the sector cap without loosening it.
    max_positions: int = 222
    # OWNER ORDER 2026-10-04 ("fix it so all 5 categories get cash", split
    # picked from the options card: "Momentum 40%, others 15%"). The 09:20
    # rotation runs momentum FIRST, and a replay of the live machine on a
    # strong week showed momentum opening 46 trades while intraday, short,
    # swing and positional opened NONE — momentum spent the cash before the
    # other four were scanned. Each category now has its own share of the
    # DEPLOYABLE capital (equity minus the regime reserve), so a correction's
    # 20% reserve shrinks every share alike instead of starving the last
    # category in line. A share a category does not use stays as cash; it is
    # never lent to another. Positions count at entry cost, the same as the
    # deploy cap. Ocean is exempt (its own 8-slot limit); the owner's BUY
    # button and Buy Desk are never refused, but what they buy counts toward
    # its category (momentum). See trading/category_budget.py.
    category_budgets_enabled: bool = True
    budget_share_momentum: float = 0.40
    budget_share_intraday: float = 0.15
    budget_share_short: float = 0.15
    budget_share_swing: float = 0.15
    budget_share_positional: float = 0.15
    # Sizing. "fixed" = the owner's own number, position_size_value above.
    #
    # REVERTED to fixed 2026-08-04 (owner: "still u r making 30000 positions").
    # ½-Kelly was live for four days; at scores just above the 62 entry floor it
    # sizes Rs 20k-60k (measured: 63 -> Rs 20,719 · 65 -> Rs 62,158), so the
    # book was opening Rs 30k trades when the owner had ordered Rs 1.25 lakh
    # each. Kelly was adopted from a question I asked, not from an instruction
    # they wrote; their number wins. "half_kelly" is still selectable in
    # Settings, and the maths stays in trading/sizing.py and the /lens page.
    # OWNER ORDER 2026-09-10: "no flat size to follow". "slab" is the live
    # mode — score-driven size on a Rs 1.5 lakh floor. "fixed" and
    # "half_kelly" stay selectable; the maths for all three is in sizing.py.
    sizing_mode: str = "slab"                 # "slab" | "fixed" | "half_kelly"
    kelly_fraction: float = 0.29              # owner's m, 2026-09-10
    kelly_ic: float = 0.05                    # ASSUMED information coefficient
    kelly_max_pct: float = 0.05               # hard ceiling: 5% of equity/name
    # Measured Kelly from the book's OWN closed trades (AshStocks design,
    # adopted on the owner's order 2026-09-10). It never RAISES a size — it
    # is a ceiling that can only cut, so a bad estimate shrinks the book and
    # can never blow it up. See trading/measured_kelly.py.
    # AUDIT F-9 (owner: "F9- write it", 2026-10-02): WIRED — until then this
    # switch was on and nothing called the estimator. engine.scan reads it:
    # no measured edge blocks new scan entries, a measured edge caps size.
    measured_kelly_enabled: bool = True
    # Owner order 2026-09-10: "no cap on closed trades, i have already more
    # than 100 in that". The count gate is off; what remains is the REASON
    # filter, which is not negotiable arithmetic — an OWNER_SELL or a stale
    # SQUARE_OFF measures the owner and a tagging bug, not the strategy.
    measured_kelly_min_trades: int = 0
    measured_kelly_full_confidence: int = 300
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
    # OWNER ORDER 2026-08-30 ("batch size also reduce to 500 from 2300+",
    # and from the options card "Top 500 by liquidity/turnover, weekly").
    # scheduler.refresh_liquid_universe rebuilds custom_universe every Friday
    # as the top N stored names by MEDIAN RUPEE TURNOVER — retiring the
    # hand-typed 50-name list as the source, which is what went stale.
    # RAISED 2026-09-10 (owner: "B- 1200 Ok") after measurement: a random
    # 150-name sample of the 2,648-name NSE file, priced from live Upstox
    # candles, put only 48.3% above the Rs 5 crore median turnover that
    # risk.passes_liquidity demands to BUY -> about 1,279 of 2,648 names
    # are tradeable at all. 1,200 is not a compromise for the box; it is
    # the size of the tradeable market. The rest can be scanned forever
    # and never bought.
    universe_target_size: int = 1200
    # User instruction 2026-07-21: names the engine must NEVER scan or buy,
    # even though they are currently listed (distress/junk the owner bans).
    # Held positions are exempt so exits still work if one is ever held.
    ticker_blocklist: tuple = ("YESBANK.NS",)
    # v4 correction playbook (user-approved 2026-07-21): momentum entries
    # re-enable inside a correction once India VIX crushes to <=75% of its
    # recent spike peak — the only correction tactic that beat cash in the
    # 10-episode preregistered test (docs/archive/PARAM_SEARCH_V4.md).
    correction_vix_reentry: bool = True
    # Ocean (mean-reversion / knife-catch engine, trading/engines/ocean.py).
    # Docs have described it as PARKED since the v3 review (-41% drawdown
    # catching 2020's falling knives), but until 2026-07-27 there was no
    # switch in the code: engine.scan() ran it on nothing more than
    # regime.ocean_active, which regime.classify() sets True for correction,
    # panic AND neutral. In a correction Everest is off, so Ocean was the
    # only engine able to open a position — and its picks skip the score
    # floor and the absolute-momentum gate. This flag is that missing park
    # switch. OWNER APPROVAL 2026-08-26 ("Approve as specced"): the crash
    # guard is wired (engines/ocean.py — VIX knife rail, damage rail on the
    # panel's own 90th percentile since 2026-10-04, per-stock reclaim, danger
    # veto) and Ocean now runs in PARALLEL with Everest behind it (ticket
    # Rs 1,68,750 since 2026-08-30 — the "HALF size" of the approval is
    # superseded). Default ON by that approval; the
    # Settings toggle still parks it.
    ocean_enabled: bool = True
    # Owner order 2026-08-30 ("when ocean activate it will use more in of
    # 35%"): Everest's fixed ticket plus 35%. Supersedes the half-size
    # Rs 62,500 of 2026-08-26. The slot ceiling is unchanged, so Ocean can
    # commit at most 8 x Rs 1,68,750 = Rs 13.5L. Both are the owner's
    # numbers to move.
    ocean_position_value: float = 168_750.0
    ocean_max_positions: int = 8
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
    # OWNER ORDER 2026-09-11 ("go with these formula" / "ash08 68"): every
    # scanned stock now shows the ASH08 (floor 68) and AshStocks (floor 70)
    # verdicts beside AM07's own. That display is ALWAYS on.
    # This switch is the FILTER half — when True a pick must clear those
    # outside floors too. It defaults OFF on the evidence
    # (research/ash_formula_study/, 23,507 observations): ASH08's floor
    # removes 3% of our candidates because its "quality" leg is really data
    # coverage, and AshStocks' live 20-day rule measured NEGATIVE in both
    # halves (-0.77 train / -0.45 test). The owner owns the switch; the
    # measurement owns the default.
    multi_formula_gate: bool = False
    # OWNER ORDER 2026-09-04 ("skipper exit suggestion is there but not
    # executed — make it happen"): the F6 rotation suggester now EXECUTES.
    # At the scan, a position >= rotation_min_progress of the way to its
    # target is SOLD to fund a fresh pick the cash cannot cover — only when
    # cash is actually short, only as many as the shortfall needs. Owner
    # picks are exempt (the score-decay rule); a name with no live quote is
    # skipped, never filled at an invented price. Settings toggle.
    rotation_auto_enabled: bool = True
    rotation_min_progress: float = 0.90       # the suggester's own bar
    # A3 breadth gate (owner: "wire it", 2026-07-27). New momentum entries
    # only when >50% of the scan panel's tickers close above their own
    # 200DMA. Preregistered evidence: docs/breadth_gate_results.json —
    # +17.7 vs +13.4 (2022-23), +7.5 vs +5.6 with DD -18.8 -> -15.6
    # (2024-26); costs a little in strong trends (2020-21 +37.0 vs +40.8).
    # Fail-open: too few tickers with 200d history = no block (journaled).
    # Momentum mode only — the other modes' history windows are too short
    # for a 200DMA and the study only covered momentum.
    breadth_gate_enabled: bool = True
    # RE-CUT 2026-09-09 (owner order "fix it", after the measurement below).
    # The gate used to block whenever breadth was <= 0.50. Measured against
    # the thing it actually blocks — the ENGINE'S OWN PICKS' forward 20-day
    # return, 540 weekly scan points 2015-2026, production selection (floor
    # 62 + abs-momentum + timing), split at 2020-06-30 — that rule was worth
    # NOTHING: blocked days returned +1.02% vs +0.88% allowed on the fitted
    # half (it blocked the BETTER days) and +2.03% vs +2.05% on the holdout.
    # The real shape is a U: both extremes pay, the MIDDLE band does not.
    # Blocking 40-65% instead: fitted +3.54pp edge, holdout +1.61pp, and
    # every one of 33 candidate band pairs was positive in BOTH halves, so
    # this is a plateau, not a lucky cell. Edges were chosen on the fitted
    # half alone (the 2026-08-13 rule: never pick a threshold by the holdout).
    # A washed-out tape (< 40%) is now BUYABLE, which is the behaviour change.
    # research/breadth_recut_2026-09-09/.
    breadth_block_low: float = 0.40
    breadth_block_high: float = 0.65
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
    # OWNER ORDER 2026-10-05 ("Go for A"): a NEW momentum buy's stop is the
    # wider of this category stop and 2 x its own 14-day ATR, capped at
    # -15% (trading/targets.py, measured in research/stop_cooldown_study).
    # Other categories keep their fixed stops; held positions keep the stop
    # they were opened with. The owner's switch; the numbers are measured
    # constants in targets.py, not knobs.
    atr_stop_enabled: bool = True
    # OWNER ORDER 2026-10-05 ("make it momentum only"): the measured-Kelly
    # check (F-9) reads ONLY this category's closed trades and gates ONLY
    # this category's scan pass. One pooled reading let a run of losing
    # intraday/swing exits block momentum. Empty tuple = the old pooled
    # check in every pass.
    measured_kelly_modes: tuple = ("nifty200_momentum",)
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
    # OWNER ORDER 2026-10-04 ("off for momentum"): categories whose entries do
    # NOT wait for a candle pattern. Measured on the committed 191-stock panel
    # (543 Fridays, 2015-2026): the gate held back 65% of momentum picks, and
    # picks WITH a pattern did worse in 2015-2020 (+1.4% vs +2.5% over 60
    # days) and about the same after (+8.5% vs +7.9%) — no measured benefit.
    # The pattern is still computed and SHOWN; it just no longer blocks a
    # momentum buy. The other four categories keep the gate.
    candle_confirm_skip_modes: tuple = ("nifty200_momentum",)
    # F7 (user-approved 2026-07-11): never open dust positions — a position
    # whose notional is below this is skipped (IDEA-12-shares problem).
    # OWNER ORDER 2026-10-04 ("leave the 1.5 minimum value no 25 k is
    # needed"): Rs 25,000 -> Rs 1,50,000, the slab. Before this, cash, the
    # category share, the sector cap or portfolio heat could trim a trade
    # down to Rs 25,000. Now risk.can_open rounds a trimmed trade UP to the
    # minimum when the money limits have room, and refuses it when they do
    # not — a trade below Rs 1.5 lakh never opens from a scan. The effective
    # floor never exceeds the trade's own target size (risk.min_trade_value),
    # so the legacy fixed Rs 1,25,000 mode still opens its own size.
    min_position_value: float = 150000.0
    # ---- market hours + in-session refresh (owner order 2026-09-10) ----
    # "trade are prohibited from now after market hours ... yes all block,
    # but i can run the advisery to review buy, watch and block list".
    # Scoring, gates, picks, news, triggers and every screen keep running at
    # any hour; only OPENING and CLOSING a position is gated. Off = the old
    # behaviour, which let a 21:00 Render restart buy through the boot
    # catch-up. Backtests set this False (they replay closed days by design).
    market_hours_only: bool = True
    # "A- instead of 20 u can do in 30, it will just update the price, and if
    # any prices match target match, rotation comes it will do and accordingly
    # it will add new position" -> then "A- OK" to running the STOP on the
    # same clock. Stops and targets are symmetric by design: firing targets
    # every 30 minutes while stops waited for 15:35 would take profits fast
    # and losses slow. This makes the -5% stop far more sensitive than the
    # twice-a-day check it replaces; UNTESTED live, the window judges it.
    intraday_refresh_minutes: int = 30
    # OWNER ORDER 2026-09-10: "for b there is simple trigger point when cash
    # is below 5%, rest all the time rotaiton will be done as per a - adding &
    # rotating from watch list". Below this share of equity in cash, rotation
    # frees a ticket even when the waiting pick is already affordable.
    rotation_cash_trigger_pct: float = 0.05
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

    def trail_is_reachable(self, stop: float | None = None,
                           target: float | None = None) -> tuple[bool, float]:
        """(is the trailing stop practically live?, peak % it would need).

        Surfaced 2026-07-31 because the app advertised a trailing stop that,
        at the then-live -5%/+12% bracket, COULD NOT fire (the peak had to
        clear +11.76% but the +12% target closed first — a 0.24pp window).
        Node 12 (owner order 2026-08-24) moved the bracket to -5%/+20%,
        where the -15% trail arms above +17.65% and is genuinely live; the
        Settings banner now only appears if a future edit deadens it
        again. This method just reports; it never changes an exit.

        AUDIT F-18 (2026-10-02): `stop` / `target` (fractions of entry) let a
        caller ask about ONE position's own levels — since 2026-09-10 each
        target is k x ATR, so a quiet name's target can sit below the
        +17.65% the trail needs even while the mode's +20% reads live."""
        stop = float(self.stop_loss_pct if stop is None else stop)
        trail = float(self.trailing_stop_pct)
        target = float(self.take_profit_pct if target is None else target)
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
