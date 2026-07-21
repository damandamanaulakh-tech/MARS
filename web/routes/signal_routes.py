"""Signal page — standing-wave parameter view (the owner's piano design).

One string per scoring factor. Amplitude = the factor's live score.
Harmonics = the factor's weight in the active mode. A factor with no
data source renders as a FLAT string: no data, no wave — a stub cannot
resonate, so it cannot pretend to be a 50.

Adapted to this app's data shapes: positions are PaperPosition objects
whose factor breakdown lives in their BUY journal entry; scan picks carry
`components` directly (Piano v2 scan writer). Weights come from the same
AM07_WEIGHTS the scorer uses; momentum-mode picks (momentum/quality blend)
use the blend split so their two strings carry honest harmonics.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth, state
from ..templating import templates

router = APIRouter()

# factors with no live data source — drawn flat, never as a score
STUB_FACTORS = {"short_interest"}   # analyst_revision went live 2026-07-16

FACTOR_ORDER = [
    ("momentum_1m",       "#1f4fff"),
    ("volume_expansion",  "#2a8cff"),
    ("earnings_growth",   "#22d3ee"),
    ("catalyst",          "#2dd4a8"),
    ("relative_strength", "#34d058"),
    ("vol_adj_upside",    "#8ee61f"),
    ("short_interest",    "#5b6477"),
    ("analyst_revision",  "#ff2d78"),
    ("breakout_quality",  "#ff6b1a"),
    ("sector_alignment",  "#a855f7"),
    # nifty200_momentum mode stores a 2-factor blend instead of the 10
    ("momentum",          "#1f4fff"),
    ("quality",           "#a855f7"),
]


def _weights() -> dict:
    """The exact weights the scorer uses, plus the momentum-mode blend split."""
    try:
        from trading.config import AM07_WEIGHTS
        w = dict(AM07_WEIGHTS)
    except ImportError:
        w = {}
    try:
        blend = float(getattr(state.cfg(), "quality_blend_pct", 0.35))
    except Exception:
        blend = 0.35
    w.setdefault("momentum", 1.0 - blend)
    w.setdefault("quality", blend)
    return w


def _rows_for(ticker: str | None):
    """Pool = open positions (components from their BUY journal entry) +
    last scan picks. Renders only what the engine actually stored."""
    p = state.portfolio()

    buy_components: dict[str, dict] = {}
    for e in reversed(p.decision_journal or []):
        t = e.get("ticker")
        if (t and t not in buy_components and e.get("action") == "BUY"
                and e.get("components")):
            buy_components[t] = e["components"]

    pool: list[dict] = []
    for pos in p.positions.values():
        pool.append({"ticker": pos.ticker,
                     "components": buy_components.get(pos.ticker) or {},
                     "entry_score": pos.entry_score})
    held = {c["ticker"] for c in pool}
    for pk in (p.last_scan_picks or []):
        t = pk.get("ticker")
        if t and t not in held:
            row = {"ticker": t, "components": pk.get("components") or {}}
            if pk.get("score") is not None:
                row["entry_score"] = pk["score"]
            pool.append(row)

    tickers = sorted({c["ticker"] for c in pool})
    chosen = None
    if ticker:
        chosen = next((c for c in pool if c["ticker"] == ticker), None)
    if chosen is None and pool:
        chosen = pool[0]
    if chosen is None:
        return None, tickers, []

    comps = chosen.get("components") or {}
    weights = _weights()
    rows: list[dict] = []
    known: set[str] = set()
    for name, colour in FACTOR_ORDER:
        if name not in comps or name in known:
            continue
        known.add(name)
        rows.append({
            "name": name,
            "label": name.replace("_", " "),
            "score": float(comps[name]),
            "weight": float(weights.get(name, 0.05)),
            "colour": colour,
            "stub": name in STUB_FACTORS,
        })
    # anything else the engine stored still renders — nothing hidden
    for name in comps:
        if name not in known:
            rows.append({"name": name, "label": name.replace("_", " "),
                         "score": float(comps[name]),
                         "weight": float(weights.get(name, 0.05)),
                         "colour": "#9aa3ad", "stub": False})
    return chosen, tickers, rows


@router.get("/signal", response_class=HTMLResponse)
def signal_page(request: Request, ticker: str | None = None):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    pick, tickers, rows = _rows_for(ticker)
    return templates.TemplateResponse(
        request, "signal.html",
        {"user": user, "active": "signal", "pick": pick, "tickers": tickers,
         "rows": rows, "selected": pick["ticker"] if pick else None},
    )
