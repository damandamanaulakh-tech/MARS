"""Reports — generate + serve CSV exports of picks, positions, trades, equity.

Every CSV is regenerated on each page load and on download — files always
exist (with headers) even when the portfolio is empty, so the user can
always download something for record.
"""

from __future__ import annotations

import csv
import logging
import os
import re
from typing import Dict, Optional

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from .. import auth, db, state as app_state
from ..templating import templates


log = logging.getLogger(__name__)
router = APIRouter()

# Owner item 28 (2026-10-12): watchlist captures. The key is the capture's
# IST minute (web/watchlist_capture.ts_key_for) — validated here so a path
# segment can never reach the filesystem as anything but that shape.
_WATCHLIST_KEY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{4}$")
_WATCHLIST_LIST_FIELDS = ("ts", "ts_key", "scan_mode", "floor", "n_rows",
                          "n_above_floor", "head", "source", "universe_count")


def _ist_label(ts: str) -> str:
    """'2026-10-12T09:35:12+05:30' -> '2026-10-12 09:35 IST' (display)."""
    s = str(ts or "")
    return (s[:10] + " " + s[11:16] + " IST") if len(s) >= 16 else s


def _snapshot_from_csv(path: str, max_rows: int = 300) -> Optional[dict]:
    """Rebuild a capture's summary + rows from its CSV (Mongo down or the
    document already trimmed). Bounded read; the CSV repeats the snapshot
    header on every row, so one file is self-describing."""
    try:
        with open(path, newline="") as fh:
            rd = csv.DictReader(fh)
            rows = []
            for r in rd:
                rows.append(r)
                if len(rows) >= max_rows:
                    break
    except OSError:
        return None
    first = rows[0] if rows else {}

    def _b(v):
        return {"True": True, "False": False}.get(str(v), None)

    return {
        "ts": first.get("snapshot_ts_ist", ""),
        "scan_mode": first.get("scan_mode", ""),
        "floor": first.get("floor", ""),
        "n_rows": len(rows),
        "head": [r.get("ticker", "") for r in rows[:8]],
        "source": "csv",
        "rows": [{
            "rank": r.get("rank", ""), "ticker": r.get("ticker", ""),
            "score": r.get("score", ""), "sector": r.get("sector", ""),
            "abs_mom": _b(r.get("abs_mom")), "timing": _b(r.get("timing")),
            "candle": r.get("candle", ""), "liquidity": _b(r.get("liquidity")),
            "liquidity_why": r.get("liquidity_why", ""),
            "outcome": r.get("outcome", ""),
            "reason_if_skipped": r.get("reason_if_skipped", ""),
            "held": _b(r.get("held")), "opened": _b(r.get("opened")),
            "first_seen": r.get("first_seen", ""),
            "multi": r.get("multi", ""),
        } for r in rows],
    }


def _watchlist_entries(limit: int = 60) -> list:
    """The captures to list on /reports, newest first. Mongo first (summary
    fields only — never the rows of 60 captures on one page); the CSV
    directory when Mongo has nothing (local run, outage). Each entry says
    where it came from. Never raises — the card hides on failure."""
    entries = []
    try:
        for d in db.list_watchlist_snapshots(limit=limit,
                                             fields=_WATCHLIST_LIST_FIELDS):
            key = str(d.get("ts_key") or "")
            if not _WATCHLIST_KEY_RE.match(key):
                continue
            entries.append({**d, "ts_key": key, "when": _ist_label(d.get("ts")),
                            "where": "mongo"})
    except Exception:
        log.exception("watchlist listing (mongo) failed")
        entries = []
    if entries:
        return entries
    try:
        from trading.reports import list_watchlist_csvs
        for f in list_watchlist_csvs(app_state.cfg().state_dir)[:limit]:
            if not _WATCHLIST_KEY_RE.match(f["ts_key"]):
                continue
            snap = _snapshot_from_csv(f["path"], max_rows=300)
            if not snap:
                continue
            entries.append({
                "ts_key": f["ts_key"], "ts": snap["ts"],
                "when": _ist_label(snap["ts"]) or f["ts_key"],
                "scan_mode": snap["scan_mode"], "floor": snap["floor"],
                "n_rows": snap["n_rows"], "head": snap["head"],
                "source": "csv", "where": "csv",
            })
    except Exception:
        log.exception("watchlist listing (csv) failed")
    return entries


def _watchlist_show(ts_key: Optional[str]) -> Optional[dict]:
    """The one capture the owner asked to see on the page, rows included
    (bounded to the capture's own cap). Mongo document first, CSV second."""
    if not ts_key or not _WATCHLIST_KEY_RE.match(ts_key):
        return None
    try:
        doc = db.load_watchlist_snapshot(ts_key)
    except Exception:
        doc = None
    if doc:
        return {**doc, "ts_key": ts_key, "when": _ist_label(doc.get("ts")),
                "where": "mongo"}
    try:
        from trading.reports import watchlist_csv_path
        path = watchlist_csv_path(app_state.cfg().state_dir, ts_key)
        if os.path.exists(path):
            snap = _snapshot_from_csv(path)
            if snap:
                return {**snap, "ts_key": ts_key, "when": _ist_label(snap["ts"]),
                        "where": "csv"}
    except Exception:
        log.exception("watchlist show (csv) failed")
    return None


