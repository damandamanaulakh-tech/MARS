"""/chart/{ticker} — the visual candle chart page (owner ask 2026-07-29).

Display only: the engine's decisions never read this page. It exists so the
owner can SEE the bars the engine read — candles, volume, every structure
fired on the exact bar it fired, and the entry/stop/target lines for a held
position. No data = DATA_NEEDED on screen, never a fake chart.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth, state
from ..candle_chart import (CHART_BARS, load_ohlcv, per_bar_structures,
                            render_svg)
from ..templating import templates

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/chart/{ticker}", response_class=HTMLResponse)
def chart_page(request: Request, ticker: str):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    ticker = ticker.upper().strip()
    if not ticker.endswith((".NS", ".BO")):
        ticker += ".NS"

    df = load_ohlcv(ticker)
    svg, recent, levels, last_bar = "", [], [], None
    if df is not None and not df.empty:
        hits = per_bar_structures(df, bars=CHART_BARS)
        pos = state.portfolio().positions.get(ticker)
        cfg = state.cfg()
        if pos is not None:
            entry = float(pos.entry_price)
            stop = float(pos.stop_price) or round(
                entry * (1 + cfg.stop_loss_pct), 2)
            target = float(pos.target_price) or round(
                entry * (1 + cfg.take_profit_pct), 2)
            levels = [("entry", entry, "#334155"),
                      ("stop", stop, "#dc2626"),
                      ("target", target, "#059669")]
        svg = render_svg(df, hits, levels)
        tail = df.dropna(subset=["Close"]).tail(CHART_BARS)
        dates = list(tail.index)
        last_bar = dates[-1].strftime("%d %b %Y") if dates else None
        # most recent fires first, newest bars at the top of the list
        for i in sorted(hits, reverse=True)[:12]:
            ts = dates[i]
            recent.append({
                "date": ts.strftime("%d %b") if hasattr(ts, "strftime") else str(ts),
                "names": [{"name": nm.replace("_", " "),
                           "key": nm, "bear": fam == "bear"}
                          for nm, fam in hits[i]],
            })

    return templates.TemplateResponse(
        request, "chart.html",
        {
            "user": auth.current_user(request),
            "active": "scan",
            "ticker": ticker,
            "svg": svg,
            "recent": recent,
            "levels": levels,
            "last_bar": last_bar,
            "bars": CHART_BARS,
        },
    )
