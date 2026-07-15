"""Gradio dashboard tabs for the live paper-trading engine.

Call `build_trading_tabs()` from inside `gr.Blocks()` / `gr.Tabs()` to attach.
Shared state (one Portfolio + Engine for the whole Space) lives in module
scope so refresh/scan calls hit the same in-memory object.
"""

from __future__ import annotations

from datetime import datetime
from typing import Tuple

import gradio as gr
import pandas as pd

from .config import AM07_WEIGHTS, Config, STUB_COMPONENTS
from .data import fetch_quotes
from .engine import TradingEngine
from .paper import Portfolio
from .reports import (
    write_equity_csv, write_picks_csv, write_positions_csv, write_trades_csv,
)


_CFG = Config()
_PORTFOLIO = Portfolio.load_or_fresh(_CFG)
_ENGINE = TradingEngine(_CFG, _PORTFOLIO)


# ============== view helpers ==============

def _summary_markdown() -> str:
    prices = _current_prices()
    eq = _PORTFOLIO.equity(prices)
    deployed = _PORTFOLIO.deployed_value(prices)
    ret_pct = (eq / _CFG.starting_capital - 1) * 100
    last_scan = _PORTFOLIO.last_scan or "never"
    return (
        f"**Equity**: Rs {eq:,.0f}  &nbsp;|&nbsp; "
        f"**Return**: {ret_pct:+.2f}%  &nbsp;|&nbsp; "
        f"**Cash**: Rs {_PORTFOLIO.cash:,.0f}  &nbsp;|&nbsp; "
        f"**Deployed**: Rs {deployed:,.0f}\n\n"
        f"Open positions: **{len(_PORTFOLIO.positions)}** &nbsp;|&nbsp; "
        f"Closed trades: **{len(_PORTFOLIO.trades)}** &nbsp;|&nbsp; "
        f"Last scan: `{last_scan}` &nbsp;|&nbsp; "
        f"Kill switch: **{'ACTIVE' if _ENGINE.risk.kill_switch else 'OK'}**"
    )


def _current_prices() -> dict:
    tickers = list(_PORTFOLIO.positions.keys())
    if not tickers:
        return {}
    return fetch_quotes(tickers)


def _picks_dataframe() -> pd.DataFrame:
    picks = _PORTFOLIO.last_scan_picks or []
    if not picks:
        return pd.DataFrame(columns=["rank", "ticker", "score", "last_close", "timing_pass", "reasoning"])
    rows = []
    for i, p in enumerate(picks, 1):
        rows.append({
            "rank": i, "ticker": p["ticker"], "score": round(p["score"], 2),
            "last_close": round(p["last_close"], 2),
            "timing_pass": "yes" if p.get("timing_pass") else "no",
            "reasoning": p.get("reasoning", ""),
        })
    return pd.DataFrame(rows)


def _positions_dataframe() -> pd.DataFrame:
    prices = _current_prices()
    rows = []
    for p in _PORTFOLIO.positions.values():
        mark = prices.get(p.ticker, p.entry_price)
        upnl = (mark - p.entry_price) * p.qty
        upct = (mark / p.entry_price - 1) * 100 if p.entry_price else 0
        rows.append({
            "ticker": p.ticker,
            "qty": p.qty,
            "entry_date": p.entry_date.strftime("%Y-%m-%d"),
            "entry_price": round(p.entry_price, 2),
            "current": round(mark, 2),
            "peak": round(p.peak_price, 2),
            "unrealised_pnl": round(upnl, 0),
            "unrealised_pct": round(upct, 2),
            "sector": p.sector,
            "entry_score": round(p.entry_score, 1),
        })
    if not rows:
        return pd.DataFrame(columns=[
            "ticker", "qty", "entry_date", "entry_price", "current", "peak",
            "unrealised_pnl", "unrealised_pct", "sector", "entry_score",
        ])
    return pd.DataFrame(rows)


def _trades_dataframe() -> pd.DataFrame:
    rows = []
    for t in _PORTFOLIO.trades:
        net_pct = (t.exit_price / t.entry_price - 1) * 100 if t.entry_price else 0
        hold = (t.exit_date.date() - t.entry_date.date()).days
        rows.append({
            "ticker": t.ticker, "qty": t.qty,
            "entry_date": t.entry_date.strftime("%Y-%m-%d"),
            "exit_date": t.exit_date.strftime("%Y-%m-%d"),
            "hold_days": hold,
            "entry_price": round(t.entry_price, 2),
            "exit_price": round(t.exit_price, 2),
            "net_pnl": round(t.net_pnl, 0),
            "net_pct": round(net_pct, 2),
            "reason": t.reason,
            "sector": t.sector,
        })
    if not rows:
        return pd.DataFrame(columns=[
            "ticker", "qty", "entry_date", "exit_date", "hold_days",
            "entry_price", "exit_price", "net_pnl", "net_pct", "reason", "sector",
        ])
    return pd.DataFrame(rows)


