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

    def attach_broker(self, broker_client) -> None:
        """Plug in a broker client (e.g. UpstoxClient) for real-time quotes.

        When attached, refresh() routes quote lookups through the broker first
        and falls back to yfinance for any unknown instruments. Passing None
        reverts to pure yfinance behaviour.
        """
        self.broker = broker_client

    # ---------- public API ----------

    def scan(self, history_period: str = "6mo") -> ScanResult:
        cfg = self.cfg
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
                if getattr(cfg, "full_universe_scan", False):
                    from .universes import (NIFTY_200_TICKERS,
                                            load_full_universe,
                                            rotating_bucket)
                    full = load_full_universe()
                    if full:
                        bucket = rotating_bucket(
                            full, 50, datetime.utcnow().date().toordinal())
                    else:
                        bucket = NIFTY_200_TICKERS
                    held = list(self.portfolio.positions.keys())
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
        history = fetch_daily(universe, period=history_period)
        bench = fetch_daily([cfg.benchmark], period=history_period).get(cfg.benchmark)

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
            log.warning("Regime classification failed, proceeding neutral: %s", e)

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
            flows = get_flows()   # live NSE -> committed CSV fallback
            fii_5d = fii_net_last_n_days(flows, n=5)
            fii_score = fii_flow_score(fii_5d)
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
        if regime is None or regime.everest_active or vix_reentry:
            everest_picks = [
                r for r in ranked
                if r["score"] >= cfg.min_score_to_enter and r["timing_pass"]
                and _abs_mom_ok(r["ticker"])
            ]

        # Ocean: only fires in correction/panic, and only when the owner has
        # switched it on. See _ocean_picks().
        ocean_picks = self._ocean_picks(regime, history, universe, now)

        picks = everest_picks + ocean_picks

        # A3 breadth gate (owner-ordered wire, 2026-07-27; preregistered
        # evidence docs/breadth_gate_results.json): when half the panel is
        # below its own 200DMA, relative rank is picking least-bad fallers —
        # block ALL new entries this scan. Exits are never touched. Fail-open
        # when the sample is too thin (short-history modes, sparse bucket).
        if picks and self._breadth_gate_blocks(history, now, n_picks=len(picks),
                                               bypass=vix_reentry):
            picks = []

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
                    "why": ifr_snapshot.reason,
                })
        except Exception:
            pass

        # v5: Update drawdown governor with current equity
        current_equity = self.portfolio.equity(prices)
        prev_dd_level = self.drawdown_gov.active_level
        self.drawdown_gov.update(current_equity)
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
        from .risk_v2 import (
            correlation_gate_blocks, daily_returns,
            passes_liquidity, vol_adjusted_qty,
        )
        open_returns = {}
        for held in self.portfolio.positions:
            if held in history:
                r = daily_returns(history[held]["Close"])
                if r is not None:
                    open_returns[held] = r

        # v5: per-ticker earnings/USFDA blackout — 3 days before known event
        try:
            from .calendars import is_ticker_event_within
        except ImportError:
            is_ticker_event_within = None

        for pick in picks:
            if binary_event is not None:
                skipped.append({
                    "ticker": pick["ticker"],
                    "reason": f"halt_pre_{binary_event.event_type}_{binary_event.event_date}",
                })
                continue
            # v5: ticker-specific event blackout (earnings, USFDA)
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
                # fails from cloud IPs. ADV + rupee_volume floor already
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
                ok_candle, candle_why = candle_confirmation(tic_hist, cfg)
                if not ok_candle:
                    skipped.append({
                        "ticker": pick["ticker"],
                        "reason": f"candle_wait_{candle_why}",
                    })
                    continue
            fill = slippage_price(pick["last_close"], "BUY", cfg)
            current_eq = self.portfolio.equity(prices)
            ok, reason, qty = can_open(
                self.risk, self.portfolio.cash, current_eq,
                self.portfolio.positions, pick["ticker"], fill, cfg,
            )
            # v7: size throttle = MIN of (strength, IFR) instead of product.
            # Previously we multiplied them, which compounded to 0.25-0.35x
            # and zeroed qty on expensive stocks. The throttles are designed
            # as alternative defensive measures, not stacking damages — take
            # whichever is more restrictive at any one time.
            if ok:
                s = pick.get("score", 0)
                strength_mult = 1.0 if s >= 75 else 0.7 if s >= 60 else 0.5
                ifr_mult = ifr_snapshot.sizing_multiplier if ifr_snapshot else 1.0
                throttle = min(strength_mult, ifr_mult)
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
                        "reason": f"throttle_zero_qty (strength={strength_mult}, ifr={ifr_mult})",
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
            px = prices.get(t) or self.portfolio.positions[t].entry_price
            self._close(t, px, "SQUARE_OFF", now)
            closed.append(t)
        if closed:
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
        history = fetch_daily(tickers, period="6mo")
        bench = fetch_daily([cfg.benchmark], period="6mo").get(cfg.benchmark)

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
        for ticker in list(self.portfolio.positions.keys()):
            pos = self.portfolio.positions[ticker]
            price = prices.get(ticker, pos.entry_price)
            # Recompute the score for score-decay check
            current_score = pos.entry_score
            if ticker in history:
                current_score, _ = compute_score(ticker, history[ticker], bench, history, cfg)
            should_exit, reason = check_exit(pos, price, current_score, cfg, now)
            if should_exit:
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
        if getattr(cfg, "pyramid_enabled", False):
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
        # disabled (buy-and-hold).
        if self.risk.kill_switch and not getattr(cfg, "disable_exits", False):
            for ticker in list(self.portfolio.positions.keys()):
                price = prices.get(ticker, self.portfolio.positions[ticker].entry_price)
                self._close(ticker, price, "KILL_SWITCH", now)
                exits.append(ticker)

        # P1/F3: drawdown-ladder crash brake. forced_cut was computed but never
        # executed anywhere — wire it: sell the worst positions until the cut
        # fraction is met. Runs EVEN in hold mode (buy-and-hold still needs a
        # crash exit; -10% peak-to-trough = cut half, -15% = 80%, -20% = all).
        self.drawdown_gov.update(self.portfolio.equity(prices))
        cut = self.drawdown_gov.forced_cut
        if cut > 0 and self.portfolio.positions:
            by_pnl = sorted(
                self.portfolio.positions.keys(),
                key=lambda t: (prices.get(t, self.portfolio.positions[t].entry_price)
                               / self.portfolio.positions[t].entry_price),
            )
            n_close = max(1, int(len(by_pnl) * cut))
            for ticker in by_pnl[:n_close]:
                price = prices.get(ticker, self.portfolio.positions[ticker].entry_price)
                self._close(ticker, price, "DRAWDOWN_LADDER", now)
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
            # P2: config snapshot so every decision stays reproducible after
            # settings change later.
            "config_snapshot": {
                "stop_loss_pct": self.cfg.stop_loss_pct,
                "take_profit_pct": self.cfg.take_profit_pct,
                "cash_reserve_pct": self.cfg.cash_reserve_pct,
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
            target_price=round(fill_price * (1 + self.cfg.take_profit_pct), 2),
        )

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
        except AttributeError:
            pass  # backward compat if engine was built before risk_v2

    def _breadth_gate_blocks(self, history: dict, now: datetime,
                             n_picks: int = 0, bypass: bool = False) -> bool:
        """A3 breadth gate (owner-ordered wire, 2026-07-27).

        When half the panel is below its own 200DMA, relative rank is picking
        least-bad fallers — block ALL new entries this scan. Exits are never
        touched. Preregistered evidence: docs/breadth_gate_results.json
        (better in chop, small cost in strong trends). Momentum mode only —
        other modes' history windows are too short for a 200DMA and the study
        only covered momentum. Fail-open on thin samples or errors, so a data
        hiccup can never freeze buying by accident.
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
            from .regime import compute_breadth
            breadth, sample = compute_breadth(history)
            if (breadth is not None
                    and sample >= getattr(cfg, "breadth_min_sample", 30)
                    and breadth <= getattr(cfg, "breadth_min_pct", 0.50)):
                self.portfolio.decision_journal.append({
                    "ts": now.isoformat(), "action": "BREADTH_BLOCKED",
                    "why": (f"Breadth gate: only {breadth*100:.0f}% of "
                            f"{sample} panel tickers above their 200DMA "
                            f"(needs >{cfg.breadth_min_pct*100:.0f}%). "
                            f"{n_picks} pick(s) held back; exits unaffected."),
                })
                return True
        except Exception as e:
            log.warning("breadth gate failed (fail-open): %s", e)
        return False

    def _ocean_picks(self, regime, history: dict, universe, now: datetime) -> List[dict]:
        """Ocean (mean-reversion) candidates — empty unless the owner opted in.

        Ocean has been described as PARKED in CLAUDE.md and docs/PARAM_SEARCH_V4
        since the v3 review (-41% drawdown catching 2020's falling knives), but
        until 2026-07-27 nothing in the code enforced that. The only condition
        was regime.ocean_active, which regime.classify() sets True for
        correction, panic AND neutral. In a correction Everest is switched off,
        so Ocean was the sole engine able to open a position — and its picks are
        handed score = 100 + pct_below_200dma and timing_pass = True, bypassing
        both the score floor and the absolute-momentum gate.

        cfg.ocean_enabled (default False) is that missing park switch. When the
        regime would have activated Ocean and the flag is off, the block is
        journalled as OCEAN_BLOCKED rather than passing silently.
        """
        cfg = self.cfg
        if regime is None or not regime.ocean_active:
            return []

        if not cfg.ocean_enabled:
            # Journal the block only where it MATTERS (Everest off — Ocean was
            # the sole buyer) and once per day, not per scan pass. In neutral
            # Everest buys anyway; 5 spam lines/day forever would just bloat
            # the journal (recheck fix, 2026-07-27).
            today_iso = now.date().isoformat()
            already = any(
                e.get("action") == "OCEAN_BLOCKED"
                and str(e.get("ts", "")).startswith(today_iso)
                for e in self.portfolio.decision_journal[-50:]
            )
            if not regime.everest_active and not already:
                self.portfolio.decision_journal.append({
                    "ts": now.isoformat(), "action": "OCEAN_BLOCKED",
                    "why": (f"Regime {regime.regime} would activate the Ocean "
                            "mean-reversion engine, but it is parked "
                            "(cfg.ocean_enabled=False). No Ocean buys. Turn it "
                            "on in Settings only after the crash guard is "
                            "tested."),
                })
            return []

        picks: List[dict] = []
        try:
            from .engines.ocean import scan as ocean_scan
            hist_map = {t: history[t] for t in universe if t in history}
            for c in ocean_scan(hist_map, cfg):
                picks.append({
                    "ticker": c.ticker,
                    "score": 100 + c.pct_below_200dma,  # deepest = highest priority
                    "components": {},
                    "timing_pass": True,
                    "timing_diag": {"ocean": True},
                    "reasoning": (
                        f"OCEAN: {c.pct_below_200dma:.1f}% off 200DMA, "
                        f"weekly RSI {c.weekly_rsi:.1f}, "
                        f"climax vol {c.climax_volume_ratio:.1f}x"
                    ),
                    "last_close": c.price,
                    "ocean": True,
                })
        except Exception:
            pass   # Ocean is bonus; never block Everest path
        return picks

    def _roll_risk_day(self, current_equity: float, now: datetime) -> None:
        """Re-baseline the daily loss halt once per trading day.

        RiskState.__post_init__ pins day_start_equity to starting_capital and
        RiskState.roll_day() was only ever called from backtest.py — so in the
        live app the "-2% daily loss halt" measured against the original ₹25L
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
