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


def _weights(mode: str | None = None) -> dict:
    """The exact weights the scorer uses for this trade category, plus the
    momentum-mode blend split.

    FIX 2026-10-04: since the owner's "use each category's weights" order
    (PR #148) intraday/short/swing/positional score with their own preset
    weights (scoring.weights_for). This page still printed the shared
    AM07_WEIGHTS for every stock — e.g. positional earnings growth shown at
    0.10 while the scorer used 0.25. It now asks the scorer's own function."""
    try:
        from trading.scoring import weights_for
        w = dict(weights_for(mode))
    except ImportError:
        w = {}
    try:
        blend = float(getattr(state.cfg(), "quality_blend_pct", 0.35))
    except Exception:
        blend = 0.35
    w.setdefault("momentum", 1.0 - blend)
    w.setdefault("quality", blend)
    return w


def _live_signal(ticker: str) -> dict | None:
    """On-demand momentum+quality for ANY universe ticker (owner order
    2026-07-29: "signal window need to link with universe, it must pick
    stock from there not the fixed"). Computed from STORED daily history
    (Mongo/zip — zero network) with the exact live-mode functions the
    09:20 scan uses. None = no stored history -> DATA_NEEDED, never fake."""
    from ..candle_chart import load_ohlcv
    try:
        from trading.quality import blend_scores, quality_score
        from trading.strategies.nifty200_momentum import momentum_score
        df = load_ohlcv(ticker, tail=420)      # momentum needs >=252 bars
        if df is None or df.empty:
            return None
        ms = momentum_score(df["Close"])
        if ms is None:                          # < 1y of history
            return None
        q = quality_score(df["Close"],
                          df["Volume"] if "Volume" in df.columns else None)
        b = float(getattr(state.cfg(), "quality_blend_pct", 0.35))
        asof = df.dropna(subset=["Close"]).index[-1]
        return {
            "ticker": ticker,
            "components": {"momentum": round(ms, 1), "quality": round(q, 1)},
            "entry_score": blend_scores(ms, q, b),
            "mode": "nifty200_momentum",
            "on_demand": True,
            "asof": asof.strftime("%d %b %Y") if hasattr(asof, "strftime") else str(asof),
        }
    except Exception:
        return None


def _rows_for(ticker: str | None):
    """Pool = open positions (components from their BUY journal entry) +
    last scan picks + today's FULL scan matrix (~50 scanned names). A
    universe ticker outside the pool is computed live from stored history.
    Renders only real engine output or a live computation labeled as such."""
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
                     "entry_score": pos.entry_score,
                     "mode": pos.mode or "nifty200_momentum"})
    held = {c["ticker"] for c in pool}
    # picks and matrix rows come from the last scan pass — its category
    scan_mode = (p.scan_matrix or {}).get("mode")
    for pk in (p.last_scan_picks or []):
        t = pk.get("ticker")
        if t and t not in held:
            row = {"ticker": t, "components": pk.get("components") or {},
                   "mode": scan_mode}
            if pk.get("score") is not None:
                row["entry_score"] = pk["score"]
            pool.append(row)
    # today's whole scanned bucket, not just the top-50 picks
    seen = {c["ticker"] for c in pool}
    for t, r in ((p.scan_matrix or {}).get("rows") or {}).items():
        if t not in seen and r.get("c"):
            pool.append({"ticker": t, "components": r["c"],
                         "entry_score": r.get("score"), "mode": scan_mode})

    tickers = sorted({c["ticker"] for c in pool})
    chosen = None
    if ticker:
        chosen = next((c for c in pool if c["ticker"] == ticker), None)
        if chosen is None:                      # universe pick outside the pool
            chosen = _live_signal(ticker)
            if chosen is None:
                return {"ticker": ticker, "no_data": True}, tickers, []
    if chosen is None and pool:
        chosen = pool[0]
    if chosen is None:
        return None, tickers, []

    comps = chosen.get("components") or {}
    weights = _weights(chosen.get("mode"))
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


def _universe() -> list[str]:
    """The full auto-refreshed NSE universe for the search box (source of
    truth: data/upstox_nse_equities.csv, refreshed daily at 08:50)."""
    try:
        from trading.universes import load_full_universe
        return load_full_universe()
    except Exception:
        return []


@router.get("/signal", response_class=HTMLResponse)
def signal_page(request: Request, ticker: str | None = None):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if ticker:
        ticker = ticker.upper().strip()
        if ticker and not ticker.endswith((".NS", ".BO")):
            ticker += ".NS"
    pick, tickers, rows = _rows_for(ticker or None)
    return templates.TemplateResponse(
        request, "signal.html",
        {"user": user, "active": "signal", "pick": pick, "tickers": tickers,
         "rows": rows, "selected": pick["ticker"] if pick else None,
         "universe": _universe()},
    )
