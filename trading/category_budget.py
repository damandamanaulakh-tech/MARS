"""Per-category cash budgets — owner order 2026-10-04.

"fix it so all 5 categories get cash", with the split the owner picked:
momentum 40%, intraday / short / swing / positional 15% each.

WHY: the 09:20 rotation scans momentum first. A replay of the live machine on
a strong week (2024-01, real engine code over the committed panel) opened 46
momentum trades and none in the other four categories — momentum had spent
the cash before they were scanned. A share per category is the only way the
later passes ever see money on a day momentum finds plenty to buy.

THE RULES, each stated to the owner before it was built:
  * A share is a fraction of the DEPLOYABLE capital — equity times
    (1 - regime reserve), the same cap `risk.can_open` already enforces. In a
    correction the 20% reserve shrinks all five shares alike; none starves.
  * Positions count at ENTRY cost (qty x entry price), the convention the
    deploy cap uses, so the two limits can never disagree about what is spent.
  * A share a category does not use stays as cash. It is never lent to
    another category: a quiet intraday day does not hand momentum more.
  * Ocean is exempt — it has its own 8-slot ceiling and ticket.
  * A position's category is the trade category it was opened under
    (`PaperPosition.mode`). A blank mode is momentum, the same reading the
    boot retag uses; an unknown mode belongs to no budget.
  * OWNER ORDER 2026-10-21: the owner's own BUY button, a limit-order fill
    and the Buy Desk open positions tagged owner_pick / buy_desk. Those sit
    in their OWN "owner" bucket OUTSIDE the five shares, exactly like Ocean
    — they never fill momentum's share (before this order they counted as
    momentum, so a Rs 2.5 Cr Buy Desk fill held momentum's 40% share
    over-full and every scan pick read category_budget_full). Owner buys
    are never refused by a share. They never call risk.can_open at all —
    cash is their only check, as before this order — but what they hold
    still counts in the deploy cap, heat and sector capital every SCAN
    entry is judged against.

Pure arithmetic, no I/O. `risk.can_open` is the one place a budget refuses a
trade; the scan page, the rotation and the Risk page read the same numbers.
"""
from __future__ import annotations

from typing import Iterable, Optional

MOMENTUM = "nifty200_momentum"
OCEAN = "ocean"
# Owner order 2026-10-21: owner BUY / limit fill / Buy Desk positions.
OWNER = "owner"
OWNER_TAGS = ("owner_pick", "buy_desk")

# Category -> the Config field holding its share. Order = the 09:20 rotation.
SHARE_FIELDS: dict[str, str] = {
    MOMENTUM: "budget_share_momentum",
    "intraday": "budget_share_intraday",
    "short": "budget_share_short",
    "swing": "budget_share_swing",
    "positional": "budget_share_positional",
}

LABELS: dict[str, str] = {
    MOMENTUM: "Momentum",
    "intraday": "Intraday",
    "short": "Short",
    "swing": "Swing",
    "positional": "Positional",
}


def enabled(cfg) -> bool:
    return bool(getattr(cfg, "category_budgets_enabled", False))


def category_of(pos) -> str:
    """The budget a held position counts against. Tag-based for the two
    buckets outside the shares: an "ocean" tag = Ocean, an owner_pick /
    buy_desk tag = the owner bucket (owner order 2026-10-21); otherwise the
    mode it was opened under, blank = momentum."""
    tags = getattr(pos, "tags", None) or []
    if OCEAN in tags:
        return OCEAN
    if any(t in tags for t in OWNER_TAGS):
        return OWNER
    mode = (getattr(pos, "mode", "") or "").strip()
    return mode or MOMENTUM


def is_owner(pos) -> bool:
    """True for a position the owner opened themselves (BUY button, limit
    order, Buy Desk) — the "owner" bucket, outside the five shares."""
    return category_of(pos) == OWNER


def share(cfg, category: Optional[str]) -> Optional[float]:
    """This category's fraction of deployable capital, or None when no
    budget applies (switched off, Ocean, an unknown category)."""
    if not enabled(cfg) or category is None:
        return None
    field = SHARE_FIELDS.get(category)
    if field is None:
        return None
    try:
        v = float(getattr(cfg, field, 0.0) or 0.0)
    except (TypeError, ValueError):
        v = 0.0
    return max(0.0, v)


def used(positions: Iterable, category: str) -> float:
    """Rupees this category has deployed, at entry cost."""
    return float(sum(float(p.qty) * float(p.entry_price)
                     for p in positions if category_of(p) == category))


def deployable(cfg, base_capital: float) -> float:
    reserve = float(getattr(cfg, "cash_reserve_pct", 0.0) or 0.0)
    return max(0.0, float(base_capital)) * (1.0 - reserve)


def limit(cfg, category: str, base_capital: float) -> Optional[float]:
    s = share(cfg, category)
    if s is None:
        return None
    return s * deployable(cfg, base_capital)


def room(cfg, positions: dict, category: Optional[str],
         base_capital: float) -> Optional[float]:
    """Rupees this category may still deploy. None = no budget applies.
    Never negative: an over-full category reads 0."""
    lim = limit(cfg, category, base_capital) if category else None
    if lim is None:
        return None
    return max(0.0, lim - used(positions.values(), category))


def min_open(cfg) -> float:
    """Smallest new trade a budget remainder may fund. Under slab sizing the
    slab is the floor of every scan trade (owner order 2026-09-10), so a
    remainder under it is a full budget, never a dust position."""
    if str(getattr(cfg, "sizing_mode", "")).lower() == "slab":
        return float(getattr(cfg, "position_slab_value", 0.0) or 0.0)
    return 0.0


def usage(cfg, positions: dict, base_capital: float) -> list[dict]:
    """One row per category for the Risk page, in rotation order."""
    vals = list(positions.values())
    rows = []
    for cat in SHARE_FIELDS:
        s = share(cfg, cat)
        lim = limit(cfg, cat, base_capital)
        u = used(vals, cat)
        rows.append({
            "category": cat,
            "label": LABELS[cat],
            "share": s,
            "limit": lim,
            "used": u,
            "left": None if lim is None else max(0.0, lim - u),
            "open": sum(1 for p in vals if category_of(p) == cat),
            "full": (lim is not None and lim - u < max(min_open(cfg), 1.0)),
        })
    return rows


def owner_usage(positions: dict) -> dict:
    """The owner bucket for the Risk page: what the owner's own buys hold at
    entry cost and how many. No share, no limit — outside the five shares
    (owner order 2026-10-21), so it can never read "full"."""
    vals = list(positions.values())
    return {
        "category": OWNER,
        "label": "Owner",
        "share": None, "limit": None, "left": None, "full": False,
        "used": used(vals, OWNER),
        "open": sum(1 for p in vals if category_of(p) == OWNER),
        "note": "your BUY button, limit fills and the Buy Desk — outside the five shares, like Ocean",
    }


def share_total(cfg) -> float:
    return float(sum((share(cfg, c) or 0.0) for c in SHARE_FIELDS))
