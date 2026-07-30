"""/lens — the Universe seen through two lenses at once (owner ask 2026-07-30:
"in Universe too u should focus on same").

  v1 = the LIVE engine's view: score >= floor, fixed Rs 1.25L, 80 slots,
       -5%/+12%, breadth + regime gates. Survival-first.
  v2 = the growth-first view: Kelly size off estimated edge, payoff asymmetry,
       capacity-aware, alpha-decay-aware. Compound-growth-first.

READ-ONLY BY CONSTRUCTION. This page computes and displays; it never opens a
position, never mutates config, and the engine never imports trading.lens_v2
(guarded by a test). v2 numbers carry their assumptions on screen — the IC is
an assumption, convexity and impact are proxies from daily bars, and a stock
with too little history says DATA_NEEDED rather than showing a guess.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth, state
from ..templating import templates

log = logging.getLogger(__name__)
router = APIRouter()

MAX_ROWS = 40          # bounded work per request (512 MB box)


def _v1_rows() -> list[dict]:
    """Everything the live engine actually scored today, best first."""
    p = state.portfolio()
    rows: list[dict] = []
    seen: set[str] = set()
    for pk in (p.last_scan_picks or []):
        t = pk.get("ticker")
        if t and t not in seen:
            seen.add(t)
            rows.append({"ticker": t, "score": float(pk.get("score") or 0.0),
                         "held": t in p.positions})
    for t, r in ((p.scan_matrix or {}).get("rows") or {}).items():
        if t not in seen:
            seen.add(t)
            rows.append({"ticker": t, "score": float(r.get("score") or 0.0),
                         "held": t in p.positions})
    rows.sort(key=lambda r: -r["score"])
    return rows


@router.get("/lens", response_class=HTMLResponse)
def lens_page(request: Request, ticker: str | None = None):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    from trading.lens_v2 import (DEFAULT_IC, KELLY_FRACTION, is_trap, rank_v2,
                                 v2_view)
    from trading.sizing import position_notional_for
    from ..candle_chart import load_ohlcv

    p = state.portfolio()
    cfg = state.cfg()
    prices = {pos.ticker: pos.entry_price for pos in p.positions.values()}
    equity = p.equity(prices)

    v1 = _v1_rows()
    if ticker:
        ticker = ticker.upper().strip()
        if not ticker.endswith((".NS", ".BO")):
            ticker += ".NS"
        if not any(r["ticker"] == ticker for r in v1):
            v1.insert(0, {"ticker": ticker, "score": 0.0,
                          "held": ticker in p.positions, "off_scan": True})

    rows, no_data = [], []
    for r in v1[:MAX_ROWS]:
        df = load_ohlcv(r["ticker"], tail=420)
        # v2's display mirrors what the engine would ACTUALLY size, so the two
        # columns are comparable: same IC, same Kelly fraction, same cap, same
        # floor as the live config.
        view = v2_view(r["ticker"], df, r["score"], equity,
                       ic=float(getattr(cfg, "kelly_ic", 0.05)),
                       stop_pct=abs(cfg.stop_loss_pct),
                       floor=float(cfg.min_score_to_enter),
                       fraction=float(getattr(cfg, "kelly_fraction", 0.5)),
                       cap=float(getattr(cfg, "kelly_max_pct", 0.05))) \
            if df is not None else None
        if view is None:
            no_data.append(r["ticker"])
            continue
        view["held"] = r["held"]
        view["off_scan"] = bool(r.get("off_scan"))
        # what the LIVE engine would actually size this at (½-Kelly since the
        # owner's 2026-07-30 order, or the flat value if reverted to "fixed")
        v1_rs, _diag = position_notional_for(
            cfg, equity, score=r["score"], close=df["Close"].dropna())
        view["v1_size_rupees"] = v1_rs
        view["v1_eligible"] = r["score"] >= float(cfg.min_score_to_enter)
        view["trap"] = is_trap(view["flags"])
        rows.append(view)

    ranked = rank_v2(rows)
    # what the two lenses would actually put money into
    v1_book = [r for r in rows if r["v1_eligible"]]
    v2_book = [r for r in ranked if r["growth_rate_pct"] > 0 and not r["trap"]][:12]
    v1_total = sum(r["v1_size_rupees"] for r in v1_book)
    v2_total = sum(r["v2_size_rupees"] for r in v2_book)

    summary = {
        "scanned": len(rows),
        "v1_count": len(v1_book),
        "v1_each": cfg.position_notional(),
        "v1_total": v1_total,
        "v2_count": len(v2_book),
        "v2_total": v2_total,
        "v2_top_weight_pct": round(
            (v2_book[0]["v2_size_rupees"] / equity * 100) if v2_book and equity else 0.0, 2),
        "capacity_binds": sum(1 for r in rows if r["capacity_binds"]),
        "traps": sum(1 for r in rows if r["trap"]),
        "equity": equity,
        "ic": float(getattr(cfg, "kelly_ic", DEFAULT_IC)),
        "kelly_fraction": float(getattr(cfg, "kelly_fraction", KELLY_FRACTION)),
        "sizing_mode": str(getattr(cfg, "sizing_mode", "fixed")),
        "stop_pct": abs(cfg.stop_loss_pct) * 100,
        "floor": cfg.min_score_to_enter,
        "no_data": no_data[:12],
    }

    universe: list[str] = []
    try:
        from trading.universes import load_full_universe
        universe = load_full_universe()
    except Exception:
        pass

    return templates.TemplateResponse(
        request, "lens.html",
        {"user": user, "active": "lens", "rows": ranked, "summary": summary,
         "v2_book": {r["ticker"] for r in v2_book}, "universe": universe,
         "selected": ticker},
    )
