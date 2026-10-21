"""Owner order ticket — manual paper orders with qty, market/limit,
custom stop and target (user ask 2026-07-21: "let it decide but when i
want to do, then it must have that too").

Market orders fill immediately through the engine's normal entry path.
Limit orders rest here (portfolio.pending_orders) and are checked
after the 09:20 scan passes, on every 30-minute in-session pass, at
14:30 (whether or not the top-up scan itself runs), and after any manual
scan run. (Audit S-14, 2026-10-02: this used to name a "5-min intraday
tick" — that job only exists when the intraday scheduler is switched on,
which it is not on the live app.) Day
validity like a real broker: whatever is still open at the 15:25
square-off expires. Every placement, fill, cancel and expiry is
journaled — append-only, as ever. Paper book only, always.
"""

from __future__ import annotations

import logging
from datetime import datetime

log = logging.getLogger(__name__)


def _journal(p, entry: dict) -> None:
    p.decision_journal.append(entry)


def place_limit(p, *, ticker: str, limit_price: float, qty: int = None,
                stop_pct: float = None, target_pct: float = None,
                now: datetime = None) -> dict:
    """Rest a day-validity limit BUY. qty None = engine auto-size at fill.
    stop_pct/target_pct are positive percents (8 -> stop 8% below fill)."""
    now = now or datetime.utcnow()
    order = {
        "id": f"ORD-{now.strftime('%Y%m%d%H%M%S')}-{len(p.pending_orders) + 1}",
        "ticker": ticker,
        "qty": int(qty) if qty else None,
        "limit_price": round(float(limit_price), 2),
        "stop_pct": float(stop_pct) if stop_pct else None,
        "target_pct": float(target_pct) if target_pct else None,
        "placed": now.isoformat(),
        "status": "open",
        "note": "",
    }
    p.pending_orders.append(order)
    _journal(p, {
        "ts": now.isoformat(), "action": "OWNER_ORDER_PLACED",
        "ticker": ticker, "order_id": order["id"],
        "limit_price": order["limit_price"], "qty": order["qty"] or "auto",
        "why": (f"Owner limit order: BUY {order['qty'] or 'auto-size'} "
                f"@ ₹{order['limit_price']} or better, valid for the day."),
    })
    p.save()
    return order


def cancel(p, order_id: str, why: str = "owner cancelled",
           now: datetime = None) -> bool:
    now = now or datetime.utcnow()
    for o in p.pending_orders:
        if o["id"] == order_id and o["status"] == "open":
            o["status"] = "cancelled"
            o["note"] = why
            _journal(p, {
                "ts": now.isoformat(), "action": "OWNER_ORDER_CANCELLED",
                "ticker": o["ticker"], "order_id": order_id, "why": why,
            })
            p.save()
            return True
    return False


def apply_custom_levels(pos, stop_pct: float = None,
                        target_pct: float = None) -> str:
    """Overwrite the position's snapshot stop/target with the owner's own
    percents (positive numbers, measured from fill). Returns a note."""
    notes = []
    if stop_pct:
        pos.stop_price = round(pos.entry_price * (1 - abs(stop_pct) / 100), 2)
        pos.custom_levels = True
        notes.append(f"stop −{abs(stop_pct):g}% (₹{pos.stop_price})")
    if target_pct:
        pos.target_price = round(pos.entry_price * (1 + abs(target_pct) / 100), 2)
        pos.custom_levels = True
        notes.append(f"target +{abs(target_pct):g}% (₹{pos.target_price})")
        # Item 19: a hand-typed target always wins over the valuation /
        # k x ATR target and is never re-marked or re-targeted.
        if hasattr(pos, "val_target"):
            prev = (getattr(pos, "val_target", None) or {}).get("source")
            pos.val_target = {"source": "owner", "replaced": prev or "",
                              "reason": "hand-typed target on the order ticket"}
    return ", ".join(notes)


def _fill(engine, o: dict, price: float, now: datetime) -> None:
    """Open the position for a touched limit order via the engine's own
    entry path — momentum identity, like the owner BUY button."""
    p = engine.portfolio
    cfg = engine.cfg
    tkr = o["ticker"]
    if tkr in p.positions:
        o["status"] = "cancelled"
        o["note"] = "already held when limit touched"
        _journal(p, {"ts": now.isoformat(), "action": "OWNER_ORDER_CANCELLED",
                     "ticker": tkr, "order_id": o["id"], "why": o["note"]})
        return

    # AUDIT S-6: held under the shared mode lock so a fill never interleaves
    # with a scan pass (it reads cash and the book the pass is changing).
    # Since F-17 the fill itself no longer flips cfg.mode.
    from .modes import holding_mode_lock
    with holding_mode_lock():
        _fill_locked(engine, o, price, now)


