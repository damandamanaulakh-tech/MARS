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


def write_trades_csv(portfolio: Portfolio, archived=()) -> str:
    """All closed trades, oldest first. `archived` = trades already moved
    out of the book to the Mongo `trades` archive (audit M-10), as
    ClosedTrade.to_dict() rows; they come first, then the book's own."""
    from .paper import ClosedTrade
    older = []
    for d in archived or ():
        try:
            older.append(ClosedTrade.from_dict(d))
        except Exception:
            continue
    path = os.path.join(_ensure_dir(portfolio), "trades.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "ticker", "qty", "entry_date", "exit_date",
            "entry_price", "exit_price", "gross_pnl", "costs",
            "net_pnl", "net_pct", "hold_days", "reason", "sector",
            "entry_score", "reasoning", "entry_notes", "tags",
        ])
        for t in older + list(portfolio.trades):
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


def write_journal_csv(portfolio: Portfolio, archived=()) -> str:
    """Full decision journal — every BUY / SELL / HOLD / RESET with context.
    The compare-and-learn record: why we bought, why we sold, why we kept.
    `archived` = lines that left the in-RAM cap for the Mongo
    journal_archive (audit M-5), oldest first; they precede the book's own."""
    path = os.path.join(_ensure_dir(portfolio), "decision_journal.csv")
    entries = list(archived or ()) + list(portfolio.decision_journal or [])
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


# ---------- watchlist captures (owner item 28, 2026-10-12) ----------

WATCHLIST_CSV_SUBDIR = "watchlist"   # <state_dir>/watchlist/watchlist_<ts_key>.csv
WATCHLIST_CSV_KEEP = 60              # files kept on disk; Mongo keeps 120 docs

WATCHLIST_CSV_COLS = [
    # The first three repeat the snapshot header on every row so a file read
    # back from disk (Mongo down) still says which scan it came from.
    "snapshot_ts_ist", "scan_mode", "floor",
    "rank", "ticker", "score", "sector", "abs_mom", "timing", "candle",
    "liquidity", "liquidity_why", "outcome", "reason_if_skipped",
    "held", "opened", "first_seen", "multi", "components", "structures",
]


def watchlist_csv_dir(state_dir: str) -> str:
    d = os.path.join(state_dir, WATCHLIST_CSV_SUBDIR)
    os.makedirs(d, exist_ok=True)
    return d


def watchlist_csv_path(state_dir: str, ts_key: str) -> str:
    return os.path.join(watchlist_csv_dir(state_dir), f"watchlist_{ts_key}.csv")


def _kv(d: dict) -> str:
    """dict -> 'k=v;k=v' (the decision-journal CSV convention)."""
    if not isinstance(d, dict) or not d:
        return ""
    return ";".join(f"{k}={v}" for k, v in d.items())


def write_watchlist_csv(snapshot: dict, state_dir: str) -> str:
    """One CSV per capture: <state_dir>/watchlist/watchlist_<ts_key>.csv.

    Writes the rows exactly as the snapshot holds them (rank order), then
    prunes the directory to the newest WATCHLIST_CSV_KEEP files — only
    `watchlist_*.csv` files are ever removed, nothing else in state_dir.
    Render wipes /tmp on restart, so the Mongo copy is the durable one and
    /reports regenerates a missing file from it; this is the no-Mongo path
    and the download cache.
    """
    key = str(snapshot.get("ts_key") or "")
    if not key:
        raise ValueError("snapshot has no ts_key")
    path = watchlist_csv_path(state_dir, key)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(WATCHLIST_CSV_COLS)
        for r in snapshot.get("rows") or []:
            w.writerow([
                snapshot.get("ts", ""), snapshot.get("scan_mode", ""),
                snapshot.get("floor", ""),
                r.get("rank", ""), r.get("ticker", ""), r.get("score", ""),
                r.get("sector", ""), r.get("abs_mom", ""), r.get("timing", ""),
                r.get("candle", ""),
                r.get("liquidity", ""), r.get("liquidity_why", ""),
                r.get("outcome", ""), r.get("reason_if_skipped", ""),
                r.get("held", ""), r.get("opened", ""), r.get("first_seen", ""),
                _kv(r.get("multi") or {}), _kv(r.get("components") or {}),
                _kv(r.get("structures") or {}),
            ])
    _prune_watchlist_csvs(os.path.dirname(path))
    return path


def _prune_watchlist_csvs(d: str, keep: int = WATCHLIST_CSV_KEEP) -> int:
    """Delete the oldest watchlist_*.csv beyond `keep`. The ts_key in the
    name sorts chronologically, so a name sort IS a time sort."""
    try:
        names = sorted(n for n in os.listdir(d)
                       if n.startswith("watchlist_") and n.endswith(".csv"))
    except OSError:
        return 0
    gone = 0
    for n in names[:-keep] if keep > 0 else names:
        try:
            os.remove(os.path.join(d, n))
            gone += 1
        except OSError:
            pass
    return gone


def list_watchlist_csvs(state_dir: str) -> list:
    """Captures present on disk, newest first: [{ts_key, path}]."""
    d = os.path.join(state_dir, WATCHLIST_CSV_SUBDIR)
    try:
        names = sorted((n for n in os.listdir(d)
                        if n.startswith("watchlist_") and n.endswith(".csv")),
                       reverse=True)
    except OSError:
        return []
    return [{"ts_key": n[len("watchlist_"):-len(".csv")],
             "path": os.path.join(d, n)} for n in names]
