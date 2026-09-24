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


#: What each callback outcome means, in the owner's words — rendered by the
#: dashboard. 2026-09-24: the callback had ALWAYS redirected with
#: ?upstox_error=... but nothing on the dashboard read it, so a failed
#: connect looked exactly like "nothing happened".
CONNECT_NOTICES = {
    "token_exchange_failed": ("error",
        "Upstox refused to exchange the login code for a token. Usually the "
        "API secret in Render no longer matches the app on Upstox's developer "
        "page. The journal line UPSTOX_CONNECT_FAILED carries Upstox's reply."),
    "state_mismatch": ("error",
        "The login came back with a different security state than this browser "
        "started with (two login tabs open?). Start Connect again from here."),
    "no_code": ("error",
        "Upstox sent the browser back without a login code — the login was "
        "cancelled or declined on Upstox's side."),
    "access_denied": ("error",
        "Upstox reported access denied for this login."),
    "config_missing": ("error",
        "UPSTOX_API_KEY / UPSTOX_API_SECRET / UPSTOX_REDIRECT_URI are not all "
        "set in Render, so the app cannot start a login."),
    "token_not_persisted": ("warn",
        "Connected — the engine has today's token and quotes are live for this "
        "run. But the token could NOT be saved to the database, so a restart "
        "will need a fresh login. The journal line UPSTOX_CONNECTED carries "
        "the database error."),
}


def _journal_connect_failed(reason: str, detail: str) -> None:
    try:
        from datetime import datetime as _dt
        p = app_state.portfolio()
        p.decision_journal.append({
            "ts": _dt.utcnow().isoformat(), "action": "UPSTOX_CONNECT_FAILED",
            "reason": reason, "detail": detail,
            "why": f"Upstox login did not complete: {reason} — {detail}",
        })
        p.save()
    except Exception:                                         # noqa: BLE001
        log.exception("could not journal UPSTOX_CONNECT_FAILED")


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
        _journal_connect_failed(error or "no_code", "Upstox returned no code")
        return RedirectResponse(
            f"/?upstox_error={error or 'no_code'}", status_code=303,
        )

    # CSRF check
    expected_state = request.session.pop("upstox_oauth_state", None)
    if state and expected_state and state != expected_state:
        log.warning("Upstox callback state mismatch")
        _journal_connect_failed("state_mismatch", "OAuth state did not match")
        return RedirectResponse("/?upstox_error=state_mismatch", status_code=303)

    try:
        client = UpstoxClient.from_env()
        body = client.exchange_code(code)
    except UpstoxConfigError:
        _journal_connect_failed("config_missing", "UPSTOX_* env vars missing")
        return RedirectResponse("/?upstox_error=config_missing", status_code=303)
    except UpstoxAuthError as e:
        log.warning("Upstox token exchange failed: %s", e)
        _journal_connect_failed("token_exchange_failed", str(e)[:200])
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
    persisted = db.save_upstox_token(token_doc)
    # Hot-attach to the running engine — IN MEMORY, from the client that just
    # did the exchange. 2026-09-24 (owner: "still red, fix it"): this used to
    # be refresh_broker_attachment(), which re-READ the token from Mongo; on
    # the live box that read handed back yesterday's document after every
    # login, so the engine kept a dead token and the dashboard said EXPIRED
    # beside a green "Upstox: <name>" pill. The engine now gets the token the
    # owner just fetched, whatever the database did.
    app_state.attach_broker_client(client)
    # A journal line per connect, with the save result — the one place the
    # owner can read what happened (a log warning is not visible to them).
    try:
        from datetime import datetime as _dt
        p = app_state.portfolio()
        entry = {
            "ts": _dt.utcnow().isoformat(), "action": "UPSTOX_CONNECTED",
            "user": user_name, "persisted": bool(persisted),
            "why": (f"Upstox login by {user_name}; token attached to the engine"
                    + (" and saved for restarts."
                       if persisted else
                       f" but NOT saved to Mongo ({db.LAST_TOKEN_SAVE_ERROR}) "
                       "— a restart will need a fresh login.")),
        }
        if not persisted:
            entry["error"] = db.LAST_TOKEN_SAVE_ERROR
        p.decision_journal.append(entry)
        p.save()
    except Exception:                                         # noqa: BLE001
        log.exception("could not journal UPSTOX_CONNECTED")
    # If today's 09:20 scan never completed (token was dead / deploy restart),
    # this login IS the unblock — run the missed scan now, in the background.
    try:
        from .. import scheduler as _sched
        _sched.catchup_scan_if_missed("upstox_connect")
        # Node 8: the login this moment IS the unblock for the option-chain
        # snapshot too (the endpoints reject tokenless calls). If today's
        # scan already ran without a token, the chain was skipped and no
        # catch-up would retry it — this does. Once-per-day guard inside.
        import threading as _th
        _th.Thread(target=_sched.run_option_chain_snapshot,
                   args=("upstox_connect",), daemon=True).start()
        # Owner order 2026-08-24: the login also unblocks the daily Upstox
        # FII/DII pull (market-info endpoints reject tokenless calls).
        _th.Thread(target=_sched.run_market_fii_snapshot,
                   args=("upstox_connect",), daemon=True).start()
    except Exception:
        pass
    if not persisted:
        return RedirectResponse(
            "/?upstox_connected=1&upstox_warn=token_not_persisted",
            status_code=303)
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


