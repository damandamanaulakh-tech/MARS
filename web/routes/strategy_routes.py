"""Strategy weights + signal library view.

Read-only page showing every AM 07 component, its weight in the active mode,
which sub-signals feed it, and whether it's live or stubbed.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth, state as app_state
from ..templating import templates


router = APIRouter()


# Truth pass 2026-10-15: each line below is read from trading/scoring.py
# (and trading/signals/news.py for the catalyst key), not from the original
# design notes — several had described blends and inputs the code never had.
COMPONENT_DESCRIPTIONS = {
    "momentum_1m":       ("Last 21-day return, absolute (not ranked against other stocks): 50 + 10 x return %, so +5% scores 100 and -5% scores 0", "live"),
    "volume_expansion":  ("5-day / 20-day average volume ratio (1.0 = 50, 2.0 = 100); a day that closed flat or down scores 0 whatever its volume", "live"),
    "earnings_growth":   ("yfinance quarterly YoY EPS growth", "live (snapshot only)"),
    "catalyst":          ("AI-scored news sentiment over the last 7 days of headlines, plus the FII flow tilt", "live (needs ANTHROPIC_API_KEY or OPENAI_API_KEY; neutral 50 without either)"),
    "relative_strength": ("63-day (3-month) return minus Nifty 50's over the same 63 days: 50 + 5 x the gap", "live"),
    "vol_adj_upside":    ("1-month return ÷ annual volatility (a return-to-vol ratio, not a Sharpe)", "live"),
    "short_interest":    ("Futures open-interest proxy (OI up + price down = short build-up) — true short interest has no free NSE source; neutral 50 for non-F&O names or a stale panel", "proxy (live since 2026-08-23)"),
    "analyst_revision":  ("yfinance consensus — LIVE since 2026-07-16, neutral 50 only when unfetchable", "live"),
    "breakout_quality":  ("Distance below the 20-day closing high: at the high = 100, 5% below = 0", "live"),
    "sector_alignment":  ("Sector-peer avg momentum + PIB policy boost", "live"),
}


@router.get("/strategy", response_class=HTMLResponse)
def strategy_view(request: Request):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    cfg = app_state.cfg()
    # 2026-10-04: show the weights the score REALLY uses (scoring.weights_for).
    # This page used to print modes.get_weights(mode), which no scorer read —
    # every category scored on the global weights until the owner ordered
    # "use each category's weights". Momentum buys AND (since audit F-1) is
    # judged for exits on its own momentum/quality blend, so for momentum the
    # composite table is shown for reference only — it moves no momentum
    # trade (copy corrected 2026-10-04; it claimed an exit role it lost).
    from trading.scoring import weights_for
    active_weights = weights_for(getattr(cfg, "mode", "swing"))

    try:
        from trading.signals.policy import active_sector_boosts
        live_boosts = active_sector_boosts()
    except Exception:
        live_boosts = {}

    p = app_state.portfolio()
    # F10: per-component "top-driver" hit counts from the BUY journal — how
    # often each factor was a stock's strongest component at entry. Plus
    # per-sector open counts + exit-reason counts. All from real journal data.
    from collections import Counter
    comp_hits: Counter = Counter()
    sector_opens: Counter = Counter()
    for e in (p.decision_journal or []):
        if e.get("action") != "BUY":
            continue
        comps = e.get("components") or {}
        if comps:
            top = max(comps.items(), key=lambda kv: kv[1])[0]
            comp_hits[top] += 1
    for t in (p.trades or []):
        sector_opens[t.sector or "Unknown"] += 1
    for pos in p.positions.values():
        sector_opens[pos.sector or "Unknown"] += 1
    exit_reasons: Counter = Counter(t.reason for t in (p.trades or []))

    rows = []
    for comp, weight in active_weights.items():
        desc, status = COMPONENT_DESCRIPTIONS.get(comp, ("?", "?"))
        rows.append({
            "component": comp,
            "weight": weight,
            "weight_pct": weight * 100,
            "description": desc,
            "status": status,
            "hits": comp_hits.get(comp, 0),      # F10: times it was top driver
        })
    rows.sort(key=lambda r: -r["weight"])

    return templates.TemplateResponse(
        request, "strategy.html",
        {
            "user": auth.current_user(request),
            "active": "strategy",
            "rows": rows,
            "current_mode": getattr(cfg, "mode", "swing"),
            "momentum_mode": getattr(cfg, "mode", "") == "nifty200_momentum",
            "live_boosts": live_boosts,
            "sector_counts": sorted(sector_opens.items(), key=lambda kv: -kv[1]),
            "exit_counts": sorted(exit_reasons.items(), key=lambda kv: -kv[1]),
            "total_buys": sum(comp_hits.values()),
        },
    )
