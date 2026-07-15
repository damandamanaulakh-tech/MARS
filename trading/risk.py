"""Pre-trade risk gates. Every entry must pass `can_open()` or it's blocked."""

from __future__ import annotations

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


def can_open(
    risk: RiskState,
    cash: float,
    current_equity: float,
    open_positions: dict,
    ticker: str,
    fill_price: float,
    cfg: Config,
) -> tuple[bool, str, int]:
    """Decide whether a new position can open. Returns (ok, reason, qty)."""
    if risk.kill_switch:
        return False, "kill_switch_active", 0

    if len(open_positions) >= cfg.max_positions:
        return False, "max_positions_reached", 0

    # 20% reserve: deployed capital cap = 80% of starting capital.
    deployed = sum(p.qty * p.entry_price for p in open_positions.values())
    deploy_cap = cfg.starting_capital * (1 - cfg.cash_reserve_pct)
    if deployed >= deploy_cap:
        return False, "reserve_breached", 0

    # 10% per stock: position notional <= 10% of starting capital.
    per_stock_cap = cfg.starting_capital * cfg.max_position_pct
    headroom = min(per_stock_cap, deploy_cap - deployed, cash)
    if headroom <= 0:
        return False, "no_headroom", 0

    qty = int(headroom // fill_price)
    if qty <= 0:
        return False, "qty_zero", 0

    # Sector concentration
    target_sector = cfg.sector_map.get(ticker, "Unknown")
    same_sector = sum(
        1 for p in open_positions.values()
        if cfg.sector_map.get(p.ticker, "Unknown") == target_sector
    )
    if same_sector >= cfg.max_sector_positions:
        return False, "sector_cap_reached", 0

    return True, "ok", qty
