"""The thing that actually runs.

scan() -> rank universe by AM 07 score, optionally enter top picks
refresh() -> mark positions to latest prices, fire exits
A typical day:
  1. user opens dashboard -> engine.refresh() updates everything
  2. user clicks "run scan" (or it auto-runs once before market open)
  3. engine.scan() returns ranked picks and opens those that pass timing + risk
  4. through the session, engine.refresh() exits anything that hits a rule
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

log = logging.getLogger(__name__)

from .config import Config, STUB_COMPONENTS
from .costs import slippage_price, trade_costs
from .data import fetch_daily
from .quote_router import fetch_quotes as routed_fetch_quotes
from .exits import check_exit
from .paper import ClosedTrade, PaperPosition, Portfolio
from .calendars import is_market_open, market_state
from .candles import candle_confirmation
from .risk import RiskState, can_open
from .scoring import (
    compute_hot_sectors, compute_score, reasoning_text, set_scoring_context,
)
from .timing import timing_ok


@dataclass
class ScanResult:
    ts: datetime
    ranked: List[dict]            # all symbols with their scores (for debugging)
    picks: List[dict]             # subset that pass min_score + timing
    opened: List[str]             # actually opened (passed risk gates too)
    skipped: List[dict]           # for the picks we couldn't open, why


class TradingEngine:
    def __init__(self, cfg: Config, portfolio: Portfolio):
        self.cfg = cfg
        self.portfolio = portfolio
        self.risk = RiskState(cfg=cfg, starting_capital=cfg.starting_capital)
        # Optional broker client for real-time quotes. Set via .attach_broker()
        # so the engine module stays free of web/auth deps.
        self.broker = None  # UpstoxClient | None
        # v5: graduated drawdown + consecutive-loss controls (stateful, persist
        # across scans within a session)
        from .risk_v2 import ConsecutiveLossTracker, DrawdownGovernor
        self.drawdown_gov = DrawdownGovernor()
        self.loss_tracker = ConsecutiveLossTracker()
        # AUDIT FIX C11: hydrate the 2-loss daily halt from the portfolio —
        # it was memory-only, so a mid-day restart forgot the halt on
        # exactly the kind of day it exists for.
        try:
            from datetime import date as _date
            if getattr(portfolio, "loss_day", None):
                self.loss_tracker.today = _date.fromisoformat(portfolio.loss_day)
                self.loss_tracker.losses_today = int(portfolio.loss_count or 0)
        except (ValueError, TypeError, AttributeError):
            pass

    def attach_broker(self, broker_client) -> None:
        """Plug in a broker client (e.g. UpstoxClient) for real-time quotes.

        When attached, refresh() routes quote lookups through the broker first
        and falls back to yfinance for any unknown instruments. Passing None
        reverts to pure yfinance behaviour.
        """
        self.broker = broker_client

    # ---------- public API ----------

    def scan_universe(self, history_period: str = "6mo") -> tuple:
        """Which tickers today's scan reads, and how much history.

        Extracted from scan() 2026-08-20 (unchanged logic + one new branch)
        so the selection is testable on its own. Priority in momentum mode:
          1. cfg.custom_universe — OWNER ORDER 2026-08-20 ("reduce the
             universe to 50 share only but all fresh"): the exact list, no
             rotation, scanned in full every day. Held names always ride
             along so exits/correlation keep their history.
          2. the rotating full-NSE bucket (P1, evidence-earned breadth).
          3. Nifty 200 when full_universe_scan is off.
        The owner blocklist applies to EVERY branch — a banned name cannot
        enter via the custom list either.
        """
        cfg = self.cfg
        held = list(self.portfolio.positions.keys())
        custom = tuple(getattr(cfg, "custom_universe", ()) or ())
        # CODEX REVIEW FIX (PR #68): the custom list applies to EVERY mode,
        # not only momentum — the 09:20 rotation also runs short/swing/
        # positional, each of which can OPEN positions, so a momentum-only
        # restriction would have let the other three passes buy outside the
        # owner's 50 and overwrite /scan with off-list results. "Reduce the
        # universe to 50" means the app's universe, not one mode's.
        if custom and cfg.mode != "nifty200_momentum":
            universe = list(dict.fromkeys(held + list(custom)))
            try:
                from .universes import apply_blocklist
                universe = apply_blocklist(
                    universe, self.portfolio.positions.keys(),
                    getattr(cfg, "ticker_blocklist", ()))
            except ImportError:
                pass
            return universe, history_period
        # v5: nifty200_momentum mode needs 2 years of history for the
        # momentum_score() to be valid.
        # P1 (search v2.1 evidence, user-approved 2026-07-19): momentum's
        # edge lives at BREADTH — scan the FULL 2,376 NSE universe via the
        # deterministic rotating bucket (~50 fresh names/day, complete
        # coverage each cycle) instead of only Nifty 200. Held tickers are
        # always included so exits/correlation keep their history. The live
        # liquidity + min-value gates do the tradability filtering the
        # close-only research panel could not.
        if cfg.mode == "nifty200_momentum":
            history_period = "2y"
            try:
                if custom:
                    universe = list(dict.fromkeys(held + list(custom)))
                elif getattr(cfg, "full_universe_scan", False):
                    from .universes import (NIFTY_200_TICKERS,
                                            load_full_universe,
                                            rotating_bucket)
                    full = load_full_universe()
                    if full:
                        bucket = rotating_bucket(
                            full, 50, datetime.utcnow().date().toordinal())
                    else:
                        bucket = NIFTY_200_TICKERS
                    universe = list(dict.fromkeys(held + bucket))
                else:
                    from .universes import NIFTY_200_TICKERS
                    universe = NIFTY_200_TICKERS
            except ImportError:
                universe = cfg.universe
        else:
            universe = cfg.universe
        # Owner blocklist (2026-07-21): banned names never enter any scan
        # path; held ones stay visible so exits keep working.
        try:
            from .universes import apply_blocklist
            universe = apply_blocklist(
                universe, self.portfolio.positions.keys(),
                getattr(cfg, "ticker_blocklist", ()))
        except ImportError:
            pass
        return universe, history_period

    def scan(self, history_period: str = "6mo") -> ScanResult:
        cfg = self.cfg
        universe, history_period = self.scan_universe(history_period)
        history = fetch_daily(universe, period=history_period)
        # 2026-09-11: fetch_daily is store-first and BOUNDS its network
        # fallback, so a scan can legitimately cover fewer names than it
        # asked for while the store fills. Say so in the journal rather than
        # letting a 1,200-name universe read as fully scanned when 170 names
        # were seen — the lying-screen fault class. Captured before the
        # benchmark fetch below overwrites the stats.
        from .data import LAST_FETCH_STATS as _fs
        cover = dict(_fs)
        bench = fetch_daily([cfg.benchmark], period=history_period).get(cfg.benchmark)
        if cover.get("asked") and cover.get("covered", 0) < cover["asked"]:
            self._journal_once_a_day("SCAN_COVERAGE_SHORT", datetime.utcnow(), {
                "asked": cover["asked"], "covered": cover["covered"],
                "from_store": cover.get("from_store"),
                "from_network": (cover.get("from_upstox", 0)
                                 + cover.get("from_yahoo", 0)),
                "not_fetched_cap": cover.get("not_fetched_cap"),
                "why": (f"Scanned {cover['covered']} of {cover['asked']} names "
                        f"({cover.get('from_store', 0)} from the stored "
                        f"history, "
                        f"{cover.get('from_upstox', 0) + cover.get('from_yahoo', 0)}"
                        f" fetched). "
                        + (f"{cover['not_fetched_cap']} were left for the "
                           f"off-hours top-up so the scan window could close."
                           if cover.get("not_fetched_cap") else
                           "The rest had no usable history.")),
            })

        # v4: regime-aware. If Ocean is the active engine (panic/correction),
        # Everest is dormant. Either way we score everything so the UI shows
        # full ranking; only the entry-filtering changes per engine.
        regime = None
        try:
            from .regime import fetch_regime
            # FIX (2026-07-12): wire real FII/DII net into the regime. Live NSE
            # feed 403s from cloud → committed data/fii_dii_cash.csv fallback.
            # classify() already treats FII net-selling < -1000 Cr as a
            # correction confirm; it was ALWAYS None before (never fed).
            fii_5d = None
            try:
                from .signals.flows import get_flows, fii_net_last_n_days
                fii_5d = fii_net_last_n_days(get_flows(), n=5)
            except Exception as e:
                log.debug("FII flow fetch failed: %s", e)
            regime = fetch_regime(fii_5d_net_override=fii_5d)
        except Exception as e:
            log.warning("Regime classification failed: %s", e)
            # AUDIT FIX C7 (2026-08-09): regime=None used to FAIL OPEN — the
            # `regime is None or everest_active` gate re-enabled full-size
            # buying with the correction brake silently gone, exactly when a
            # broken feed is most likely (a panic). The last KNOWN regime is
            # sticky instead: if we were in correction/panic yesterday, the
            # brake stays on until the feed answers again; journaled once/day.
            last_known = (self.portfolio.last_regime or "").lower()
            if last_known in ("correction", "panic"):
                today_key = datetime.utcnow().date().isoformat()
                already = any(
                    e2.get("action") == "REGIME_FEED_DOWN"
                    and str(e2.get("ts", "")).startswith(today_key)
                    for e2 in self.portfolio.decision_journal[-100:])
                if not already:
                    self.portfolio.decision_journal.append({
                        "ts": datetime.utcnow().isoformat(),
                        "action": "REGIME_FEED_DOWN",
                        "why": (f"Regime feed failed and the last known regime "
                                f"was {last_known.upper()} — the correction "
                                "brake STAYS ON until the feed answers."),
                    })
                regime_feed_down_brake = True
            else:
                regime_feed_down_brake = False
        else:
            regime_feed_down_brake = False

        # v5: detect regime switch + persist + notify
        if regime is not None and regime.regime != self.portfolio.last_regime:
            entry = {
                "ts": datetime.utcnow().isoformat(),
                "prev": self.portfolio.last_regime,
                "new": regime.regime,
                "reason": regime.reasoning,
            }
            self.portfolio.regime_history.append(entry)
            try:
                from .alerts import notify_regime_switch
                notify_regime_switch(
                    self.portfolio.last_regime or "unknown",
                    regime.regime, regime.reasoning,
                )
            except Exception:
                pass
            self.portfolio.last_regime = regime.regime

        # F2: festival/seasonal context — journaled so the ⚡card and the
        # journal show WHY seasonal names may be moving. Flag only, no alpha.
        # FIX (2026-07-12): `now` was referenced here but only defined ~150
        # lines later, so this block silently NameError'd every scan (festival
        # journaling never fired). Define it up front.
        now = datetime.utcnow()

        # Roll the daily loss-halt baseline at the day's FIRST engine action,
        # BEFORE _record_equity appends today's marks. Recheck fixes
        # (2026-07-27): rolling only inside refresh() meant (a) the 15:35 call
        # measured "daily" loss from today's own 14:30 mark — a one-hour
        # window — and (b) a kill-switch liquidation emptied the book, which
        # made refresh() early-return forever, so the tripped switch never
        # re-armed and all automatic buying stayed dead until a restart.
        self._roll_risk_day(self.portfolio.equity({}), now)

        # AUDIT FIX A3 (2026-08-09): the -2% daily halt was EVALUATED only in
        # refresh() (15:35), so the 09:20 scan and the 14:30 top-up happily
        # bought on a day already past the halt — buy-at-2pm, force-sell-at-
        # 3:35 churn on exactly the worst days. Evaluate it here with live
        # marks on the held book; can_open then blocks every new entry with
        # "kill_switch_active". No live quotes -> can't measure honestly ->
        # leave it to refresh (never trip a halt on entry-price marks).
        held_now = list(self.portfolio.positions.keys())
        if held_now:
            try:
                held_px = routed_fetch_quotes(held_now, upstox_client=self.broker)
            except Exception:
                held_px = {}
            if held_px and self.risk.check_kill_switch(self.portfolio.equity(held_px)):
                if not self.portfolio.risk_day_killed:
                    self.portfolio.decision_journal.append({
                        "ts": now.isoformat(), "action": "KILL_SWITCH_SCAN",
                        "why": (f"Daily loss past {self.cfg.daily_loss_halt_pct:.1%} "
                                "at scan time — new entries blocked for the day."),
                    })
                self.portfolio.risk_day_killed = True
        elif self.risk.check_kill_switch(self.portfolio.equity({})):
            # empty book = all cash, equity is exact without quotes
            self.portfolio.risk_day_killed = True

        try:
            from .calendars import festival_context
            fest = festival_context(days=5)
            if fest:
                self.portfolio.decision_journal.append({
                    "ts": now.isoformat(),
                    "action": "CONTEXT",
                    "festival": fest,
                    "why": f"Festival window active: {fest} — seasonal demand context.",
                })
        except Exception as e:
            log.debug("Festival context skipped: %s", e)

        # P2/B2: dynamic cash reserve — 0% in bull/neutral (deploy fully) but
        # correction/panic force protective cash back in.
        if regime is not None:
            try:
                from .risk_v2 import dynamic_cash_reserve_pct
                cfg.cash_reserve_pct = dynamic_cash_reserve_pct(
                    regime.regime, floor=0.0,
                )
            except Exception:
                pass

        # v5: alternative scoring path for nifty200_momentum mode
        n200_scorer = None
        if cfg.mode == "nifty200_momentum":
            try:
                from .strategies.nifty200_momentum import momentum_score as n200_scorer
            except ImportError:
                n200_scorer = None

        # v5: build shared scoring context (FII tide + hot sectors) once per scan
        fii_score = None
        try:
            from .signals.flows import (
                get_flows, fii_flow_score, fii_net_last_n_days,
            )
            flows = get_flows()   # live NSE -> committed CSV, staleness-guarded
            fii_5d = fii_net_last_n_days(flows, n=5)
            # pass how many days the sum really covers — the live endpoint
            # returns ONE day, so an un-normalised 5-day threshold would read a
            # strong single day as a weak week (fixed 2026-07-31)
            fii_score = fii_flow_score(fii_5d, days=min(len(flows or []), 5) or 5)
        except Exception as e:
            log.debug("FII scoring context skipped: %s", e)

        try:
            hot_sectors = compute_hot_sectors(
                {t: history[t] for t in universe if t in history},
                cfg.sector_map, cfg,
            )
        except Exception:
            hot_sectors = set()

        set_scoring_context(fii_score=fii_score, hot_sectors=hot_sectors)

        ranked: List[dict] = []
        for t in universe:
            if t not in history:
                continue
            if n200_scorer is not None:
                ms = n200_scorer(history[t]["Close"])
                if ms is None:
                    continue
                # C1: blend a value/quality tilt (low-vol + liquidity) into the
                # momentum rank so the book isn't 100% high-beta leaders.
                from .quality import quality_score, blend_scores
                q = quality_score(history[t]["Close"], history[t].get("Volume"))
                b = getattr(cfg, "quality_blend_pct", 0.0)
                score = blend_scores(ms, q, b)
                components = {"momentum": round(ms, 1), "quality": round(q, 1)}
                reasoning = (
                    f"MOM {ms:.0f} + QUALITY {q:.0f} (blend {int(b*100)}%) = {score:.0f}"
                )
            else:
                score, components = compute_score(t, history[t], bench, history, cfg)
                reasoning = reasoning_text(components, score)
            timing_pass, timing_diag = timing_ok(history[t], cfg)
            ranked.append({
                "ticker": t,
                "score": score,
                "components": components,
                "timing_pass": timing_pass,
                "timing_diag": timing_diag,
                "reasoning": reasoning,
                "last_close": float(history[t]["Close"].iloc[-1]),
                # Carried so _target_for can read the name's own ATR without
                # a second fetch. Never serialised into the portfolio.
                "history": history[t],
            })
        ranked.sort(key=lambda r: -r["score"])

        # Engine selection: which picks are eligible for entry today?
        everest_picks: List[dict] = []

        # P0/A2 — ABSOLUTE momentum filter (dual momentum): relative rank alone
        # buys the "least-bad faller" in a weak tape. In momentum mode a pick
        # must also have a positive 6-month return of its own.
        def _abs_mom_ok(t: str) -> bool:
            if cfg.mode != "nifty200_momentum":
                return True
            c = history[t]["Close"].dropna()
            return len(c) > 127 and float(c.iloc[-1]) > float(c.iloc[-127])

        # v4 correction playbook (user-approved 2026-07-21): in a correction
        # the brake holds UNTIL the VIX crushes to <=75% of its recent spike
        # peak — the tested V-recovery signal (docs/PARAM_SEARCH_V4.md:
        # +24% COVID, +7.3% 2022, +2.0% current episode; never fires in
        # mild corrections, so cash remains the default stance).
        vix_reentry = False
        if (regime is not None and not regime.everest_active
                and getattr(cfg, "correction_vix_reentry", False)):
            try:
                from .regime import vix_crush_ok
                vixd = fetch_daily(["^INDIAVIX"], period="1y").get("^INDIAVIX")
                if vixd is not None and not vixd.empty:
                    vix_reentry = vix_crush_ok(vixd["Close"])
            except Exception as e:
                log.warning("VIX-crush check failed: %s", e)
            if vix_reentry:
                self.portfolio.decision_journal.append({
                    "ts": now.isoformat(), "action": "CORRECTION_VIX_REENTRY",
                    "why": ("Correction regime, but India VIX has crushed to "
                            "<=75% of its recent spike peak — v4-tested "
                            "V-recovery signal. Momentum entries re-enabled."),
                })

        # Everest: usual momentum + timing rules. Active in bull/neutral —
        # and in correction once the VIX-crush re-entry signal fires.
        # AUDIT FIX C7: with the feed down, the last known correction/panic
        # keeps the brake on (regime_feed_down_brake) instead of failing open.
        everest_ok = (regime.everest_active if regime is not None
                      else not regime_feed_down_brake)
        # OWNER ORDER 2026-08-09 ("take risk on 200 DMA" -> brake fully
        # off): with correction_brake_enabled False, the CORRECTION regime
        # alone no longer blocks entries. PANIC still does, and a dead
        # regime feed still brakes (C7) — this override needs a live
        # reading that says "correction", never a guess.
        brake_off = (regime is not None and regime.regime == "correction"
                     and not getattr(cfg, "correction_brake_enabled", True))
        if brake_off and not everest_ok:
            self.portfolio.decision_journal.append({
                "ts": now.isoformat(), "action": "CORRECTION_BRAKE_OFF",
                "why": ("Correction regime (Nifty < 200DMA) but the owner "
                        "has switched the brake off (2026-08-09 order) — "
                        "momentum entries proceed under all other gates."),
            })
        if everest_ok or vix_reentry or brake_off:
            everest_picks = [
                r for r in ranked
                if r["score"] >= cfg.min_score_to_enter and r["timing_pass"]
                and _abs_mom_ok(r["ticker"])
            ]

        # Node 9, moved up 2026-08-26: the canonical damage reading now
        # computes BEFORE pick assembly so Ocean's rail 2 reads TODAY'S
        # panel, not yesterday's. Same panel IFR reads, same store.
        canonical = None
        try:
            from .internals import damage_reading
            canonical = damage_reading(history)
            if canonical is not None:
                canonical["computed"] = now.isoformat()
                self.portfolio.damage_store = canonical
        except Exception:
            pass

        # Ocean (owner approval 2026-08-26: "Approve as specced") — runs in
        # PARALLEL with Everest in every regime. Its four crash-guard rails
        # replace the momentum gates for dip entries; see _ocean_picks().
        ocean_picks = self._ocean_picks(regime, history, universe, now)

        picks = everest_picks

        # A3 breadth gate (owner-ordered wire, 2026-07-27; KEPT ON by owner
        # choice 2026-08-26): when half the panel is below its own 200DMA,
        # relative rank is picking least-bad fallers — block new MOMENTUM
        # entries this scan. Ocean picks are exempt BY DESIGN: a dip engine
        # exists exactly for fallen markets, where breadth is always low —
        # gating it here would mean Ocean could never buy at all; its own
        # four rails are its gate. Exits are never touched.
        if picks and self._breadth_gate_blocks(history, now, n_picks=len(picks),
                                               bypass=vix_reentry):
            picks = []
        picks = picks + ocean_picks

        # v4: HALT NEW ENTRIES around known binary events (elections, RBI, Budget).
        binary_event = None
        try:
            from .calendars import is_system_event_within
            binary_event = is_system_event_within(days=3)
        except Exception:
            pass

        # v4: Sector blacklist — drop picks in sectors that bled us historically.
        blacklist = set(getattr(cfg, "blacklisted_sectors", []) or [])
        if blacklist:
            picks = [
                p for p in picks
                if cfg.sector_map.get(p["ticker"], "Unknown") not in blacklist
            ]

        # v4: Nifty trend gate — only enter if Nifty itself is trending (ADX > N).
        # Skips entries during chop where momentum strategies bleed.
        nifty_chop = False
        try:
            from .signals.technicals import adx as _adx
            if bench is not None and len(bench) > 30 and cfg.nifty_min_adx > 0:
                nifty_adx = _adx(bench["High"], bench["Low"], bench["Close"])
                if nifty_adx is not None and nifty_adx < cfg.nifty_min_adx:
                    nifty_chop = True
        except Exception:
            pass

        opened: List[str] = []
        skipped: List[dict] = []
        now = datetime.utcnow()
        prices = {r["ticker"]: r["last_close"] for r in ranked}

        # Node 9: `canonical` was computed earlier this scan (moved before
        # pick assembly 2026-08-26 so Ocean's rail 2 reads today's panel);
        # the IFR journal below still writes it beside IFR's own numbers.

        # v6/v7: IFR check — DAMAGE_CLUSTER + TAIL_DAMAGE_RATIO (Q2-validated)
        ifr_snapshot = None
        try:
            from .ifr import check_damage_cluster
            ifr_snapshot = check_damage_cluster(
                history, now=now,
                last_fire_date=self.portfolio.last_ifr_fire_date,
                ratio_last_fire_date=self.portfolio.last_ifr_ratio_fire_date,
            )
            self.portfolio.last_ifr_fire_date = ifr_snapshot.last_fire_date
            self.portfolio.last_ifr_ratio_fire_date = ifr_snapshot.ratio_last_fire_date
            if ifr_snapshot.fired_today:
                self.portfolio.decision_journal.append({
                    "ts": now.isoformat(),
                    "action": "IFR_SIGNAL_FIRED",
                    "which": list(ifr_snapshot.which_fired),
                    "cluster_fires_in_window": ifr_snapshot.fires_in_window,
                    "tail_down3_5d": ifr_snapshot.tail_down3_5d_today,
                    "cluster_threshold": ifr_snapshot.threshold_value,
                    "ratio_today": ifr_snapshot.ratio_today,
                    "ratio_threshold": ifr_snapshot.ratio_threshold,
                    "sizing_multiplier": ifr_snapshot.sizing_multiplier,
                    # Node 9 step 2: the canonical number beside IFR's own.
                    # IFR counts stocks with any single -3% DAY in 5 days;
                    # the owner's formula takes the worst 3-DAY return in
                    # 5 days. 60 days of these pairs decide step 3.
                    "canonical_tail_down3_5d": (
                        None if canonical is None else canonical["value"]),
                    "canonical_static_cluster": (
                        None if canonical is None
                        else canonical["static_cluster"]),
                    "canonical_q90_cluster": (
                        None if canonical is None
                        else canonical["q90_cluster"]),
                    "why": ifr_snapshot.reason,
                })
        except Exception:
            pass

        # v5: Update drawdown governor with current equity
        current_equity = self.portfolio.equity(prices)
        prev_dd_level = self.drawdown_gov.active_level
        # FIXED 2026-07-31: the governor is rebuilt on every boot with
        # peak_equity=0, so after any Render restart the measured drawdown was
        # 0 and every throttle/halt it controls was disabled until a new peak
        # formed. Restore the persisted peak before updating, and save it after.
        saved_peak = float(getattr(self.portfolio, "dd_peak_equity", 0.0) or 0.0)
        if saved_peak > self.drawdown_gov.peak_equity:
            self.drawdown_gov.peak_equity = saved_peak
        self.drawdown_gov.update(current_equity)
        self.portfolio.dd_peak_equity = float(self.drawdown_gov.peak_equity)
        # v5: notify on drawdown level escalation
        if self.drawdown_gov.active_level != prev_dd_level:
            try:
                from .alerts import notify_drawdown
                snap = self.drawdown_gov.snapshot()
                notify_drawdown(
                    snap["level"], snap["drawdown_pct"],
                    snap["peak"], snap["current"],
                )
            except Exception:
                pass

        # v5: Build correlation-gate state (returns of each open position)
        # AUDIT E (2026-08-09): vol_adjusted_qty was imported here and never
        # called — dead code that read as a live control. Deleted with the
        # rest of the never-wired vol-tier scheme.
        from .risk_v2 import (
            correlation_gate_blocks, daily_returns, passes_liquidity,
        )
        open_returns = {}
        for held in self.portfolio.positions:
            if held in history:
                r = daily_returns(history[held]["Close"])
                if r is not None:
                    open_returns[held] = r

        # v5: per-ticker earnings blackout — 3 days before a known event
        try:
            from .calendars import is_ticker_event_within
        except ImportError:
            is_ticker_event_within = None

        # AUDIT FIX A10 (2026-08-09): entries used to fill at YESTERDAY'S
        # close +5bps even with a live broker attached — every paper fill
        # ignored the overnight gap, so the 60-day verdict was measured on
        # fills nobody could get. One batched quote call for today's picks;
        # a pick with no live quote falls back to the prior close and the
        # BUY journal says which one was used (fill_source).
        live_fill_px: dict = {}
        if picks:
            try:
                live_fill_px = routed_fetch_quotes(
                    [pk["ticker"] for pk in picks], upstox_client=self.broker,
                ) or {}
            except Exception as e:
                log.warning("live fill quotes unavailable — prior close: %s", e)

        # OWNER ORDER 2026-09-10: "trade are prohibited from now after market
        # hours ... yes all block, but i can run the advisery to review buy,
        # watch and block list". Everything above this line is READ-ONLY —
        # scoring, gates, picks, the scan matrix, the journal — and still runs
        # at any hour so the owner can review. Nothing below it may trade.
        #
        # This closes the real hole: catchup_scan_if_missed gated on
        # is_trading_day(), the DAY and never the CLOCK, and fires ~2 minutes
        # after every boot — so a Render restart at 21:00 opened positions at
        # prices nobody could have traded at.
        if not self._trading_allowed(now):
            state = market_state(now)
            self.portfolio.last_scan = now.isoformat()
            self._journal_once_a_day("MARKET_CLOSED_NO_ENTRY", now, {
                "market_state": state,
                "picks_held_back": [pk["ticker"] for pk in picks],
                "why": (f"Market is {state.replace('_', ' ')} — {len(picks)} "
                        f"pick(s) scored and shown for review, none bought. "
                        f"Owner order 2026-09-10: no trades outside "
                        f"09:15-15:30 IST on a trading day."),
            })
            for pk in picks:
                skipped.append({"ticker": pk["ticker"],
                                "reason": f"market_{state}"})
            picks = []

        # OWNER ORDER 2026-09-04: rotation executes. Runs BEFORE the opening
        # loop so the cash it frees is there when can_open counts it.
        try:
            rotated_out = self._rotation_exits(picks, now)
        except Exception as e:
            log.exception("rotation exits failed — picks proceed on cash as-is: %s", e)
            rotated_out = []
        if rotated_out:
            picks = [pk for pk in picks if pk["ticker"] not in rotated_out]

        for pick in picks:
            if binary_event is not None:
                skipped.append({
                    "ticker": pick["ticker"],
                    "reason": f"halt_pre_{binary_event.event_type}_{binary_event.event_date}",
                })
                continue
            # v5: ticker-specific event blackout (earnings)
            if is_ticker_event_within is not None:
                tic_event = is_ticker_event_within(pick["ticker"], days=3)
                if tic_event is not None:
                    skipped.append({
                        "ticker": pick["ticker"],
                        "reason": f"blackout_{tic_event.event_type}_{tic_event.event_date}",
                    })
                    continue
            if nifty_chop:
                skipped.append({
                    "ticker": pick["ticker"],
                    "reason": "nifty_chop_low_adx",
                })
                continue
            # v5: graduated drawdown halt
            if self.drawdown_gov.halt_new_entries:
                skipped.append({
                    "ticker": pick["ticker"],
                    "reason": f"drawdown_{self.drawdown_gov.active_level}",
                })
                continue
            # v5: consecutive-loss halt
            if self.loss_tracker.halted(today=now.date()):
                skipped.append({
                    "ticker": pick["ticker"],
                    "reason": "consecutive_loss_halt",
                })
                continue
            if pick["ticker"] in self.portfolio.positions:
                skipped.append({"ticker": pick["ticker"], "reason": "already_held"})
                continue
            # v5: liquidity hard floor
            tic_hist = history.get(pick["ticker"])
            if tic_hist is not None:
                # P2: never buy on stale candles (dead feed / unnoticed holiday).
                try:
                    stale = (now.date() - tic_hist.index[-1].date()).days
                except Exception:
                    stale = 0
                if stale > getattr(cfg, "max_stale_days", 7):
                    skipped.append({
                        "ticker": pick["ticker"],
                        "reason": f"stale_data_{stale}d",
                    })
                    continue
                last_open = float(tic_hist["Open"].iloc[-1]) if "Open" in tic_hist else None
                last_high = float(tic_hist["High"].iloc[-1]) if "High" in tic_hist else None
                last_low = float(tic_hist["Low"].iloc[-1]) if "Low" in tic_hist else None
                last_close = float(tic_hist["Close"].iloc[-1])
                # v5: skip live mcap fetch — yfinance .info is slow + often
                # fails from cloud IPs. The rupee-turnover floor already
                # block illiquid names; live mcap was a redundant second gate.
                mcap_cr = None
                spread_pct = None
                if self.broker is not None:
                    try:
                        bid, ask = self.broker.bid_ask(pick["ticker"])
                        if bid and ask and bid > 0:
                            spread_pct = (ask - bid) / ((ask + bid) / 2)
                    except Exception:
                        pass
                ok_liq, reason_liq = passes_liquidity(
                    tic_hist["Close"], tic_hist["Volume"],
                    last_high=last_high, last_low=last_low,
                    last_open=last_open, last_close=last_close,
                    market_cap_cr=mcap_cr, spread_pct=spread_pct,
                )
                if not ok_liq:
                    skipped.append({
                        "ticker": pick["ticker"],
                        "reason": f"liquidity_{reason_liq}",
                    })
                    continue
                # v5: correlation gate — was 0.70, tightened to 0.85 because
                # N200 stocks routinely correlate 0.55-0.75 with each other
                # (banks + financials cluster) and the strict 0.70 was blocking
                # every diversification candidate. 0.85 still catches the
                # genuinely-same-bet pairs.
                cand_ret = daily_returns(tic_hist["Close"])
                blocker = correlation_gate_blocks(
                    cand_ret, open_returns, threshold=0.85,
                )
                if blocker is not None:
                    skipped.append({
                        "ticker": pick["ticker"],
                        "reason": f"correlated_{blocker[0]}_{blocker[1]:.2f}",
                    })
                    continue
                # Batch C: candle BUY-CONFIRMATION — execute only when the
                # latest candle confirms the signal; otherwise wait (skip is
                # journaled and surfaces on the watchlist / triggers card).
                # Ocean picks are exempt: their reclaim rail (close above the
                # prior day's high, no fresh 20d low) IS the confirmation —
                # a capitulation-reversal bar rarely forms a textbook
                # bullish structure, and double-gating would starve the
                # engine the owner just approved.
                if not pick.get("ocean"):
                    ok_candle, candle_why = candle_confirmation(tic_hist, cfg)
                    if not ok_candle:
                        skipped.append({
                            "ticker": pick["ticker"],
                            "reason": f"candle_wait_{candle_why}",
                        })
                        continue
            live_px = live_fill_px.get(pick["ticker"])
            if live_px and live_px > 0:
                # 2026-08-26: the router can now serve a ~1-minute-delayed
                # public candle when no login token exists — label it as
                # such, never as a live tick (the 2026-07-31 lesson).
                from . import quote_router as _qr
                src = _qr.LAST_SOURCES.get(pick["ticker"], "live_quote")
                raw_fill = float(live_px)
                pick["fill_source"] = ("live_quote" if src == "upstox"
                                       else src)
            else:
                raw_fill, pick["fill_source"] = pick["last_close"], "prev_close"
            fill = slippage_price(raw_fill, "BUY", cfg)
            current_eq = self.portfolio.equity(prices)
            # Owner order 2026-07-30: size by ½-Kelly off this stock's own
            # estimated edge and volatility instead of a flat rupee amount.
            # Falls back to the fixed size when history is missing, and every
            # risk gate inside can_open still applies to the result.
            from .sizing import position_notional_for
            cand_close = history.get(pick["ticker"], {}).get("Close") \
                if isinstance(history.get(pick["ticker"]), dict) else None
            if cand_close is None:
                try:
                    cand_close = history[pick["ticker"]]["Close"].dropna()
                except Exception:
                    cand_close = None
            kelly_notional_rs, size_diag = position_notional_for(
                cfg, current_eq, score=pick.get("score"), close=cand_close)
            if pick.get("ocean"):
                # Owner-approved 2026-08-26: Ocean entries are HALF size
                # (Rs 1,68,750, owner order 2026-08-30) — the rails are the
                # edge claim, the size is
                # the humility. Every can_open gate still applies.
                ocean_rs = float(getattr(cfg, "ocean_position_value",
                                         168_750.0) or 168_750.0)
                size_diag = {"mode": "ocean_fixed", "notional": ocean_rs}
                notional_arg = ocean_rs
            else:
                notional_arg = (kelly_notional_rs
                                if size_diag.get("mode") != "fixed" else None)
            ok, reason, qty = can_open(
                self.risk, self.portfolio.cash, current_eq,
                self.portfolio.positions, pick["ticker"], fill, cfg,
                notional_override=notional_arg,
            )
            pick["size_diag"] = size_diag
            # v7: size throttle = MIN of (strength, IFR) instead of product.
            # Previously we multiplied them, which compounded to 0.25-0.35x
            # and zeroed qty on expensive stocks. The throttles are designed
            # as alternative defensive measures, not stacking damages — take
            # whichever is more restrictive at any one time.
            if ok:
                s = pick.get("score", 0)
                # Under Kelly the score ALREADY sets the size (mu is derived
                # from it), so applying the strength ladder too would count the
                # score twice and shrink good picks for no reason. The IFR
                # throttle stays either way — it is a damage-cluster defence,
                # not a score judgement.
                # AUDIT FIX A1 (2026-08-09): the strength ladder
                # (0.5/0.7/1.0x by score) is GONE. Under Kelly it double-
                # counted the score; under fixed it silently cut the owner's
                # written Rs 1.25L to Rs 87.5k for every 62-74.9 pick — the
                # size is the owner's number, not a suggestion. The IFR
                # throttle stays: it is a damage-cluster defence, not a
                # score judgement.
                ifr_mult = ifr_snapshot.sizing_multiplier if ifr_snapshot else 1.0
                # AUDIT E (2026-08-09): the drawdown governor's documented
                # "watchful → size 75%" tier was set in update() and consumed
                # by NOTHING — the risk page described a control that never
                # ran. It throttles for real now (most restrictive wins).
                dd_mult = float(getattr(self.drawdown_gov,
                                        "sizing_multiplier", 1.0) or 1.0)
                throttle = min(ifr_mult, dd_mult)
                qty = int(qty * throttle)
                min_val = float(getattr(cfg, "min_position_value", 0) or 0)
                if qty > 0 and min_val > 0 and qty * fill < min_val:
                    skipped.append({
                        "ticker": pick["ticker"],
                        "reason": f"below_min_value_{int(min_val)}",
                    })
                    continue
                if qty <= 0:
                    skipped.append({
                        "ticker": pick["ticker"],
                        "reason": f"throttle_zero_qty (ifr={ifr_mult})",
                    })
                    continue
            if not ok:
                skipped.append({"ticker": pick["ticker"], "reason": reason})
                continue
            self._open(pick, qty, fill, now)
            opened.append(pick["ticker"])

        self.portfolio.last_scan = now.isoformat()

        def _candle_label(tkr: str) -> str:
            # F17: record which candle pattern confirmed (or why waiting) so
            # the dashboard can SHOW it with no refetch — history is in RAM.
            h = history.get(tkr)
            if h is None:
                return ""
            try:
                ok_c, why = candle_confirmation(h, cfg)
                return why
            except Exception:
                return ""

        def _all_structures(tkr: str) -> dict:
            # Owner ask 2026-07-26: every candle structure is its OWN
            # parameter, not one lumped "candle confirm" lane. 18 structures
            # (10 bullish / 6 bearish / 2 neutral) recorded per stock.
            h = history.get(tkr)
            if h is None:
                return {}
            try:
                from .candles import scan_all_patterns
                return scan_all_patterns(
                    h, int(getattr(cfg, "candle_breakout_lookback", 20)),
                    float(getattr(cfg, "candle_volume_mult", 1.5)))
            except Exception:
                return {}

        self.portfolio.last_scan_picks = [
            {
                "ticker": r["ticker"], "score": r["score"],
                "reasoning": r["reasoning"], "last_close": r["last_close"],
                "timing_pass": r["timing_pass"],
                "components": r.get("components") or {},
                "candles": history[r["ticker"]][["Open", "High", "Low", "Close"]].tail(20).values.tolist(),
                "candle": _candle_label(r["ticker"]),
                "structures": _all_structures(r["ticker"]),
                "opened": r["ticker"] in opened,
            }
            for r in ranked[:50]
        ]
        self.portfolio.last_scan_skipped = list(skipped)
        self.portfolio.last_scan_opened = list(opened)

        # 🎹 Parameter Piano — the FULL scan hit-map, every ticker × every real
        # computed check (factor scores, timing, absolute momentum, candle,
        # gate outcome). Rendered as the grid on /scan. Overwritten per scan,
        # fixed size (~universe × factors) — no RAM growth.
        skip_by_t = {s.get("ticker"): str(s.get("reason", ""))[:40]
                     for s in skipped if s.get("ticker")}
        self.portfolio.scan_matrix = {
            "ts": now.isoformat(),
            "mode": getattr(cfg, "mode", "?"),
            "score_floor": float(cfg.min_score_to_enter),
            "universe_size": len(universe),
            "rows": {
                r["ticker"]: {
                    "c": {k: round(float(v), 1)
                          for k, v in (r.get("components") or {}).items()},
                    "score": round(float(r["score"]), 1),
                    "timing": bool(r["timing_pass"]),
                    "abs_mom": bool(_abs_mom_ok(r["ticker"])),
                    "candle": _candle_label(r["ticker"]),
                    "structures": _all_structures(r["ticker"]),
                    "outcome": ("opened" if r["ticker"] in opened
                                else skip_by_t.get(r["ticker"], "")),
                }
                for r in ranked
            },
        }
        # Structural flags (owner-approved 2026-07-26): context markers from
        # the same history panel — display/journal only, never a gate.
        try:
            from .flags import compute_flags
            self.portfolio.scan_matrix["flags"] = compute_flags(
                {t: history[t] for t in universe if t in history},
                list(self.portfolio.positions.keys()), cfg.sector_map)
        except Exception:
            pass

        # F8: shadow-book the YES-scored picks we did NOT buy (opportunity
        # cost of the gates), then mark the whole book to market.
        try:
            from .virtual import record_virtual, mark_virtual
            _OPP = ("candle_wait", "reserve", "cash", "max_positions",
                    "sector_exposure_cap", "below_min_value", "throttle",
                    "correlated")
            rank_by_t = {r["ticker"]: r for r in ranked}
            tgt = float(getattr(cfg, "take_profit_pct", 0.30))
            stp = float(getattr(cfg, "stop_loss_pct", -0.12))
            for s in skipped:
                reason = str(s.get("reason", ""))
                tkr = s.get("ticker")
                if tkr and any(k in reason for k in _OPP) and tkr in rank_by_t:
                    record_virtual(
                        self.portfolio.virtual_book, tkr,
                        float(rank_by_t[tkr]["last_close"]),
                        float(rank_by_t[tkr]["score"]),
                        cfg.sector_map.get(tkr, "Unknown"), reason,
                        tgt, stp, now.isoformat())
            # cap the shadow book so it can't grow unbounded (512 MB)
            if len(self.portfolio.virtual_book) > 300:
                keep = sorted(self.portfolio.virtual_book.items(),
                              key=lambda kv: kv[1].get("first_seen", ""))[-300:]
                self.portfolio.virtual_book = dict(keep)
            mark_virtual(self.portfolio.virtual_book, prices, now.isoformat())
        except Exception as e:
            log.debug("Shadow-book update skipped: %s", e)

        # v5: archive a compact scan summary for the history page
        self.portfolio.scan_history.append({
            "ts": now.isoformat(),
            "regime": regime.regime if regime is not None else None,
            "ranked_count": len(ranked),
            "picks_count": len(picks),
            "opened_count": len(opened),
            "skipped_count": len(skipped),
            "top_score": float(ranked[0]["score"]) if ranked else 0.0,
        })
        self._record_equity(prices, now)
        self.portfolio.save()

        return ScanResult(ts=now, ranked=ranked, picks=picks, opened=opened, skipped=skipped)

    def square_off_intraday(self, prices: dict | None = None) -> list[str]:
        """15:25 IST: force-close every position tagged mode='intraday'.

        Keyed off the POSITION's mode, not cfg.mode — the 09:20 rotation
        opens intraday picks even when the app's active mode is momentum.
        Runs regardless of disable_exits: intraday must never sleep overnight.
        """
        targets = [t for t, p in self.portfolio.positions.items()
                   if (getattr(p, "mode", "") or "") == "intraday"]
        if not targets:
            return []
        if prices is None:
            prices = routed_fetch_quotes(targets, upstox_client=self.broker)
        now = datetime.utcnow()
        closed: list[str] = []
        for t in targets:
            px = prices.get(t)
            if not px or px <= 0:
                # AUDIT FIX A2: never square off at a fabricated entry-price
                # fill. The position stays open and visible; policy-C retags
                # a stale intraday tag to momentum at the next boot, so it
                # cannot silently live on under intraday rules.
                self.portfolio.decision_journal.append({
                    "ts": now.isoformat(), "action": "SQUARE_OFF_HELD_NO_QUOTE",
                    "ticker": t,
                    "why": ("15:25 square-off: no live quote — not closed at a "
                            "made-up price. Sell manually with your own fill "
                            "price, or it retags to momentum rules tomorrow."),
                })
                continue
            self._close(t, float(px), "SQUARE_OFF", now)
            closed.append(t)
        if closed or targets:
            self.portfolio.save()
        return closed

    def refresh(self) -> dict:
        """Pull latest prices, mark portfolio, fire exits."""
        cfg = self.cfg
        if not self.portfolio.positions:
            # Empty book still rolls the risk day — otherwise a kill-switch
            # liquidation (which empties the book) would freeze the tripped
            # switch here forever (recheck fix, 2026-07-27).
            prices = {}
            self._roll_risk_day(self.portfolio.equity(prices), datetime.utcnow())
            return self._summary_dict(prices)

        tickers = list(self.portfolio.positions.keys())
        prices = routed_fetch_quotes(tickers, upstox_client=self.broker)
        if not prices:
            # FIX (2026-07-12): stale-data guard. No live quotes = we must NOT
            # fire exits on entry-price marks (a missed Upstox login would
            # falsely trip/skip stops). Journal it loudly and hold, once/day.
            today_iso = datetime.utcnow().date().isoformat()
            already = any(
                e.get("action") == "DATA_STALE" and str(e.get("ts", "")).startswith(today_iso)
                for e in self.portfolio.decision_journal[-50:]
            )
            if not already:
                self.portfolio.decision_journal.append({
                    "ts": datetime.utcnow().isoformat(), "action": "DATA_STALE",
                    "why": "No live quotes (Upstox down + Yahoo blocked). Exits "
                           "held to avoid false triggers on stale marks. Reconnect Upstox.",
                })
                self.portfolio.save()
            log.warning("refresh: no live quotes — exits held (stale-data guard)")
            return self._summary_dict({p.ticker: p.entry_price for p in self.portfolio.positions.values()})

        now = datetime.utcnow()
        history = fetch_daily(tickers, period="1y")
        bench = fetch_daily([cfg.benchmark], period="6mo").get(cfg.benchmark)

        # OWNER ORDER 2026-09-10: no trades outside 09:15-15:30 IST, and the
        # 30-minute in-session pass now carries stops AND targets (owner: "A -
        # OK", both on the same clock). So a closed-market refresh MARKS the
        # book — cash, equity, P&L, journal all update, screens stay honest —
        # and fires nothing. Exits are not lost, they moved INSIDE the session
        # where they can actually be filled.
        #
        # This is a real change to when a stop can fire. It used to fire at
        # 15:35, five minutes after the close, on a price nobody could trade.
        marking_only = not self._trading_allowed(now)
        if marking_only:
            self._journal_once_a_day("MARKET_CLOSED_MARK_ONLY", now, {
                "market_state": market_state(now),
                "positions": len(tickers),
                "why": (f"Market {market_state(now).replace('_', ' ')} — book "
                        f"marked, no exits fired. Stops and targets run on the "
                        f"in-session pass every "
                        f"{int(getattr(cfg, 'intraday_refresh_minutes', 30))} "
                        f"minutes."),
            })

        # OWNER ORDER 2026-08-11 danger layer (C then auto B): the strict-
        # calibration-VERIFIED danger tripwires guard HELD names. Sell side
        # only; runs before the exit loop so a tightened stop is live today.
        try:
            if not marking_only:
                self._danger_pass(prices, history, now)
        except Exception:
            log.exception("danger layer pass failed")
            self.portfolio.decision_journal.append({
                "ts": now.isoformat(), "action": "DANGER_LAYER_FAILED",
                "why": "danger pass raised; positions untouched this run",
            })

        # Daily kill switch check. Roll the baseline FIRST — without this the
        # baseline stays pinned at starting_capital forever and the "-2% daily"
        # halt becomes "-2% since inception", force-closing the whole book on
        # an ordinary drawdown.
        current_eq = self.portfolio.equity(prices)
        self._roll_risk_day(current_eq, now)
        if self.risk.check_kill_switch(current_eq):
            # Persist the trip so a mid-day restart can't forget the halt.
            self.portfolio.risk_day_killed = True

        # Track which tickers already have a HOLD entry today (avoid spam on
        # multiple refreshes per day) — one why-retained line per ticker per day.
        today_iso = now.date().isoformat()
        held_today = {
            e["ticker"] for e in self.portfolio.decision_journal[-200:]
            if e.get("action") == "HOLD" and e.get("ts", "").startswith(today_iso)
        }

        exits: list[str] = []
        no_quote_held: list[str] = []
        for ticker in list(self.portfolio.positions.keys()):
            pos = self.portfolio.positions[ticker]
            price = prices.get(ticker)
            if not price or price <= 0:
                # AUDIT FIX A2 (2026-08-09): the stale-data guard above only
                # fires when ALL quotes fail. A single missing quote used to
                # fall back to the ENTRY price — so a winner whose peak sat
                # >= ~17.7% above entry "showed" a -15% trail breach and was
                # closed at entry, erasing the recorded gain. No quote = no
                # exit decision for that name, journaled once per day.
                no_quote_held.append(ticker)
                today_key = now.date().isoformat()
                seen = any(e.get("action") == "EXIT_HELD_NO_QUOTE"
                           and e.get("ticker") == ticker
                           and str(e.get("ts", "")).startswith(today_key)
                           for e in self.portfolio.decision_journal[-100:])
                if not seen:
                    self.portfolio.decision_journal.append({
                        "ts": now.isoformat(), "action": "EXIT_HELD_NO_QUOTE",
                        "ticker": ticker,
                        "why": ("No live quote for this name — exit rules "
                                "held rather than evaluated on the entry-"
                                "price mark. Reconnect Upstox / check the "
                                "instrument mapping."),
                    })
                continue
            # Recompute the score for score-decay check
            current_score = pos.entry_score
            if ticker in history:
                current_score, _ = compute_score(ticker, history[ticker], bench, history, cfg)
            should_exit, reason = check_exit(pos, price, current_score, cfg, now)
            if should_exit and marking_only:
                # Owner order 2026-09-10: outside 09:15-15:30 the rule is SEEN
                # and RECORDED, never executed — a fill after the close is a
                # price nobody could get. It fires on the next in-session pass.
                self.portfolio.decision_journal.append({
                    "ts": now.isoformat(), "action": "EXIT_DUE_MARKET_CLOSED",
                    "ticker": ticker, "rule": reason,
                    "price": round(float(price), 2),
                    "why": (f"{reason} is due on {ticker} at ₹{price:,.2f}, but "
                            f"the market is {market_state(now).replace('_', ' ')}. "
                            f"It will fire on the next in-session pass."),
                })
            elif should_exit:
                self._close(ticker, price, reason, now)
                exits.append(ticker)
            elif ticker not in held_today:
                # WHY-RETAINED journal entry — why we kept it today
                upct = (price / pos.entry_price - 1) * 100 if pos.entry_price else 0
                self.portfolio.decision_journal.append({
                    "ts": now.isoformat(),
                    "action": "HOLD",
                    "ticker": ticker,
                    "price": round(price, 2),
                    "unrealised_pct": round(upct, 2),
                    "current_score": round(current_score, 1),
                    "score_at_entry": round(pos.entry_score, 1),
                    "why": (
                        f"No exit rule hit: ret {upct:+.1f}% inside stop({cfg.stop_loss_pct*100:.0f}%)"
                        f"/target(+{cfg.take_profit_pct*100:.0f}%), score {current_score:.0f} "
                        f"above decay-floor {cfg.score_decay_exit:.0f}, "
                        f"held {(now.date() - pos.entry_date.date()).days}d of max {cfg.max_hold_days}d"
                    ),
                })

        # F15: pyramid — add tranches to winners (opt-in, cfg.pyramid_enabled).
        # A pyramid add is a BUY; the market-hours rule covers it.
        if getattr(cfg, "pyramid_enabled", False) and not marking_only:
            try:
                from .pyramid import pyramid_plan, apply_pyramid
                for ticker in list(self.portfolio.positions.keys()):
                    pos = self.portfolio.positions[ticker]
                    ltp = prices.get(ticker, pos.entry_price)
                    plan = pyramid_plan(pos, ltp, self.portfolio.cash, cfg)
                    if plan is None:
                        continue
                    add_qty, notional = plan
                    fill = slippage_price(ltp, "BUY", cfg)
                    self.portfolio.cash -= add_qty * fill
                    prev_adds = pos.pyramid_adds
                    apply_pyramid(pos, add_qty, fill)
                    self.portfolio.decision_journal.append({
                        "ts": now.isoformat(), "action": "PYRAMID_ADD",
                        "ticker": ticker, "add_qty": add_qty,
                        "price": round(fill, 2), "tranche": prev_adds + 1,
                        "new_qty": pos.qty, "new_avg_entry": round(pos.entry_price, 2),
                        "why": f"Winner up {(ltp/pos.entry_price-1)*100:.1f}% — pyramid tranche {prev_adds+1}.",
                    })
            except Exception as e:
                log.warning("Pyramid add failed: %s", e)

        # If kill switch tripped, force-close everything else — unless exits are
        # disabled (buy-and-hold). AUDIT FIX A2: only names with a REAL quote
        # are closed; an unpriced name stays open (visible above) instead of
        # being force-filled at its entry price.
        if (self.risk.kill_switch and not getattr(cfg, "disable_exits", False)
                and not marking_only):
            for ticker in list(self.portfolio.positions.keys()):
                price = prices.get(ticker)
                if not price or price <= 0:
                    continue
                self._close(ticker, price, "KILL_SWITCH", now)
                exits.append(ticker)

        # P1/F3: drawdown-ladder crash brake. forced_cut was computed but never
        # executed anywhere — wire it: sell the worst positions until the cut
        # fraction is met. Runs EVEN in hold mode (buy-and-hold still needs a
        # crash exit; -10% peak-to-trough = cut half, -15% = 80%, -20% = all).
        # AUDIT FIX C11 (2026-08-09): the persisted drawdown peak was
        # restored only in scan() — a post-restart refresh() measured 0%
        # drawdown until the next scan. Restore before, save after, here too.
        _saved_peak = float(getattr(self.portfolio, "dd_peak_equity", 0.0) or 0.0)
        if _saved_peak > self.drawdown_gov.peak_equity:
            self.drawdown_gov.peak_equity = _saved_peak
        self.drawdown_gov.update(self.portfolio.equity(prices))
        self.portfolio.dd_peak_equity = float(self.drawdown_gov.peak_equity)
        cut = self.drawdown_gov.forced_cut
        if cut > 0 and self.portfolio.positions and not marking_only:
            # AUDIT FIX A2: rank and cut only names with a real quote — an
            # unpriced position can neither be ranked honestly nor filled.
            priced = [t for t in self.portfolio.positions
                      if prices.get(t) and prices[t] > 0]
            by_pnl = sorted(
                priced,
                key=lambda t: prices[t] / self.portfolio.positions[t].entry_price,
            )
            n_close = max(1, int(len(self.portfolio.positions) * cut))
            for ticker in by_pnl[:n_close]:
                self._close(ticker, prices[ticker], "DRAWDOWN_LADDER", now)
                exits.append(ticker)

        self._record_equity(prices, now)
        self.portfolio.save()
        summary = self._summary_dict(prices)
        summary["exits_this_refresh"] = exits
        return summary

    # ---------- internals ----------

    def _open(self, pick: dict, qty: int, fill_price: float, now: datetime) -> None:
        notional = qty * fill_price
        self.portfolio.cash -= notional
        # WHY-BOUGHT journal entry — full decision context, never deleted
        self.portfolio.decision_journal.append({
            "ts": now.isoformat(),
            "action": "BUY",
            "ticker": pick["ticker"],
            "qty": qty,
            "price": round(fill_price, 2),
            "notional": round(notional, 2),
            "score": round(pick.get("score", 0), 2),
            "components": {k: round(v, 1) for k, v in (pick.get("components") or {}).items()},
            "timing_pass": pick.get("timing_pass"),
            "mode": getattr(self.cfg, "mode", "?"),
            "regime": self.portfolio.last_regime,
            "drawdown_level": getattr(self.drawdown_gov, "active_level", "?"),
            "ifr_active": self.portfolio.last_ifr_fire_date is not None,
            "ifr_last_fire": self.portfolio.last_ifr_fire_date,
            "why": pick.get("reasoning", ""),
            "cash_after": round(self.portfolio.cash, 2),
            # AUDIT FIX A10: where the fill price came from — live_quote when
            # the broker answered, prev_close when it didn't. Never hidden.
            "fill_source": pick.get("fill_source", "prev_close"),
            # Owner order 2026-07-30: how this size was decided (½-Kelly inputs
            # or "fixed"), so any trade's size can be re-derived later.
            "sizing": pick.get("size_diag") or {"mode": "fixed"},
            # P2: config snapshot so every decision stays reproducible after
            # settings change later.
            "config_snapshot": {
                "stop_loss_pct": self.cfg.stop_loss_pct,
                "take_profit_pct": self.cfg.take_profit_pct,
                "cash_reserve_pct": self.cfg.cash_reserve_pct,
                "sizing_mode": getattr(self.cfg, "sizing_mode", "fixed"),
                "kelly_fraction": getattr(self.cfg, "kelly_fraction", 0.0),
                "kelly_ic": getattr(self.cfg, "kelly_ic", 0.0),
                "kelly_max_pct": getattr(self.cfg, "kelly_max_pct", 0.0),
                "position_size_value": getattr(self.cfg, "position_size_value", 0.0),
                "max_position_pct": self.cfg.max_position_pct,
                "max_positions": self.cfg.max_positions,
                "min_score_to_enter": self.cfg.min_score_to_enter,
                "disable_exits": getattr(self.cfg, "disable_exits", False),
            },
        })
        # Snapshot the trade category + exit levels at entry so the position
        # carries its own stop/target (the daily scan rotates cfg.mode across
        # intraday/short/swing/positional, so the live cfg won't match later).
        self.portfolio.positions[pick["ticker"]] = PaperPosition(
            ticker=pick["ticker"], qty=qty, entry_price=fill_price,
            entry_date=now, sector=self.cfg.sector_map.get(pick["ticker"], "Unknown"),
            peak_price=fill_price, entry_score=pick["score"],
            reasoning=pick["reasoning"],
            mode=getattr(self.cfg, "mode", ""),
            stop_price=round(fill_price * (1 + self.cfg.stop_loss_pct), 2),
            # OWNER ORDER 2026-09-10 ("C- k x ATR"): the target is k average
            # days of range away, per horizon, not a flat percentage — a quiet
            # name gets a nearer target than a wild one. Falls back to the
            # mode's fixed percentage when the ATR cannot be computed; the
            # diagnostic in the BUY journal says which was used, so a screen
            # can never show an ATR target that was really the old number.
            target_price=self._target_for(pick, fill_price),
            # the tag is the Ocean slot counter (max 8) and the audit trail
            tags=(["ocean"] if pick.get("ocean") else []),
        )

    def _watchlist_candidates(self, held: dict) -> list:
        """Top-scored not-held names from the last scan, best first.

        The SAME rows the dashboard rotation card reads
        (`portfolio.scan_matrix`), so the screen and the engine can never
        disagree about what is worth rotating into — the disagreement the
        owner reported on 2026-09-10. Names below the live entry floor are
        excluded: rotation must never sell a real position to buy something
        the entry gate would have refused.
        """
        rows = (getattr(self.portfolio, "scan_matrix", None) or {}).get("rows") or {}
        floor = float(getattr(self.cfg, "min_score_to_enter", 62.0))
        block = set(getattr(self.cfg, "ticker_blocklist", ()) or ())
        out = []
        for tkr, row in rows.items():
            if tkr in held or tkr in block:
                continue
            try:
                score = float(row.get("score"))
            except (TypeError, ValueError):
                continue
            if score < floor:
                continue
            out.append({"ticker": tkr, "score": score,
                        "reasoning": row.get("reasoning") or "watchlist rotation",
                        "source": "watchlist"})
        out.sort(key=lambda r: -r["score"])
        return out[:10]

    def _target_for(self, pick: dict, fill_price: float) -> float:
        """k x ATR target for a new position (owner order 2026-09-10).

        `pick` carries the scan's own history frame under "history" when the
        caller has one; without it the fallback is the mode's fixed percentage
        — never an invented range.
        """
        from .targets import target_price_for
        df = pick.get("history")
        try:
            price, diag = target_price_for(
                getattr(self.cfg, "mode", ""), float(fill_price), df,
                float(self.cfg.take_profit_pct))
        except Exception:
            log.exception("ATR target failed — mode percentage used")
            return round(float(fill_price) * (1 + self.cfg.take_profit_pct), 2)
        pick["target_diag"] = diag
        return price

    def _danger_pass(self, prices: dict, history: dict, now: datetime) -> None:
        """Mode C (alerts, first 7 days) then mode B (auto per-stock defence).
        1 VERIFIED danger trigger on a held name -> tighten stop to -2.5%
        off the live price (never loosened); 2+ -> exit half. Journaled and
        pushed; once per ticker per day."""
        cfg = self.cfg
        if not getattr(cfg, "danger_layer_enabled", False):
            return
        from . import danger
        if not danger.VERIFIED:
            return                      # calibration file missing = inert
        today_iso = now.date().isoformat()
        if not self.portfolio.danger_alerts_start:
            self.portfolio.danger_alerts_start = today_iso
            self.portfolio.decision_journal.append({
                "ts": now.isoformat(), "action": "DANGER_LAYER_ARMED",
                "why": (f"Danger layer live (owner order 2026-08-11): "
                        f"{len(danger.VERIFIED)} VERIFIED tripwires "
                        f"({len(danger.PANEL_FED)} panel-fed, added on the "
                        "2026-08-13 order, cleared the main +/-10% labels; the "
                        "rest cleared strict +/-15%) "
                        f"watch held names. Alerts-only for "
                        f"{danger.ALERT_DAYS} days, then auto per-stock "
                        "defence (tighten/-2.5% on 1 fire, exit half on 2+)."),
            })
        auto = danger.auto_mode_active(self.portfolio.danger_alerts_start,
                                       today_iso)
        # Panels for the panel-fed tripwires (owner order 2026-08-13: Index
        # Risk Overlay + Sector Contagion Count). ONE light load for the whole
        # held book — peer panel + index only, never the 3.5M-row delivery
        # file. None on any failure, which makes those two abstain instead of
        # guessing; journaled once a day so a silent loss of cover is visible.
        held = list(self.portfolio.positions.keys())
        ext_map = danger.externals_for(held) if danger.PANEL_FED else None
        if danger.PANEL_FED and ext_map is None and held:
            already = any(
                e.get("action") == "DANGER_PANELS_UNAVAILABLE"
                and str(e.get("ts", "")).startswith(today_iso)
                for e in self.portfolio.decision_journal[-300:])
            if not already:
                self.portfolio.decision_journal.append({
                    "ts": now.isoformat(),
                    "action": "DANGER_PANELS_UNAVAILABLE",
                    "why": (f"{len(danger.PANEL_FED)} panel-fed tripwires "
                            f"({', '.join(danger.PANEL_FED)}) could not read "
                            "their panels this run and abstained. The "
                            f"{len(danger.VERIFIED) - len(danger.PANEL_FED)} "
                            "OHLCV tripwires still ran."),
                })
        seen_today = {
            e.get("ticker") for e in self.portfolio.decision_journal[-300:]
            if str(e.get("action", "")).startswith("DANGER_")
            and str(e.get("ts", "")).startswith(today_iso)
        }
        for ticker in list(self.portfolio.positions.keys()):
            if ticker in seen_today:
                continue
            price = prices.get(ticker)
            df = history.get(ticker)
            if not price or price <= 0 or df is None or len(df) < 60:
                continue                # no quote / thin history = no action
            fired = danger.evaluate_stock(
                df, ext=(ext_map or {}).get(ticker))
            reading = danger.plan(ticker, fired, auto)
            if reading.action == "NONE":
                continue
            pos = self.portfolio.positions[ticker]
            entry = {
                "ts": now.isoformat(), "ticker": ticker,
                "fired": reading.fired, "price": round(price, 2),
                "why": danger.describe(reading, auto),
            }
            if reading.action == "ALERT":
                entry["action"] = "DANGER_ALERT"
            elif reading.action == "TIGHTEN":
                new_stop = round(price * (1 + danger.TIGHTEN_PCT), 2)
                if new_stop > (pos.stop_price or 0):
                    pos.stop_price = new_stop
                    pos.custom_levels = True   # survives boot retag
                    entry["action"] = "DANGER_TIGHTEN"
                    entry["new_stop"] = new_stop
                else:
                    entry["action"] = "DANGER_ALERT"
                    entry["why"] += " (existing stop already tighter)"
            elif reading.action == "EXIT_HALF":
                self._exit_half(ticker, price, danger.describe(reading, auto),
                                now)
                entry["action"] = "DANGER_EXIT_HALF"
            self.portfolio.decision_journal.append(entry)
        self.portfolio.save()

    def _rotation_exits(self, picks: list, now: datetime) -> list:
        """OWNER ORDER 2026-09-04 ("skipper exit suggestion is there but not
        executed — make it happen"): the F6 rotation suggester EXECUTES.

        The suggester's own rule, unchanged: a position >= rotation_min_progress
        of the way to its target has little upside left, so if a fresh pick
        cannot be funded from cash, that position is sold to free the capital
        and the opening loop that follows funds the pick.

        Guards, each with its reason:
          * only when cash is actually SHORT for the fresh picks — otherwise
            the +20% target handles the near-target name, and selling early
            just gives up its last stretch for nothing;
          * only as many rotations as the shortfall needs, highest progress
            first — three near-target names must never be sold to fund one;
          * owner_pick positions are exempt, the same rule the score-decay
            exit follows: the owner's own picks are theirs to sell;
          * a LIVE quote is fetched for every candidate, and a name with none
            is left alone and journaled — never an invented fill (the
            2026-08-04 rule). The suggester reads progress off the live quote,
            so an unquoted name cannot even be suggested.
        Returns the tickers rotated out.
        """
        cfg = self.cfg
        if not bool(getattr(cfg, "rotation_auto_enabled", True)):
            return []
        held = self.portfolio.positions
        fresh = [pk for pk in (picks or [])
                 if pk.get("ticker") and pk["ticker"] not in held]
        if not held:
            return []

        # OWNER ORDER 2026-09-10, answering why this never fired: "Rotation is
        # combinaton of a & b, but for b there is simple trigger point when
        # cash is below 5%, rest all the time rotaiton will be done as per a-
        # adding & rotating from watch list".
        #
        # WHAT WAS WRONG. The 2026-09-04 cut required BOTH a funded scan pick
        # AND a cash shortfall. On this book the breadth band and the score
        # floor mean most days produce zero picks, and cash is plentiful — so
        # both conditions were false and the branch returned at its first line
        # every single day. Worse, the dashboard card calls
        # rotation_suggestions() directly against the WATCHLIST, so the screen
        # could say "rotate X" while the engine, needing something else
        # entirely, did nothing. That disagreement is what the owner saw.
        #
        # Now there are two independent triggers:
        #   (a) ALWAYS — a near-target name rotates into the best watchlist
        #       candidate, whether or not cash is short. This is the one that
        #       makes the card and the engine agree.
        #   (b) CASH BELOW 5% — a hard trigger: when the book is nearly fully
        #       deployed, rotate enough to fund what is waiting.
        from .sizing import position_notional_for
        eq = self.portfolio.cash + sum(
            float(p.qty) * float(p.entry_price) for p in held.values())
        reserve = float(getattr(cfg, "cash_reserve_pct", 0.0) or 0.0)
        spendable = self.portfolio.cash - eq * reserve
        need = 0.0
        for pk in fresh:
            if pk.get("ocean"):
                need += float(getattr(cfg, "ocean_position_value", 0) or 0)
            else:
                need += float(position_notional_for(
                    cfg, eq, score=pk.get("score"), close=None)[0] or 0)
        shortfall = need - spendable

        # (b) the owner's cash trigger, independent of whether a pick exists.
        cash_floor = float(getattr(cfg, "rotation_cash_trigger_pct", 0.05) or 0.0)
        cash_low = eq > 0 and (self.portfolio.cash / eq) < cash_floor

        # (a) the watchlist candidates, used whenever the scan produced no
        # funded pick of its own. These are scored, not-held names — the same
        # rows the dashboard card reads, so the two can no longer disagree.
        if not fresh:
            fresh = self._watchlist_candidates(held)
            if not fresh:
                return []
            # Rotation into a watchlist name is one-for-one: free exactly what
            # the best candidate needs, never a fire sale.
            need = float(position_notional_for(
                cfg, eq, score=fresh[0].get("score"), close=None)[0] or 0)
            shortfall = need - spendable

        if shortfall <= 0 and not cash_low:
            return []
        if cash_low and shortfall <= 0:
            # Cash is under the floor but the waiting pick is affordable.
            # Free one ticket's worth so the book is not left with nothing to
            # act on tomorrow.
            shortfall = need if need > 0 else float(
                position_notional_for(cfg, eq, score=fresh[0].get("score"),
                                      close=None)[0] or 0)
            self._journal_once_a_day("ROTATION_CASH_TRIGGER", now, {
                "cash": round(self.portfolio.cash, 2),
                "cash_pct": round(self.portfolio.cash / eq * 100, 2),
                "floor_pct": round(cash_floor * 100, 2),
                "why": (f"Cash is {self.portfolio.cash / eq * 100:.1f}% of the "
                        f"book, under the {cash_floor * 100:.0f}% floor — "
                        f"owner's rotation trigger (2026-09-10)."),
            })

        candidates = [t for t, p in held.items()
                      if "owner_pick" not in (getattr(p, "tags", None) or [])]
        if not candidates:
            return []
        try:
            live = routed_fetch_quotes(candidates, upstox_client=self.broker) or {}
        except Exception as e:
            log.warning("rotation: live quotes unavailable: %s", e)
            live = {}
        unquoted = sorted(t for t in candidates
                          if not live.get(t) or float(live.get(t) or 0) <= 0)
        if unquoted:
            self.portfolio.decision_journal.append({
                "ts": now.isoformat(), "action": "ROTATION_HELD_NO_QUOTE",
                "tickers": unquoted,
                "why": (f"Rotation could not assess {', '.join(unquoted)}: no "
                        f"live quote, so no invented fill. Cash short by "
                        f"₹{shortfall:,.0f} for {len(fresh)} fresh pick(s)."),
            })

        from .rotation import rotation_suggestions
        min_prog = float(getattr(cfg, "rotation_min_progress", 0.90) or 0.90)
        suggestions = rotation_suggestions(
            {t: held[t] for t in candidates}, live, fresh, cfg,
            min_progress=min_prog)

        rotated: list = []
        freed = 0.0
        for s in suggestions:
            if freed >= shortfall:
                break
            t = s["ticker"]
            px = float(live.get(t) or 0)
            if px <= 0 or t not in held:
                continue
            value = float(held[t].qty) * px
            self._close(t, px, "ROTATION_EXIT", now)
            freed += value
            rotated.append(t)
            self.portfolio.decision_journal.append({
                "ts": now.isoformat(), "action": "ROTATION_EXIT", "ticker": t,
                "price": round(px, 2), "progress_pct": s["progress_pct"],
                "target": s["target"], "freed": round(value, 2),
                "fund_ticker": s.get("fund_ticker"),
                "fund_score": s.get("fund_score"),
                "why": (f"Rotated out {t} at ₹{px:,.2f} — {s['progress_pct']}% "
                        f"of the way to its ₹{s['target']:,.2f} target — to "
                        f"free ₹{value:,.0f} for {s.get('fund_ticker') or 'a fresh pick'}"
                        f" (score {s.get('fund_score')}). Cash was short by "
                        f"₹{shortfall:,.0f}; ₹{freed:,.0f} freed so far. "
                        f"Owner order 2026-09-04 — the suggester executes."),
                "cash_after": round(self.portfolio.cash, 2),
            })
        return rotated

    def _exit_half(self, ticker: str, raw_price: float, reason: str,
                   now: datetime) -> None:
        """Sell half the position at the live price (full close if qty==1).
        Same slippage/cost model as _close; the sold half becomes a real
        ClosedTrade — nothing vanishes from the record."""
        pos = self.portfolio.positions[ticker]
        if pos.qty <= 1:
            self._close(ticker, raw_price, reason, now)
            return
        half = pos.qty // 2
        exit_price = slippage_price(raw_price, "SELL", self.cfg)
        gross = (exit_price - pos.entry_price) * half
        costs = trade_costs(half, pos.entry_price, exit_price, self.cfg)
        self.portfolio.cash += half * exit_price - costs
        self.portfolio.trades.append(ClosedTrade(
            ticker=ticker, qty=half,
            entry_date=pos.entry_date, exit_date=now,
            entry_price=pos.entry_price, exit_price=exit_price,
            gross_pnl=gross, costs=costs, net_pnl=gross - costs,
            reason=reason, sector=pos.sector,
            entry_score=pos.entry_score, reasoning=pos.reasoning,
            entry_notes=getattr(pos, "entry_notes", "") or "",
            tags=list(getattr(pos, "tags", []) or []),
        ))
        pos.qty -= half

    def _close(self, ticker: str, raw_price: float, reason: str, now: datetime) -> None:
        pos = self.portfolio.positions.pop(ticker)
        exit_price = slippage_price(raw_price, "SELL", self.cfg)
        gross = (exit_price - pos.entry_price) * pos.qty
        costs = trade_costs(pos.qty, pos.entry_price, exit_price, self.cfg)
        self.portfolio.cash += pos.qty * exit_price - costs
        net_pnl = gross - costs
        self.portfolio.trades.append(ClosedTrade(
            ticker=ticker, qty=pos.qty,
            entry_date=pos.entry_date, exit_date=now,
            entry_price=pos.entry_price, exit_price=exit_price,
            gross_pnl=gross, costs=costs, net_pnl=net_pnl,
            reason=reason, sector=pos.sector,
            entry_score=pos.entry_score, reasoning=pos.reasoning,
            entry_notes=getattr(pos, "entry_notes", "") or "",
            tags=list(getattr(pos, "tags", []) or []),
        ))
        # WHY-SOLD journal entry — full exit context, never deleted
        hold_days = (now.date() - pos.entry_date.date()).days
        self.portfolio.decision_journal.append({
            "ts": now.isoformat(),
            "action": "SELL",
            "ticker": ticker,
            "qty": pos.qty,
            "entry_price": round(pos.entry_price, 2),
            "exit_price": round(exit_price, 2),
            "net_pnl": round(net_pnl, 2),
            "net_pct": round((exit_price / pos.entry_price - 1) * 100, 2) if pos.entry_price else 0,
            "hold_days": hold_days,
            "exit_reason": reason,
            "peak_price": round(pos.peak_price, 2),
            "gave_back_from_peak_pct": round((exit_price / pos.peak_price - 1) * 100, 2) if pos.peak_price else 0,
            "regime": self.portfolio.last_regime,
            "why_bought_was": pos.reasoning,
            "entry_notes": getattr(pos, "entry_notes", "") or "",
            "cash_after": round(self.portfolio.cash, 2),
        })
        # v5: feed the consecutive-loss tracker
        try:
            self.loss_tracker.record_close(now.date(), net_pnl)
            # AUDIT FIX C11: persist so a restart can't forget the halt
            self.portfolio.loss_day = (self.loss_tracker.today.isoformat()
                                       if self.loss_tracker.today else None)
            self.portfolio.loss_count = int(self.loss_tracker.losses_today)
        except AttributeError:
            pass  # backward compat if engine was built before risk_v2

    def _breadth_gate_blocks(self, history: dict, now: datetime,
                             n_picks: int = 0, bypass: bool = False) -> bool:
        """A3 breadth gate (owner-ordered wire 2026-07-27; RE-CUT 2026-09-09).

        Blocks new momentum entries while breadth sits in the measured DEAD
        BAND (cfg.breadth_block_low..high, 40-65%). It is not a "weak market"
        gate any more: measured on the engine's own picks, a washed-out tape
        (< 40% above their 200DMA) was one of the BEST states to buy, and the
        old "block at <= 50%" rule blocked the better days on the fitted half
        and was worth +0.02pp on the holdout, i.e. nothing. See the ENTRY
        BREADTH BAND note in config.py for the numbers and the holdout.

        Exits are never touched. Momentum mode only — the other modes' history
        windows are too short for a 200DMA and the study only covered
        momentum. Fail-open on thin samples or errors, so a data hiccup can
        never freeze buying by accident.
        """
        cfg = self.cfg
        # Owner decision 2026-07-27 ("yes — we cant wait for recovery, that
        # is movement of entries"): when the VIX-crush re-entry fires, its
        # entries BYPASS the breadth gate. At the exact turn of a crash,
        # breadth is always still <=50% — stacking the two gates would have
        # blocked the v4-tested +24% COVID rebound entirely. Breadth still
        # guards every normal bull/neutral day.
        if bypass:
            return False
        if cfg.mode != "nifty200_momentum" or not getattr(
                cfg, "breadth_gate_enabled", False):
            return False
        try:
            # F3 FIX (owner order 2026-08-20: "fix F3"): PREFER the
            # full-store reading (~2,700 names, ~±1pp sampling noise,
            # computed by scheduler.refresh_breadth_store from one streamed
            # Mongo query) over the scan's own ~50-name bucket (±7pp — the
            # market sat at 50.8% vs the <=50% line and buy-or-block was
            # decided by WHICH names the rotation served that day). The
            # stored reading is trusted only while its closes are fresh
            # (cfg.breadth_store_max_age_days); otherwise this degrades to
            # exactly the pre-fix bucket behaviour, never something new.
            from .breadth import is_fresh
            breadth = sample = None
            source = ""
            stored = getattr(self.portfolio, "breadth_store", None) or {}
            if is_fresh(stored, now.date(),
                        getattr(cfg, "breadth_store_max_age_days", 10)):
                breadth = float(stored["pct"])
                sample = int(stored["sample"])
                source = (f"full store ({sample} names, closes to "
                          f"{stored.get('asof')})")
            else:
                from .regime import compute_breadth
                breadth, sample = compute_breadth(history)
                source = f"scan bucket sample ({sample} names, ±7pp noise)"
            lo = float(getattr(cfg, "breadth_block_low", 0.40))
            hi = float(getattr(cfg, "breadth_block_high", 0.65))
            if (breadth is not None
                    and sample >= getattr(cfg, "breadth_min_sample", 30)
                    and lo <= breadth < hi):
                self.portfolio.decision_journal.append({
                    "ts": now.isoformat(), "action": "BREADTH_BLOCKED",
                    "why": (f"Breadth gate: {breadth*100:.1f}% above their "
                            f"200DMA sits in the measured dead band "
                            f"{lo*100:.0f}-{hi*100:.0f}% — {source}. "
                            f"{n_picks} pick(s) held back; exits unaffected. "
                            f"(Below {lo*100:.0f}% the tape is washed out and "
                            f"entries are ALLOWED — re-cut 2026-09-09.)"),
                })
                return True
        except Exception as e:
            log.warning("breadth gate failed (fail-open): %s", e)
        return False

    def _ocean_journal_once(self, now: datetime, action: str, why: str) -> None:
        today_iso = now.date().isoformat()
        if not any(e.get("action") == action
                   and str(e.get("ts", "")).startswith(today_iso)
                   for e in self.portfolio.decision_journal[-60:]):
            self.portfolio.decision_journal.append(
                {"ts": now.isoformat(), "action": action, "why": why})

    # --- market-hours gate (owner order 2026-09-10) -----------------------
    def _trading_allowed(self, now: Optional[datetime] = None) -> bool:
        """Can a position be opened or closed RIGHT NOW?

        One predicate, one place. `cfg.market_hours_only` exists so tests and
        backtests (which replay closed days by definition) are not gated, and
        so the owner can switch the rule off from Settings if they ever want
        the old behaviour back — but it defaults ON per their order.
        """
        if not bool(getattr(self.cfg, "market_hours_only", True)):
            return True
        return is_market_open(now)

    def _journal_once_a_day(self, action: str, now: datetime,
                            fields: dict) -> None:
        """Append `action` at most once per calendar day.

        The window is the last 60 entries, the same bound _ocean_journal_once
        uses. NOTE for future tests: a repeated same-day run against a
        persistent state dir will therefore SKIP the second append — count
        only your own test's appends, never a tail slice of the journal (the
        2026-08-24 fault class).
        """
        today_iso = now.date().isoformat()
        if any(e.get("action") == action
               and str(e.get("ts", "")).startswith(today_iso)
               for e in self.portfolio.decision_journal[-60:]):
            return
        entry = {"ts": now.isoformat(), "action": action}
        entry.update(fields)
        self.portfolio.decision_journal.append(entry)

    def _ocean_picks(self, regime, history: dict, universe, now: datetime) -> List[dict]:
        """Ocean dip candidates behind the owner-approved crash guard
        (2026-08-26: "Approve as specced", max 8
        slots, four rails). Runs in PARALLEL with Everest in every regime
        — the market works both ways (owner's words); Ocean's own
        selection (30% under the 200DMA, weekly RSI < 25, climax volume,
        fundamentals survival) makes candidates rare outside sell-offs.

        The 2026-07-27 park is superseded by the approval. What replaced
        the missing guard, all evidence-traced (engines/ocean.py):
        R1 VIX knife (v4's measured VIX-crush is the only tested dip
        entry), R2 the owner's own 0.213 damage cluster on the canonical
        store, R3 per-stock reclaim (no fresh 20d low, close above prior
        high), R4 the 12 VERIFIED danger tripwires as a veto. Every block
        journals its rail; unknowable = blocked, never assumed safe.
        """
        cfg = self.cfg
        if not getattr(cfg, "ocean_enabled", False):
            return []
        try:
            from .engines.ocean import (crash_guard_market, crash_guard_stock,
                                        scan as ocean_scan)
            # Cap: at most ocean_max_positions concurrent Ocean holdings.
            max_slots = int(getattr(cfg, "ocean_max_positions", 8) or 8)
            open_ocean = sum(1 for p in self.portfolio.positions.values()
                             if "ocean" in (getattr(p, "tags", None) or []))
            free = max_slots - open_ocean
            if free <= 0:
                return []
            hist_map = {t: history[t] for t in universe if t in history}
            candidates = ocean_scan(hist_map, cfg)
            if not candidates:
                return []
            # Rails 1+2 — market level, once per scan.
            vix = None
            try:
                vixd = fetch_daily(["^INDIAVIX"], period="1y").get("^INDIAVIX")
                if vixd is not None and not vixd.empty:
                    vix = vixd["Close"]
            except Exception:
                pass
            ok_mkt, why_mkt = crash_guard_market(
                vix, self.portfolio.damage_store)
            if not ok_mkt:
                self._ocean_journal_once(
                    now, "OCEAN_BLOCKED",
                    f"{len(candidates)} Ocean candidate(s) but the market "
                    f"rail said no: {why_mkt}. A dip engine must prove the "
                    "fall is exhausted — it never assumes.")
                return []
            picks: List[dict] = []
            blocked: list[str] = []
            from . import danger
            for c in candidates[:free * 2]:      # bounded work per scan
                df = hist_map.get(c.ticker)
                try:
                    fired = danger.evaluate_stock(df)
                except Exception:
                    fired = ["danger_unverifiable"]
                ok_stk, why_stk = crash_guard_stock(df, fired)
                if not ok_stk:
                    blocked.append(f"{c.ticker}:{why_stk}")
                    continue
                picks.append({
                    "ticker": c.ticker,
                    "score": 100 + c.pct_below_200dma,  # deepest first
                    "components": {},
                    "timing_pass": True,
                    "timing_diag": {"ocean": True},
                    "reasoning": (
                        f"OCEAN: {c.pct_below_200dma:.1f}% off 200DMA, "
                        f"weekly RSI {c.weekly_rsi:.1f}, "
                        f"climax vol {c.climax_volume_ratio:.1f}x; rails ok "
                        f"({why_mkt})"
                    ),
                    "last_close": c.price,
                    "ocean": True,
                })
                if len(picks) >= free:
                    break
            if blocked:
                self._ocean_journal_once(
                    now, "OCEAN_STOCK_BLOCKED",
                    "Ocean per-stock rails blocked: " + " · ".join(blocked))
            return picks
        except Exception:
            log.exception("ocean picks failed")
            return []   # Ocean is bonus; never block the Everest path

    def _roll_risk_day(self, current_equity: float, now: datetime) -> None:
        """Re-baseline the daily loss halt once per trading day.

        RiskState.__post_init__ pins day_start_equity to starting_capital and
        RiskState.roll_day() was only ever called from backtest.py — so in the
        live app the "-2% daily loss halt" measured against the original
        forever. A -2% *cumulative* drawdown therefore tripped the kill switch
        and force-closed every position.

        The baseline for a new day is yesterday's last mark (the previous
        equity_history point), not today's — today's move is exactly what the
        halt is supposed to measure. Both the date and the baseline live on the
        portfolio so a mid-day restart restores them instead of starting over.
        """
        p = self.portfolio
        # `now` is UTC (refresh uses datetime.utcnow()). IST is UTC+5:30, so the
        # two dates only diverge between 00:00 and 05:30 IST — outside market
        # hours and outside every scheduled job (09:20 / 14:30 / 15:25 / 15:35
        # IST all land on the same UTC date). Safe as written.
        today = now.date().isoformat()

        if p.risk_day == today:
            # Same day, possibly after a restart: restore the stored baseline
            # AND the tripped flag — otherwise a mid-day restart on a halted
            # day would buy again at catch-up, re-trip at 15:35, and churn.
            if p.risk_day_equity > 0:
                self.risk.day_start_equity = p.risk_day_equity
            if getattr(p, "risk_day_killed", False):
                self.risk.kill_switch = True
            return

        prior_eq = None
        if p.equity_history:
            try:
                prior_eq = float(p.equity_history[-1][1])
            except (TypeError, ValueError, IndexError):
                prior_eq = None
        baseline = prior_eq if prior_eq and prior_eq > 0 else current_equity

        self.risk.roll_day(baseline)
        p.risk_day = today
        p.risk_day_equity = baseline
        p.risk_day_killed = False
        p.decision_journal.append({
            "ts": now.isoformat(), "action": "RISK_DAY_ROLL",
            "why": (f"Daily loss baseline set to Rs {baseline:,.0f} for {today}. "
                    f"Kill switch re-armed at "
                    f"{self.cfg.daily_loss_halt_pct * 100:.1f}% of that."),
        })

    def _record_equity(self, prices: Dict[str, float], now: datetime) -> None:
        eq = self.portfolio.equity(prices)
        self.portfolio.equity_history.append((now.isoformat(), eq))
        if len(self.portfolio.equity_history) > 50_000:
            self.portfolio.equity_history = self.portfolio.equity_history[-50_000:]

    def _summary_dict(self, prices: Dict[str, float]) -> dict:
        eq = self.portfolio.equity(prices)
        deployed = self.portfolio.deployed_value(prices)
        return {
            "equity": eq,
            "cash": self.portfolio.cash,
            "deployed": deployed,
            "open_positions": len(self.portfolio.positions),
            "closed_trades": len(self.portfolio.trades),
            "return_pct": (eq / self.cfg.starting_capital - 1) * 100,
            "kill_switch": self.risk.kill_switch,
        }
