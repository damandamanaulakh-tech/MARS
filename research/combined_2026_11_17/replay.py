"""Offline adapter for TradingEngine.scan/refresh, not a second strategy.

Only I/O, clock, notification delivery and persistence are replaced. Scores,
timing, sizing, gates, rotation, exits and accounting execute production code.
Unavailable historical providers follow the existing missing-input policy and
are disclosed; this is NOT a reconstruction of all 1,205 production names.
"""
from collections import Counter
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime
import importlib
import itertools
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import pandas as pd
from freezegun import freeze_time

from trading import engine as E, data, intraday_bars as ib, intraday_data, quote_evidence as qe
from trading.config import Config
from trading.paper import Portfolio
from trading.validation import MODES, performance, settings

MISSING = ["point_in_time_news", "earnings_growth", "analyst_revisions", "futures_oi",
           "FII_DII_publication_times", "policy_announcements", "sector_identity_and_indices",
           "historical_valuations", "historical_bid_ask", "full_universe_and_delisted_names",
           "provider_adjustment_certification", "corporate_action_announcement_times",
           "event_calendar_publication_lineage"]


class Inputs:
    def __init__(self, daily, intraday):
        self.daily = daily
        self.intraday = intraday
        self.now = None
        self.coverage = Counter()
        self.network_attempts = []

    def fetch_daily(self, tickers, **kwargs):
        # The session's final OHLCV is NEVER available during its morning.
        day = self.now.tz_convert(ib.IST).date()
        out = {}
        for ticker in tickers:
            frame = self.daily.get(ticker)
            self.coverage["daily_requested"] += 1
            if frame is not None:
                frame = frame.loc[frame.index.date < day].copy()
                frame.attrs.update(interval="1d", source="frozen_upstox_daily")
                if not frame.empty:
                    out[ticker] = frame
                    self.coverage["daily_supplied"] += 1
        data.LAST_FETCH_STATS.clear()
        data.LAST_FETCH_STATS.update(asked=len(tickers), covered=len(out), from_store=len(out))
        return out

    def fetch_intraday(self, tickers, cfg, client=None, now=None, **kwargs):
        out, details = {}, {}
        for ticker in tickers:
            self.coverage["intraday_requested"] += 1
            frame = self.intraday.get(ticker)
            if frame is not None:
                # A source can contain future rows. The strategy cannot see them.
                frame = frame.loc[frame.index + pd.Timedelta(minutes=5) <= self.now].copy()
                frame.attrs.update(interval="5m", source="frozen_upstox_v3")
            clean, diag = ib.validate(frame, self.now.to_pydatetime(), min_bars=ib.required_bars(cfg))
            details[ticker] = diag
            self.coverage["intraday_" + diag["reason"]] += 1
            if diag["status"] == "ready":
                out[ticker] = clean
                self.coverage["intraday_supplied"] += 1
        return out, details

    def quotes(self, tickers, **kwargs):
        # At a scheduled boundary the new bar's OPEN is observable. Its high,
        # low and close are not. Missing boundary quotes cannot fill any order.
        # Explicit research provenance; never relabelled as a real Upstox tick.
        out = qe.QuoteBatch()
        for ticker in tickers:
            self.coverage["quotes_requested"] += 1
            frame = self.intraday.get(ticker)
            if frame is not None and self.now in frame.index:
                value = float(frame.loc[self.now, "Open"])
                if value > 0:
                    out[ticker] = value
                    out.evidence[ticker] = qe.make(value, "replay_boundary_open", self.now.to_pydatetime(),
                                                   provider_at=self.now.to_pydatetime(), interval="bar_open")
                    self.coverage["quotes_supplied"] += 1
        return out

    def no_network(self, *args, **kwargs):
        self.network_attempts.append("network_request_refused")
        raise RuntimeError("Offline replay attempted network access")


def configured(saved, universe, slippage_bps):
    cfg = Config()
    for key, value in saved.items():
        if key == "state_dir" or not hasattr(cfg, key):
            continue
        old = getattr(cfg, key)
        if isinstance(old, tuple) and isinstance(value, list):
            value = tuple(value)
        setattr(cfg, key, deepcopy(value))
    cfg.apply_mode_preset()
    cfg.disable_exits = False  # same unconditional bootstrap policy as web/state
    cfg.custom_universe = tuple(universe)
    cfg.slippage_bps = slippage_bps
    return cfg


