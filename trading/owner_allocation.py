"""Owner entries share their strategy's funds (owner decision 2026-11-03).

These checks concern allocation only. They do not silently opt a manual
ticket into the scanner's signal gates or remove its source/exit policy.
Callers hold MODE_LOCK from the final check through the entry mutation.
"""
from __future__ import annotations

import math

from . import category_budget as cb


def segment(value: str | None) -> str:
    """Missing legacy ticket/order mode was Momentum; explicit unknown fails."""
    mode = (value or "").strip() or cb.MOMENTUM
    if mode not in cb.SHARE_FIELDS:
        raise ValueError("bad_segment")
    return mode


def available(portfolio, cfg, mode: str) -> tuple[float, float | None]:
    """Cash and segment headroom at entry marks, matching the Risk page.

    The budget counts entry notional, as the engine does. Trade charges are
    handled separately: buy fees at entry, sell fees at exit (SEQ-05).
    Callers check fee-inclusive cash before booking. No balance is moved here.
    """
    mode = segment(mode)
    marks = {p.ticker: p.entry_price for p in portfolio.positions.values()}
    equity = float(portfolio.equity(marks))
    left = cb.room(cfg, portfolio.positions, mode, equity)
    cash = float(portfolio.cash)
    if not math.isfinite(equity) or not math.isfinite(cash) or (
            left is not None and not math.isfinite(left)):
        return 0.0, 0.0
    return max(0.0, min(cash, left) if left is not None else cash), left


def refusal(portfolio, cfg, mode: str, cost: float) -> str | None:
    if not math.isfinite(cost) or cost <= 0:
        return "bad_ticket"
    _, left = available(portfolio, cfg, mode)
    if left is not None and cost > left + 1e-8:
        return "category_budget_full"
    if not math.isfinite(float(portfolio.cash)) or cost > portfolio.cash:
        return "insufficient_cash"
    return None