def _equity_dataframe() -> pd.DataFrame:
    if not _PORTFOLIO.equity_history:
        return pd.DataFrame(columns=["timestamp", "equity"])
    return pd.DataFrame(
        [{"timestamp": pd.to_datetime(ts), "equity": eq}
         for ts, eq in _PORTFOLIO.equity_history]
    )


def _performance_markdown() -> str:
    trades = _PORTFOLIO.trades
    if not trades:
        return "_No closed trades yet._"
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]
    total = sum(t.net_pnl for t in trades)
    costs = sum(t.costs for t in trades)
    win_rate = len(wins) / len(trades) * 100
    avg_win = sum(t.net_pnl for t in wins) / max(len(wins), 1)
    avg_loss = sum(t.net_pnl for t in losses) / max(len(losses), 1)
    return (
        f"**Closed trades**: {len(trades)}  &nbsp;|&nbsp; "
        f"**Win rate**: {win_rate:.1f}%  &nbsp;|&nbsp; "
        f"**Net P&L**: Rs {total:,.0f}  &nbsp;|&nbsp; "
        f"**Total costs**: Rs {costs:,.0f}\n\n"
        f"Avg win: Rs {avg_win:,.0f}  &nbsp;|&nbsp; "
        f"Avg loss: Rs {avg_loss:,.0f}"
    )


def _config_markdown() -> str:
    weights_md = "\n".join(
        f"- `{k}`: **{v*100:.0f}%**" + (" _(stubbed at 50 — needs data feed)_" if k in STUB_COMPONENTS else "")
        for k, v in AM07_WEIGHTS.items()
    )
    return (
        f"### Capital\n"
        f"- Starting capital: Rs {_CFG.starting_capital:,.0f}\n"
        f"- Cash reserve: {_CFG.cash_reserve_pct*100:.0f}% (always held back)\n"
        f"- Max per position: {_CFG.max_position_pct*100:.0f}% of capital\n"
        f"- Max positions: {_CFG.max_positions}\n"
        f"- Max per sector: {_CFG.max_sector_positions}\n\n"
        f"### Per-trade risk\n"
        f"- Hard stop loss: {_CFG.stop_loss_pct*100:.1f}%\n"
        f"- Take profit: {_CFG.take_profit_pct*100:.1f}%  (R:R ~ 1:2)\n"
        f"- Trailing stop: {_CFG.trailing_stop_pct*100:.1f}% from peak\n"
        f"- Score-decay exit: drop below {_CFG.score_decay_exit:.0f}\n"
        f"- Max hold: {_CFG.max_hold_days} trading days\n\n"
        f"### Portfolio risk\n"
        f"- Daily loss kill switch: {_CFG.daily_loss_halt_pct*100:.1f}%\n\n"
        f"### Entry overlay (in addition to score >= {_CFG.min_score_to_enter:.0f})\n"
        f"- Price within {_CFG.breakout_threshold_pct*100:.0f}% of {_CFG.breakout_lookback_days}-day high\n"
        f"- 5-day volume >= {_CFG.volume_confirm_ratio:.1f}x 20-day volume\n\n"
        f"### AM 07 scoring weights\n{weights_md}\n\n"
        f"### Data source\n"
        f"- Market data: **yfinance** (delayed ~15 min for NSE). Upstox real-time wiring is a v2 task.\n"
        f"- Mode: **paper trading only**. No real orders sent.\n"
    )


# ============== handlers ==============

def _do_scan() -> Tuple[str, pd.DataFrame, pd.DataFrame, str]:
    try:
        result = _ENGINE.scan()
        status = (
            f"Scanned {len(result.ranked)} symbols at "
            f"{result.ts.strftime('%Y-%m-%d %H:%M:%S')} UTC. "
            f"{len(result.picks)} picks above threshold, "
            f"**{len(result.opened)} opened**, {len(result.skipped)} skipped."
        )
        if result.opened:
            status += "\n\n**Opened**: " + ", ".join(result.opened)
        if result.skipped:
            status += "\n\n**Skipped**: " + ", ".join(
                f"{s['ticker']} ({s['reason']})" for s in result.skipped
            )
    except Exception as e:
        status = f"Scan failed: `{e}`"
    return status, _picks_dataframe(), _positions_dataframe(), _summary_markdown()