def adapters(stack, inputs):
    # Replace ONLY external providers. No patched scores, timing or decisions.
    stack.enter_context(patch.object(data, "fetch_daily", inputs.fetch_daily))
    stack.enter_context(patch.object(E, "fetch_daily", inputs.fetch_daily))
    stack.enter_context(patch.object(E, "routed_fetch_quotes", inputs.quotes))
    stack.enter_context(patch.object(intraday_data, "fetch", inputs.fetch_intraday))
    missing_providers = {
        "trading.scoring.fetch_earnings_growth": None,
        "trading.signals.analysts.fetch_analyst_score": None,
        "trading.signals.news.fetch_sentiment": SimpleNamespace(score=50.0, reason="historical_input_unknown"),
        "trading.signals.futures_oi.score_short_interest": 50.0,
        "trading.signals.policy.active_sector_boosts": {},
        "trading.signals.flows.get_flows": None,
        "trading.sector_identity.snapshot": {},
        "trading.context_inputs.daily_sectors": {},
        "trading.valuation_exit.records_for": {},
        "trading.valuation_exit.level": None,
        "trading.danger.externals_for": None,
        "trading.alerts.send_slack": None,
    }
    for target, value in missing_providers.items():
        stack.enter_context(patch(target, return_value=value))
    # Side effects never leave the isolated replay. Keep all trade bookkeeping.
    stack.enter_context(patch.object(Portfolio, "save", return_value=True))
    stack.enter_context(patch.dict("os.environ", {"SB_IGNORE_MARKET_HOURS": "0"}))
    for target in ("requests.sessions.Session.request", "httpx.Client.request",
                   "curl_cffi.requests.Session.request"):
        stack.enter_context(patch(target, side_effect=inputs.no_network))
    # Execution adapter recognises ONLY this run's contemporaneous simulated
    # boundary quote. The production engine still checks its normal quote gate.
    real_executable = qe.executable
    def executable(batch, ticker, now=None, **kwargs):
        ev = (getattr(batch, "evidence", {}) or {}).get(ticker, {})
        if ev.get("source") == "replay_boundary_open":
            return (ev.get("provider_at") == inputs.now.isoformat()
                    and 0 < float(batch.get(ticker, 0)) < float("inf"))
        return real_executable(batch, ticker, now, **kwargs)
    stack.enter_context(patch.object(qe, "executable", executable))
    # Stable identities make ledger and run hashes reproducible.
    counter = itertools.count(1)
    for target in ("uuid.uuid4", "trading.fees.uuid4"):
        stack.enter_context(patch(target, side_effect=lambda: UUID(int=next(counter))))


def schedule(day, refresh_minutes=30):
    base = pd.Timestamp(str(day), tz=ib.IST)
    events = {(9*60+20, "scan"), (14*60+30, "scan"), (15*60+25, "square_off")}
    events.update((minute, "refresh") for minute in range(9*60, 16*60)
                  if minute % 60 % refresh_minutes == 0 and 9*60+15 <= minute < 15*60+30)
    return [(base + pd.Timedelta(minutes=minute), action)
            for minute, action in sorted(events, key=lambda x: (x[0], x[1] != "refresh"))]


