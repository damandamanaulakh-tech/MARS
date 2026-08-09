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

from .data import YF_THREADS

from .config import Config
from .costs import slippage_price, trade_costs
from .exits import check_exit
from .paper import ClosedTrade, PaperPosition, Portfolio
from .risk import RiskState, can_open
from .scoring import compute_score, reasoning_text
from .timing import timing_ok


log = logging.getLogger(__name__)

# Secondary benchmark (owner-approved adoption, 2026-07-27): the strategy IS
# a Nifty-200 momentum strategy, so judging it only against Nifty 50 (^NSEI)
# is the wrong ruler. ^CNX200 = Nifty 200 on Yahoo Finance. Fail-visible:
# when the series can't be fetched the result says DATA_NEEDED instead of
# silently dropping the comparison.
BENCHMARK2 = "^CNX200"
BENCHMARK2_LABEL = "Nifty 200"


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

    # Secondary benchmark — Nifty 200 (^CNX200), the fair ruler for a
    # Nifty-200 momentum strategy. None when the series was unfetchable
    # (a warning is appended so the gap is visible, never silent).
    benchmark2: str = BENCHMARK2
    benchmark2_total_return_pct: Optional[float] = None
    alpha2_pct: Optional[float] = None

    # Raw artefacts
    trades: list = field(default_factory=list)        # list[dict]
    equity_curve: list = field(default_factory=list)  # list[(iso_date, equity)]
    per_sector_pnl: dict = field(default_factory=dict)
    per_reason_pnl: dict = field(default_factory=dict)

    # Disclosures
    warnings: list = field(default_factory=list)
    # P1 survivorship fix: POINT-IN-TIME when every entry day had an NSE
    # snapshot; SURVIVORSHIP-BIASED otherwise. Rides with the numbers — a
    # biased result must never be quoted as strategy performance.
    universe_integrity: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}

    def summary_markdown(self) -> str:
        sign = "" if self.alpha_pct >= 0 else ""
        lines = [
            f"# Backtest Report: AM 07 ({self.cfg_snapshot.get('mode', 'swing')})",
            "",
            f"**Period**: {self.start_date} → {self.end_date}",
            f"**Universe**: {self.universe_size} symbols · integrity: "
            f"{', '.join(self.universe_integrity) if self.universe_integrity else 'UNKNOWN'}",
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
            f"- **Alpha vs Nifty 50**: {self.alpha_pct:+.2f}% {sign}",
        ]
        if self.benchmark2_total_return_pct is not None:
            lines += [
                f"- Nifty 200 return same window: "
                f"{self.benchmark2_total_return_pct:+.2f}%",
                f"- **Alpha vs Nifty 200** (the fair ruler for this strategy): "
                f"{self.alpha2_pct:+.2f}%",
            ]
        lines += [
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


def _score_for_mode(cfg, ticker, hist, bench_as_of, history_as_of):
    """Score a ticker with the SAME scorer the live engine uses for this mode.

    nifty200_momentum -> momentum_score (6m+12m sharpe); every other mode ->
    AM07 compute_score. Without this the backtest replayed a different
    strategy than what actually trades live. Returns (score, components).
    """
    if getattr(cfg, "mode", "") == "nifty200_momentum":
        try:
            from .strategies.nifty200_momentum import momentum_score
            from .quality import quality_score, blend_scores
            s = momentum_score(hist["Close"])
            if s is None:
                return 0.0, {"nifty200_momentum": 0.0}
            # C1: same momentum+quality blend as the live engine, so the
            # backtest proves the strategy that actually trades.
            q = quality_score(hist["Close"], hist.get("Volume"))
            blended = blend_scores(float(s), q, getattr(cfg, "quality_blend_pct", 0.0))
            return blended, {"momentum": float(s), "quality": q}
        except ImportError:
            pass
    return compute_score(ticker, hist, bench_as_of, history_as_of, cfg)


def _regime_allows_entries(bench_as_of) -> bool:
    """Replay the live regime master-switch as-of this slice: True if the
    momentum (Everest) engine would be active. Without this the backtest keeps
    buying momentum straight into corrections the live engine sits out, so its
    drawdowns look far worse than what actually trades."""
    if bench_as_of is None:
        return True
    try:
        from .regime import classify
        close = bench_as_of["Close"].dropna()
    except Exception:
        return True
    if len(close) < 60:
        return True
    last = float(close.iloc[-1])
    dma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None
    last_252 = close.iloc[-252:]
    high_52w = float(last_252.max())
    pct_below = (last - high_52w) / high_52w * 100 if high_52w > 0 else None
    _, everest_on, *_ = classify(
        nifty_close=last, nifty_200dma=dma200,
        pct_below_52w_high=pct_below, vix=None, fii_5d_net=None,
    )
    return everest_on


def run_backtest(
    cfg: Config,
    start_date: str,
    end_date: str,
    universe: Optional[list[str]] = None,
    benchmark: str = "^NSEI",
    history_period: str = "max",
    preloaded_history: Optional[dict] = None,
) -> BacktestResult:
    """Walk-forward replay of the engine on historical NSE bars.

    Pulls all OHLCV in one batch up front (efficient), then sequentially
    feeds the engine truncated slices ending at each simulated day.
    """
    universe = universe or cfg.universe
    # P1 survivorship fix: entries restricted to the point-in-time Nifty-200
    # membership when NSE snapshot CSVs exist in data/universe_snapshots/.
    # Without snapshots behavior is unchanged — but the result is tagged
    # SURVIVORSHIP-BIASED so the contamination can never be hidden again.
    from .universes_pit import PITUniverse
    pit = PITUniverse.from_dir(static_fallback=universe)
    universe_tags: set = set()
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
        f"Costs ARE modelled and returns are NET: {cfg.slippage_bps:.0f} bps "
        f"slippage/side + STT {cfg.stt_buy_pct*100:.1f}%/{cfg.stt_sell_pct*100:.1f}% "
        f"buy/sell + brokerage ₹{cfg.brokerage_per_order:.0f}/order. "
        "Benchmark is buy-and-hold (no costs) — the strategy carries a real "
        "cost drag the benchmark does not.",
    ]

    # Bulk download ONLY the window we need (+ ~14mo lookback for the momentum /
    # AM07 scorers). period="max" over 190+ stocks blows Render's memory and
    # makes the run appear to hang — bound it to the actual window.
    dl_start = (pd.Timestamp(start_date) - pd.Timedelta(days=420)).date().isoformat()
    dl_end = (pd.Timestamp(end_date) + pd.Timedelta(days=5)).date().isoformat()
    if preloaded_history is not None:
        all_hist = preloaded_history
    else:
        log.info("Backtest: downloading %d universe + %s benchmark [%s..%s]",
                 len(universe), benchmark, dl_start, dl_end)
        all_hist = _bulk_download(universe + [benchmark, BENCHMARK2],
                                  start=dl_start, end=dl_end)
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
    prev_equity = cfg.starting_capital   # yesterday's close — for the daily kill-switch reset

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

            # Reset the daily-loss kill switch each morning. The live engine
            # rolls the day; without this the -2% halt is measured against the
            # ORIGINAL capital forever, so it latches permanently after the
            # first losing stretch and blocks every later entry (the bug that
            # made all trades land in week 1).
            risk.roll_day(prev_equity)

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
                    current_score, _ = _score_for_mode(
                        cfg, ticker, hist, bench_as_of, history_as_of,
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

            # 2. Scan for new entries (uses close-of-D data; fills next bar open).
            # Replay the regime master-switch: skip momentum entries when the
            # live engine would be in correction/panic (Everest off).
            if (not risk.kill_switch and i + 1 < len(trading_dates)
                    and _regime_allows_entries(bench_as_of)):
                next_date = trading_dates[i + 1]
                # PIT gate: only names that were index members ON this date are
                # buyable; held names stay in scope so exits keep marking.
                tickers_asof, _tag = pit.asof(date.date())
                universe_tags.add(_tag)
                _asof = set(tickers_asof)
                entry_hist = {t: h for t, h in history_as_of.items()
                              if t in _asof or t in portfolio.positions}
                _try_open_positions(
                    cfg, portfolio, risk, entry_hist, bench_as_of,
                    all_hist, next_date,
                )

            # 3. Record equity at end of day
            equity_curve.append((str(date.date()), current_eq))
            if portfolio.positions:
                time_in_market_days += 1
            prev_equity = current_eq
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
        bench2_hist=all_hist.get(BENCHMARK2),
        time_in_market_days=time_in_market_days,
        total_days=len(trading_dates),
        warnings=warnings + _benchmark_staleness_warnings(
            bench_in_window, end_date),
        final_equity=final_equity,
        universe_integrity=sorted(universe_tags) or ["UNKNOWN"],
    )


