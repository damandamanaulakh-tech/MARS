"""Open positions + closed trades pages."""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth, market_gate, state
from ..templating import templates
from trading.paper import PaperPosition


log = logging.getLogger(__name__)
router = APIRouter()


# Friendly category labels for the position's entry mode.
_MODE_LABEL = {
    "intraday":          "Intraday",
    "short":             "Short",
    "swing":             "Swing",
    "positional":        "Long",
    "nifty200_momentum": "Momentum",
}


@router.get("/positions", response_class=HTMLResponse)
def positions_view(
    request: Request, error: str | None = None, seeded: str | None = None,
    removed: str | None = None, closed_all: str | None = None,
    no_price: str | None = None, sold: str | None = None,
):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    p = state.portfolio()
    cfg = state.cfg()

    tickers = [pos.ticker for pos in p.positions.values()]
    prices, quotes_live = state.marks(tickers)

    # F4: latest journaled current_score per ticker (from HOLD entries) so we
    # can flag positions decaying toward the score-decay exit floor.
    decay_floor = float(getattr(cfg, "score_decay_exit", 45.0))
    latest_score: dict[str, float] = {}
    # Piano detail row: the per-factor scores journaled at BUY time — the
    # position itself only stores the total, the journal has the breakdown.
    buy_components: dict[str, dict] = {}
    for e in reversed(p.decision_journal or []):
        t = e.get("ticker")
        if t and t not in latest_score and e.get("current_score") is not None:
            try:
                latest_score[t] = float(e["current_score"])
            except (TypeError, ValueError):
                pass
        if (t and t not in buy_components and e.get("action") == "BUY"
                and e.get("components")):
            buy_components[t] = e["components"]

    rows = []
    tot_invested = tot_value = 0.0
    for pos in p.positions.values():
        mode = getattr(pos, "mode", "") or ""
        category = _MODE_LABEL.get(mode, mode.title() if mode else "Manual")
        # Use the stop/target snapshotted at entry; fall back to the live cfg
        # percentages for manual/legacy positions that never stored them.
        stop_price = getattr(pos, "stop_price", 0.0) or 0.0
        target_price = getattr(pos, "target_price", 0.0) or 0.0
        if stop_price <= 0:
            stop_price = pos.entry_price * (1 + cfg.stop_loss_pct)
        if target_price <= 0:
            target_price = pos.entry_price * (1 + cfg.take_profit_pct)
        # Per-row honesty (owner 2026-07-31): say whether THIS price is a live
        # quote or the entry-price fallback. A page-level "quotes live" flag hid
        # the fact that individual names could be showing their entry price.
        _live_px = prices.get(pos.ticker)
        ltp_is_live = bool(_live_px and _live_px > 0)
        ltp = _live_px if ltp_is_live else pos.entry_price
        invested = pos.qty * pos.entry_price
        value = pos.qty * ltp
        pnl = value - invested
        pnl_pct = (ltp / pos.entry_price - 1) * 100 if pos.entry_price else 0.0
        tot_invested += invested
        tot_value += value
        cur_score = latest_score.get(pos.ticker)
        decay_watch = cur_score is not None and cur_score <= decay_floor + 5
        rows.append({
            "ticker": pos.ticker,
            "category": category,
            "qty": pos.qty,
            "entry_date": pos.entry_date.strftime("%Y-%m-%d"),
            "entry_price": pos.entry_price,
            "ltp": ltp,
            "ltp_live": ltp_is_live,
            "value": value,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "current_score": cur_score,
            "decay_watch": decay_watch,
            "decay_floor": decay_floor,
            "target_price": target_price,
            "stop_price": stop_price,
            "to_target_pct": (target_price / ltp - 1) * 100 if ltp else 0.0,
            "to_stop_pct": (stop_price / ltp - 1) * 100 if ltp else 0.0,
            "peak_price": getattr(pos, "peak_price", pos.entry_price),
            "sector": pos.sector,
            "entry_score": pos.entry_score,
            "components": buy_components.get(pos.ticker) or {},
            "reasoning": pos.reasoning,
            "entry_notes": getattr(pos, "entry_notes", "") or "",
            "tags": getattr(pos, "tags", []) or [],
        })
    rows.sort(key=lambda r: -r["value"])
    totals = {
        "invested": tot_invested,
        "value": tot_value,
        "pnl": tot_value - tot_invested,
        "pnl_pct": ((tot_value / tot_invested - 1) * 100) if tot_invested else 0.0,
    }

    return templates.TemplateResponse(
        request, "positions.html",
        {
            "user": auth.current_user(request),
            "active": "positions",
            "rows": rows,
            "totals": totals,
            "quotes_live": quotes_live,
            "max_positions": cfg.max_positions,
            "error": error,
            "seeded": seeded,
            "removed": removed,
            "sold": sold,
            "closed_all": closed_all,
            "no_price": no_price,
            "quotes_age": state.marks_age(tickers),
            "seed_count": len(_SEED_JUNE_2026),
            **_sector_event_context(p, cfg),
        },
    )


