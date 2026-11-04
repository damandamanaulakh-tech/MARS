"""Read-only source status and official notices, served from saved readings."""
from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth
from ..templating import templates


router = APIRouter()


def _public_url(value) -> str:
    """Stored feed content is untrusted, including its link scheme."""
    value = str(value or "").strip()
    if not value or any(ord(c) < 32 for c in value):
        return ""
    try:
        parts = urlsplit(value)
        if (parts.scheme.lower() in ("https", "http") and parts.hostname
                and not parts.username and not parts.password):
            return value
    except ValueError:
        pass
    return ""


@router.get("/resources", response_class=HTMLResponse)
def resources_page(request: Request):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    # Keep the owner's existing viewer boundary explicit for direct callers.
    if user.get("role") != "owner":
        return HTMLResponse("Owner access required for resource diagnostics.", status_code=403)
    from .. import resource_feed
    try:
        saved = resource_feed.resource_view() or {}
    except Exception:
        saved = {"warnings": ["Saved source readings could not be loaded. Try again after the database connection recovers."]}
    # Work on copies: rendering cannot alter the cached report.
    sources = [dict(r, url=_public_url(r.get("url")))
               for r in saved.get("sources", []) if isinstance(r, dict)]
    notices = [dict(r, url=_public_url(r.get("url")))
               for r in saved.get("notices", []) if isinstance(r, dict)]
    return templates.TemplateResponse(request, "resources.html", {
        "user": user, "active": "resources",
        "sources": sources, "notices": notices,
        "updated_at": saved.get("updated_at") or "",
        "warnings": saved.get("warnings") or [],
    })
