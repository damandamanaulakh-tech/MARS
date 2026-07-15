"""Exit logic: hard stop, take profit, trailing stop, score decay, max hold."""

from __future__ import annotations

from datetime import datetime

from .config import Config


def check_exit(
    position,
    current_price: float,
    current_score: float,
    cfg: Config,
    now: datetime | None = None,
    history=None,            # accepted for forward-compat with v2 callers (ATR stops); ignored here
) -> tuple[bool, str]:
    """Returns (should_exit, reason). Reasons priority: hard_stop, target, trail, score, time."""
    entry = position.entry_price
    peak = max(position.peak_price, current_price)
    position.peak_price = peak
    ret = (current_price - entry) / entry

    if ret <= cfg.stop_loss_pct:
        return True, "STOP_LOSS"
    if ret >= cfg.take_profit_pct:
        return True, "TAKE_PROFIT"

    # Trailing only kicks in once we're in profit. v5: tighten the trailing
    # stop by 40% when score has decayed below entry_score - 15 (early
    # weakness signal) — locks in gains before full decay-exit fires.
    if peak > entry:
        trail_ret = (current_price - peak) / peak
        trail_pct = cfg.trailing_stop_pct
        if current_score < (position.entry_score - 15.0):
            trail_pct = cfg.trailing_stop_pct * 0.6  # e.g. -8% -> -4.8%
        if trail_ret <= trail_pct:
            return True, "TRAILING_STOP"

    if current_score < cfg.score_decay_exit:
        return True, "SCORE_DECAY"

    if now is not None:
        days_held = (now.date() - position.entry_date.date()).days
        if days_held >= cfg.max_hold_days:
            return True, "MAX_HOLD"

    return False, ""