def _sector_event_context(p, cfg) -> dict:
    """Today's sector news alerts for the holdings page (owner pick C-narrow,
    2026-08-29). Display only — a broken panel hides, never takes the page
    down (the _panel() convention, and the /positions 500 lesson)."""
    try:
        from trading.sector_events import sector_alerts, unknown_feeds
        rows = (p.news_watch or {}).get("rows") or {}
        return {
            "sector_alerts": sector_alerts(p.positions, rows, cfg.sector_map),
            "news_unknown": unknown_feeds(p.positions, rows),
            "news_asof": (p.news_watch or {}).get("ts", "")[:16],
        }
    except Exception:
        log.exception("sector event panel failed")
        return {"sector_alerts": [], "news_unknown": [], "news_asof": ""}


@router.get("/trades", response_class=HTMLResponse)
def trades_view(request: Request):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    p = state.portfolio()
    cfg = state.cfg()

    starting_capital = cfg.starting_capital or 1.0
    rows = []
    for t in p.trades:
        net_pct = (t.exit_price / t.entry_price - 1) * 100 if t.entry_price else 0
        hold = (t.exit_date.date() - t.entry_date.date()).days
        capital_used = t.qty * t.entry_price            # F9: rupees deployed
        pct_of_capital = capital_used / starting_capital * 100
        rows.append({
            "ticker": t.ticker,
            "qty": t.qty,
            "entry_date": t.entry_date.strftime("%Y-%m-%d"),
            "exit_date": t.exit_date.strftime("%Y-%m-%d"),
            "hold_days": hold,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "capital_used": capital_used,
            "pct_of_capital": pct_of_capital,
            "net_pnl": t.net_pnl,
            "net_pct": net_pct,
            "reason": t.reason,
            "sector": t.sector,
        })

    wins = sum(1 for t in p.trades if t.net_pnl > 0)
    losses = sum(1 for t in p.trades if t.net_pnl < 0)
    total = len(p.trades)
    win_rate = (wins / total * 100) if total else 0.0
    net_total = sum(t.net_pnl for t in p.trades)
    # F9 + F19: gross profit vs gross loss so P&L reads next to win-rate
    gross_profit = sum(t.net_pnl for t in p.trades if t.net_pnl > 0)
    gross_loss = sum(t.net_pnl for t in p.trades if t.net_pnl < 0)
    total_capital_used = sum(r["capital_used"] for r in rows)
    net_pct_of_capital = net_total / starting_capital * 100
    # Audit M-10: once the book reaches the cap, older trades live in the
    # Mongo archive. Asked only then, so an ordinary render costs no query.
    from trading.paper import TRADES_DOC_CAP
    archived = 0
    if total >= TRADES_DOC_CAP:
        from .. import db
        archived = db.count_archived_trades()

    return templates.TemplateResponse(
        request, "trades.html",
        {
            "losses": losses,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "total_capital_used": total_capital_used,
            "net_pct_of_capital": net_pct_of_capital,
            "starting_capital": starting_capital,
            "user": auth.current_user(request),
            "active": "trades",
            "rows": rows,
            "wins": wins,
            "total": total,
            "win_rate": win_rate,
            "net_total": net_total,
            "archived_trades": archived,
        },
    )


