"""Strategy weights + signal library view.

Read-only page showing every AM 07 component, its weight in the active mode,
which sub-signals feed it, and whether it's live or stubbed.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth, state as app_state
from ..templating import templates


router = APIRouter()


COMPONENT_DESCRIPTIONS = {
    "momentum_1m":       ("1-month + 3-month return blend (cross-sectional rank)", "live"),
    "volume_expansion":  ("5d/20d volume ratio + OBV slope direction", "live"),
    "earnings_growth":   ("yfinance quarterly YoY EPS growth", "live (snapshot only)"),
    "catalyst":          ("Claude-scored news sentiment over last 7-day headlines", "live (needs ANTHROPIC_API_KEY)"),
    "relative_strength": ("3M + 6M return vs Nifty 50 benchmark blend", "live"),
    "vol_adj_upside":    ("Sharpe + Sortino-style return-to-vol blend", "live"),
    "short_interest":    ("Put-Call Ratio proxy — no free NSE source", "stubbed @ 50"),
    "analyst_revision":  ("yfinance recommendations — flaky for NSE", "stubbed @ 50"),
    "breakout_quality":  ("Distance from 20-day + 50-day high blend", "live"),
    "sector_alignment":  ("Sector-peer avg momentum + PIB policy boost", "live"),
}


@router.get("/strategy", response_class=HTMLResponse)
def strategy_view(request: Request):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    cfg = app_state.cfg()
    try:
        from trading.modes import get_weights
        active_weights = get_weights(getattr(cfg, "mode", "swing"))
    except ImportError:
        from trading.config import AM07_WEIGHTS
        active_weights = AM07_WEIGHTS

    try:
        from trading.signals.policy import active_sector_boosts
        live_boosts = active_sector_boosts()
    except Exception:
        live_boosts = {}

    rows = []
    for comp, weight in active_weights.items():
        desc, status = COMPONENT_DESCRIPTIONS.get(comp, ("?", "?"))
        rows.append({
            "component": comp,
            "weight": weight,
            "weight_pct": weight * 100,
            "description": desc,
            "status": status,
        })
    rows.sort(key=lambda r: -r["weight"])

    return templates.TemplateResponse(
        request, "strategy.html",
        {
            "user": auth.current_user(request),
            "active": "strategy",
            "rows": rows,
            "current_mode": getattr(cfg, "mode", "swing"),
            "live_boosts": live_boosts,
        },
    )
