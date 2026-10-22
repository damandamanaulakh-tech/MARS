"""Buy Desk — the owner's shortlist, sized to a rupee budget, bought in one tap.

OWNER ORDER 2026-09-24 (their words): "add the shortlist and recomenderd to
buy in app / use upto 2.5 cr" -> asked what the budget governs -> "m saying
buy the stocks from listed by app, use upto 2.5 cr of capital amount, bypass
the market hours filter for this only for now one time" -> Shortlist =
"Cleared floor + momentum + liquidity".

WHAT IT IS
  * SHORTLIST: every name in the last scan's matrix that cleared the entry
    floor, the absolute-momentum test and the liquidity gate, is not already
    held and is not blocklisted — ranked by score. Timing, candle, breadth,
    sector and correlation gates are NOT applied here: those are the engine's
    own hesitations, and the owner's order is to buy what the app listed.
  * RECOMMENDED: the shortlist filled in score order through the live slab
    ladder (`sizing.position_notional_for`, the SAME arithmetic the engine
    sizes with) until the budget is spent. Everything past the budget is
    shown, greyed, never hidden ("no block just show").
  * BUY: the owner taps once; every recommended name opens through
    `engine._open`, the same path the 09:20 scan and the owner ticket use,
    at the LIVE quote (slippage applied like an engine fill). A name with no
    quote is SKIPPED AND REPORTED — never an invented fill (2026-08-04 rule).

WHAT IT IS NOT
  * Not automatic. Nothing here runs on a schedule; the engine's daily
    buying is untouched and keeps its gates. The one-time market-hours
    bypass is a checkbox on the form, journaled as such, never a config
    change ("for now one time").
  * Not a new sizing rule. Budget only caps the TOTAL; each name's size is
    the ladder's. When the budget's remainder is below the slab the desk
    stops rather than open a sub-slab dust position.
  * XR-083 (entry-path batch 2026-10-22): quantities are sized on the
    SLIPPAGE-APPLIED fill price — the price the buy really pays — not the raw
    quote, and the buy keeps a running total and skips a name
    ("budget_short") rather than pass the typed budget. Before this a
    Rs 2.5 Cr batch could spend Rs 2.5 Cr + 5 bps (about Rs 12,500 more).

Every run journals OWNER_BATCH_BUY with the budget, what opened at what
price, what was skipped and why, and whether market hours were bypassed.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Optional

log = logging.getLogger(__name__)

#: the owner's number, 2026-09-24
DEFAULT_BUDGET_RS = 2_50_00_000.0
#: bound on shortlist rows the page prices and sizes in one request
MAX_NAMES = 60
#: momentum is the recipe the shortlist definition describes (floor +
#: 6-month momentum + liquidity); positions carry this identity like the
#: owner ticket's do
DESK_MODE = "nifty200_momentum"


def shortlist(portfolio, cfg) -> dict:
    """The names the app listed that cleared floor + momentum + liquidity.

    AUDIT S-11 (owner: "go for next", 2026-10-02): reads the MOMENTUM pass's
    own record (`portfolio.momentum_shortlist`, written by engine.scan) — the
    shared scan matrix is overwritten by every pass and positional runs last,
    so the desk had been reading positional scores. Liquidity is the entry
    gate's own verdict stored per row; a name with no verdict is left out and
    counted, never assumed liquid (the old rule read "no liquidity_* outcome"
    as cleared, but a pass stopped early never reaches the liquidity gate).
    Falls back to the last scan matrix, said on the card, when no momentum
    record exists yet (first boot after this change).

    The floor is the record's OWN floor — the one those scores were judged
    against — so the desk never re-judges yesterday's scores with today's
    setting.
    """
    ms = getattr(portfolio, "momentum_shortlist", None) or {}
    use_momentum = bool(ms.get("rows") is not None and ms.get("ts"))
    m = ms if use_momentum else (portfolio.scan_matrix or {})
    rows = m.get("rows") or {}
    try:
        floor = float(m.get("score_floor") or cfg.min_score_to_enter)
    except (TypeError, ValueError):
        floor = float(cfg.min_score_to_enter)
    held = set(portfolio.positions.keys())
    block = set(getattr(cfg, "ticker_blocklist", ()) or ())
    # 2026-10-16: fund units (ETFs / liquid funds) are not stocks — a list
    # stored before the scan stopped reading them must not offer one.
    try:
        from trading.universes import fund_unit_tickers
        block |= set(fund_unit_tickers())
    except Exception:                                   # noqa: BLE001
        pass
    out = []
    not_judged = 0
    for t, r in rows.items():
        score = r.get("score")
        if score is None:
            continue
        try:
            score = float(score)
        except (TypeError, ValueError):
            continue
        outcome = str(r.get("outcome") or "")
        if score < floor:
            continue
        if not r.get("abs_mom", True):
            continue
        if use_momentum:
            if r.get("liq") is None:
                not_judged += 1
                continue
            if not r.get("liq"):
                continue
        elif outcome.startswith("liquidity_"):
            continue
        if t in held or t in block:
            continue
        out.append({
            "ticker": t, "score": score,
            "timing": bool(r.get("timing")),
            "outcome": outcome,
            "components": dict(r.get("c") or {}),
        })
    out.sort(key=lambda x: (-x["score"], x["ticker"]))
    return {
        "mode": m.get("mode"), "floor": floor, "asof": m.get("ts"),
        "source": "momentum_pass" if use_momentum else "last_scan_matrix",
        "liquidity_not_judged": not_judged,
        "total": len(out), "rows": out[:MAX_NAMES],
        "truncated": max(0, len(out) - MAX_NAMES),
    }


def plan(portfolio, cfg, rows: list, budget_rs: float, prices: dict,
         close_of: Callable[[str], Optional[object]]) -> dict:
    """Size the shortlist through the ladder and fill the budget in score
    order. Pure: no positions change here.

    `cfg` must already be in DESK_MODE (the caller switches, exactly as the
    owner ticket does) so the ladder reads the momentum floor. `close_of`
    returns a Close series or None; None = "no price history" and the ladder
    honestly falls to the slab (sizing.py's own rule).
    """
    from trading.costs import slippage_price
    from trading.sizing import position_notional_for

    marks = {pos.ticker: pos.entry_price for pos in portfolio.positions.values()}
    equity = float(portfolio.equity(marks))
    slab = float(getattr(cfg, "position_slab_value", 0.0) or 0.0)
    remaining = float(budget_rs)
    planned = []
    for r in rows:
        t = r["ticker"]
        row = dict(r)
        row.update({"price": None, "fill": None, "notional": None, "qty": 0,
                    "cost": 0.0, "recommended": False, "reason": ""})
        price = prices.get(t)
        if not price or float(price) <= 0:
            row["reason"] = "no_quote"
            planned.append(row)
            continue
        price = float(price)
        row["price"] = price
        # XR-083: what the buy will really pay per share (execute fills at
        # exactly this), so qty x fill — the cost — never passes the budget.
        fill = float(slippage_price(price, "BUY", cfg))
        row["fill"] = fill
        try:
            close = close_of(t)
        except Exception:                                     # noqa: BLE001
            close = None
        notional, diag = position_notional_for(cfg, equity, score=r["score"],
                                               close=close)
        row["size_diag"] = diag
        if notional <= 0:
            row["reason"] = str(diag.get("reason") or "no_estimated_edge")
            planned.append(row)
            continue
        row["notional"] = float(notional)
        if remaining <= 0:
            row["reason"] = "budget_exhausted"
            planned.append(row)
            continue
        take = min(float(notional), remaining)
        if take < float(notional) and slab > 0 and take < slab:
            row["reason"] = "budget_remainder_below_slab"
            planned.append(row)
            continue
        qty = int(take // fill)
        if qty < 1:
            row["reason"] = "price_above_size"
            planned.append(row)
            continue
        cost = qty * fill
        remaining -= cost
        row.update({"qty": qty, "cost": cost, "recommended": True})
        planned.append(row)
    rec = [r for r in planned if r["recommended"]]
    return {
        "budget": float(budget_rs),
        "planned_total": round(sum(r["cost"] for r in rec), 2),
        "remaining": round(remaining, 2),
        "recommended_count": len(rec),
        "rows": planned,
        "equity": equity,
    }


def execute(eng, portfolio, cfg, planned_rows: list, now: datetime, *,
            budget_rs: float, market_hours_bypassed: bool,
            price_sources: Optional[dict] = None) -> dict:
    """Open every recommended row through engine._open. Caller has switched
    cfg into DESK_MODE and holds the owner's authority. Returns the summary
    that is also journaled as OWNER_BATCH_BUY."""
    from trading.costs import slippage_price

    opened, skipped = [], []
    budget = float(budget_rs)
    spent_so_far = 0.0
    for r in planned_rows:
        t = r["ticker"]
        if not r.get("recommended"):
            skipped.append({"ticker": t, "reason": r.get("reason") or "not_recommended"})
            continue
        if t in portfolio.positions:
            skipped.append({"ticker": t, "reason": "already_held"})
            continue
        fill = slippage_price(float(r["price"]), "BUY", cfg)
        qty = int(r["qty"])
        cost = qty * fill
        if qty < 1 or cost > portfolio.cash:
            skipped.append({"ticker": t, "reason": "cash_short"})
            continue
        # XR-083: the typed budget is a ceiling on what the batch PAYS. The
        # plan already sizes on this fill; this catches a stale or hand-built
        # plan, so the total can never pass the budget.
        if spent_so_far + cost > budget + 1e-6:
            skipped.append({"ticker": t, "reason": "budget_short"})
            continue
        spent_so_far += cost
        src = (price_sources or {}).get(t) or "live_quote"
        pick = {
            "ticker": t, "score": float(r["score"]),
            "components": dict(r.get("components") or {}),
            "timing_pass": bool(r.get("timing")),
            "last_close": float(r["price"]),
            "fill_source": ("live_quote" if src == "upstox" else src),
            "size_diag": r.get("size_diag") or {"mode": "buy_desk"},
            "reasoning": (f"OWNER BUY DESK — bought from the app's shortlist "
                          f"(score {r['score']:.1f} >= floor; abs-momentum and "
                          f"liquidity cleared). Budget-filled in score order."),
        }
        eng._open(pick, qty, fill, now)
        pos = portfolio.positions[t]
        pos.tags.append("owner_pick")
        pos.tags.append("buy_desk")
        opened.append({"ticker": t, "qty": qty, "price": round(fill, 2),
                       "notional": round(cost, 2), "score": float(r["score"])})
    spent = round(sum(o["notional"] for o in opened), 2)
    summary = {
        "ts": now.isoformat(), "action": "OWNER_BATCH_BUY",
        "budget": float(budget_rs), "spent": spent,
        "opened": opened, "skipped": skipped,
        "market_hours_bypassed": bool(market_hours_bypassed),
        "mode": getattr(cfg, "mode", "?"),
        "why": (f"Owner bought the app's shortlist: {len(opened)} name(s), "
                f"Rs {spent:,.0f} of a Rs {float(budget_rs):,.0f} budget"
                + (" — market-hours gate bypassed for this one order."
                   if market_hours_bypassed else ".")),
    }
    portfolio.decision_journal.append(summary)
    return summary


def last_run(portfolio) -> Optional[dict]:
    """The most recent OWNER_BATCH_BUY journal entry, for the card."""
    for e in reversed(portfolio.decision_journal or []):
        if e.get("action") == "OWNER_BATCH_BUY":
            return e
    return None
