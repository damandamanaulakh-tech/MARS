"""Pre-trade risk gates. Every entry must pass `can_open()` or it's blocked."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import Config


@dataclass
class RiskState:
    cfg: Config
    starting_capital: float
    day_start_equity: float = 0.0
    kill_switch: bool = False

    def __post_init__(self) -> None:
        self.day_start_equity = self.starting_capital

    def roll_day(self, current_equity: float) -> None:
        self.day_start_equity = current_equity
        self.kill_switch = False

    def daily_pnl_pct(self, current_equity: float) -> float:
        if self.day_start_equity <= 0:
            return 0.0
        return (current_equity - self.day_start_equity) / self.day_start_equity

    def check_kill_switch(self, current_equity: float) -> bool:
        """Trip the kill switch if today's loss breaches threshold."""
        if self.daily_pnl_pct(current_equity) <= self.cfg.daily_loss_halt_pct:
            self.kill_switch = True
        return self.kill_switch


def min_trade_value(cfg: Config, target: float | None = None) -> float:
    """The smallest trade a scan may open (owner order 2026-10-04: Rs 1.5
    lakh, "no 25 k is needed"). Never above the trade's own target size, so
    a mode whose whole ticket is smaller (the legacy fixed Rs 1,25,000)
    still opens its own size instead of being refused outright."""
    floor = float(getattr(cfg, "min_position_value", 0) or 0)
    if target is not None and target > 0:
        floor = min(floor, float(target))
    return max(0.0, floor)


