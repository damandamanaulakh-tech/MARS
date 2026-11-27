"""Pre-trade risk gates. Every entry must pass `can_open()` or it's blocked."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import Config
from . import decision_evidence as de


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


def blocked_regardless_of_cash(
    cfg: Config,
    risk: RiskState,
    open_positions: dict,
    ticker: str,
    fill_price: float,
    notional_override: Optional[float] = None,
    current_equity: float = 0.0,
    freeing: tuple = (),
) -> Optional[str]:
    """Would this pick be refused however much cash were freed for it?

    XR-052 (2026-11-27). Rotation sells a near-target winner to fund a fresh
    pick, and it decides BEFORE `can_open` ever runs — the two-pass order the
    2026-10-02 S-4 repair deliberately created, because the freed cash has to
    exist when `can_open` counts it. So on some days the winner was sold, a
    round trip of costs and slippage was booked, the last stretch to target was
    given up, and the pick was then refused for a reason cash cannot fix.
    Reproduced three ways: a kill-switch day (the day the book has already lost
    2%, and the day the engine would sell a winner for nothing), a full sector,
    and a ticket under the minimum.

    DO NOT fix that by reordering: `trading/tests/test_rotation_exec.py`
    deliberately pins rotation BEFORE `can_open`, and moving it would break the
    feature. This is the other half — a read-only test of the gates that no
    amount of freed cash changes, so rotation can stand down instead.

    `freeing` are the tickers rotation is willing to sell. The sector gates are
    judged with those excluded, because selling a same-sector holding DOES free
    a sector slot — so without this the helper would stand rotation down in the
    one case where it genuinely helps.

    Deliberately NOT tested here, each with its reason:
      * `max_positions_reached` — the rotation sale itself frees the slot it
        needs. Measured: 6 held against a ceiling of 6, and after the sale
        5 < 6 passes.
      * `portfolio_heat_cap` — arithmetically unreachable under live settings.
        `max_portfolio_heat_pct` is 0.25 while the widest stop any entry can
        carry is ATR_STOP_CAP 0.15, and deployed capital is bounded by the
        deploy cap. The worst case that could be built — eight wide-stop
        holdings plus a winner whose stop has trailed above entry, so selling
        it frees no heat budget — reached 12.6% of a 25% cap.
      * `category_budget_full` — `_rotation_exits` already does its own share
        arithmetic (`cb.room` / `cb.limit` / `cb.min_open`), so it is
        pre-empted there.
      * cash, headroom, quantity, costs and the minimum round-up — those are
        exactly what rotation exists to fix.

    Returns the reason, or None when the pick could open given enough cash.
    """
    if risk.kill_switch:
        return "kill_switch_active"
    if notional_override is not None and notional_override <= 0:
        return "no_estimated_edge"
    if not fill_price or fill_price <= 0:
        return "invalid_fill_price"

    per_stock_cap = (float(notional_override) if notional_override is not None
                     else cfg.position_notional())
    if per_stock_cap < fill_price:
        # One whole share costs more than the whole ticket this pick may use.
        return "qty_zero"

    base_capital = (current_equity if current_equity and current_equity > 0
                    else cfg.starting_capital)
    dropped = set(freeing or ())
    staying = {t: p for t, p in (open_positions or {}).items() if t not in dropped}

    target_sector = cfg.sector_map.get(ticker, "Unknown")
    if target_sector != "Unknown":
        same_sector = sum(1 for p in staying.values()
                          if cfg.sector_map.get(p.ticker, "Unknown") == target_sector)
        if same_sector >= cfg.max_sector_positions:
            return "sector_cap_reached"
        frac = getattr(cfg, "max_sector_exposure_pct", None)
        if frac is not None:
            notional = sum(p.qty * p.entry_price for p in staying.values()
                           if cfg.sector_map.get(p.ticker, "Unknown") == target_sector)
            if base_capital * frac - notional < fill_price:
                return "sector_exposure_cap"
    return None


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
    *, when=None, include_entry_fees: bool = True,
    proposed_stop_price: float | None = None,
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
    # Compute the stop before sizing and reuse it at booking. A wider ATR
    # stop must never consume only the category floor's risk budget.
    if proposed_stop_price is not None:
        try:
            proposed_stop_price = float(proposed_stop_price)
            valid_stop = (math.isfinite(proposed_stop_price) and
                          0 < proposed_stop_price < fill_price)
        except (ValueError, TypeError):
            valid_stop = False
        de.record("initial_stop", proposed_stop_price, {"above": 0, "below": fill_price},
                  passed=valid_stop, role="sizing")
        if not valid_stop:
            return False, "invalid_proposed_stop", 0
    de.record("kill_switch", risk.kill_switch, False, passed=not risk.kill_switch)
    if risk.kill_switch:
        return False, "kill_switch_active", 0

    de.record("slots", len(open_positions), cfg.max_positions, passed=len(open_positions) < cfg.max_positions)
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
    de.record("reserve", deployed, deploy_cap, passed=deployed < deploy_cap,
              reason=f"Current equity base {base_capital}; reserve fraction {cfg.cash_reserve_pct}")
    if deployed >= deploy_cap:
        return False, "reserve_breached", 0

    # Per-trade size: caller-computed (½-Kelly) if supplied, else the owner's
    # fixed Rs value, else the pct fallback.
    de.record("estimated_edge", notional_override, 0,
              state="skipped" if notional_override is None else None,
              passed=notional_override is not None and notional_override > 0,
              reason="Fixed size" if notional_override is None else "Caller sizing notional")
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
        de.record("category_budget", left, max(cb.min_open(cfg), fill_price),
                  state="skipped" if left is None else None,
                  passed=left is not None and left >= max(cb.min_open(cfg), fill_price),
                  reason=f"Category {category}; disabled or exempt when no share")
        if left is not None:
            if left < max(cb.min_open(cfg), fill_price):
                return False, "category_budget_full", 0
            headroom = min(headroom, left)
            hard_room = min(hard_room, left)
    de.record("cash", {"cash": cash, "headroom": headroom, "hard_room": hard_room}, 0, passed=headroom > 0)
    if headroom <= 0:
        return False, "no_headroom", 0
    min_val = min_trade_value(cfg, per_stock_cap)

    qty = int(headroom // fill_price)
    de.record("quantity", qty, 1, passed=qty >= 1)
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
        de.record("sector_count", same_sector, cfg.max_sector_positions, passed=same_sector < cfg.max_sector_positions, reason=target_sector)
        if same_sector >= cfg.max_sector_positions:
            return False, "sector_cap_reached", 0

    else:
        de.record("sector_count", state="unavailable", reason="Sector identity unknown; existing policy exempts unmapped names")

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
        de.record("sector_cap", budget, fill_price, passed=budget >= fill_price, reason=f"Sector cap fraction {sector_cap_frac}; existing notional {sector_notional}")
        if budget < fill_price:
            return False, "sector_exposure_cap", 0
        qty = min(qty, int(budget // fill_price))
        hard_room = min(hard_room, budget)

    else:
        de.record("sector_cap", state="unavailable" if target_sector == "Unknown" else "skipped", reason="Unknown sector or disabled capital cap")

    # v7: PORTFOLIO_HEAT gate (absorbed from G07 research framework).
    # Total open risk-at-stop = sum((entry - stop_price) * qty). Block the new
    # entry if adding it would push heat above max_portfolio_heat_pct of equity.
    heat_cap_pct = getattr(cfg, "max_portfolio_heat_pct", None)
    if heat_cap_pct is not None and current_equity > 0:
        stop_frac = abs(cfg.stop_loss_pct)  # e.g. 0.05 -> 5% risk per position

        # 2026-10-05 ("Go for A"): a held position's risk at stop is read off
        # ITS OWN stop (an ATR-widened momentum stop, an owner's custom stop,
        # a danger-tightened one) — the formula this gate documents. The flat
        # category percentage is only the fallback for a position with no
        # snapshot. A stop already above entry carries no risk at stop.
        def _risk_frac(p) -> float:
            sp = float(getattr(p, "stop_price", 0.0) or 0.0)
            ep = float(getattr(p, "entry_price", 0.0) or 0.0)
            if sp > 0 and ep > 0:
                return max(0.0, (ep - sp) / ep)
            return stop_frac
        existing_heat = sum(
            p.qty * p.entry_price * _risk_frac(p) for p in open_positions.values()
        )
        unit_risk = (fill_price - proposed_stop_price if proposed_stop_price is not None
                     else fill_price * stop_frac)
        remaining = heat_cap_pct * current_equity - existing_heat
        # Also bound the final minimum-ticket round-up, even when the first
        # integer quantity is just under the cap. This is the existing gross
        # price-at-stop policy; fees and gap risk are not called zero.
        if unit_risk > 0:
            hard_room = min(hard_room, max(0.0, remaining) / unit_risk * fill_price)
        new_heat = qty * unit_risk
        de.record("portfolio_heat", {"existing": existing_heat, "proposed": new_heat,
            "equity": current_equity, "initial_stop": proposed_stop_price,
            "unit_price_risk": unit_risk, "costs_included": False}, heat_cap_pct,
            passed=True, reason="Exact proposed stop; category fallback only for legacy callers. Gross stop risk excludes fees, slippage and gaps.")
        if (existing_heat + new_heat) / current_equity > heat_cap_pct:
            # Try to shrink qty to fit remaining heat budget before rejecting.
            if remaining <= 0:
                de.record("portfolio_heat", remaining, 0, passed=False, reason="No remaining risk-at-stop budget")
                return False, "portfolio_heat_cap", 0
            qty = int(remaining / unit_risk) if unit_risk > 0 else qty
            if qty <= 0:
                de.record("portfolio_heat", qty, 1, passed=False, reason="Heat budget cannot fund a whole share")
                return False, "portfolio_heat_cap", 0
            de.record("portfolio_heat", {"existing": existing_heat, "accepted_qty": qty, "accepted_heat": qty * unit_risk}, heat_cap_pct * current_equity, passed=True, reason="Quantity reduced to exact stop risk budget")

    else:
        de.record("portfolio_heat", state="skipped", reason="Heat cap disabled or equity unavailable")

    from . import fees
    from datetime import datetime
    fee_when = when or datetime.utcnow()
    if include_entry_fees:
        try:
            fees.check_instrument(ticker, cfg.sector_map.get(ticker, ""))
            qty = fees.affordable_qty(qty, fill_price, cash, cfg.mode, fee_when, ticker)
        except ValueError as exc:
            de.record("trading_costs", state="unavailable", reason=str(exc))
            return False, str(exc), 0
        if qty <= 0:
            return False, "costs_cash_short", 0

    # OWNER ORDER 2026-10-04: nothing below the minimum opens. A trade that
    # whole-share rounding or a trim left just short (93 x Rs 1,600 =
    # Rs 1,48,800) is rounded UP to the minimum when every money limit above
    # still has room; otherwise it is refused, never opened small.
    if min_val > 0 and qty * fill_price < min_val:
        need = int(math.ceil(min_val / fill_price))
        if (need * fill_price <= hard_room + 1e-6 and
                (not include_entry_fees or fees.entry_total(need, fill_price, cfg.mode, fee_when, ticker) <= cash + 1e-8)):
            qty = need
        else:
            de.record("minimum_value", qty * fill_price, min_val, passed=False, reason="Whole-share rounding cannot fit hard room")
            return False, f"below_min_value_{int(min_val)}", 0
    de.record("minimum_value", qty * fill_price, min_val, passed=True)
    if include_entry_fees:
        de.record("trading_costs", fees.entry_total(qty, fill_price, cfg.mode, fee_when, ticker), cash, passed=True, reason="Notional plus dated buy charges fits cash")
    return True, "ok", qty
