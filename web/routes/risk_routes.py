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

    # AUDIT FIX B3 (2026-08-09): this page used to re-run the governor
    # with ENTRY-price marks on every page view — mutating live risk state
    # with stale data and showing the result unlabeled. It is read-only now:
    # the governor is updated only by the engine's own scan/refresh passes on
    # live quotes, and the page says which reading it is showing.
    # (An external review on PR#40 caught that the first cut of this fix deleted
    # the equity the template still renders — /risk 500'd. Equity is computed
    # read-only here, on entry marks, and labelled as such by the template.)
    prices = {p2.ticker: p2.entry_price for p2 in eng.portfolio.positions.values()}
    equity = eng.portfolio.equity(prices)
    try:
        dd = eng.drawdown_gov.snapshot()
    except AttributeError:
        dd = None
    try:
        cl = eng.loss_tracker.snapshot(
            open_positions=len(eng.portfolio.positions))
    except AttributeError:
        cl = None

    # v6/v7: IFR status — DAMAGE_CLUSTER + TAIL_DAMAGE_RATIO (Q2-validated).
    # Read from persisted fire dates; full recompute is too expensive per pageview.
    from datetime import datetime as _dt
    try:
        from trading.ifr import ACTIVE_DAYS_AFTER_FIRE
    except ImportError:
        ACTIVE_DAYS_AFTER_FIRE = 22

    def _ifr_signal(last_fire: str | None) -> dict | None:
        if not last_fire:
            return None
        try:
            prev = _dt.fromisoformat(last_fire).date()
            days_since = (_dt.utcnow().date() - prev).days
            return {
                "last_fire": last_fire,
                "days_since": days_since,
                "active": days_since <= ACTIVE_DAYS_AFTER_FIRE,
            }
        except Exception:
            return None

    cluster = _ifr_signal(eng.portfolio.last_ifr_fire_date)
    ratio   = _ifr_signal(eng.portfolio.last_ifr_ratio_fire_date)
    any_active = bool((cluster and cluster["active"]) or (ratio and ratio["active"]))
    ifr = {
        "cluster": cluster,
        "ratio":   ratio,
        "active":  any_active,
        "sizing_multiplier": 0.50 if any_active else 1.0,
        "window_days": ACTIVE_DAYS_AFTER_FIRE,
    } if (cluster or ratio) else None

    return templates.TemplateResponse(
        request, "risk.html",
        {
            "user": auth.current_user(request),
            "active": "risk",
            "dd": dd,
            "cl": cl,
            "ifr": ifr,
            "cfg": cfg,
            "equity": equity,
        },
    )