def can_open(
    risk: RiskState,
    cash: float,
    current_equity: float,
    open_positions: dict,
    ticker: str,
    fill_price: float,
    cfg: Config,
    notional_override: float | None = None,
    category: str | None = None,
) -> tuple[bool, str, int]:
    """Decide whether a new position can open. Returns (ok, reason, qty).

    `notional_override` is the per-stock rupee size the caller computed (½-Kelly,
    owner order 2026-07-30). It replaces the flat per-trade size but is still
    subject to EVERY gate below — cash, reserve, sector count, sector capital,
    portfolio heat, min position value. Sizing got smarter; nothing got looser.

    `category` is the trade category the entry opens under (owner order
    2026-10-04, "fix it so all 5 categories get cash"): its share of the
    deployable capital is one more ceiling on the headroom. None (the
    default, and Ocean) = no category budget, the pre-2026-10-04 gate."""
    if risk.kill_switch:
        return False, "kill_switch_active", 0

    if len(open_positions) >= cfg.max_positions:
        return False, "max_positions_reached", 0

    # Reserve cap on deployed capital. AUDIT FIX A9 (2026-08-09): measured
    # against CURRENT EQUITY, not the frozen starting_capital — the old base
    # froze the book at Rs 50L forever (couldn't deploy growth) and, worse,
    # did NOT shrink the correction/panic reserve after losses. Falls back to
    # starting_capital only when the caller passes no equity.
    base_capital = current_equity if current_equity and current_equity > 0 \
        else cfg.starting_capital
    deployed = sum(p.qty * p.entry_price for p in open_positions.values())
    deploy_cap = base_capital * (1 - cfg.cash_reserve_pct)
    if deployed >= deploy_cap:
        return False, "reserve_breached", 0

    # Per-trade size: caller-computed (½-Kelly) if supplied, else the owner's
    # fixed Rs value, else the pct fallback.
    if notional_override is not None:
        if notional_override <= 0:
            return False, "no_estimated_edge", 0
        per_stock_cap = float(notional_override)
    else:
        per_stock_cap = cfg.position_notional()
    headroom = min(per_stock_cap, deploy_cap - deployed, cash)

    # OWNER ORDER 2026-10-04: per-category cash budget (trading/
    # category_budget.py). A remainder under the slab is a FULL budget, never
    # a dust trade — the same floor the IFR throttle respects.
    # The money that may not be exceeded, whatever the rounding below does.
    hard_room = min(deploy_cap - deployed, cash)
    if category is not None:
        from . import category_budget as cb
        left = cb.room(cfg, open_positions, category, base_capital)
        if left is not None:
            if left < max(cb.min_open(cfg), fill_price):
                return False, "category_budget_full", 0
            headroom = min(headroom, left)
            hard_room = min(hard_room, left)
    if headroom <= 0:
        return False, "no_headroom", 0
    min_val = min_trade_value(cfg, per_stock_cap)

    qty = int(headroom // fill_price)
    if qty <= 0:
        return False, "qty_zero", 0

    # Sector concentration
    # OWNER ORDER 2026-10-02 (audit item 7): "Unknown" is not one real
    # sector — it is every name the sector map does not cover, ~950 of the
    # ~1,205 the scan reads since the 1,200-name universe. Counting them as
    # one sector capped ALL unmapped names at 12 held positions at once. The
    # capital cap below already exempted "Unknown" for the same reason; the
    # count cap now does too. Mapped sectors keep the 12-name cap.
    target_sector = cfg.sector_map.get(ticker, "Unknown")
    if target_sector != "Unknown":
        same_sector = sum(
            1 for p in open_positions.values()
            if cfg.sector_map.get(p.ticker, "Unknown") == target_sector
        )
        if same_sector >= cfg.max_sector_positions:
            return False, "sector_cap_reached", 0

    # P2: sector CAPITAL cap — a count cap alone lets 12 x 2.5% = 30% pile
    # into one sector. Shrink qty to fit the sector budget; reject if none.
    # "Unknown" (unmapped) tickers are exempt — they aren't one real sector.
    sector_cap_frac = getattr(cfg, "max_sector_exposure_pct", None)
    if sector_cap_frac is not None and target_sector != "Unknown":
        sector_notional = sum(
            p.qty * p.entry_price for p in open_positions.values()
            if cfg.sector_map.get(p.ticker, "Unknown") == target_sector
        )
        # AUDIT FIX A9: sector budget follows equity too (same rationale).
        budget = base_capital * sector_cap_frac - sector_notional
        if budget < fill_price:
            return False, "sector_exposure_cap", 0
        qty = min(qty, int(budget // fill_price))
        hard_room = min(hard_room, budget)

    # v7: PORTFOLIO_HEAT gate (absorbed from G07 research framework).
    # Total open risk-at-stop = sum((entry - stop_price) * qty). Block the new
    # entry if adding it would push heat above max_portfolio_heat_pct of equity.
    heat_cap_pct = getattr(cfg, "max_portfolio_heat_pct", None)
    if heat_cap_pct is not None and current_equity > 0:
        stop_frac = abs(cfg.stop_loss_pct)  # e.g. 0.05 -> 5% risk per position
        existing_heat = sum(
            p.qty * p.entry_price * stop_frac for p in open_positions.values()
        )
        new_heat = qty * fill_price * stop_frac
        if (existing_heat + new_heat) / current_equity > heat_cap_pct:
            # Try to shrink qty to fit remaining heat budget before rejecting.
            remaining = heat_cap_pct * current_equity - existing_heat
            if remaining <= 0:
                return False, "portfolio_heat_cap", 0
            qty = int(remaining / (fill_price * stop_frac))
            if qty <= 0:
                return False, "portfolio_heat_cap", 0
            if stop_frac > 0:
                hard_room = min(hard_room, remaining / stop_frac)

    # OWNER ORDER 2026-10-04: nothing below the minimum opens. A trade that
    # whole-share rounding or a trim left just short (93 x Rs 1,600 =
    # Rs 1,48,800) is rounded UP to the minimum when every money limit above
    # still has room; otherwise it is refused, never opened small.
    if min_val > 0 and qty * fill_price < min_val:
        need = int(math.ceil(min_val / fill_price))
        if need * fill_price <= hard_room + 1e-6:
            qty = need
        else:
            return False, f"below_min_value_{int(min_val)}", 0
    return True, "ok", qty
