"""Open positions + closed trades pages."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth, state
from ..templating import templates
from trading.paper import PaperPosition


router = APIRouter()


# Friendly category labels for the position's entry mode.
_MODE_LABEL = {
    "intraday":          "Intraday",
    "short":             "Short",
    "swing":             "Swing",
    "positional":        "Long",
    "nifty200_momentum": "Momentum",
}


@router.get("/positions", response_class=HTMLResponse)
def positions_view(
    request: Request, error: str | None = None, seeded: str | None = None,
    removed: str | None = None,
):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    p = state.portfolio()
    cfg = state.cfg()

    tickers = [pos.ticker for pos in p.positions.values()]
    prices, quotes_live = state.marks(tickers)

    # F4: latest journaled current_score per ticker (from HOLD entries) so we
    # can flag positions decaying toward the score-decay exit floor.
    decay_floor = float(getattr(cfg, "score_decay_exit", 45.0))
    latest_score: dict[str, float] = {}
    # Piano detail row: the per-factor scores journaled at BUY time — the
    # position itself only stores the total, the journal has the breakdown.
    buy_components: dict[str, dict] = {}
    for e in reversed(p.decision_journal or []):
        t = e.get("ticker")
        if t and t not in latest_score and e.get("current_score") is not None:
            try:
                latest_score[t] = float(e["current_score"])
            except (TypeError, ValueError):
                pass
        if (t and t not in buy_components and e.get("action") == "BUY"
                and e.get("components")):
            buy_components[t] = e["components"]

    rows = []
    tot_invested = tot_value = 0.0
    for pos in p.positions.values():
        mode = getattr(pos, "mode", "") or ""
        category = _MODE_LABEL.get(mode, mode.title() if mode else "Manual")
        # Use the stop/target snapshotted at entry; fall back to the live cfg
        # percentages for manual/legacy positions that never stored them.
        stop_price = getattr(pos, "stop_price", 0.0) or 0.0
        target_price = getattr(pos, "target_price", 0.0) or 0.0
        if stop_price <= 0:
            stop_price = pos.entry_price * (1 + cfg.stop_loss_pct)
        if target_price <= 0:
            target_price = pos.entry_price * (1 + cfg.take_profit_pct)
        ltp = prices.get(pos.ticker) or pos.entry_price
        invested = pos.qty * pos.entry_price
        value = pos.qty * ltp
        pnl = value - invested
        pnl_pct = (ltp / pos.entry_price - 1) * 100 if pos.entry_price else 0.0
        tot_invested += invested
        tot_value += value
        cur_score = latest_score.get(pos.ticker)
        decay_watch = cur_score is not None and cur_score <= decay_floor + 5
        rows.append({
            "ticker": pos.ticker,
            "category": category,
            "qty": pos.qty,
            "entry_date": pos.entry_date.strftime("%Y-%m-%d"),
            "entry_price": pos.entry_price,
            "ltp": ltp,
            "value": value,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "current_score": cur_score,
            "decay_watch": decay_watch,
            "decay_floor": decay_floor,
            "target_price": target_price,
            "stop_price": stop_price,
            "to_target_pct": (target_price / ltp - 1) * 100 if ltp else 0.0,
            "to_stop_pct": (stop_price / ltp - 1) * 100 if ltp else 0.0,
            "peak_price": getattr(pos, "peak_price", pos.entry_price),
            "sector": pos.sector,
            "entry_score": pos.entry_score,
            "partial_taken": getattr(pos, "partial_profit_taken", False),
            "components": buy_components.get(pos.ticker) or {},
            "reasoning": pos.reasoning,
            "entry_notes": getattr(pos, "entry_notes", "") or "",
            "tags": getattr(pos, "tags", []) or [],
        })
    rows.sort(key=lambda r: -r["value"])
    totals = {
        "invested": tot_invested,
        "value": tot_value,
        "pnl": tot_value - tot_invested,
        "pnl_pct": ((tot_value / tot_invested - 1) * 100) if tot_invested else 0.0,
    }

    return templates.TemplateResponse(
        request, "positions.html",
        {
            "user": auth.current_user(request),
            "active": "positions",
            "rows": rows,
            "totals": totals,
            "quotes_live": quotes_live,
            "max_positions": cfg.max_positions,
            "error": error,
            "seeded": seeded,
            "removed": removed,
            "seed_count": len(_SEED_JUNE_2026),
        },
    )


@router.get("/trades", response_class=HTMLResponse)
def trades_view(request: Request):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    p = state.portfolio()
    cfg = state.cfg()

    starting_capital = cfg.starting_capital or 1.0
    rows = []
    for t in p.trades:
        net_pct = (t.exit_price / t.entry_price - 1) * 100 if t.entry_price else 0
        hold = (t.exit_date.date() - t.entry_date.date()).days
        capital_used = t.qty * t.entry_price            # F9: rupees deployed
        pct_of_capital = capital_used / starting_capital * 100
        rows.append({
            "ticker": t.ticker,
            "qty": t.qty,
            "entry_date": t.entry_date.strftime("%Y-%m-%d"),
            "exit_date": t.exit_date.strftime("%Y-%m-%d"),
            "hold_days": hold,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "capital_used": capital_used,
            "pct_of_capital": pct_of_capital,
            "net_pnl": t.net_pnl,
            "net_pct": net_pct,
            "reason": t.reason,
            "sector": t.sector,
        })

    wins = sum(1 for t in p.trades if t.net_pnl > 0)
    losses = sum(1 for t in p.trades if t.net_pnl < 0)
    total = len(p.trades)
    win_rate = (wins / total * 100) if total else 0.0
    net_total = sum(t.net_pnl for t in p.trades)
    # F9 + F19: gross profit vs gross loss so P&L reads next to win-rate
    gross_profit = sum(t.net_pnl for t in p.trades if t.net_pnl > 0)
    gross_loss = sum(t.net_pnl for t in p.trades if t.net_pnl < 0)
    total_capital_used = sum(r["capital_used"] for r in rows)
    net_pct_of_capital = net_total / starting_capital * 100

    return templates.TemplateResponse(
        request, "trades.html",
        {
            "losses": losses,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "total_capital_used": total_capital_used,
            "net_pct_of_capital": net_pct_of_capital,
            "starting_capital": starting_capital,
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


@router.post("/positions/{ticker}/remove")
def remove_position(request: Request, ticker: str):
    """Owner-only: drop a position from the book and refund its entry cash.
    The removal is journaled (append-only) — history is never deleted."""
    user = auth.current_user(request)
    if not user or user.get("role") != "owner":
        return RedirectResponse("/positions?error=owner_only", status_code=303)
    p = state.portfolio()
    pos = p.positions.pop(ticker, None)
    if pos is None:
        return RedirectResponse("/positions?error=not_found", status_code=303)
    refund = pos.qty * pos.entry_price
    p.cash += refund
    p.decision_journal.append({
        "ts": datetime.utcnow().isoformat(),
        "action": "REMOVE",
        "ticker": ticker,
        "qty": pos.qty,
        "entry_price": round(pos.entry_price, 2),
        "refunded": round(refund, 2),
        "why": "Manually removed from book by owner.",
        "cash_after": round(p.cash, 2),
    })
    p.save()
    return RedirectResponse(f"/positions?removed={ticker}", status_code=303)


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
