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

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from .config import Config, STUB_COMPONENTS
from .costs import slippage_price, trade_costs
from .data import fetch_daily
from .quote_router import fetch_quotes as routed_fetch_quotes
from .exits import check_exit
from .paper import ClosedTrade, PaperPosition, Portfolio
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
        # v5: nifty200_momentum mode swaps the universe to Nifty 200 and
        # needs 2 years of history for the momentum_score() to be valid.
        if cfg.mode == "nifty200_momentum":
            try:
                from .universes import NIFTY_200_TICKERS
                universe = NIFTY_200_TICKERS
                history_period = "2y"
            except ImportError:
                universe = cfg.universe
        else:
            universe = cfg.universe
        history = fetch_daily(universe, period=history_period)
        bench = fetch_daily([cfg.benchmark], period=history_period).get(cfg.benchmark)

        # v4: regime-aware. If Ocean is the active engine (panic/correction),
        # Everest is dormant. Either way we score everything so the UI shows
        # full ranking; only the entry-filtering changes per engine.
        regime = None
        try:
            from .regime import fetch_regime
            regime = fetch_regime()
        except Exception:
            pass

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

        # v5: regime-aware dynamic cash reserve
        if regime is not None:
            try:
                from .risk_v2 import dynamic_cash_reserve_pct
                cfg.cash_reserve_pct = dynamic_cash_reserve_pct(
                    regime.regime, floor=0.20,
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
                fetch_latest_flows, fii_flow_score, fii_net_last_n_days,
            )
            flows = fetch_latest_flows()
            fii_5d = fii_net_last_n_days(flows, n=5)
            fii_score = fii_flow_score(fii_5d)
        except Exception:
            pass

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
                score = ms
                components = {"nifty200_momentum": ms}
                reasoning = f"NIFTY200_MOMENTUM: 6m+12m sharpe momentum {ms:.1f}/100"
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
        ocean_picks: List[dict] = []

        # Everest: usual momentum + timing rules. Active in bull/neutral.
        if regime is None or regime.everest_active:
            everest_picks = [
                r for r in ranked
                if r["score"] >= cfg.min_score_to_enter and r["timing_pass"]
            ]

        # Ocean: only fires in correction/panic. Different selection logic.
        if regime is not None and regime.ocean_active:
            try:
                from .engines.ocean import scan as ocean_scan
                hist_map = {t: history[t] for t in universe if t in history}
                cands = ocean_scan(hist_map, cfg)
                for c in cands:
                    ocean_picks.append({
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

        picks = everest_picks + ocean_picks

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
                last_open = float(tic_hist["Open"].iloc[-1]) if "Open" in tic_hist else None
                last_high = float(tic_hist["High"].iloc[-1]) if "High" in tic_hist else None
                last_low = float(tic_hist["Low"].iloc[-1]) if "Low" in tic_hist else None
                last_close = float(tic_hist["Close"].iloc[-1])
                # v5: pull live mcap + spread from broker if available
                mcap_cr = None
                spread_pct = None
                try:
                    from .data import fetch_market_cap_cr
                    mcap_cr = fetch_market_cap_cr(pick["ticker"])
                except Exception:
                    pass
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
                # v5: correlation gate
                cand_ret = daily_returns(tic_hist["Close"])
                blocker = correlation_gate_blocks(
                    cand_ret, open_returns, threshold=0.70,
                )
                if blocker is not None:
                    skipped.append({
                        "ticker": pick["ticker"],
                        "reason": f"correlated_{blocker[0]}_{blocker[1]:.2f}",
                    })
                    continue
            fill = slippage_price(pick["last_close"], "BUY", cfg)
            current_eq = self.portfolio.equity(prices)
            ok, reason, qty = can_open(
                self.risk, self.portfolio.cash, current_eq,
                self.portfolio.positions, pick["ticker"], fill, cfg,
            )
            if not ok:
                skipped.append({"ticker": pick["ticker"], "reason": reason})
                continue
            self._open(pick, qty, fill, now)
            opened.append(pick["ticker"])

        self.portfolio.last_scan = now.isoformat()
        self.portfolio.last_scan_picks = [
            {
                "ticker": r["ticker"], "score": r["score"],
                "reasoning": r["reasoning"], "last_close": r["last_close"],
                "timing_pass": r["timing_pass"],
            }
            for r in ranked[:50]
        ]
        self.portfolio.last_scan_skipped = list(skipped)
        self.portfolio.last_scan_opened = list(opened)
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

    def refresh(self) -> dict:
        """Pull latest prices, mark portfolio, fire exits."""
        cfg = self.cfg
        if not self.portfolio.positions:
            prices = {}
            return self._summary_dict(prices)

        tickers = list(self.portfolio.positions.keys())
        prices = routed_fetch_quotes(tickers, upstox_client=self.broker)
        if not prices:
            return self._summary_dict({p.ticker: p.entry_price for p in self.portfolio.positions.values()})

        now = datetime.utcnow()
        history = fetch_daily(tickers, period="6mo")
        bench = fetch_daily([cfg.benchmark], period="6mo").get(cfg.benchmark)

        # Daily kill switch check
        current_eq = self.portfolio.equity(prices)
        self.risk.check_kill_switch(current_eq)

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

        # If kill switch tripped, force-close everything else.
        if self.risk.kill_switch:
            for ticker in list(self.portfolio.positions.keys()):
                price = prices.get(ticker, self.portfolio.positions[ticker].entry_price)
                self._close(ticker, price, "KILL_SWITCH", now)
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
        self.portfolio.positions[pick["ticker"]] = PaperPosition(
            ticker=pick["ticker"], qty=qty, entry_price=fill_price,
            entry_date=now, sector=self.cfg.sector_map.get(pick["ticker"], "Unknown"),
            peak_price=fill_price, entry_score=pick["score"],
            reasoning=pick["reasoning"],
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
        # v5: feed the consecutive-loss tracker
        try:
            self.loss_tracker.record_close(now.date(), net_pnl)
        except AttributeError:
            pass  # backward compat if engine was built before risk_v2

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
