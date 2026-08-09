"""Login / logout routes."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth
from ..templating import templates


router = APIRouter()
_FAILS = 0


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str | None = None):
    if auth.current_user(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request, "login.html",
        {"error": error},
    )


@router.post("/login")
def login_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    # AUDIT E (2026-08-09): there was NO rate limit — combined with the
    # fallback password, the owner desk was brute-forceable at network speed
    # on a public URL. 1s per attempt, +5s once 10 straight failures pile up
    # (decays on the next attempt — never a permanent lockout of the owner).
    # SB_LOGIN_DELAY_SEC=0 (tests) turns the throttle off; production default 1s.
    import os as _os
    import time as _time
    global _FAILS
    try:
        _delay = float(_os.environ.get("SB_LOGIN_DELAY_SEC", "1"))
    except ValueError:
        _delay = 1.0
    if _FAILS >= 10:
        _time.sleep(_delay * 5)
        _FAILS = 8
    _time.sleep(_delay)
    user = auth.verify(email, password)
    if not user:
        _bump_fail()
        return RedirectResponse(
            "/login?error=invalid",
            status_code=303,
        )
    _FAILS = 0
    request.session["user"] = user
    return RedirectResponse("/", status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.pop("user", None)
    return RedirectResponse("/login", status_code=303)


def _bump_fail() -> None:
    global _FAILS
    _FAILS += 1
