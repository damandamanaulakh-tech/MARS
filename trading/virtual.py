"""F8 — virtual "shadow book" of YES-scored picks that were NOT bought.

Records every pick that passed scoring but couldn't be opened (cash reserve,
slots full, sector cap, waiting-candle, throttle) at its price on that day,
then marks it to market. When a virtual pick would hit its target or stop, it
is closed virtually and its result frozen. This shows the OPPORTUNITY COST /
BENEFIT of the gates: "what the picks we skipped actually did."

Paper-only, purely informational. No orders, no capital used.
"""

from __future__ import annotations


def record_virtual(book: dict, ticker: str, price: float, score: float,
                   sector: str, reason: str, target_pct: float,
                   stop_pct: float, now_iso: str) -> None:
    """Add a skipped YES pick to the shadow book if not already tracked open."""
    if price <= 0:
        return
    existing = book.get(ticker)
    if existing and existing.get("status") == "open":
        return                       # already tracking this one
    book[ticker] = {
        "ticker": ticker,
        "first_seen": now_iso,
        "entry_px": round(price, 2),
        "score": round(score, 1),
        "sector": sector,
        "skip_reason": reason,
        "status": "open",
        "target_px": round(price * (1 + target_pct), 2),
        "stop_px": round(price * (1 + stop_pct), 2),   # stop_pct is negative
        "last_px": round(price, 2),
        "pnl_pct": 0.0,
    }


def mark_virtual(book: dict, prices: dict, now_iso: str) -> dict:
    """Mark every open virtual pick to market; freeze it if target/stop hit.
    Returns a small summary dict."""
    open_n = win = loss = 0
    open_pnl = realized_pnl = 0.0
    for tkr, v in book.items():
        if v.get("status") != "open":
            if v.get("status") == "target":
                win += 1
            elif v.get("status") == "stop":
                loss += 1
            realized_pnl += float(v.get("pnl_pct", 0.0))
            continue
        px = float(prices.get(tkr, v["last_px"]) or v["last_px"])
        v["last_px"] = round(px, 2)
        entry = float(v["entry_px"]) or 1.0
        v["pnl_pct"] = round((px / entry - 1) * 100, 2)
        if px >= float(v["target_px"]):
            v["status"] = "target"; v["exit_at"] = now_iso; win += 1
            realized_pnl += v["pnl_pct"]
        elif px <= float(v["stop_px"]):
            v["status"] = "stop"; v["exit_at"] = now_iso; loss += 1
            realized_pnl += v["pnl_pct"]
        else:
            open_n += 1
            open_pnl += v["pnl_pct"]
    return {
        "open": open_n, "target_hits": win, "stop_hits": loss,
        "open_avg_pnl_pct": round(open_pnl / open_n, 2) if open_n else 0.0,
        "closed_total_pnl_pct": round(realized_pnl, 2),
        "count": len(book),
    }
