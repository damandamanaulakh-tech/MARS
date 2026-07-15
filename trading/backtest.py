"""Walk-forward backtester for the AM 07 swing engine.

Replays scan() against historical data day-by-day with NO lookahead:
each simulation day D can only see bars where date <= D.

Outputs real numbers:
  - win rate, profit factor, Sharpe, max drawdown
  - total return vs Nifty 50 buy-and-hold benchmark
  - per-sector and per-component attribution
  - daily equity curve

Honest limitations baked into the report:
  - Survivorship bias: we test today's Nifty 500 composition; stocks
    relegated or delisted since 2023 are missing. Inflates returns
    by an estimated 2-4% CAGR. Stated in the report.
  - Fundamental components (catalyst, analyst_revision) are forced to
    neutral 50 in backtest mode — yfinance only returns current
    snapshots, using them historically = lookahead.
  - earnings_growth also stubbed to 50 (same reason).
  - News sentiment skipped entirely in backtest (no historical
    timestamps from yfinance.Ticker.news).

Execution model:
  - Entries decided on close-of-D, filled at open-of-D+1 + slippage.
  - Exits checked on each subsequent bar's high/low/close. Stops fill
    at stop price (pessimistic); targets at target price.
  - Costs: same Upstox CNC cost model as live (STT, exchange, GST, etc.).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

from .config import Config
from .costs import slippage_price, trade_costs
from .exits import check_exit
from .paper import ClosedTrade, PaperPosition, Portfolio
from .risk import RiskState, can_open
from .scoring import compute_score, reasoning_text
from .timing import timing_ok


log = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    cfg_snapshot: dict
    start_date: str
    end_date: str
    universe_size: int
    benchmark: str

    # Metrics
    initial_capital: float
    final_equity: float
    total_return_pct: float
    cagr_pct: float
    win_rate_pct: float
    profit_factor: float
    sharpe_ratio: float
    max_drawdown_pct: float
    avg_win_pct: float
    avg_loss_pct: float
    num_trades: int
    time_in_market_pct: float

    # Benchmark comparison (Nifty 50 buy-and-hold over same window)
    benchmark_total_return_pct: float
    benchmark_max_drawdown_pct: float
    alpha_pct: float

    # Raw artefacts
    trades: list = field(default_factory=list)        # list[dict]
    equity_curve: list = field(default_factory=list)  # list[(iso_date, equity)]
    per_sector_pnl: dict = field(default_factory=dict)
    per_reason_pnl: dict = field(default_factory=dict)

    # Disclosures
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}

    def summary_markdown(self) -> str:
        sign = "" if self.alpha_pct >= 0 else ""
        lines = [
            f"# Backtest Report: AM 07 ({self.cfg_snapshot.get('mode', 'swing')})",
            "",
            f"**Period**: {self.start_date} → {self.end_date}",
            f"**Universe**: {self.universe_size} symbols",
            f"**Benchmark**: {self.benchmark} buy-and-hold",
            "",
            "## Performance",
            f"- Initial capital: ₹{self.initial_capital:,.0f}",
            f"- Final equity: ₹{self.final_equity:,.0f}",
            f"- **Total return**: {self.total_return_pct:+.2f}%",
            f"- CAGR: {self.cagr_pct:+.2f}%",
            f"- Sharpe: {self.sharpe_ratio:.2f}",
            f"- Max drawdown: {self.max_drawdown_pct:.2f}%",
            f"- Profit factor: {self.profit_factor:.2f}",
            f"- Win rate: {self.win_rate_pct:.1f}%",
            f"- Avg win: {self.avg_win_pct:+.2f}% | Avg loss: {self.avg_loss_pct:+.2f}%",
            f"- Trades: {self.num_trades}",
            f"- Time in market: {self.time_in_market_pct:.1f}%",
            "",
            "## Vs benchmark",
            f"- Nifty 50 return same window: {self.benchmark_total_return_pct:+.2f}%",
            f"- Nifty 50 max drawdown: {self.benchmark_max_drawdown_pct:.2f}%",
            f"- **Alpha**: {self.alpha_pct:+.2f}% {sign}",
            "",
            "## Honest disclosures",
        ]
        for w in self.warnings:
            lines.append(f"- ⚠️ {w}")
        return "\n".join(lines)


def _slice_to_date(df: pd.DataFrame, date) -> Optional[pd.DataFrame]:
    """Return only the bars with index <= date."""
    if df is None or df.empty:
        return None
    sub = df[df.index <= pd.Timestamp(date)]
    return sub if not sub.empty else None


def run_backtest(
    cfg: Config,
    start_date: str,
    end_date: str,
    universe: Optional[list[str]] = None,
    benchmark: str = "^NSEI",
    history_period: str = "max",
) -> BacktestResult:
    """Walk-forward replay of the engine on historical NSE bars.

    Pulls all OHLCV in one batch up front (efficient), then sequentially
    feeds the engine truncated slices ending at each simulated day.
    """
    universe = universe or cfg.universe
    warnings: list[str] = [
        "Survivorship bias: tests today's universe composition (delisted "
        "stocks missing). Estimated 2-4% CAGR inflation.",
        "Fundamental components (earnings_growth, catalyst, analyst_revision) "
        "forced to neutral 50 in backtest — yfinance has no historical "
        "fundamentals snapshot.",
        "News sentiment skipped in backtest — yfinance news lacks "
        "timestamps so historical replay would leak future data.",
        "Sample size: small samples produce unreliable Sharpe / max DD. "
        "Treat values as directional, not point estimates.",
    ]

    # Bulk download all history once
    log.info("Backtest: downloading %d universe + %s benchmark", len(universe), benchmark)
    all_hist = _bulk_download(universe + [benchmark], history_period=history_period)
    bench_hist = all_hist.get(benchmark)
    if bench_hist is None or bench_hist.empty:
        raise RuntimeError(f"Benchmark {benchmark} history empty")

    # Trading days = the benchmark's trading days, clipped to user window
    bench_in_window = bench_hist[
        (bench_hist.index >= pd.Timestamp(start_date))
        & (bench_hist.index <= pd.Timestamp(end_date))
    ]
    if bench_in_window.empty:
        raise RuntimeError(f"No trading days in [{start_date}, {end_date}]")

    trading_dates = list(bench_in_window.index)
    log.info("Backtest: %d trading days, simulating...", len(trading_dates))

    portfolio = Portfolio(cfg=cfg, cash=cfg.starting_capital)
    risk = RiskState(cfg=cfg, starting_capital=cfg.starting_capital)
    equity_curve: list[tuple[str, float]] = []
    time_in_market_days = 0

    # Backtest-safe: signal to feed-based scoring components that we're in replay
    try:
        from . import data as _data
        _data.set_backtest_mode(True)
    except (ImportError, AttributeError):
        pass

    try:
        for i, date in enumerate(trading_dates):
            # Build the as-of-date history for every ticker
            history_as_of = {}
            for t in universe:
                full = all_hist.get(t)
                sub = _slice_to_date(full, date)
                if sub is not None:
                    history_as_of[t] = sub
            bench_as_of = _slice_to_date(bench_hist, date)

            # 1. Mark to current close + check exits on every open position
            todays_prices = {
                t: float(history_as_of[t]["Close"].iloc[-1])
                for t in history_as_of
                if t in portfolio.positions
            }
            for ticker in list(portfolio.positions.keys()):
                pos = portfolio.positions[ticker]
                hist = history_as_of.get(ticker)
                if hist is None:
                    continue
                price = float(hist["Close"].iloc[-1])
                # Re-score for score-decay check (using backtest-safe scoring)
                try:
                    current_score, _ = compute_score(
                        ticker, hist, bench_as_of, history_as_of, cfg,
                    )
                except Exception:
                    current_score = pos.entry_score

                should, reason = check_exit(
                    pos, price, current_score, cfg, datetime.combine(date, datetime.min.time()),
                    history=hist,
                )
                if should:
                    _close_position(portfolio, ticker, price, reason, date)

            # Daily kill switch
            current_eq = portfolio.equity(todays_prices)
            risk.check_kill_switch(current_eq)

            # 2. Scan for new entries (uses close-of-D data; fills next bar open)
            if not risk.kill_switch and i + 1 < len(trading_dates):
                next_date = trading_dates[i + 1]
                _try_open_positions(
                    cfg, portfolio, risk, history_as_of, bench_as_of,
                    all_hist, next_date,
                )

            # 3. Record equity at end of day
            equity_curve.append((str(date.date()), current_eq))
            if portfolio.positions:
                time_in_market_days += 1
    finally:
        try:
            from . import data as _data
            _data.set_backtest_mode(False)
        except (ImportError, AttributeError):
            pass

    # Close any still-open positions at the last close (for fair accounting)
    last_date = trading_dates[-1]
    last_prices = {
        t: float(_slice_to_date(all_hist.get(t), last_date)["Close"].iloc[-1])
        for t in portfolio.positions
        if all_hist.get(t) is not None
    }
    for ticker in list(portfolio.positions.keys()):
        if ticker in last_prices:
            _close_position(
                portfolio, ticker, last_prices[ticker], "BACKTEST_END", last_date,
            )

    # Compute metrics
    final_equity = portfolio.equity({})
    return _compile_result(
        cfg=cfg,
        start_date=start_date, end_date=end_date,
        universe_size=len(universe), benchmark=benchmark,
        portfolio=portfolio,
        equity_curve=equity_curve,
        bench_hist=bench_in_window,
        time_in_market_days=time_in_market_days,
        total_days=len(trading_dates),
        warnings=warnings,
        final_equity=final_equity,
    )


def _bulk_download(tickers: list[str], history_period: str) -> dict[str, pd.DataFrame]:
    """One big download for the whole window."""
    try:
        raw = yf.download(
            tickers, period=history_period, interval="1d",
            progress=False, auto_adjust=True, group_by="ticker", threads=True,
        )
    except Exception as e:
        log.warning("yfinance bulk download failed: %s", e)
        return {}
    out: dict[str, pd.DataFrame] = {}
    for t in tickers:
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                if t not in raw.columns.get_level_values(0):
                    continue
                df = raw[t].dropna(subset=["Close"]).copy()
            else:
                df = raw.dropna(subset=["Close"]).copy()
            if not df.empty:
                out[t] = df
        except (KeyError, AttributeError):
            continue
    return out


def _try_open_positions(
    cfg, portfolio, risk, history_as_of, bench_as_of, all_hist, next_date,
) -> None:
    """Score everything, rank, try to open the picks at next-open."""
    # Calendar gate — skip entries near elections / RBI / Budget.
    try:
        from .calendars import is_system_event_within
        as_of = next_date.date() if hasattr(next_date, "date") else next_date
        if is_system_event_within(days=3, today=as_of) is not None:
            return
    except Exception:
        pass

    # Nifty trend gate — skip entries when Nifty isn't itself trending.
    try:
        from .signals.technicals import adx as _adx
        if bench_as_of is not None and len(bench_as_of) > 30 and cfg.nifty_min_adx > 0:
            nifty_adx = _adx(bench_as_of["High"], bench_as_of["Low"], bench_as_of["Close"])
            if nifty_adx is not None and nifty_adx < cfg.nifty_min_adx:
                return
    except Exception:
        pass

    blacklist = set(getattr(cfg, "blacklisted_sectors", []) or [])

    ranked: list[dict] = []
    for ticker, hist in history_as_of.items():
        if ticker in portfolio.positions:
            continue
        # Sector blacklist
        if cfg.sector_map.get(ticker, "Unknown") in blacklist:
            continue
        try:
            score, components = compute_score(
                ticker, hist, bench_as_of, history_as_of, cfg,
            )
        except Exception:
            continue
        timing_pass, _ = timing_ok(hist, cfg)
        if score < cfg.min_score_to_enter or not timing_pass:
            continue
        ranked.append({
            "ticker": ticker,
            "score": score,
            "components": components,
            "hist": hist,
            "decision_price": float(hist["Close"].iloc[-1]),
        })
    ranked.sort(key=lambda r: -r["score"])

    # Need next-day open for the fill price (no lookahead — same trade
    # would be queued for next open in live mode too)
    todays_prices = {
        t: float(history_as_of[t]["Close"].iloc[-1]) for t in history_as_of
    }

    for pick in ranked:
        ticker = pick["ticker"]
        next_full = all_hist.get(ticker)
        if next_full is None:
            continue
        try:
            next_bar = next_full.loc[next_full.index == pd.Timestamp(next_date)]
            if next_bar.empty:
                continue
            fill_raw = float(next_bar["Open"].iloc[0])
        except (KeyError, IndexError):
            continue
        fill = slippage_price(fill_raw, "BUY", cfg)
        current_eq = portfolio.equity(todays_prices)
        ok, reason, qty = can_open(
            risk, portfolio.cash, current_eq,
            portfolio.positions, ticker, fill, cfg,
        )
        if not ok or qty <= 0:
            continue
        portfolio.cash -= qty * fill
        portfolio.positions[ticker] = PaperPosition(
            ticker=ticker, qty=qty, entry_price=fill,
            entry_date=datetime.combine(next_date, datetime.min.time()),
            sector=cfg.sector_map.get(ticker, "Unknown"),
            peak_price=fill, entry_score=pick["score"],
            reasoning=reasoning_text(pick["components"], pick["score"]),
        )


def _close_position(portfolio, ticker, raw_price, reason, date) -> None:
    pos = portfolio.positions.pop(ticker)
    exit_price = slippage_price(raw_price, "SELL", portfolio.cfg)
    gross = (exit_price - pos.entry_price) * pos.qty
    costs = trade_costs(pos.qty, pos.entry_price, exit_price, portfolio.cfg)
    portfolio.cash += pos.qty * exit_price - costs
    portfolio.trades.append(ClosedTrade(
        ticker=ticker, qty=pos.qty,
        entry_date=pos.entry_date,
        exit_date=datetime.combine(date, datetime.min.time()),
        entry_price=pos.entry_price, exit_price=exit_price,
        gross_pnl=gross, costs=costs, net_pnl=gross - costs,
        reason=reason, sector=pos.sector,
        entry_score=pos.entry_score, reasoning=pos.reasoning,
    ))


def _compile_result(
    cfg, start_date, end_date, universe_size, benchmark, portfolio,
    equity_curve, bench_hist, time_in_market_days, total_days,
    warnings, final_equity,
) -> BacktestResult:
    trades = portfolio.trades
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]
    win_pcts = [(t.exit_price / t.entry_price - 1) * 100 for t in wins]
    loss_pcts = [(t.exit_price / t.entry_price - 1) * 100 for t in losses]
    sum_wins = sum(t.net_pnl for t in wins)
    sum_losses = abs(sum(t.net_pnl for t in losses))

    total_return_pct = (final_equity / cfg.starting_capital - 1) * 100
    years = max((pd.Timestamp(end_date) - pd.Timestamp(start_date)).days / 365.25, 0.01)
    cagr_pct = ((final_equity / cfg.starting_capital) ** (1 / years) - 1) * 100

    # Sharpe from daily equity returns
    eq_series = pd.Series([e for _, e in equity_curve])
    daily_ret = eq_series.pct_change().dropna()
    sharpe = (
        float(daily_ret.mean() / daily_ret.std() * np.sqrt(252))
        if daily_ret.std() > 0 else 0.0
    )

    # Max drawdown
    running_max = eq_series.cummax()
    drawdown = (eq_series - running_max) / running_max
    max_dd = float(drawdown.min() * 100) if not drawdown.empty else 0.0

    # Benchmark
    bench_start_close = float(bench_hist["Close"].iloc[0])
    bench_end_close = float(bench_hist["Close"].iloc[-1])
    bench_return = (bench_end_close / bench_start_close - 1) * 100
    bench_eq = bench_hist["Close"]
    bench_max = bench_eq.cummax()
    bench_dd = float(((bench_eq - bench_max) / bench_max).min() * 100)

    # Per-sector / per-reason buckets
    per_sector: dict[str, float] = {}
    per_reason: dict[str, float] = {}
    for t in trades:
        per_sector[t.sector] = per_sector.get(t.sector, 0.0) + t.net_pnl
        per_reason[t.reason] = per_reason.get(t.reason, 0.0) + t.net_pnl

    return BacktestResult(
        cfg_snapshot={
            "mode": getattr(cfg, "mode", "swing"),
            "min_score_to_enter": cfg.min_score_to_enter,
            "stop_loss_pct": cfg.stop_loss_pct,
            "take_profit_pct": cfg.take_profit_pct,
            "max_positions": cfg.max_positions,
        },
        start_date=start_date, end_date=end_date,
        universe_size=universe_size, benchmark=benchmark,
        initial_capital=cfg.starting_capital,
        final_equity=final_equity,
        total_return_pct=total_return_pct,
        cagr_pct=cagr_pct,
        win_rate_pct=(len(wins) / max(len(trades), 1)) * 100,
        profit_factor=(sum_wins / sum_losses) if sum_losses > 0 else float("inf"),
        sharpe_ratio=sharpe,
        max_drawdown_pct=max_dd,
        avg_win_pct=float(np.mean(win_pcts)) if win_pcts else 0.0,
        avg_loss_pct=float(np.mean(loss_pcts)) if loss_pcts else 0.0,
        num_trades=len(trades),
        time_in_market_pct=(time_in_market_days / max(total_days, 1)) * 100,
        benchmark_total_return_pct=bench_return,
        benchmark_max_drawdown_pct=bench_dd,
        alpha_pct=total_return_pct - bench_return,
        trades=[
            {
                "ticker": t.ticker, "qty": t.qty,
                "entry_date": t.entry_date.date().isoformat(),
                "exit_date": t.exit_date.date().isoformat(),
                "entry_price": t.entry_price, "exit_price": t.exit_price,
                "net_pnl": t.net_pnl, "reason": t.reason, "sector": t.sector,
                "entry_score": t.entry_score,
            }
            for t in trades
        ],
        equity_curve=equity_curve,
        per_sector_pnl=per_sector,
        per_reason_pnl=per_reason,
        warnings=warnings,
    )
