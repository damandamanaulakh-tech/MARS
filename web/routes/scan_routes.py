"""Scan + refresh routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth, state
from ..templating import templates


router = APIRouter()


@router.get("/scan", response_class=HTMLResponse)
def scan_view(request: Request, error: str | None = None):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    p = state.portfolio()
    return templates.TemplateResponse(
        request, "scan.html",
        {
            "user": auth.current_user(request),
            "active": "scan",
            "picks": p.last_scan_picks or [],
            "skipped": p.last_scan_skipped or [],
            "opened": p.last_scan_opened or [],
            "last_scan": p.last_scan,
            "error": error,
        },
    )


@router.post("/scan/run")
def scan_run(request: Request):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    eng = state.engine()
    try:
        eng.scan()
    except Exception as e:
        # Phase 1: surface error via flash query param; Phase 2 add toast UI.
        return RedirectResponse(f"/scan?error={type(e).__name__}", status_code=303)
    return RedirectResponse("/scan", status_code=303)


@router.post("/refresh")
def refresh(request: Request):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    eng = state.engine()
    try:
        eng.refresh()
    except Exception as e:
        return RedirectResponse(f"/?error={type(e).__name__}", status_code=303)
    return RedirectResponse("/", status_code=303)