def _fill_locked(engine, o: dict, price: float, now: datetime) -> None:
    """AUDIT F-17 (owner: "go for next", 2026-10-02), two changes:

    1. NO MODE FLIP. The fill used to set the SHARED cfg.mode to momentum and
       put it back afterwards; it now works on a momentum COPY
       (`engine.mode_cfg`) and hands that copy to `engine._open`, so the
       live config is never touched.
    2. SAME SLIPPAGE AS EVERY ENGINE FILL, never above the limit. Engine and
       Buy Desk fills pay `slippage_price` (5 bps); limit fills paid
       nothing, so they looked slightly better than any other trade. A limit
       order still can never fill above its own limit — the slipped price is
       capped there.
    """
    from .costs import slippage_price
    p = engine.portfolio
    cfg = engine.mode_cfg("nifty200_momentum")
    tkr = o["ticker"]
    quote = float(price)
    fill = min(slippage_price(quote, "BUY", cfg), float(o["limit_price"]))
    qty = o["qty"]
    if not qty:
        marks = {pos.ticker: pos.entry_price for pos in p.positions.values()}
        # AUDIT FIX A11 (2026-08-09): the one shared sizing entry point —
        # with no score/history it resolves to the owner's fixed rupee
        # amount under every sizing mode (a manual order is the owner's
        # own number), and manual fills can never drift from engine sizing.
        from .sizing import position_notional_for
        notional, _diag = position_notional_for(cfg, p.equity(marks))
        qty = int(notional // fill)
    if qty < 1 or qty * fill > p.cash:
        o["status"] = "cancelled"
        o["note"] = "insufficient cash when limit touched"
        _journal(p, {"ts": now.isoformat(), "action": "OWNER_ORDER_CANCELLED",
                     "ticker": tkr, "order_id": o["id"], "why": o["note"]})
        return
    # AUDIT FIX A4 (2026-08-09): carry the real scan score when one
    # exists (score 0.0 made refresh's score-decay auto-sell the owner's
    # deliberate pick), and tag the position owner_pick so the decay
    # exit never judges it. Stop/target/trail/max-hold still apply.
    score = 0.0
    components: dict = {}
    try:
        row = ((p.scan_matrix or {}).get("rows") or {}).get(tkr) or {}
        score = float(row.get("score") or 0.0)
        components = dict(row.get("c") or {})
    except Exception:
        pass
    pick = {"ticker": tkr, "score": score, "components": components,
            "timing_pass": True,
            "fill_source": "limit_order",
            "reasoning": (f"OWNER LIMIT ORDER {o['id']} filled @ ₹{fill:.2f} "
                          f"(quote ₹{quote:.2f}, limit ₹{o['limit_price']:.2f}) "
                          "— owner's call, regime halt overridden by owner.")}
    engine._open(pick, qty, fill, now, cfg=cfg)
    p.positions[tkr].tags.append("owner_pick")
    note = apply_custom_levels(p.positions[tkr], o["stop_pct"], o["target_pct"])
    if note:
        p.positions[tkr].reasoning += f" Custom levels: {note}."
    o["status"] = "filled"
    o["filled_price"] = round(fill, 2)
    o["filled_qty"] = qty
    o["filled_ts"] = now.isoformat()
    _journal(p, {
        "ts": now.isoformat(), "action": "OWNER_ORDER_FILLED",
        "ticker": tkr, "order_id": o["id"], "qty": qty,
        "price": round(fill, 2), "quote": round(quote, 2),
        "why": (f"Limit ₹{o['limit_price']:.2f} touched at ₹{quote:.2f} — "
                f"bought {qty} @ ₹{fill:.2f} (after the usual slippage, never "
                f"above the limit)." + (f" {note}." if note else "")),
    })


def process_open_orders(engine, now: datetime = None) -> list:
    """Check every resting limit order against live quotes; fill the
    touched ones. Called wherever the engine already looks at prices."""
    p = engine.portfolio
    open_orders = [o for o in getattr(p, "pending_orders", []) or []
                   if o.get("status") == "open"]
    if not open_orders:
        return []
    # Owner order 2026-09-10: a fill is a trade. A resting order may WAIT
    # outside market hours — that is the point of resting — but it may not
    # execute against a price the market is not making. The 15:25 expiry pass
    # still runs; an unfilled day order still dies on time.
    if not engine._trading_allowed(now):
        return []
    now = now or datetime.utcnow()
    from .quote_router import fetch_quotes
    tickers = sorted({o["ticker"] for o in open_orders})
    try:
        prices = fetch_quotes(tickers, upstox_client=engine.broker)
    except Exception as e:
        log.warning("Pending-order quote fetch failed: %s", e)
        return []
    actions = []
    for o in open_orders:
        q = prices.get(o["ticker"])
        if q and q > 0 and q <= o["limit_price"]:
            _fill(engine, o, float(q), now)
            actions.append(o["id"])
    if actions:
        p.save()
    return actions


def expire_day_orders(p, now: datetime = None) -> int:
    """15:25 square-off moment: day validity ends — expire what's left."""
    now = now or datetime.utcnow()
    n = 0
    for o in getattr(p, "pending_orders", []) or []:
        if o.get("status") == "open":
            o["status"] = "expired"
            o["note"] = "day validity over (15:25)"
            _journal(p, {
                "ts": now.isoformat(), "action": "OWNER_ORDER_EXPIRED",
                "ticker": o["ticker"], "order_id": o["id"],
                "why": f"Limit ₹{o['limit_price']:.2f} never touched today.",
            })
            n += 1
    if n:
        p.save()
    return n