def _generate_all() -> Dict[str, Optional[str]]:
    """Triggers report generation; returns {name: path}. Each file is
    always written (headers + rows). Errors are caught per-report so one
    bad generator can't take the others down."""
    try:
        from trading.reports import (
            write_equity_csv, write_picks_csv, write_skipped_csv,
            write_positions_csv, write_trades_csv, write_journal_csv,
        )
    except ImportError:
        return {}
    portfolio = app_state.portfolio()
    # AUDIT FIX B4 (2026-08-09): this export used to pass ENTRY prices as
    # "current_price", so unrealised P&L was always 0.00 — fabricated flat
    # marks in the owner's own download. Live marks now; a name without a
    # live quote falls back to entry and the row SAYS so (price_source).
    tickers = [pos.ticker for pos in portfolio.positions.values()]
    live, _ = app_state.marks(tickers)
    prices = {pos.ticker: live.get(pos.ticker, pos.entry_price)
              for pos in portfolio.positions.values()}
    srcs = app_state.marks_sources(tickers)
    price_source = {
        pos.ticker: ("live" if srcs.get(pos.ticker) == "upstox"
                     else "delayed_close" if pos.ticker in live
                     else "entry")
        for pos in portfolio.positions.values()}

    paths: Dict[str, Optional[str]] = {}
    writers = [
        ("today_picks", lambda: write_picks_csv(portfolio)),
        ("positions",   lambda: write_positions_csv(portfolio, prices,
                                                     price_source=price_source)),
        # Audit M-5 / M-10: the archives are read here so a line or a trade
        # that left the book is still in the owner's download.
        ("trades",      lambda: write_trades_csv(portfolio,
                                                 archived=db.list_trades())),
        ("equity",      lambda: write_equity_csv(portfolio)),
        ("skipped",     lambda: write_skipped_csv(portfolio)),
        ("decision_journal", lambda: write_journal_csv(
            portfolio, archived=db.list_journal_archive())),
    ]
    for name, fn in writers:
        try:
            paths[name] = fn()
        except Exception:
            paths[name] = None
    return paths


@router.get("/reports", response_class=HTMLResponse)
def reports_view(request: Request, generated: str | None = None,
                 show: str | None = None):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    paths = _generate_all()
    files = []
    for name, p in paths.items():
        if p and os.path.exists(p):
            size_kb = os.path.getsize(p) / 1024
            # Row count = non-empty data lines (excl. header)
            try:
                with open(p) as fh:
                    rows = max(sum(1 for _ in fh) - 1, 0)
            except Exception:
                rows = 0
            files.append({"name": name, "path": p, "size_kb": size_kb, "rows": rows})
        else:
            files.append({"name": name, "path": None, "size_kb": 0.0, "rows": 0})
    portfolio = app_state.portfolio()
    scan_hist = list(reversed(portfolio.scan_history or []))[:50]
    return templates.TemplateResponse(
        request, "reports.html",
        {
            "user": auth.current_user(request),
            "active": "reports",
            "files": files,
            "generated": generated,
            "scan_history": scan_hist,
            # Owner item 28: the captured watchlists, newest first, and the
            # one expanded on the page (?show=<ts_key>).
            "watchlist": _watchlist_entries(),
            "watchlist_show": _watchlist_show(show),
        },
    )


@router.get("/reports/watchlist/{ts_key}.csv")
def download_watchlist(request: Request, ts_key: str):
    """One capture as CSV. The file on disk when it exists; otherwise
    regenerated from the Mongo document (Render wipes /tmp on restart) —
    the download never depends on a file surviving a deploy."""
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    if not _WATCHLIST_KEY_RE.match(ts_key or ""):
        return RedirectResponse("/reports?generated=missing", status_code=303)
    from trading.reports import watchlist_csv_path, write_watchlist_csv
    state_dir = app_state.cfg().state_dir
    path = watchlist_csv_path(state_dir, ts_key)
    if not os.path.exists(path):
        doc = None
        try:
            doc = db.load_watchlist_snapshot(ts_key)
        except Exception:
            doc = None
        if not doc:
            return RedirectResponse("/reports?generated=missing", status_code=303)
        try:
            path = write_watchlist_csv({**doc, "ts_key": ts_key}, state_dir)
        except Exception:
            log.exception("watchlist CSV regeneration failed")
            return RedirectResponse("/reports?generated=missing", status_code=303)
    return FileResponse(path, media_type="text/csv",
                        filename=f"sourceborn_watchlist_{ts_key}.csv")


@router.post("/reports/backup")
def trigger_backup(request: Request):
    """Force a Mongo backup of the in-memory portfolio (save() round-trips)."""
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    try:
        app_state.portfolio().save()
        return RedirectResponse("/reports?generated=backup_ok", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/reports?generated=backup_err_{type(e).__name__}", status_code=303)


@router.get("/reports/download/{name}")
def download(request: Request, name: str):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    paths = _generate_all()
    p = paths.get(name)
    if not p or not os.path.exists(p):
        return RedirectResponse("/reports?generated=missing", status_code=303)
    filename = f"sourceborn_{name}.csv"
    return FileResponse(p, media_type="text/csv", filename=filename)
