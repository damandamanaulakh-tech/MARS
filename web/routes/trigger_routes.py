"""⚡ /triggers — the owner's Pre-Rise/Fall Trigger Layer, on screen.

Wired 2026-08-04 on the owner's order ("wire it") after the gap was measured:
112 designed triggers, 0 running. Scope they approved: DISPLAY + WATCHLIST.
This page and its routes never open, close or size a position — the entry
gate's verdict is shown, not executed, per the owner's own
REQUIRES_15Y_BACKTEST rule in their sheets.

Data honesty: everything is computed from STORED daily history and labelled
with its as-of date; a stock with no stored history gets DATA_NEEDED, and the
33 external-data triggers are listed with exactly what would unlock them.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth, state
from ..templating import templates

log = logging.getLogger(__name__)
router = APIRouter()


def _universe_names() -> list[str]:
    """Full auto-refreshed universe for the search box datalist."""
    try:
        from trading.universes import load_full_universe
        # FIXED 2026-08-09: load_full_universe returns a flat list; the old
        # [0] indexed the FIRST TICKER STRING, so the search datalist held
        # the characters of "20MICRONS.NS" instead of 2,400+ names.
        return sorted(load_full_universe())
    except Exception:
        return []


@router.get("/triggers", response_class=HTMLResponse)
def triggers_page(request: Request):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    from trading.triggers import (ASSUMED_CONSTANTS, COMPUTABLE, EXTERNAL,
                                  GATE_ROWS, TRIGGERS)
    p = state.portfolio()
    tw = p.trigger_watch or {}
    rows = []
    for t, r in sorted((tw.get("rows") or {}).items(),
                       key=lambda kv: (-len(kv[1].get("gates_passed") or []),
                                       -(kv[1].get("scores") or {}).get("RISE", 0))):
        rows.append({"ticker": t, **r})
    return templates.TemplateResponse(
        request, "triggers.html",
        {
            "user": user,
            "active": "triggers",
            "tw": tw,
            "rows": rows,
            "watchlist": tw.get("watchlist") or [],
            "no_history": tw.get("no_history") or [],
            "n_triggers": len(TRIGGERS),
            "n_computable": len(COMPUTABLE),
            "n_external": len(EXTERNAL),
            "gate_rows": GATE_ROWS,
            "score_note": ASSUMED_CONSTANTS["score_formula"],
            "universe": _universe_names(),
            "detail": None,
        },
    )


@router.post("/triggers/run")
def triggers_run_now(request: Request):
    """Owner button: run the trigger-layer pass now (same job as post-scan)."""
    user = auth.current_user(request)
    if not user or user.get("role") != "owner":
        return RedirectResponse("/triggers?error=owner_only", status_code=303)
    from ..scheduler import run_trigger_layer
    run_trigger_layer("owner-button")
    return RedirectResponse("/triggers", status_code=303)


@router.get("/triggers/{ticker}", response_class=HTMLResponse)
def trigger_detail(request: Request, ticker: str):
    """One stock through all 112 triggers: fired, quiet, and DATA_NEEDED —
    computed now from stored history, labelled with its as-of date."""
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    from trading.triggers import (ASSUMED_CONSTANTS, EXTERNAL, GATE_ROWS,
                                  TRIGGERS, stock_view)
    from ..candle_chart import load_ohlcv

    ticker = ticker.strip().upper()
    if not ticker.endswith(".NS"):
        ticker += ".NS"
    df = load_ohlcv(ticker, tail=420)
    detail = None
    if df is not None and not df.empty:
        asof = ""
        try:
            asof = str(df.index[-1].date() if hasattr(df.index[-1], "date")
                       else df.iloc[-1].get("date", ""))[:10]
        except Exception:
            pass
        view = stock_view(df, asof=asof)
        by_family: dict[str, list[dict]] = {}
        for t in TRIGGERS:
            h = view["hits"].get(t.id)
            by_family.setdefault(t.family, []).append({
                "id": t.id, "name": t.name, "side": t.side,
                "weight": t.weight, "definition": t.definition,
                "formula": t.formula,
                "status": ("DATA_NEEDED" if h is None and not t.ohlcv_ready
                           else "n/a" if h is None
                           else "FIRED" if h else "quiet"),
                "needs": t.needs,
            })
        detail = {"ticker": ticker, "view": view, "by_family": by_family,
                  "bars": len(df)}
    return templates.TemplateResponse(
        request, "triggers.html",
        {
            "user": user,
            "active": "triggers",
            "tw": state.portfolio().trigger_watch or {},
            "rows": [],
            "watchlist": [],
            "no_history": [],
            "n_triggers": len(TRIGGERS),
            "n_computable": sum(1 for t in TRIGGERS if t.ohlcv_ready),
            "n_external": len(EXTERNAL),
            "gate_rows": GATE_ROWS,
            "score_note": ASSUMED_CONSTANTS["score_formula"],
            "universe": _universe_names(),
            "detail": detail,
            "detail_ticker": ticker,
        },
    )
