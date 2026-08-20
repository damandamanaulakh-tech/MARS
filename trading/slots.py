"""Slot sizing — the owner's funnel arithmetic, on screen.

OWNER'S DESIGN (2026-08-13, their words): "in real life capital amount is
fixed once i use 60-70% then cant use as using here ... we will pick 50 stock
which u already filter" — i.e. the candidate list should be sized to what cash
can actually fund, because surfacing 50 names when only 12 are fundable is
wasted motion.

The arithmetic is the same one `risk.can_open` applies when a trade actually
opens (deployed at ENTRY prices, the regime reserve, the fixed per-trade
notional, the max-positions ceiling) so the number shown is the number the
gate will enforce — not a second formula that can disagree with it.

DISPLAY ONLY. Nothing here opens, blocks or sizes a trade; `risk.can_open`
remains the sole authority at execution time. This module exists so the scan
page can say "you can fund N of these today" and mean it.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SlotView:
    """The funnel numbers for one moment of the book."""
    per_trade: float          # rupee size of one new trade (cfg.position_notional)
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


def slot_view(portfolio, cfg) -> SlotView:
    """How many NEW trades today's cash can fund, by the live gate's own math.

    Deployed value is at ENTRY prices — the exact convention `risk.can_open`
    uses for its reserve test — so this display can never promise a slot the
    gate would refuse. base_capital is cash + deployed (entry-price equity):
    can_open uses live-marked equity when the engine has quotes, which this
    page does not fetch, so the reserve line here can differ from the gate's
    by the day's unrealised P&L. That is a display approximation, said on the
    page, never a different rule.
    """
    per_trade = float(cfg.position_notional())
    cash = float(portfolio.cash)
    deployed = float(sum(p.qty * p.entry_price
                         for p in portfolio.positions.values()))
    base_capital = cash + deployed
    if base_capital <= 0:
        base_capital = float(cfg.starting_capital)
    reserve_pct = float(getattr(cfg, "cash_reserve_pct", 0.0) or 0.0)
    deploy_cap = base_capital * (1.0 - reserve_pct)
    headroom = min(cash, deploy_cap - deployed)
    slots_by_cash = int(headroom // per_trade) if per_trade > 0 else 0
    open_count = len(portfolio.positions)
    slots_by_ceiling = int(cfg.max_positions) - open_count
    free = max(0, min(slots_by_cash, slots_by_ceiling))
    return SlotView(
        per_trade=per_trade,
        cash=cash,
        deployed=deployed,
        base_capital=base_capital,
        reserve_pct=reserve_pct,
        deploy_cap=deploy_cap,
        open_count=open_count,
        max_positions=int(cfg.max_positions),
        slots_by_cash=max(0, slots_by_cash),
        slots_by_ceiling=max(0, slots_by_ceiling),
        free=free,
    )
