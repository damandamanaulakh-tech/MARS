"""CSV report generators. Returns paths to files in cfg.state_dir."""

from __future__ import annotations

import csv
import os
from datetime import datetime
from typing import List

from .config import Config
from .paper import Portfolio


def _ensure_dir(portfolio: Portfolio) -> str:
    os.makedirs(portfolio.cfg.state_dir, exist_ok=True)
    return portfolio.cfg.state_dir


def write_picks_csv(portfolio: Portfolio) -> str:
    path = os.path.join(_ensure_dir(portfolio), "today_picks.csv")
    picks = portfolio.last_scan_picks or []
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "ticker", "score", "last_close", "timing_pass", "reasoning"])
        for i, p in enumerate(picks, 1):
            w.writerow([
                i, p.get("ticker"), f"{p.get('score', 0):.2f}",
                f"{p.get('last_close', 0):.2f}", p.get("timing_pass"),
                p.get("reasoning", ""),
            ])
    return path


def write_positions_csv(portfolio: Portfolio, prices: dict,
                        price_source: dict | None = None) -> str:
    path = os.path.join(_ensure_dir(portfolio), "positions.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "ticker", "qty", "entry_date", "entry_price", "current_price",
            "unrealised_pnl", "unrealised_pct", "price_source",
            "peak_price", "sector",
            "entry_score", "reasoning", "entry_notes", "tags",
        ])
        for p in portfolio.positions.values():
            mark = prices.get(p.ticker, p.entry_price)
            upnl = (mark - p.entry_price) * p.qty
            upct = (mark / p.entry_price - 1) * 100 if p.entry_price else 0
            src = (price_source or {}).get(p.ticker, "entry")
            w.writerow([
                p.ticker, p.qty, p.entry_date.isoformat(),
                f"{p.entry_price:.2f}", f"{mark:.2f}",
                f"{upnl:.2f}", f"{upct:.2f}", src,
                f"{p.peak_price:.2f}", p.sector,
                f"{p.entry_score:.2f}", p.reasoning,
                getattr(p, "entry_notes", "") or "",
                ",".join(getattr(p, "tags", []) or []),
            ])
    return path


def write_trades_csv(portfolio: Portfolio) -> str:
    path = os.path.join(_ensure_dir(portfolio), "trades.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "ticker", "qty", "entry_date", "exit_date",
            "entry_price", "exit_price", "gross_pnl", "costs",
            "net_pnl", "net_pct", "hold_days", "reason", "sector",
            "entry_score", "reasoning", "entry_notes", "tags",
        ])
        for t in portfolio.trades:
            net_pct = (t.exit_price / t.entry_price - 1) * 100 if t.entry_price else 0
            hold = (t.exit_date.date() - t.entry_date.date()).days
            w.writerow([
                t.ticker, t.qty,
                t.entry_date.isoformat(), t.exit_date.isoformat(),
                f"{t.entry_price:.2f}", f"{t.exit_price:.2f}",
                f"{t.gross_pnl:.2f}", f"{t.costs:.2f}",
                f"{t.net_pnl:.2f}", f"{net_pct:.2f}",
                hold, t.reason, t.sector,
                f"{t.entry_score:.2f}", t.reasoning,
                getattr(t, "entry_notes", "") or "",
                ",".join(getattr(t, "tags", []) or []),
            ])
    return path


def write_equity_csv(portfolio: Portfolio) -> str:
    path = os.path.join(_ensure_dir(portfolio), "equity_curve.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "equity"])
        for ts, eq in portfolio.equity_history:
            w.writerow([ts, f"{eq:.2f}"])
    return path


def write_journal_csv(portfolio: Portfolio) -> str:
    """Full decision journal — every BUY / SELL / HOLD / RESET with context.
    The compare-and-learn record: why we bought, why we sold, why we kept."""
    path = os.path.join(_ensure_dir(portfolio), "decision_journal.csv")
    entries = portfolio.decision_journal or []
    cols = [
        "ts", "action", "ticker", "qty", "price", "entry_price", "exit_price",
        "notional", "net_pnl", "net_pct", "hold_days", "exit_reason",
        "unrealised_pct", "current_score", "score_at_entry", "score",
        "timing_pass", "mode", "regime", "drawdown_level",
        "peak_price", "gave_back_from_peak_pct",
        "why", "why_bought_was", "entry_notes", "cash_after", "detail",
    ]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for e in entries:
            row = []
            for c in cols:
                v = e.get(c, "")
                if isinstance(v, dict):
                    v = ";".join(f"{k}={x}" for k, x in v.items())
                row.append(v)
            w.writerow(row)
    return path


def write_skipped_csv(portfolio: Portfolio) -> str:
    """Why candidates were rejected at the entry gate — needed to diagnose
    'no positions created' moments."""
    path = os.path.join(_ensure_dir(portfolio), "skipped.csv")
    skipped = portfolio.last_scan_skipped or []
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "reason"])
        for s in skipped:
            w.writerow([s.get("ticker", ""), s.get("reason", "")])
    return path
