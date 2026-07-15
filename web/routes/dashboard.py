"""Master dashboard route."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth, state
from ..templating import templates


router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    user = auth.current_user(request)
    p = state.portfolio()
    cfg = state.cfg()

    # No quote fetch on dashboard load — that hits yfinance on every page hit.
    # Use entry_price as a mark estimate; live quotes happen on /scan refresh.
    prices = {pos.ticker: pos.entry_price for pos in p.positions.values()}
    equity = p.equity(prices)
    deployed = p.deployed_value(prices)
    cash_reserve_pct = (p.cash / equity * 100) if equity else 100
    today_pnl = 0.0       # filled by background refresh later
    today_pnl_pct = 0.0
    exits_due = 0          # filled by check_exit on refresh

    kpis = [
        {"label": "TOTAL EQUITY", "big": equity, "fmt": "inr_lakh",
         "trail": "Paper portfolio mark", "href": "/positions"},
        {"label": "OPEN POSITIONS", "big": len(p.positions), "fmt": "int",
         "trail": f"of {cfg.max_positions} max", "href": "/positions",
         "highlight": False},
        {"label": "TODAY'S P&L", "big": today_pnl, "fmt": "inr",
         "trail": f"{today_pnl_pct:+.2f}% session",
         "href": "/positions", "highlight": True},
        {"label": "EXITS DUE TODAY", "big": exits_due, "fmt": "int",
         "trail": "From rule triggers",
         "href": "/scan"},
    ]

    cash_pct = (p.cash / cfg.starting_capital * 100) if cfg.starting_capital else 0
    deployed_pct = (deployed / cfg.starting_capital * 100) if cfg.starting_capital else 0
    reserve_floor_pct = cfg.cash_reserve_pct * 100

    # Equity curve: last 100 points (timestamps + values for Chart.js)
    eq_hist = list(p.equity_history or [])[-100:]
    eq_labels = [ts[:16].replace("T", " ") for ts, _ in eq_hist]
    eq_values = [round(float(v), 2) for _, v in eq_hist]

    # Engine tweaks: surface anything the engine is overriding vs config baseline
    tweaks = []
    if deployed_pct < 60 and len(p.positions) < cfg.max_positions:
        tweaks.append(f"Deployment {deployed_pct:.0f}% — target 60-70%. Run scan or wait for daily auto-scan at 09:20 IST.")
    if cfg.cash_reserve_pct > 0.25:
        tweaks.append(f"Cash reserve raised to {cfg.cash_reserve_pct*100:.0f}% by regime classifier (defensive tilt).")
    if cfg.mode == "intraday":
        tweaks.append("Intraday mode active — all positions auto-square-off at 15:25 IST.")
    import os as _os
    if _os.environ.get("DAILY_SCHEDULER") != "1":
        tweaks.append("Daily auto-scan is OFF. Set DAILY_SCHEDULER=1 in Render env to turn on automatic 09:20 IST trades.")

    return templates.TemplateResponse(
        request, "dashboard.html",
        {
            "user": user,
            "active": "dashboard",
            "today": datetime.utcnow().strftime("%a, %d %b"),
            "kpis": kpis,
            "positions": list(p.positions.values())[:5],
            "last_scan": p.last_scan,
            "deployed_pct": deployed_pct,
            "cash_pct": cash_pct,
            "reserve_floor_pct": reserve_floor_pct,
            "starting_capital": cfg.starting_capital,
            "deployed": deployed,
            "cash": p.cash,
            "quote_source": state.quote_source_status(),
            "db_status": state.db_status(),
            "current_mode": getattr(cfg, "mode", "swing"),
            "mode_choices": [
                ("intraday", "Intraday"),
                ("short", "Short"),
                ("swing", "Swing"),
                ("positional", "Positional"),
                ("nifty200_momentum", "N200 Momentum"),
            ],
            "eq_labels": eq_labels,
            "eq_values": eq_values,
            "tweaks": tweaks,
        },
    )