def _benchmark_staleness_warnings(bench_hist, end_date: str) -> list[str]:
    """AUDIT FIX B11 (2026-08-09): the committed index CSV that feeds the web
    backtest's Nifty benchmark ends 2026-06-05 and nothing refreshes it — any
    window past that computed 'alpha' against a benchmark frozen mid-window
    with NO disclosure. Now the truncation is printed on the result."""
    out: list[str] = []
    try:
        if bench_hist is None or bench_hist.empty:
            return ["Benchmark series missing — alpha not computable."]
        last = bench_hist.index[-1].date()
        import pandas as _pd
        end = _pd.Timestamp(end_date).date()
        gap = (end - last).days
        if gap > 7:
            out.append(
                f"BENCHMARK TRUNCATED: Nifty data ends {last.isoformat()} — "
                f"the final {gap} days of this window have no benchmark, so "
                "alpha for that stretch is not measured.")
    except Exception:
        pass
    return out


def _bulk_download(tickers: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
    """One bounded download covering only [start, end] (incl. scoring lookback).
    Bounded by date (not period='max') so memory stays small enough for Render."""
    from . import data as _data
    try:
        return _data.fetch_daily_range(tickers, start, end)
    except Exception as e:
        log.warning("bulk history download failed: %s", e)
        return {}


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
            score, components = _score_for_mode(
                cfg, ticker, hist, bench_as_of, history_as_of,
            )
        except Exception:
            continue
        timing_pass, _ = timing_ok(hist, cfg)
        if score < cfg.min_score_to_enter or not timing_pass:
            continue
        # P0/A2: absolute-momentum filter — mirror the live engine. In momentum
        # mode a pick must have a positive 6-month return of its own.
        if getattr(cfg, "mode", "") == "nifty200_momentum":
            c = hist["Close"].dropna()
            if len(c) <= 127 or float(c.iloc[-1]) <= float(c.iloc[-127]):
                continue
        # Batch C: candle BUY-CONFIRMATION gate — mirrors the live engine so
        # backtests measure the same entry discipline.
        try:
            from .candles import candle_confirmation as _cc
            if not _cc(hist, cfg)[0]:
                continue
        except Exception:
            pass
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
        if getattr(cfg, "mode", "") == "nifty200_momentum":
            reasoning = f"NIFTY200_MOMENTUM: 6m+12m sharpe {pick['score']:.1f}/100"
        else:
            reasoning = reasoning_text(pick["components"], pick["score"])
        portfolio.positions[ticker] = PaperPosition(
            ticker=ticker, qty=qty, entry_price=fill,
            entry_date=datetime.combine(next_date, datetime.min.time()),
            sector=cfg.sector_map.get(ticker, "Unknown"),
            peak_price=fill, entry_score=pick["score"],
            reasoning=reasoning,
            mode=getattr(cfg, "mode", ""),
            stop_price=round(fill * (1 + cfg.stop_loss_pct), 2),
            target_price=round(fill * (1 + cfg.take_profit_pct), 2),
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
    warnings, final_equity, universe_integrity=None, bench2_hist=None,
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

    # Secondary benchmark (Nifty 200) over the same window — the honest ruler
    # for a Nifty-200 momentum strategy. None (+ a warning) when unfetchable.
    bench2_return = None
    if bench2_hist is not None and not bench2_hist.empty:
        b2 = bench2_hist[
            (bench2_hist.index >= pd.Timestamp(start_date))
            & (bench2_hist.index <= pd.Timestamp(end_date))
        ]
        if len(b2) >= 2:
            bench2_return = (float(b2["Close"].iloc[-1])
                             / float(b2["Close"].iloc[0]) - 1) * 100
    if bench2_return is None:
        warnings = list(warnings) + [
            f"{BENCHMARK2_LABEL} ({BENCHMARK2}) series unavailable for this "
            "window — alpha shown vs Nifty 50 only (DATA_NEEDED).",
        ]

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
        universe_integrity=list(universe_integrity or []),
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
        benchmark2=BENCHMARK2,
        benchmark2_total_return_pct=bench2_return,
        alpha2_pct=(total_return_pct - bench2_return
                    if bench2_return is not None else None),
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


def walk_forward(
    cfg: Config,
    start_date: str,
    end_date: str,
    window_years: int = 1,
    universe: Optional[list[str]] = None,
    benchmark: str = "^NSEI",
    preloaded_history: Optional[dict] = None,
) -> tuple[list[dict], dict]:
    """P1/A1 — rolling out-of-sample validation. Splits [start, end] into
    consecutive windows and replays the FULL engine on each, sharing one
    download. Shows whether the edge holds across periods instead of one
    cherry-picked range. Uses today's universe — survivorship-biased, so
    treat results as OPTIMISTIC (point-in-time membership: DATA_NEEDED)."""
    universe = universe or cfg.universe
    if preloaded_history is None:
        dl_start = (pd.Timestamp(start_date) - pd.Timedelta(days=420)).date().isoformat()
        dl_end = (pd.Timestamp(end_date) + pd.Timedelta(days=5)).date().isoformat()
        preloaded_history = _bulk_download(universe + [benchmark, BENCHMARK2],
                                           start=dl_start, end=dl_end)

    windows: list[dict] = []
    w_start, end_ts = pd.Timestamp(start_date), pd.Timestamp(end_date)
    while w_start < end_ts:
        w_end = min(w_start + pd.DateOffset(years=window_years), end_ts)
        row: dict = {"start": w_start.date().isoformat(), "end": w_end.date().isoformat()}
        try:
            r = run_backtest(
                cfg, row["start"], row["end"], universe=universe,
                benchmark=benchmark, preloaded_history=preloaded_history,
            )
            row.update(
                n200_return_pct=r.benchmark2_total_return_pct,
                alpha_vs_n200_pct=r.alpha2_pct,
                total_return_pct=r.total_return_pct, alpha_pct=r.alpha_pct,
                benchmark_return_pct=r.benchmark_total_return_pct,
                num_trades=r.num_trades, win_rate_pct=r.win_rate_pct,
                max_drawdown_pct=r.max_drawdown_pct, sharpe=r.sharpe_ratio,
            )
        except Exception as e:
            row["error"] = str(e)[:120]
        windows.append(row)
        w_start = w_end

    ok = [w for w in windows if "error" not in w]
    summary = {
        "windows": len(windows),
        "positive_windows": sum(1 for w in ok if w["total_return_pct"] > 0),
        "beat_benchmark_windows": sum(1 for w in ok if w["alpha_pct"] > 0),
        "worst_window_return_pct": min((w["total_return_pct"] for w in ok), default=None),
        "note": "Survivorship-biased (today's universe); treat as optimistic.",
    }
    return windows, summary