@router.post("/positions/{ticker}/notes")
def update_notes(
    request: Request,
    ticker: str,
    entry_notes: str = Form(""),
    tags: str = Form(""),
):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    p = state.portfolio()
    pos = p.positions.get(ticker)
    if pos is None:
        return RedirectResponse("/positions?error=not_found", status_code=303)
    pos.entry_notes = entry_notes.strip()
    pos.tags = [t.strip() for t in tags.split(",") if t.strip()]
    p.save()
    return RedirectResponse("/positions", status_code=303)


@router.post("/positions/{ticker}/sell")
async def sell_position(request: Request, ticker: str):
    """Owner-only: SELL this position. It lands in Closed Trades with real P&L.

    Replaces the old ✕ (owner 2026-08-04: "i just did exist from divilabs, it
    just vanished, why not going to closed trade, or instead of coss give sell
    option so it go to closed trade"). ✕ called remove_position(), which POPPED
    the position and refunded its ENTRY cash — no closed trade, no P&L, no exit
    price. The trade disappeared from the owner's record entirely. That was
    data loss dressed up as a button.

    Price: the live quote if we have one, else whatever price the owner types.
    An exit is NEVER blocked for want of a quote — the owner can always name
    their own fill. Which of the two was used is journaled, so the record says
    where the number came from.
    """
    user = auth.current_user(request)
    if not user or user.get("role") != "owner":
        return RedirectResponse("/positions?error=owner_only", status_code=303)
    p = state.portfolio()
    if ticker not in p.positions:
        return RedirectResponse("/positions?error=not_found", status_code=303)

    form = await request.form()
    typed = (form.get("price") or "").strip()
    price, source = None, ""
    if typed:
        try:
            price = float(typed)
            source = "owner_typed"
        except ValueError:
            return RedirectResponse(f"/positions?error=bad_price&t={ticker}",
                                    status_code=303)
    if price is None:
        prices, live = state.marks([ticker])
        px = prices.get(ticker)
        if px and px > 0 and live:
            price, source = float(px), "live_quote"
    if not price or price <= 0:
        # Not a refusal — the owner just has to name the fill, because no live
        # quote exists and this app does not invent one.
        return RedirectResponse(f"/positions?error=need_price&t={ticker}",
                                status_code=303)

    # Owner order 2026-09-10: no trades outside market hours, their own
    # buttons included (asked directly, answered "yes all block").
    _blocked = market_gate.trading_blocked(state.cfg())
    if _blocked:
        return market_gate.blocked_redirect("/positions", _blocked)

    now = datetime.utcnow()
    eng = state.engine()
    eng._close(ticker, float(price), "OWNER_SELL", now)
    p.decision_journal.append({
        "ts": now.isoformat(),
        "action": "OWNER_SELL",
        "ticker": ticker,
        "price": round(float(price), 2),
        "price_source": source,
        "why": (f"Owner sold {ticker} at ₹{price:,.2f} "
                f"({'live quote' if source == 'live_quote' else 'price entered by owner'})."
                " Booked to Closed Trades with real P&L."),
        "cash_after": round(p.cash, 2),
    })
    p.save()
    return RedirectResponse(f"/positions?sold={ticker}", status_code=303)


@router.post("/positions/{ticker}/remove")
def remove_position(request: Request, ticker: str):
    """LEGACY, no longer reachable from the UI — kept only so an old bookmarked
    form cannot 404. It now redirects to the sell flow instead of silently
    deleting a trade: dropping a position without booking it destroyed the
    owner's record (DIVISLAB, 2026-08-04)."""
    return RedirectResponse(f"/positions?error=use_sell&t={ticker}",
                            status_code=303)


