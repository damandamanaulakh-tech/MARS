"""Regime + Ocean engine status page."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth, state as app_state
from ..templating import templates


router = APIRouter()
_LAST_REGIME = None
_LAST_REGIME_AT: Optional[datetime] = None


@router.get("/engine", response_class=HTMLResponse)
def engine_view(request: Request):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)

    global _LAST_REGIME, _LAST_REGIME_AT
    # Refresh regime read every 15 min max (network-bound)
    needs_refresh = (
        _LAST_REGIME is None
        or _LAST_REGIME_AT is None
        or (datetime.utcnow() - _LAST_REGIME_AT).total_seconds() > 900
    )
    if needs_refresh:
        try:
            from trading.regime import fetch_regime
            _LAST_REGIME = fetch_regime()
            _LAST_REGIME_AT = datetime.utcnow()
        except Exception as e:
            _LAST_REGIME = None

    return templates.TemplateResponse(
        request, "engine.html",
        {
            "user": auth.current_user(request),
            "active": "engine",
            "regime": _LAST_REGIME,
            "regime_at": _LAST_REGIME_AT,
        },
    )


@router.post("/engine/refresh")
def engine_refresh(request: Request):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    global _LAST_REGIME, _LAST_REGIME_AT
    try:
        from trading.regime import fetch_regime
        _LAST_REGIME = fetch_regime()
        _LAST_REGIME_AT = datetime.utcnow()
    except Exception:
        pass
    return RedirectResponse("/engine", status_code=303)
