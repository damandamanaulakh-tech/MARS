"""Login / logout routes."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth
from ..templating import templates


router = APIRouter()


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
    user = auth.verify(email, password)
    if not user:
        return RedirectResponse(
            "/login?error=invalid",
            status_code=303,
        )
    request.session["user"] = user
    return RedirectResponse("/", status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.pop("user", None)
    return RedirectResponse("/login", status_code=303)