@router.post("/positions/close-all")
def close_all_positions(request: Request):
    """Owner order 2026-07-31: "close all positions right away / put in the
    closed trade".

    Every position is SOLD at its LIVE price and lands in Closed Trades with
    real P&L, costs and slippage — the engine's own exit path, so nothing is
    special-cased. If a live price is missing for a name it is NOT closed at a
    stale mark; it stays open and is reported. Closing at an invented price
    would be exactly the fault the owner is angry about.
    """
    user = auth.current_user(request)
    if not user or user.get("role") != "owner":
        return RedirectResponse("/positions?error=owner_only", status_code=303)

    # Owner order 2026-09-10: no trades outside market hours — their
    # own buttons included ("yes all block").
    _blocked = market_gate.trading_blocked(state.cfg())
    if _blocked:
        return market_gate.blocked_redirect("/positions", _blocked)

    p = state.portfolio()
    eng = state.engine()
    tickers = sorted(p.positions.keys())
    if not tickers:
        return RedirectResponse("/positions?error=nothing_to_close", status_code=303)

    prices, live = state.marks(tickers)
    closed, no_price = [], []
    now = datetime.utcnow()
    for t in tickers:
        px = prices.get(t)
        if not px or px <= 0:
            no_price.append(t)          # never close at a stale/guessed price
            continue
        eng._close(t, float(px), "OWNER_CLOSE_ALL", now)
        closed.append(t)

    p.decision_journal.append({
        "ts": now.isoformat(),
        "action": "CLOSE_ALL",
        "closed": len(closed),
        "skipped_no_live_price": no_price,
        "why": ("Owner order 2026-07-31: close all positions. Exits taken at "
                "live prices; names without a live quote were left open "
                "rather than closed at a stale mark."),
        "cash_after": round(p.cash, 2),
    })
    p.save()
    if no_price:
        return RedirectResponse(
            f"/positions?closed_all={len(closed)}&no_price={len(no_price)}",
            status_code=303)
    return RedirectResponse(f"/positions?closed_all={len(closed)}", status_code=303)


