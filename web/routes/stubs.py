"""Placeholder pages for nav items that ship in Phase 2 / 3."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth
from ..templating import templates


router = APIRouter()


def _stub(request: Request, active: str, eyebrow: str, title: str,
          subtitle: str, emoji: str, note: str) -> HTMLResponse:
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request, "stub.html",
        {
            "user": auth.current_user(request),
            "active": active,
            "eyebrow": eyebrow,
            "title": title,
            "subtitle": subtitle,
            "emoji": emoji,
            "note": note,
        },
    )


# /alerts now lives in routes/alerts_routes.py (real implementation)
