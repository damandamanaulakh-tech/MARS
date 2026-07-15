"""Upstox OAuth flow + connection status routes.

Routes:
  GET  /upstox/login      Kicks off OAuth (302 -> Upstox auth dialog)
  GET  /upstox/callback   Upstox returns here with ?code=...; we exchange
                          for an access_token and store it in the session.
  POST /upstox/disconnect Clears the token from the session.
  GET  /upstox/status     JSON: connection state for the UI to poll.

Token storage: Phase 3 stores the access_token in the FastAPI session
cookie. Phase 4 moves it to MongoDB so it survives server restarts.
"""

from __future__ import annotations

import logging
import secrets
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from trading.brokers.upstox import UpstoxAuthError, UpstoxClient, UpstoxConfigError

from .. import auth, db
from .. import state as app_state


log = logging.getLogger(__name__)
router = APIRouter()


def _client_from_session(request: Request) -> Optional[UpstoxClient]:
    """Rebuild an UpstoxClient from the session's stored token.

    Falls back to Mongo-persisted token if the session is empty (e.g. after
    a Render restart). Hot-restoration is the main reason we persist to Mongo.
    """
    token = request.session.get("upstox_token")
    user_id = request.session.get("upstox_user_id")
    user_name = request.session.get("upstox_user_name")
    connected_at = request.session.get("upstox_connected_at")

    # Fallback to Mongo if session is empty
    if not token:
        persisted = db.load_upstox_token()
        if persisted:
            token = persisted.get("access_token")
            user_id = persisted.get("user_id")
            user_name = persisted.get("user_name")
            connected_at = persisted.get("connected_at")
            # Rehydrate the session so subsequent requests on this device skip Mongo
            if token:
                request.session["upstox_token"] = token
                request.session["upstox_user_id"] = user_id
                request.session["upstox_user_name"] = user_name
                request.session["upstox_connected_at"] = connected_at

    if not token:
        return None
    try:
        client = UpstoxClient.from_env()
    except UpstoxConfigError:
        return None
    client.access_token = token
    client.user_id = user_id
    client.user_name = user_name
    client.connected_at = connected_at
    return client


@router.get("/upstox/login")
def login(request: Request):
    """Redirect the user to Upstox's authorization dialog."""
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    try:
        client = UpstoxClient.from_env()
    except UpstoxConfigError as e:
        log.warning("Upstox login attempt without config: %s", e)
        return RedirectResponse("/?upstox_error=config_missing", status_code=303)

    # CSRF protection: store a random state in the session, verify on callback
    state = secrets.token_urlsafe(16)
    request.session["upstox_oauth_state"] = state
    return RedirectResponse(client.authorization_url(state=state), status_code=303)


@router.get("/upstox/callback")
def callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    """Receive the auth code from Upstox and exchange it for a token."""
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)

    if error or not code:
        log.info("Upstox callback declined: error=%s code=%s", error, code)
        return RedirectResponse(
            f"/?upstox_error={error or 'no_code'}", status_code=303,
        )

    # CSRF check
    expected_state = request.session.pop("upstox_oauth_state", None)
    if state and expected_state and state != expected_state:
        log.warning("Upstox callback state mismatch")
        return RedirectResponse("/?upstox_error=state_mismatch", status_code=303)

    try:
        client = UpstoxClient.from_env()
        body = client.exchange_code(code)
    except UpstoxConfigError:
        return RedirectResponse("/?upstox_error=config_missing", status_code=303)
    except UpstoxAuthError as e:
        log.warning("Upstox token exchange failed: %s", e)
        return RedirectResponse("/?upstox_error=token_exchange_failed", status_code=303)

    user_name = body.get("user_name") or body.get("email") or "Upstox"
    token_doc = {
        "access_token": body.get("access_token"),
        "user_id": body.get("user_id"),
        "user_name": user_name,
        "connected_at": client.connected_at,
    }
    # Session (this browser)
    request.session["upstox_token"] = token_doc["access_token"]
    request.session["upstox_user_id"] = token_doc["user_id"]
    request.session["upstox_user_name"] = user_name
    request.session["upstox_connected_at"] = client.connected_at
    # Mongo (survives Render restart, accessible from any browser)
    db.save_upstox_token(token_doc)
    # Hot-attach to the running engine so refresh() starts using Upstox quotes
    app_state.refresh_broker_attachment()
    return RedirectResponse("/?upstox_connected=1", status_code=303)


@router.post("/upstox/disconnect")
def disconnect(request: Request):
    """Clear stored Upstox credentials (token still valid on Upstox side
    until 03:30 IST; this only forgets it locally)."""
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    for key in (
        "upstox_token", "upstox_user_id", "upstox_user_name",
        "upstox_connected_at", "upstox_oauth_state",
    ):
        request.session.pop(key, None)
    db.clear_upstox_token()
    app_state.refresh_broker_attachment()
    return RedirectResponse("/?upstox_disconnected=1", status_code=303)


@router.get("/upstox/status")
def status(request: Request):
    """JSON endpoint for the dashboard to poll (or pages to render)."""
    if not auth.current_user(request):
        return JSONResponse({"connected": False, "reason": "not_logged_in"})

    # Env presence check (so the UI can suggest "set env vars" vs "click connect")
    try:
        UpstoxClient.from_env()
        env_ok = True
    except UpstoxConfigError:
        env_ok = False

    client = _client_from_session(request)
    if not client:
        return JSONResponse({
            "connected": False,
            "env_ok": env_ok,
        })

    return JSONResponse({
        "connected": True,
        "env_ok": True,
        "user_id": client.user_id,
        "user_name": client.user_name,
        "connected_at": client.connected_at,
        "hours_until_expiry": client.hours_until_expiry(),
    })
