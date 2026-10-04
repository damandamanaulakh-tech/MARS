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
  * The owner's own BUY button and the Buy Desk are never refused by a
    budget (their call, their book), but what they buy carries momentum's
    mode, so it counts toward momentum's share and can fill it.

Pure arithmetic, no I/O. `risk.can_open` is the one place a budget refuses a
trade; the scan page, the rotation and the Risk page read the same numbers.
"""
from __future__ import annotations

from typing import Iterable, Optional

MOMENTUM = "nifty200_momentum"
OCEAN = "ocean"

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
    """The budget a held position counts against."""
    if OCEAN in (getattr(pos, "tags", None) or []):
        return OCEAN
    mode = (getattr(pos, "mode", "") or "").strip()
    return mode or MOMENTUM


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


def share_total(cfg) -> float:
    return float(sum((share(cfg, c) or 0.0) for c in SHARE_FIELDS))