def run(daily, intraday, saved, protocol, slippage_bps):
    inputs = Inputs(daily, intraday)
    cfg = configured(saved, protocol["symbols"], slippage_bps)
    portfolio = Portfolio(cfg=cfg, cash=cfg.starting_capital)
    engine = E.TradingEngine(cfg, portfolio)
    decisions, daily_equity, gates = [], [], Counter()
    mode_equity = {mode: [] for mode in MODES}
    from trading.category_budget import share, category_of
    mode_capital = {mode: cfg.starting_capital * (share(cfg, mode) or 0) for mode in MODES}
    days = [d.date() for d in pd.date_range(protocol["start"], protocol["end"])
            if E.ir.is_trading_day(d.date())] if hasattr(E.ir, "is_trading_day") else []
    if not days:
        from trading.calendars import is_trading_day
        days = [d.date() for d in pd.date_range(protocol["start"], protocol["end"]) if is_trading_day(d.date())]
    with tempfile.TemporaryDirectory(prefix="mars-seq11-") as tmp, ExitStack() as stack:
        cfg.state_dir = tmp
        adapters(stack, inputs)
        clock = stack.enter_context(freeze_time(str(days[0]) + " 03:45:00"))
        for day in days:
            for now, action in schedule(day, cfg.intraday_refresh_minutes):
                inputs.now = now.tz_convert("UTC")
                clock.move_to(inputs.now.to_pydatetime())
                if action == "scan":
                    for mode in MODES:
                        cfg.mode = mode
                        cfg.apply_mode_preset()
                        engine.refresh()  # actual rotation checks exits between categories
                        result = engine.scan()
                        for row in result.ranked:
                            if isinstance(row, dict):
                                d = row.get("decision") or {}
                                for name, info in d.get("gates", {}).items():
                                    gates[f"{mode}:{name}:{info.get('state', 'unknown')}"] += 1
                        decisions.append({"at": now.isoformat(), "mode": mode,
                            "ranked": [{"ticker": r["ticker"], "score": round(float(r["score"]), 8),
                                        "timing": r["timing_pass"]} for r in result.ranked],
                            "opened": result.opened, "skipped": result.skipped})
                elif action == "square_off":
                    engine.square_off_intraday()
                else:
                    engine.refresh()
                if inputs.network_attempts:
                    raise RuntimeError("Unexpected network call; replay invalid")
            # End-of-session observed closing marks, not invented fills.
            marks = {}
            for ticker in portfolio.positions:
                frame = intraday.get(ticker)
                end = pd.Timestamp(str(day) + " 15:30", tz=ib.IST).tz_convert("UTC")
                if frame is not None:
                    sub = frame.loc[(frame.index + pd.Timedelta(minutes=5) <= end)
                                    & (frame.index.tz_convert(ib.IST).date == day)]
                    if not sub.empty:
                        marks[ticker] = float(sub["Close"].iloc[-1])
            from trading.portfolio_marks import missing_marks
            known = all(t in marks for t in portfolio.positions)
            daily_equity.append([str(day), portfolio.equity(marks) if known else None])
            for mode in MODES:
                held = [p for p in portfolio.positions.values() if category_of(p) == mode]
                realised = sum(t.net_pnl for t in portfolio.trades if t.mode == mode)
                open_pnl = sum((marks.get(p.ticker, p.entry_price) - p.entry_price) * p.qty
                    - sum(o.get("remaining_fee", 0) for o in p.cost_basis.get("orders", [])
                          if o.get("cash_paid")) for p in held)
                mode_equity[mode].append([str(day), mode_capital[mode] + realised + open_pnl
                                         if all(p.ticker in marks for p in held) else None])
    trades = [t.to_dict() for t in portfolio.trades]
    # Exclude generated provenance from numerical output. Archive actual fills,
    # quantities, dates, reasons, full costs, net P&L and economic trade identity.
    rows = []
    for trade in trades:
        rows.append({k: trade[k] for k in ("ticker", "qty", "entry_date", "exit_date",
            "entry_price", "exit_price", "gross_pnl", "costs", "net_pnl", "reason", "mode", "cost_evidence")}
            | {"trade_id": trade["trade_evidence"]["trade_id"]})
    open_ids = [p.trade_evidence.get("trade_id") for p in portfolio.positions.values()]
    result = performance(rows, daily_equity, cfg.starting_capital, open_ids)
    # A separate estimated liquidation value includes the next sell's costs
    # and slippage, without manufacturing closing trades in the actual replay.
    from trading import fees
    from trading.costs import slippage_price
    liquidation = portfolio.cash
    liquidation_known = all(t in marks for t in portfolio.positions)
    when = pd.Timestamp(protocol["end"] + " 15:30", tz=ib.IST).to_pydatetime()
    for ticker, pos in portfolio.positions.items():
        if ticker in marks:
            px = slippage_price(marks[ticker], "SELL", cfg)
            cost, _, _ = fees.prepare_exit(portfolio, pos, pos.qty, px, when, cfg)
            liquidation += cost["cash_credit"]
            liquidation_known &= cost.get("status") != "unavailable"
    scheduled = performance(rows, portfolio.equity_history, cfg.starting_capital, open_ids)
    result.update(estimated_liquidation_equity=liquidation if liquidation_known else None,
        scheduled_observed_drawdown_pct=scheduled["max_drawdown_pct"],
        scheduled_equity_observations=len(portfolio.equity_history),
        slippage_bps=slippage_bps, coverage=dict(inputs.coverage),
        sessions=len(days), configured_universe=len(saved.get("custom_universe") or cfg.universe),
        tested_universe=len(protocol["symbols"]), missing_inputs=MISSING,
        gates=dict(gates), modes={},
        cash=portfolio.cash, open_positions=[{"ticker": p.ticker, "mode": p.mode,
            "qty": p.qty, "entry_price": p.entry_price, "stop": p.stop_price, "target": p.target_price}
            for p in portfolio.positions.values()])
    for mode in MODES + ("ocean",):
        result["modes"][mode] = performance([t for t in rows if t["mode"] == mode], mode_equity.get(mode, []),
            mode_capital.get(mode, 0), [p.trade_evidence.get("trade_id") for p in portfolio.positions.values() if p.mode == mode])
        result["modes"][mode]["drawdown_basis"] = "initial_segment_capital_plus_net_realised_and_marked_open_PnL; no hypothetical_terminal_sale"
    return {"summary": result, "trades": rows, "equity": daily_equity, "mode_equity": mode_equity, "decisions": decisions}
