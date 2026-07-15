"""Alerts — Slack + email config status, recent alerts log, test-send."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth, state as app_state
from ..templating import templates


router = APIRouter()


@router.get("/alerts", response_class=HTMLResponse)
def alerts_view(request: Request, sent: str | None = None, error: str | None = None):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    try:
        from trading.alerts import alerts_status
        status = alerts_status()
    except Exception as e:
        status = {"slack_configured": False, "email_configured": False,
                  "recent": [], "error": str(e)}
    portfolio = app_state.portfolio()
    return templates.TemplateResponse(
        request, "alerts.html",
        {
            "user": auth.current_user(request),
            "active": "alerts",
            "status": status,
            "regime_history": list(reversed(portfolio.regime_history or []))[:20],
            "sent": sent,
            "error": error,
        },
    )


@router.post("/alerts/test")
def alerts_test(request: Request):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    try:
        from trading.alerts import send_slack
        r = send_slack(":wave: AM07 alert test from /alerts page.")
        if r.sent:
            return RedirectResponse("/alerts?sent=ok", status_code=303)
        return RedirectResponse(f"/alerts?error={r.error or 'unknown'}", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/alerts?error={type(e).__name__}", status_code=303)