def _do_refresh() -> Tuple[str, pd.DataFrame, pd.DataFrame, str]:
    try:
        summary = _ENGINE.refresh()
        if summary.get("exits_this_refresh"):
            msg = "Exits this refresh: " + ", ".join(summary["exits_this_refresh"])
        else:
            msg = "No exits triggered."
        if summary.get("kill_switch"):
            msg = "**KILL SWITCH ACTIVE** — daily loss limit breached. " + msg
    except Exception as e:
        msg = f"Refresh failed: `{e}`"
    return msg, _positions_dataframe(), _trades_dataframe(), _summary_markdown()


def _do_reset(confirm: str) -> str:
    if confirm.strip().upper() != "RESET":
        return "_Type `RESET` exactly to confirm. Nothing changed._"
    _PORTFOLIO.reset()
    _ENGINE.risk = type(_ENGINE.risk)(cfg=_CFG, starting_capital=_CFG.starting_capital)
    return "Paper portfolio reset. Cash restored, positions cleared, trade history wiped."


def _generate_reports() -> list:
    prices = _current_prices()
    paths = [
        write_picks_csv(_PORTFOLIO),
        write_positions_csv(_PORTFOLIO, prices),
        write_trades_csv(_PORTFOLIO),
        write_equity_csv(_PORTFOLIO),
    ]
    return paths


# ============== UI builder ==============

def build_trading_tabs() -> None:
    """Call inside `gr.Blocks` + `gr.Tabs` context to attach trading tabs."""

    with gr.Tab("Summary"):
        summary_md = gr.Markdown(_summary_markdown())
        with gr.Row():
            refresh_btn = gr.Button("Refresh prices & check exits", variant="primary")
            scan_btn = gr.Button("Run pre-market scan now")
        status_md = gr.Markdown("_Click a button to update._")
        gr.Markdown("### Open positions")
        positions_df = gr.Dataframe(value=_positions_dataframe(), interactive=False, wrap=True)
        gr.Markdown("### Performance")
        perf_md = gr.Markdown(_performance_markdown())

    with gr.Tab("Picks"):
        gr.Markdown(
            "AM 07 score ranking of the universe. Picks above the threshold "
            f"({_CFG.min_score_to_enter:.0f}) that also pass the timing overlay are "
            "candidates for entry."
        )
        picks_df = gr.Dataframe(value=_picks_dataframe(), interactive=False, wrap=True)

    with gr.Tab("Trades"):
        gr.Markdown("Every closed paper trade. Use the Reports tab to download as CSV.")
        trades_df = gr.Dataframe(value=_trades_dataframe(), interactive=False, wrap=True)

    with gr.Tab("Equity"):
        gr.Markdown("Equity curve since the last reset.")
        eq_plot = gr.LinePlot(
            value=_equity_dataframe(), x="timestamp", y="equity",
            title="Paper portfolio equity (Rs)", height=420,
        )

    with gr.Tab("Reports"):
        gr.Markdown("Click to generate fresh CSV exports. Then click each file to download.")
        report_btn = gr.Button("Generate CSV reports")
        report_files = gr.File(label="Downloads", file_count="multiple", interactive=False)
        report_btn.click(_generate_reports, outputs=report_files)

    with gr.Tab("Settings"):
        gr.Markdown(_config_markdown())
        gr.Markdown("---\n### Reset paper portfolio")
        gr.Markdown(
            "Wipes all open positions, trade history, and equity curve. "
            "Cash is restored to the starting capital. Type `RESET` to confirm."
        )
        reset_box = gr.Textbox(label="Confirmation", placeholder="Type RESET")
        reset_btn = gr.Button("Reset paper portfolio", variant="stop")
        reset_status = gr.Markdown("")
        reset_btn.click(_do_reset, inputs=reset_box, outputs=reset_status)

    # wire summary / scan handlers (output multiple components)
    scan_btn.click(
        _do_scan,
        outputs=[status_md, picks_df, positions_df, summary_md],
    )
    refresh_btn.click(
        _do_refresh,
        outputs=[status_md, positions_df, trades_df, summary_md],
    )