@router.post("/positions/sector-exit")
async def sector_exit(request: Request):
    """One tap to square off a segment the owner judges hit by an event.

    Owner pick C-narrow (2026-08-29), answering "how will app algorithm
    decide to exit the affected stock/segment like IT, metals, healthcare".
    The app does NOT decide: it groups the holdings a risk headline named,
    and this closes them only when the owner presses the button. No rule
    fires by itself — news cannot be calibrated here, so it never gets to
    move money on its own.

    `scope=flagged` (default) closes only the names news actually named.
    `scope=sector` closes every holding in that segment — the owner's own
    words were "exit the effected stock/segement", and a sanctions headline
    naming three metal names is usually a whole-segment event. Which one ran
    is journaled, so the record says what was intended.

    Same exit path as every other sale (`eng._close`), so costs, slippage
    and Closed Trades are identical. A name with no live quote is LEFT OPEN
    and reported — closing at an invented price is the fault this app does
    not commit.
    """
    user = auth.current_user(request)
    if not user or user.get("role") != "owner":
        return RedirectResponse("/positions?error=owner_only", status_code=303)

    # Owner order 2026-09-10: no trades outside market hours — their
    # own buttons included ("yes all block").
    _blocked = market_gate.trading_blocked(state.cfg())
    if _blocked:
        return market_gate.blocked_redirect("/positions", _blocked)

    form = await request.form()
    sector = (form.get("sector") or "").strip()
    scope = (form.get("scope") or "flagged").strip()
    if not sector:
        return RedirectResponse("/positions?error=no_sector", status_code=303)

    p, cfg = state.portfolio(), state.cfg()
    from trading.sector_events import sector_alerts
    alerts = sector_alerts(p.positions, (p.news_watch or {}).get("rows") or {},
                           cfg.sector_map)
    hit = next((a for a in alerts if a["sector"] == sector), None)
    if not hit:
        return RedirectResponse("/positions?error=sector_not_flagged",
                                status_code=303)

    targets = (hit["held"] if scope == "sector"
               else [f["ticker"] for f in hit["flagged"]])
    targets = [t for t in targets if t in p.positions]
    if not targets:
        return RedirectResponse("/positions?error=nothing_to_close",
                                status_code=303)

    eng = state.engine()
    prices, _live = state.marks(targets)
    closed, no_price = [], []
    now = datetime.utcnow()
    for t in targets:
        px = prices.get(t)
        if not px or px <= 0:
            no_price.append(t)
            continue
        eng._close(t, float(px), "OWNER_SECTOR_EXIT", now)
        closed.append(t)

    p.decision_journal.append({
        "ts": now.isoformat(),
        "action": "OWNER_SECTOR_EXIT",
        "sector": sector,
        "scope": scope,
        "closed": closed,
        "skipped_no_live_price": no_price,
        "why": (f"Owner squared off {sector} after risk news named "
                f"{hit['n_flagged']} of {hit['n_held']} holding(s) in that "
                f"segment ({', '.join(f['ticker'] for f in hit['flagged'])}). "
                f"Scope={scope}. Exits at live prices; names without a live "
                f"quote were left open rather than closed at a stale mark. "
                f"Owner-initiated — no news rule fires on its own."),
        "cash_after": round(p.cash, 2),
    })
    p.save()
    if no_price:
        return RedirectResponse(
            f"/positions?closed_all={len(closed)}&no_price={len(no_price)}",
            status_code=303)
    return RedirectResponse(f"/positions?closed_all={len(closed)}",
                            status_code=303)


@router.post("/positions/purge-universe")
def purge_universe(request: Request):
    """Owner order 2026-07-31: "remove all the old universe right away".

    Drops every cached/stale universe artefact so nothing old can be scanned
    or displayed again: the scan matrix, last picks/skips/opened, the virtual
    shadow book, and the on-disk Upstox instrument cache. Then pulls a FRESH
    universe from Upstox's public instrument file. Journaled, append-only —
    trade history is untouched.
    """
    user = auth.current_user(request)
    if not user or user.get("role") != "owner":
        return RedirectResponse("/settings?saved=denied", status_code=303)

    p = state.portfolio()
    before = {
        "scan_matrix_rows": len((p.scan_matrix or {}).get("rows") or {}),
        "last_picks": len(p.last_scan_picks or []),
        "virtual_book": len(p.virtual_book or {}),
    }
    p.scan_matrix = {}
    p.last_scan_picks = []
    p.last_scan_skipped = []
    p.last_scan_opened = []
    p.virtual_book = {}
    p.last_scan = None

    # blow away the cached instrument master so the refresh is genuinely fresh
    refreshed: dict = {}
    try:
        from trading.brokers.upstox_instruments import CACHE_FILE, get_lookup
        try:
            CACHE_FILE.unlink()
        except (OSError, FileNotFoundError):
            pass
        get_lookup(force_refresh=True)
    except Exception as e:
        log.warning("instrument cache purge failed: %s", e)
    try:
        from trading.universe_refresh import refresh_universe
        refreshed = refresh_universe() or {}
    except Exception as e:
        log.warning("universe refresh after purge failed: %s", e)
        refreshed = {"error": str(e)[:120]}

    p.decision_journal.append({
        "ts": datetime.utcnow().isoformat(),
        "action": "UNIVERSE_PURGE",
        "cleared": before,
        "refreshed": {k: refreshed.get(k) for k in ("count", "file_date", "error")
                      if k in refreshed},
        "why": "Owner order 2026-07-31: remove all the old universe.",
    })
    p.save()
    state.reload_config()
    return RedirectResponse(
        f"/settings?saved=universe_purged&n={refreshed.get('count', 0)}",
        status_code=303)


