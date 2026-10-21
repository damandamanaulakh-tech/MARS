"""Dashboard route.

REBUILT 2026-10-12 on the owner's order (items 30 a-m, 29, 12, 32, 43): one
page called Dashboard — the fund summary on top (capital / allocated /
reserved / used / cash / P&L per segment and in total), the index strip
under it, the full picks and watchlist, the month's equity curve, open
positions, one Status card, breadth and the calendar. The engine-triggers
card, regime card, walk-forward evidence, the ATR panel, the share-candle
chart and the capability panel left this page for the MARS Den (built
separately). Every number here comes from the book or the config.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth, market_panels as mp, state
from ..templating import templates


router = APIRouter()


def _ist_now_str() -> str:
    """Owner item 30k: the page date was datetime.utcnow() — WRONG before
    05:30 IST, the owner read yesterday's date every morning. IST, always.
    web/clock.py (same branch) is the one clock; a local fallback keeps
    this page correct if that module is ever absent."""
    try:
        from ..clock import ist_now_str
        return ist_now_str()
    except Exception:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Kolkata")).strftime(
            "%a, %d %b %Y · %H:%M IST")


def _bearish_names() -> set:
    from trading.candles import BEARISH_PATTERNS
    return set(BEARISH_PATTERNS)


def _panel(fn, *args):
    """A broken panel must never take the whole dashboard down with it.
    The wrapper itself lives in market_panels.safe_panel since 2026-10-12
    so /scan shares it; this name stays for the existing guard test."""
    return mp.safe_panel(fn, *args)


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
    # How many held names actually carry a LIVE mark: the fund block labels
    # itself "entry marks" when none do, and says "N of M" when some do —
    # a partial fetch must never wear the word live over an entry price.
    live_count = sum(1 for t in tickers if t in live_prices)
    deployed = p.deployed_value(prices)

    # ---- Fund summary (owner item 30b): per segment + total ----
    fund = _panel(mp.fund_summary, p, cfg, prices, live_count)

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
    # Owner item 30c: the FULL list (was [:6]); each row stays light — the
    # expand-on-click detail (_pick_detail.html) gets only what it renders.
    today_picks = [{
        "ticker": r.get("ticker", "?"),
        "score": round(_score_of(r), 1),
        "last_close": r.get("last_close"),
        "held": r.get("ticker") in held,
        "candle": _CANDLE_LABELS.get(r.get("candle", ""), r.get("candle", "—")),
        # the raw structure name, so a bearish name can never style green
        # (styling derives from candles.BEARISH_PATTERNS, the 18->34 lesson)
        "candle_key": str(r.get("candle", "") or ""),
        # expand-row detail (_pick_detail.html): pass through what the scan stored
        "components": r.get("components") or {},
        "candles": r.get("candles"),
        "timing_pass": r.get("timing_pass"),
        "reasoning": r.get("reasoning", ""),
    } for r in picks_raw]
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
    # Owner item 30c: the FULL watchlist (was [:8]).
    watchlist = [{
        "ticker": r.get("ticker", "?"),
        "score": round(_score_of(r), 1),
        "last_close": r.get("last_close"),
        "blocked": _blocked_reason(r.get("ticker", "")),
    } for r in picks_raw if r.get("ticker") not in held]

    # The "why not today" block (item 32) left the page on the owner's
    # 2026-10-12 screenshot markup; market_panels.why_not_today stays (pure,
    # tested). Owner 2026-10-21 ("why there is no pop for gates or book, how
    # do i know its blocked"): the WHY NO BUY card reads the rotation's own
    # per-category record (web/why_no_buy.py) — the engine's words, no guess.
    from ..clock import ist_today_iso
    from .. import why_no_buy as _wnb
    why_no_buy = _panel(_wnb.card, p, cfg, ist_today_iso())

    deployed_pct = (deployed / cfg.starting_capital * 100) if cfg.starting_capital else 0

    # Equity curve (owner item 30d): the current month, one point per day,
    # the previous month beside it. Real marks only — p.equity_history.
    eq = _panel(mp.equity_month_series, p.equity_history) or {}

    # F6: rotation suggestions — positions >=90% to target + a pick to fund
    try:
        from trading.rotation import rotation_suggestions
        rotations = rotation_suggestions(p.positions, prices, watchlist, cfg)
    except Exception:
        rotations = []

    # Engine tweaks: surface anything the engine is overriding vs config baseline
    tweaks = []
    if deployed_pct < 60 and len(p.positions) < cfg.max_positions:
        # The old line promised a "target 60-70%" — that deployment target
        # left the code on 2026-10-04 (category shares replaced it). Say
        # what is true: the rotations buy what clears the gates.
        tweaks.append(f"Deployment {deployed_pct:.0f}% of capital — the 09:20 and "
                      "14:30 rotations add what clears the gates; the 'Why not "
                      "today' rows in the fund block name any block recorded.")
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

    # 2026-09-24: the OAuth callback always redirected here with the outcome
    # in the URL, and this page never read it — a failed connect looked like
    # nothing happened. Now every outcome the callback can report is shown.
    from .upstox import CONNECT_NOTICES
    upstox_notice = None
    code = (request.query_params.get("upstox_error")
            or request.query_params.get("upstox_warn"))
    if code:
        level, text = CONNECT_NOTICES.get(
            code, ("error", f"Upstox login did not complete ({code})."))
        # Owner screenshot 2026-09-24 19:15 IST: the amber bar told them to
        # go find a journal line — which only exists as a CSV download. The
        # database's own words belong ON the bar (this is the owner's
        # authenticated page; /healthz stays value-free).
        from .. import db as _db
        if code == "token_not_persisted" and _db.LAST_TOKEN_SAVE_ERROR:
            text = f"{text} Database said: {_db.LAST_TOKEN_SAVE_ERROR}"
        # 2026-09-26: Upstox's own words + the redirect_uri the app sent, so
        # the owner can hold it against the developer page line by line.
        from .upstox import LAST_APP_REJECTION as _rej
        if code == "app_rejected" and _rej:
            text = (f"{text} Upstox said: {_rej.get('code')} "
                    f"{_rej.get('message')}. The app sent redirect_uri = "
                    f"{_rej.get('redirect_uri')} with an API key ending "
                    f"…{_rej.get('client_id_tail')}. On upstox.com → Developer "
                    f"Apps, the app with that key must list exactly that "
                    f"redirect URL (same https, same path, no trailing slash, "
                    f"no spaces).")
        upstox_notice = {"level": level, "code": code, "text": text}

    return templates.TemplateResponse(
        request, "dashboard.html",
        {
            "user": user,
            "upstox_notice": upstox_notice,
            "scan_flags": (p.scan_matrix or {}).get("flags") or {},
            "filings": (p.filings or [])[:12],
            "active": "dashboard",
            # Owner item 30k: IST, formatted "%a, %d %b %Y · %H:%M IST"
            "today": _ist_now_str(),
            "fund": fund,
            # the count feeds the OPEN POSITIONS button; the table itself left
            # the dashboard (owner 2026-10-12 markup) — it lives on /positions
            "positions": sorted(p.positions.values(), key=lambda x: x.ticker),
            "last_scan": p.last_scan,
            # Owner item 12: a STATE (Nifty vs its 200-DMA), never a forecast
            "regime": (p.last_regime or "—").upper(),
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
            "eq": eq,
            "tweaks": tweaks,
            "quotes_live": quotes_live,
            "prices": prices,
            "today_picks": today_picks,
            "watchlist": watchlist,
            # derived from the registry so a new bearish structure can never
            # show as a green chip here (the 18->34 expansion caught this)
            "bearish_names": _bearish_names(),
            # Owner ask 2026-07-30 (5 reference mockups): index strip, market
            # breadth, event calendar and the horizons. All real data — see
            # web/market_panels.py. The sector strip and scan coverage moved
            # to /scan, evidence and ATR to the MARS Den (owner 2026-10-12).
            "index_strip": _panel(mp.index_strip),
            "international_note": mp.INTERNATIONAL_NOTE,
            "breadth": _panel(mp.breadth_panel),
            "why_no_buy": why_no_buy,
            "events": _panel(mp.events_panel),
            "horizons": _panel(mp.horizons_panel),
            "gates": _panel(mp.gates_in_force, cfg),
            "rotations": rotations,
        },
    )