@router.get("/upstox/price-check")
def price_check(request: Request, tickers: str = ""):
    """Owner-facing proof of what the LIVE feed actually returns, right now.

    Built 2026-07-31 after the owner found the app showing prices that were not
    the market. Rather than asking them to trust a claim, this shows the whole
    chain per ticker: did the symbol resolve to an Upstox instrument key, did
    the batched LTP call return a price, and what price. Compare it against
    your broker — if a number here is wrong, the feed is wrong, and that is a
    bug worth reporting.

    /upstox/price-check?tickers=VSSL,ULTRACEMCO
    """
    if not auth.current_user(request):
        return JSONResponse({"error": "not_logged_in"}, status_code=401)

    from datetime import datetime
    from trading.brokers.upstox_instruments import to_instrument_keys

    # DEFAULT = the owner's whole book plus the four indices, so this proves
    # EVERY held stock is priced, not a hand-picked sample (owner 2026-07-31:
    # "hope not only 3 are updated, rest all stocks have real prices").
    if tickers.strip():
        syms = [s.strip().upper() for s in tickers.split(",") if s.strip()]
    else:
        from .. import state as _state
        syms = sorted(_state.portfolio().positions.keys())
        syms += ["^NSEI", "^BSESN", "^NSEBANK", "^INDIAVIX"]
    syms = [s if s.startswith("^") or s.endswith((".NS", ".BO")) else f"{s}.NS"
            for s in syms][:120]

    client = _client_from_session(request)
    keymap = to_instrument_keys(syms)
    prices: dict = {}
    err = None
    if client is not None and syms:
        try:
            prices = client.get_ltp([keymap[s] for s in syms if s in keymap]) or {}
        except Exception as e:                                  # noqa: BLE001
            err = str(e)[:200]

    rows = []
    for s in syms:
        key = keymap.get(s)
        px = prices.get(key) if key else None
        rows.append({
            "ticker": s,
            "instrument_key": key or "NOT FOUND in Upstox instrument master",
            "live_price": px,
            "status": ("live" if px else
                       "symbol not in instrument master" if not key else
                       "no price returned by Upstox"),
        })
    n_live = sum(1 for r in rows if r["live_price"])
    return JSONResponse({
        "checked_at": datetime.utcnow().isoformat() + "Z",
        "summary": (f"{n_live} of {len(rows)} symbols returned a LIVE price"
                    if rows else "no symbols checked"),
        "live": n_live,
        "not_live": [r["ticker"] for r in rows if not r["live_price"]],
        "upstox_connected": client is not None,
        "endpoint": "GET /v2/market-quote/ltp?instrument_key=...",
        "batched_calls": 1 if rows else 0,
        "error": err,
        "results": rows,
        "note": ("Compare these against your broker. A wrong number here means "
                 "the feed is wrong — report it. A null means no live price was "
                 "returned, and the app will show a dash or an ENTRY tag rather "
                 "than inventing one."),
    })
