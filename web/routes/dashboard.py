"""Master dashboard route."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth, market_panels as mp, state
from ..templating import templates


router = APIRouter()


def _bearish_names() -> set:
    from trading.candles import BEARISH_PATTERNS
    return set(BEARISH_PATTERNS)


def _panel(fn, *args):
    """A broken panel must never take the whole dashboard down with it."""
    try:
        return fn(*args)
    except Exception:                       # pragma: no cover - safety net
        import logging
        logging.getLogger(__name__).warning("panel %s failed", getattr(fn, "__name__", fn))
        return None


def _ifr_window_active(p) -> bool:
    """AUDIT FIX B7 (2026-08-09): the dashboard's "IFR throttle active" badge
    used to latch forever after one fire. Same 22-day window the risk page
    applies."""
    from datetime import date as _date
    try:
        from trading.ifr import ACTIVE_DAYS_AFTER_FIRE
    except ImportError:
        return False
    for d in (p.last_ifr_fire_date, p.last_ifr_ratio_fire_date):
        if not d:
            continue
        try:
            days = (_date.today() - _date.fromisoformat(str(d)[:10])).days
            if 0 <= days <= ACTIVE_DAYS_AFTER_FIRE:
                return True
        except ValueError:
            continue
    return False


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    user = auth.current_user(request)
    p = state.portfolio()
    cfg = state.cfg()

    # Live marks via the 60s-cached Upstox-first router; entry-price fallback.
    tickers = [pos.ticker for pos in p.positions.values()]
    live_prices, quotes_live = state.marks(tickers)
    prices = {pos.ticker: live_prices.get(pos.ticker, pos.entry_price)
              for pos in p.positions.values()}
    equity = p.equity(prices)
    deployed = p.deployed_value(prices)
    cash_reserve_pct = (p.cash / equity * 100) if equity else 100
    invested = sum(pos.qty * pos.entry_price for pos in p.positions.values())
    unreal_pnl = deployed - invested
    unreal_pct = (unreal_pnl / invested * 100) if invested else 0.0

    kpis = [
        {"label": "TOTAL EQUITY", "big": equity, "fmt": "inr_lakh",
         "trail": "Cash + holdings, live marks" if quotes_live else "Cash + holdings (entry marks — connect Upstox)",
         "href": "/positions"},
        {"label": "OPEN POSITIONS", "big": len(p.positions), "fmt": "int",
         "trail": f"of {cfg.max_positions} max", "href": "/positions",
         "highlight": False},
        {"label": "UNREALIZED P&L", "big": unreal_pnl, "fmt": "inr",
         "trail": f"{unreal_pct:+.2f}% vs entry",
         "href": "/positions", "highlight": True},
        {"label": "REGIME", "big": (p.last_regime or "—").upper(), "fmt": "str",
         "trail": "Engine market state",
         "href": "/scan"},
    ]

    # ---- Today's Picks + Watchlist (top-scored picks NOT held) ----
    held = set(p.positions.keys())
    def _score_of(row: dict) -> float:
        for k in ("score", "final_score", "blended_score", "total_score"):
            if k in row:
                try:
                    return float(row[k])
                except (TypeError, ValueError):
                    pass
        return 0.0
    picks_raw = sorted((p.last_scan_picks or []), key=_score_of, reverse=True)
    _CANDLE_LABELS = {
        "breakout": "✓ Breakout", "urr": "✓ URR retest",
        "engulfing": "✓ Engulfing", "hammer": "✓ Hammer",
        "morning_star": "✓ Morning Star", "piercing": "✓ Piercing",
        "tweezer_bottom": "✓ Tweezer", "three_soldiers": "✓ 3 Soldiers",
        "no_pattern": "⏳ waiting", "insufficient_bars": "· no data",
        "disabled": "—", "": "—",
    }
    today_picks = [{
        "ticker": r.get("ticker", "?"),
        "score": round(_score_of(r), 1),
        "last_close": r.get("last_close"),
        "held": r.get("ticker") in held,
        "candle": _CANDLE_LABELS.get(r.get("candle", ""), r.get("candle", "—")),
        # expand-row detail (_pick_detail.html): pass through what the scan stored
        "components": r.get("components") or {},
        "candles": r.get("candles"),
        "timing_pass": r.get("timing_pass"),
        "reasoning": r.get("reasoning", ""),
    } for r in picks_raw[:6]]
    # F5: why a watchlist pick was NOT bought (join with the scan's skips)
    _SKIP_LABELS = {
        "reserve": "cash reserve", "cash": "no cash", "max_positions": "slots full",
        "sector_exposure_cap": "sector cap", "below_min_value": "too small",
        "correlated": "correlated", "liquidity": "illiquid", "stale": "stale data",
        "throttle_zero": "throttled", "already_held": "held",
    }
    def _blocked_reason(tkr: str) -> str:
        for s in (p.last_scan_skipped or []):
            if s.get("ticker") == tkr:
                raw = str(s.get("reason", s.get("why", "")))
                if raw.startswith("candle_wait"):
                    return "⏳ waiting candle"
                for key, lab in _SKIP_LABELS.items():
                    if key in raw:
                        return f"⛔ {lab}"
                return f"⛔ {raw[:18]}"
        return ""
    watchlist = [{
        "ticker": r.get("ticker", "?"),
        "score": round(_score_of(r), 1),
        "last_close": r.get("last_close"),
        "blocked": _blocked_reason(r.get("ticker", "")),
    } for r in picks_raw if r.get("ticker") not in held][:8]

    # ---- Engine trigger-map: what fired today (from the decision journal) ----
    from collections import Counter
    today_iso = datetime.utcnow().date().isoformat()
    tj = [e for e in (p.decision_journal or [])[-800:]
          if str(e.get("ts", "")).startswith(today_iso)]
    action_counts = Counter(str(e.get("action", "?")) for e in tj)
    exit_reasons = Counter(
        str(e.get("reason", "?")) for e in tj
        if str(e.get("action", "")).upper() in ("SELL", "CLOSE", "EXIT"))
    skip_reasons = Counter()
    for srow in (p.last_scan_skipped or []):
        skip_reasons[str(srow.get("reason", srow.get("why", "?")))[:42]] += 1
    from trading.calendars import festival_context
    triggers = {
        "festival": festival_context(days=5),
        "journal_today": len(tj),
        "actions": action_counts.most_common(8),
        "exits": exit_reasons.most_common(6),
        "skips": skip_reasons.most_common(8),
        "regime": p.last_regime or "not classified yet",
        # AUDIT FIX B7 (2026-08-09): "IFR throttle active" used to latch
        # forever after one fire. Same 22-day window the risk page uses.
        "ifr_active": _ifr_window_active(p),
    }

    cash_pct = (p.cash / cfg.starting_capital * 100) if cfg.starting_capital else 0
    deployed_pct = (deployed / cfg.starting_capital * 100) if cfg.starting_capital else 0
    reserve_floor_pct = cfg.cash_reserve_pct * 100

    # Equity curve: last 100 points (timestamps + values for Chart.js)
    eq_hist = list(p.equity_history or [])[-100:]
    eq_labels = [ts[:16].replace("T", " ") for ts, _ in eq_hist]
    eq_values = [round(float(v), 2) for _, v in eq_hist]

    # F17: Nifty candle chart (cached, Upstox-first)
    # Owner ask 2026-07-26: "why do i need CANDLE OF NIFTY, I NEED SHARE
    # CANDLE" — big chart now shows a stock the owner holds (or today's top
    # pick when the book is empty). ?chart=TICKER switches it.
    # Zero extra network: the 09:20 scan already stores 20 bars per pick;
    # only a held stock that missed the top-50 falls back to a cached fetch.
    nifty_candles = []
    chart_ticker = (request.query_params.get("chart") or "").upper().strip()
    if chart_ticker and not chart_ticker.endswith((".NS", ".BO")):
        chart_ticker += ".NS"
    if not chart_ticker:
        if p.positions:
            chart_ticker = sorted(p.positions.keys())[0]
        elif p.last_scan_picks:
            chart_ticker = (p.last_scan_picks[0] or {}).get("ticker", "")
    share_candles, share_structs = [], {}
    if chart_ticker:
        for pk in (p.last_scan_picks or []):
            if pk.get("ticker") == chart_ticker:
                share_structs = pk.get("structures") or {}
                for i, bar in enumerate(pk.get("candles") or []):
                    if len(bar) >= 4:
                        share_candles.append({
                            "t": "", "o": round(float(bar[0]), 2),
                            "h": round(float(bar[1]), 2),
                            "l": round(float(bar[2]), 2),
                            "c": round(float(bar[3]), 2)})
                break
        if not share_candles and chart_ticker in p.positions:
            try:
                share_candles = state.recent_candles(chart_ticker, bars=40) or []
            except Exception:
                share_candles = []
    chart_choices = sorted(p.positions.keys()) or [
        (pk or {}).get("ticker") for pk in (p.last_scan_picks or [])[:8]]

    # F6: rotation suggestions — positions >=90% to target + a pick to fund
    try:
        from trading.rotation import rotation_suggestions
        rotations = rotation_suggestions(p.positions, prices, watchlist, cfg)
    except Exception:
        rotations = []

    # Engine tweaks: surface anything the engine is overriding vs config baseline
    tweaks = []
    if deployed_pct < 60 and len(p.positions) < cfg.max_positions:
        tweaks.append(f"Deployment {deployed_pct:.0f}% — target 60-70%. Run scan or wait for daily auto-scan at 09:20 IST.")
    if cfg.cash_reserve_pct > 0.25:
        tweaks.append(f"Cash reserve raised to {cfg.cash_reserve_pct*100:.0f}% by regime classifier (defensive tilt).")
    if cfg.mode == "intraday":
        tweaks.append("Intraday mode active — all positions auto-square-off at 15:25 IST.")
    # FIXED 2026-08-29 (owner: "fix the DAILY_SCHEDULER dashboard bug").
    # This read was `!= "1"`, so an UNSET variable — the default, and what
    # render.yaml actually ships — printed "Daily auto-scan is OFF" while
    # the scheduler was running the 09:20 pass every morning. Both now read
    # scheduler.daily_enabled(), the single source of truth.
    from ..scheduler import daily_enabled
    if not daily_enabled():
        tweaks.append("Daily auto-scan is OFF (DAILY_SCHEDULER=0 in Render env). "
                      "Remove that variable, or set it to 1, to turn the "
                      "automatic 09:20 IST scan back on.")

    return templates.TemplateResponse(
        request, "dashboard.html",
        {
            "user": user,
            "scan_flags": (p.scan_matrix or {}).get("flags") or {},
            "filings": (p.filings or [])[:12],
            "active": "dashboard",
            "today": datetime.utcnow().strftime("%a, %d %b"),
            "kpis": kpis,
            "positions": list(p.positions.values())[:5],
            "last_scan": p.last_scan,
            "deployed_pct": deployed_pct,
            "cash_pct": cash_pct,
            "reserve_floor_pct": reserve_floor_pct,
            "starting_capital": cfg.starting_capital,
            "deployed": deployed,
            "cash": p.cash,
            "quote_source": state.quote_source_status(),
            "db_status": state.db_status(),
            "current_mode": getattr(cfg, "mode", "swing"),
            "mode_choices": [
                ("intraday", "Intraday"),
                ("short", "Short"),
                ("swing", "Swing"),
                ("positional", "Positional"),
                ("nifty200_momentum", "N200 Momentum"),
            ],
            "eq_labels": eq_labels,
            "eq_values": eq_values,
            "tweaks": tweaks,
            "quotes_live": quotes_live,
            "prices": prices,
            "today_picks": today_picks,
            "watchlist": watchlist,
            "triggers": triggers,
            "nifty_candles": nifty_candles,
            "share_candles": share_candles,
            "chart_ticker": chart_ticker,
            "chart_choices": [c for c in chart_choices if c],
            "share_structs": share_structs,
            # derived from the registry so a new bearish structure can never
            # show as a green chip here (the 18->34 expansion caught this)
            "bearish_names": _bearish_names(),
            # Owner ask 2026-07-30 (5 reference mockups): index strip, market
            # breadth, event calendar, walk-forward evidence, the 4 horizons
            # and ATR stop context. All real data — see web/market_panels.py.
            "index_strip": _panel(mp.index_strip),
            "breadth": _panel(mp.breadth_panel),
            "sectors": _panel(mp.sector_panel),
            # Owner ask 2026-09-10: "system is not reading all 2400
            # stocks every time" — answer it with a count, per sector.
            "coverage": _panel(mp.coverage_panel),
            "events": _panel(mp.events_panel),
            "evidence": _panel(mp.evidence_panel),
            "horizons": _panel(mp.horizons_panel),
            "atr_rows": _panel(mp.atr_panel, sorted(p.positions.keys())),
            "rotations": rotations,
        },
    )
