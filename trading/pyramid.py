"""F15 — partial-BUY pyramiding (add to winners in tranches).

Source: the Chityy pyramid running-maps. A position that keeps working earns
add-on tranches at rising trigger steps, capped at `max_pyramid_adds`. Each
add uses `pyramid_add_pct` of the ORIGINAL entry size and re-weights the
average entry price. Opt-in (cfg.pyramid_enabled) so it never surprises the
live buy-and-hold book.
"""

from __future__ import annotations


def pyramid_plan(pos, ltp: float, cash: float, cfg) -> tuple[int, float] | None:
    """Return (add_qty, add_notional) if this position qualifies for a pyramid
    tranche right now, else None. Pure — no mutation, no I/O."""
    if not getattr(cfg, "pyramid_enabled", False):
        return None
    entry = float(getattr(pos, "entry_price", 0) or 0)
    if entry <= 0 or ltp <= 0:
        return None
    adds = int(getattr(pos, "pyramid_adds", 0) or 0)
    if adds >= int(getattr(cfg, "max_pyramid_adds", 2)):
        return None
    trigger = float(getattr(cfg, "pyramid_trigger_pct", 0.08))
    # Each tranche needs a HIGHER bar: +8%, then +16%, ...
    needed = trigger * (adds + 1)
    if (ltp / entry - 1) < needed:
        return None
    base_qty = int(getattr(pos, "orig_qty", 0) or getattr(pos, "qty", 0) or 0)
    add_qty = int(base_qty * float(getattr(cfg, "pyramid_add_pct", 0.5)))
    if add_qty <= 0:
        return None
    notional = add_qty * ltp
    if notional > cash:
        # shrink to fit available cash
        add_qty = int(cash // ltp)
        notional = add_qty * ltp
        if add_qty <= 0:
            return None
    return add_qty, notional


def apply_pyramid(pos, add_qty: int, fill_price: float) -> None:
    """Mutate the position: add the tranche, re-weight avg entry, bump count."""
    old_qty = int(pos.qty)
    if not getattr(pos, "orig_qty", 0):
        pos.orig_qty = old_qty
    new_qty = old_qty + add_qty
    # weighted-average entry price across the combined lot
    pos.entry_price = (pos.entry_price * old_qty + fill_price * add_qty) / new_qty
    pos.qty = new_qty
    pos.pyramid_adds = int(getattr(pos, "pyramid_adds", 0)) + 1
