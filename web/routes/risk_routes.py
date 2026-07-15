"""Risk dashboard — drawdown governor + consecutive-loss + active filters."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth, state as app_state
from ..templating import templates


router = APIRouter()


@router.get("/risk", response_class=HTMLResponse)
def risk_view(request: Request):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    eng = app_state.engine()
    cfg = app_state.cfg()

    # Refresh drawdown read with current equity
    prices = {p.ticker: p.entry_price for p in eng.portfolio.positions.values()}
    equity = eng.portfolio.equity(prices)
    try:
        eng.drawdown_gov.update(equity)
        dd = eng.drawdown_gov.snapshot()
    except AttributeError:
        dd = None
    try:
        cl = eng.loss_tracker.snapshot()
    except AttributeError:
        cl = None

    return templates.TemplateResponse(
        request, "risk.html",
        {
            "user": auth.current_user(request),
            "active": "risk",
            "dd": dd,
            "cl": cl,
            "cfg": cfg,
            "equity": equity,
        },
    )