# Manual sector hints for tickers not in cfg.sector_map yet
_SECTOR_HINTS = {
    "COCHINSHIP.NS": "Defense",
    "ATGL.NS":       "Energy",
    "SBINEQWETF.NS": "ETF",
    "COALINDIA.NS":  "Metal",
}


@router.post("/positions/new")
def add_manual_position(
    request: Request,
    ticker: str = Form(...),
    qty: int = Form(...),
    entry_price: float = Form(...),
    entry_date: str = Form(...),
    sector: str = Form(""),
    entry_notes: str = Form(""),
    tags: str = Form(""),
):
    """Manual position open — for real-world holdings you want to track here."""
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    p = state.portfolio()
    cfg = state.cfg()

    ticker = ticker.strip().upper()
    if not ticker.endswith(".NS") and not ticker.endswith(".BO"):
        ticker = f"{ticker}.NS"
    if ticker in p.positions:
        return RedirectResponse("/positions?error=already_held", status_code=303)
    try:
        entry_dt = datetime.fromisoformat(entry_date)
    except ValueError:
        entry_dt = datetime.utcnow()
    sec = (sector.strip() or cfg.sector_map.get(ticker)
           or _SECTOR_HINTS.get(ticker) or "Unknown")
    notional = qty * entry_price
    p.cash -= notional
    p.positions[ticker] = PaperPosition(
        ticker=ticker, qty=qty, entry_price=float(entry_price),
        entry_date=entry_dt, sector=sec, peak_price=float(entry_price),
        entry_score=70.0, reasoning="Manual entry — real holding tracked here.",
        entry_notes=entry_notes.strip(),
        tags=[t.strip() for t in tags.split(",") if t.strip()],
    )
    p.save()
    return RedirectResponse("/positions", status_code=303)


# Your 5 holdings from 1-2 June 2026 (Upstox + Up app screenshots),
# plus COALINDIA (the only YES timing-pass from the recent scan).
_SEED_JUNE_2026 = [
    # (ticker,        qty,  entry_price, entry_date,  sector,    notes)
    ("COCHINSHIP.NS", 15,   1453.80,    "2026-06-01", "Defense", "From Upstox holdings"),
    ("HDFCBANK.NS",   15,    745.55,    "2026-06-01", "Financial","From Upstox holdings"),
    ("TCS.NS",        15,   2320.90,    "2026-06-01", "IT",      "From Upstox holdings"),
    ("ATGL.NS",       51,    753.70,    "2026-06-01", "Energy",  "From Up app — Adani Total Gas"),
    ("SBINEQWETF.NS", 101,    32.89,    "2026-06-01", "ETF",     "From Up app — SBI Nifty 50 ETF"),
    ("COALINDIA.NS",  100,   472.60,    "2026-06-02", "Metal",   "Scan YES-timing pick (June 2)"),
]


@router.post("/positions/seed-mine")
def seed_mine(request: Request):
    """Idempotent: adds the 6 seed positions if not already present."""
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    p = state.portfolio()
    added = []
    for ticker, qty, price, date_iso, sector, note in _SEED_JUNE_2026:
        if ticker in p.positions:
            continue
        notional = qty * price
        p.cash -= notional
        p.positions[ticker] = PaperPosition(
            ticker=ticker, qty=qty, entry_price=float(price),
            entry_date=datetime.fromisoformat(date_iso),
            sector=sector, peak_price=float(price),
            entry_score=70.0,
            reasoning=f"Seeded from real holding on {date_iso}.",
            entry_notes=note, tags=["seed_june_2026", "real_holding"],
        )
        added.append(ticker)
    if added:
        p.save()
    return RedirectResponse(f"/positions?seeded={len(added)}", status_code=303)
