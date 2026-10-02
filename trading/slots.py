"""Slot sizing — the owner's funnel arithmetic, on screen.

OWNER'S DESIGN (2026-08-13, their words): "in real life capital amount is
fixed once i use 60-70% then cant use as using here ... we will pick 50 stock
which u already filter" — i.e. the candidate list should be sized to what cash
can actually fund, because surfacing 50 names when only 12 are fundable is
wasted motion.

The arithmetic is the same one `risk.can_open` applies when a trade actually
opens (deployed at ENTRY prices, the regime reserve, the per-trade size
from `sizing.position_notional_for`, and the max-positions ceiling) so the
number shown is the number the gate will enforce — not a second formula
that can disagree with it.

DISPLAY ONLY. Nothing here opens, blocks or sizes a trade; `risk.can_open`
remains the sole authority at execution time. This module exists so the scan
page can say "you can fund N of these today" and mean it.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SlotView:
    """The funnel numbers for one moment of the book."""
    per_trade: float          # size of a trade with no edge estimate (the slab
                              # under slab sizing; the fixed amount under fixed)
    cash: float               # free cash in the book
    deployed: float           # sum of qty * entry_price, as risk.can_open counts it
    base_capital: float       # equity base the reserve is measured against
    reserve_pct: float        # regime reserve in force (0 / 0.20 / 0.40)
    deploy_cap: float         # base_capital * (1 - reserve_pct)
    open_count: int
    max_positions: int
    slots_by_cash: int        # what headroom (cash AND reserve) can fund
    slots_by_ceiling: int     # what the max-positions ceiling still allows
    free: int                 # min of the two, floored at 0
    # Audit F-14 (2026-10-02): when the ranked picks are passed in, each one
    # is sized on the live ladder and `cut` is the ROW where cash runs out
    # (held names and no-edge rows cost nothing, so they do not move it).
    cut: int = -1             # rows above the divider; -1 = same as `free`
    sizes: tuple = ()         # rupee size of each fundable new trade, in order
    ladder: bool = False      # True when sized per pick on the live ladder

    @property
    def divider_row(self) -> int:
        return self.free if self.cut < 0 else self.cut

    @property
    def limiting(self) -> str:
        """Which constraint binds — shown so the owner knows what to change."""
        if self.free <= 0:
            if self.slots_by_ceiling <= 0:
                return "max_positions"
            return "cash"
        if self.slots_by_cash <= self.slots_by_ceiling:
            return "cash"
        return "max_positions"


def slot_view(portfolio, cfg, picks=None, close_of=None) -> SlotView:
    """How many NEW trades today's cash can fund, by the live gate's own math.

    Deployed value is at ENTRY prices — the exact convention `risk.can_open`
    uses for its reserve test — so this display can never promise a slot the
    gate would refuse. base_capital is cash + deployed (entry-price equity):
    can_open uses live-marked equity when the engine has quotes, which this
    page does not fetch, so the reserve line here can differ from the gate's
    by the day's unrealised P&L. That is a display approximation, said on the
    page, never a different rule.

    AUDIT F-14 (owner: "go for next", 2026-10-02): this used to divide cash
    by `cfg.position_notional()` — the old flat Rs 1,25,000 — while every
    real trade since 2026-09-10 is sized by the slab ladder (at least the
    Rs 1,50,000 slab, Rs 4-13 lakh at higher scores). The card showed 20% to
    10x more slots than cash could fund. Now the per-trade size comes from
    `sizing.position_notional_for`, the engine's own entry point, and when
    the ranked `picks` are given each pick is sized at its own score (and
    stored close history via `close_of`, when the caller can supply it).
    """
    from .sizing import position_notional_for

    cash = float(portfolio.cash)
    deployed = float(sum(p.qty * p.entry_price
                         for p in portfolio.positions.values()))
    base_capital = cash + deployed
    if base_capital <= 0:
        base_capital = float(cfg.starting_capital)
    # No score = the size a trade gets with no edge estimate: the slab under
    # slab sizing, the fixed amount under fixed sizing.
    per_trade = float(position_notional_for(cfg, base_capital)[0] or 0.0)
    reserve_pct = float(getattr(cfg, "cash_reserve_pct", 0.0) or 0.0)
    deploy_cap = base_capital * (1.0 - reserve_pct)
    headroom = min(cash, deploy_cap - deployed)
    open_count = len(portfolio.positions)
    slots_by_ceiling = int(cfg.max_positions) - open_count

    if not picks:
        slots_by_cash = int(headroom // per_trade) if per_trade > 0 else 0
        free = max(0, min(slots_by_cash, slots_by_ceiling))
        return SlotView(
            per_trade=per_trade, cash=cash, deployed=deployed,
            base_capital=base_capital, reserve_pct=reserve_pct,
            deploy_cap=deploy_cap, open_count=open_count,
            max_positions=int(cfg.max_positions),
            slots_by_cash=max(0, slots_by_cash),
            slots_by_ceiling=max(0, slots_by_ceiling), free=free)

    held = set(portfolio.positions)
    trades: list = []                      # (row index, rupees), in rank order
    remaining = headroom
    stop_row = len(picks)
    for i, pk in enumerate(picks):
        t = pk.get("ticker")
        if t in held:
            continue                       # already owned: not a new trade
        close = None
        if close_of is not None:
            try:
                close = close_of(t)
            except Exception:
                close = None
        n = float(position_notional_for(cfg, base_capital,
                                        score=pk.get("score"),
                                        close=close)[0] or 0.0)
        if n <= 0:
            continue                       # no estimated edge: never opened
        if n > remaining:
            stop_row = i
            break
        remaining -= n
        trades.append((i, n))
    slots_by_cash = len(trades)
    free = max(0, min(slots_by_cash, slots_by_ceiling))
    cut = trades[free][0] if free < len(trades) else stop_row
    sizes = tuple(round(n, 0) for _, n in trades[:free])
    return SlotView(
        per_trade=per_trade, cash=cash,
        deployed=deployed, base_capital=base_capital,
        reserve_pct=reserve_pct, deploy_cap=deploy_cap,
        open_count=open_count, max_positions=int(cfg.max_positions),
        slots_by_cash=max(0, slots_by_cash),
        slots_by_ceiling=max(0, slots_by_ceiling), free=free,
        cut=cut, sizes=sizes, ladder=True)
