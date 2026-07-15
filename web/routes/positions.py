"""Open positions + closed trades pages."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth, state
from ..templating import templates
from trading.paper import PaperPosition


router = APIRouter()


@router.get("/positions", response_class=HTMLResponse)
def positions_view(
    request: Request, error: str | None = None, seeded: str | None = None,
):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    p = state.portfolio()
    cfg = state.cfg()

    rows = []
    for pos in p.positions.values():
        rows.append({
            "ticker": pos.ticker,
            "qty": pos.qty,
            "entry_date": pos.entry_date.strftime("%Y-%m-%d"),
            "entry_price": pos.entry_price,
            "peak_price": getattr(pos, "peak_price", pos.entry_price),
            "sector": pos.sector,
            "entry_score": pos.entry_score,
            "partial_taken": getattr(pos, "partial_profit_taken", False),
            "reasoning": pos.reasoning,
            "entry_notes": getattr(pos, "entry_notes", "") or "",
            "tags": getattr(pos, "tags", []) or [],
        })

    return templates.TemplateResponse(
        request, "positions.html",
        {
            "user": auth.current_user(request),
            "active": "positions",
            "rows": rows,
            "max_positions": cfg.max_positions,
            "error": error,
            "seeded": seeded,
            "seed_count": len(_SEED_JUNE_2026),
        },
    )


@router.get("/trades", response_class=HTMLResponse)
def trades_view(request: Request):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    p = state.portfolio()

    rows = []
    for t in p.trades:
        net_pct = (t.exit_price / t.entry_price - 1) * 100 if t.entry_price else 0
        hold = (t.exit_date.date() - t.entry_date.date()).days
        rows.append({
            "ticker": t.ticker,
            "qty": t.qty,
            "entry_date": t.entry_date.strftime("%Y-%m-%d"),
            "exit_date": t.exit_date.strftime("%Y-%m-%d"),
            "hold_days": hold,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "net_pnl": t.net_pnl,
            "net_pct": net_pct,
            "reason": t.reason,
            "sector": t.sector,
        })

    wins = sum(1 for t in p.trades if t.net_pnl > 0)
    total = len(p.trades)
    win_rate = (wins / total * 100) if total else 0.0
    net_total = sum(t.net_pnl for t in p.trades)

    return templates.TemplateResponse(
        request, "trades.html",
        {
            "user": auth.current_user(request),
            "active": "trades",
            "rows": rows,
            "wins": wins,
            "total": total,
            "win_rate": win_rate,
            "net_total": net_total,
        },
    )


@router.post("/positions/{ticker}/notes")
def update_notes(
    request: Request,
    ticker: str,
    entry_notes: str = Form(""),
    tags: str = Form(""),
):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    p = state.portfolio()
    pos = p.positions.get(ticker)
    if pos is None:
        return RedirectResponse("/positions?error=not_found", status_code=303)
    pos.entry_notes = entry_notes.strip()
    pos.tags = [t.strip() for t in tags.split(",") if t.strip()]
    p.save()
    return RedirectResponse("/positions", status_code=303)


# Manual sector hints for tickers not in cfg.sector_map yet
_SECTOR_HINTS = {
    "COCHINSHIP.NS": "Defense",
    "ATGL.NS":       "Energy",
    "SBINEQWETF.NS": "ETF",
    "COALINDIA.NS":  "Metal",
}


@router.post("/positions/new")
def add_manual_position(
    request: Request,
    ticker: str = Form(...),
    qty: int = Form(...),
    entry_price: float = Form(...),
    entry_date: str = Form(...),
    sector: str = Form(""),
    entry_notes: str = Form(""),
    tags: str = Form(""),
):
    """Manual position open — for real-world holdings you want to track here."""
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    p = state.portfolio()
    cfg = state.cfg()

    ticker = ticker.strip().upper()
    if not ticker.endswith(".NS") and not ticker.endswith(".BO"):
        ticker = f"{ticker}.NS"
    if ticker in p.positions:
        return RedirectResponse("/positions?error=already_held", status_code=303)
    try:
        entry_dt = datetime.fromisoformat(entry_date)
    except ValueError:
        entry_dt = datetime.utcnow()
    sec = (sector.strip() or cfg.sector_map.get(ticker)
           or _SECTOR_HINTS.get(ticker) or "Unknown")
    notional = qty * entry_price
    p.cash -= notional
    p.positions[ticker] = PaperPosition(
        ticker=ticker, qty=qty, entry_price=float(entry_price),
        entry_date=entry_dt, sector=sec, peak_price=float(entry_price),
        entry_score=70.0, reasoning="Manual entry — real holding tracked here.",
        entry_notes=entry_notes.strip(),
        tags=[t.strip() for t in tags.split(",") if t.strip()],
    )
    p.save()
    return RedirectResponse("/positions", status_code=303)


# Your 5 holdings from 1-2 June 2026 (Upstox + Up app screenshots),
# plus COALINDIA (the only YES timing-pass from the recent scan).
_SEED_JUNE_2026 = [
    # (ticker,        qty,  entry_price, entry_date,  sector,    notes)
    ("COCHINSHIP.NS", 15,   1453.80,    "2026-06-01", "Defense", "From Upstox holdings"),
    ("HDFCBANK.NS",   15,    745.55,    "2026-06-01", "Financial","From Upstox holdings"),
    ("TCS.NS",        15,   2320.90,    "2026-06-01", "IT",      "From Upstox holdings"),
    ("ATGL.NS",       51,    753.70,    "2026-06-01", "Energy",  "From Up app — Adani Total Gas"),
    ("SBINEQWETF.NS", 101,    32.89,    "2026-06-01", "ETF",     "From Up app — SBI Nifty 50 ETF"),
    ("COALINDIA.NS",  100,   472.60,    "2026-06-02", "Metal",   "Scan YES-timing pick (June 2)"),
]


@router.post("/positions/seed-mine")
def seed_mine(request: Request):
    """Idempotent: adds the 6 seed positions if not already present."""
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    p = state.portfolio()
    added = []
    for ticker, qty, price, date_iso, sector, note in _SEED_JUNE_2026:
        if ticker in p.positions:
            continue
        notional = qty * price
        p.cash -= notional
        p.positions[ticker] = PaperPosition(
            ticker=ticker, qty=qty, entry_price=float(price),
            entry_date=datetime.fromisoformat(date_iso),
            sector=sector, peak_price=float(price),
            entry_score=70.0,
            reasoning=f"Seeded from real holding on {date_iso}.",
            entry_notes=note, tags=["seed_june_2026", "real_holding"],
        )
        added.append(ticker)
    if added:
        p.save()
    return RedirectResponse(f"/positions?seeded={len(added)}", status_code=303)
