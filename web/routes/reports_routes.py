"""Reports — generate + serve CSV exports of picks, positions, trades, equity.

Every CSV is regenerated on each page load and on download — files always
exist (with headers) even when the portfolio is empty, so the user can
always download something for record.
"""

from __future__ import annotations

import os
from typing import Dict, Optional

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from .. import auth, state as app_state
from ..templating import templates


router = APIRouter()


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
        ("trades",      lambda: write_trades_csv(portfolio)),
        ("equity",      lambda: write_equity_csv(portfolio)),
        ("skipped",     lambda: write_skipped_csv(portfolio)),
        ("decision_journal", lambda: write_journal_csv(portfolio)),
    ]
    for name, fn in writers:
        try:
            paths[name] = fn()
        except Exception:
            paths[name] = None
    return paths


@router.get("/reports", response_class=HTMLResponse)
def reports_view(request: Request, generated: str | None = None):
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
        },
    )


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
