"""Exit logic: hard stop, take profit, trailing stop, score decay, max hold."""

from __future__ import annotations

from datetime import datetime

from .config import Config


def retag_positions_per_mode(portfolio, now: datetime | None = None) -> int:
    """Policy C (user-approved 2026-07-16): exits ON with per-mode stops.

    Re-snapshot every open position's stop/target from ITS OWN mode's preset
    (intraday ±2.5/1.5 … momentum +30/−12). Blank/manual tags and STALE
    intraday tags (older than same-day — intraday cannot exist overnight,
    the 15:25 square-off enforces that) get the momentum preset, the app's
    primary strategy. Every change is journaled; reruns are no-ops.
    Returns how many positions were retagged."""
    from .modes import PRESETS
    now = now or datetime.utcnow()
    changed = 0
    for pos in portfolio.positions.values():
        # FIXED 2026-07-31: an owner who set a stop/target by hand on the order
        # ticket had it silently overwritten by the mode preset on the next
        # boot. Their number wins.
        if getattr(pos, "custom_levels", False):
            continue
        tag = (getattr(pos, "mode", "") or "").strip()
        stale_intraday = (tag == "intraday"
                          and pos.entry_date.date() < now.date())
        retagged = not tag or tag not in PRESETS or stale_intraday
        if retagged:
            tag = "nifty200_momentum"
        preset = PRESETS[tag]
        new_stop = round(pos.entry_price * (1 + preset.stop_loss_pct), 2)
        old_stop = getattr(pos, "stop_price", 0.0) or 0.0
        old_target = getattr(pos, "target_price", 0.0) or 0.0
        # OWNER ORDER 2026-10-02 (audit item 6): since 2026-09-10 a target is
        # k x ATR per stock, set at entry (engine._target_for). This retag
        # runs on EVERY boot and used to overwrite it with the flat preset
        # percentage — and the app restarts many times a day. A position
        # whose category is still valid keeps the target it was opened with.
        # Only a blank, unknown or stale-intraday tag (the case this retag
        # exists for) or a missing target gets the preset target.
        if retagged or old_target <= 0:
            new_target = round(pos.entry_price * (1 + preset.take_profit_pct), 2)
        else:
            new_target = round(old_target, 2)
        if abs(new_stop - old_stop) < 0.01 and abs(new_target - old_target) < 0.01:
            continue
        pos.stop_price = new_stop
        pos.target_price = new_target
        changed += 1
        portfolio.decision_journal.append({
            "ts": now.isoformat(),
            "action": "RETAG_EXITS",
            "ticker": pos.ticker,
            "mode_used": tag,
            "old_stop": round(old_stop, 2),
            "old_target": round(old_target, 2),
            "stop_price": new_stop,
            "target_price": new_target,
            "why": ("Policy C: exits re-enabled; stop/target re-snapshotted "
                    "from the per-mode table (stale/blank tag -> momentum)."),
        })
    return changed


def check_exit(
    position,
    current_price: float,
    current_score: float,
    cfg: Config,
    now: datetime | None = None,
    history=None,            # accepted for forward-compat with v2 callers (ATR stops); ignored here
) -> tuple[bool, str]:
    """Returns (should_exit, reason). Reasons priority: hard_stop, target, trail, score, time."""
    # Buy-and-hold (P0/B1): no profit-taking exits, but a momentum book still
    # needs a RISK exit — score-decay stays live so dead leaders rotate out.
    # Stop / target / trailing / max-hold remain off.
    # AUDIT FIX A4 (2026-08-09): a position the OWNER opened by hand (BUY
    # button / limit-order ticket) must never be auto-sold by score decay —
    # owner limit fills carried entry score 0, so an ordinary composite
    # reading in the 30s dumped their deliberate pick days later. Stop,
    # target, trailing and max-hold still protect the position; only the
    # score judgement is theirs, not the engine's.
    owner_pick = "owner_pick" in (getattr(position, "tags", None) or [])
    # OWNER ORDER 2026-10-02 ("fix 1 to 3"): an Ocean dip buy is bought BECAUSE
    # it has fallen (>=30% under its 200DMA, weekly RSI < 25). Its momentum
    # and relative-strength readings are near zero by construction, so any
    # score check would sell it on the next 30-minute pass — measured: a
    # candidate clearing every Ocean rule scored 36.8 against the decay floor
    # of 40. Its entry score (100 + % below the 200DMA) is also on a scale no
    # current score can be compared with, so the trail-tighten is skipped
    # too. The stop, target, trailing stop and max-hold still protect it.
    ocean = "ocean" in (getattr(position, "tags", None) or [])
    score_exempt = owner_pick or ocean

    if getattr(cfg, "disable_exits", False):
        position.peak_price = max(position.peak_price, current_price)
        if current_score < cfg.score_decay_exit and not score_exempt:
            return True, "SCORE_DECAY"
        return False, ""

    entry = position.entry_price
    peak = max(position.peak_price, current_price)
    position.peak_price = peak
    ret = (current_price - entry) / entry

    # Prefer the stop/target snapshotted at entry (stays correct even after the
    # daily scan rotates cfg.mode). Fall back to the live cfg percentages for
    # positions opened before these were tracked (stored as 0.0).
    stop_price = getattr(position, "stop_price", 0.0) or 0.0
    target_price = getattr(position, "target_price", 0.0) or 0.0

    if stop_price > 0:
        if current_price <= stop_price:
            return True, "STOP_LOSS"
    elif ret <= cfg.stop_loss_pct:
        return True, "STOP_LOSS"

    if target_price > 0:
        if current_price >= target_price:
            return True, "TAKE_PROFIT"
    elif ret >= cfg.take_profit_pct:
        return True, "TAKE_PROFIT"

    # Trailing only kicks in once we're in profit. v5: tighten the trailing
    # stop by 40% when score has decayed below entry_score - 15 (early
    # weakness signal) — locks in gains before full decay-exit fires.
    if peak > entry:
        trail_ret = (current_price - peak) / peak
        trail_pct = cfg.trailing_stop_pct
        if not ocean and current_score < (position.entry_score - 15.0):
            trail_pct = cfg.trailing_stop_pct * 0.6  # e.g. -8% -> -4.8%
        if trail_ret <= trail_pct:
            return True, "TRAILING_STOP"

    if current_score < cfg.score_decay_exit and not score_exempt:
        return True, "SCORE_DECAY"

    if now is not None:
        days_held = (now.date() - position.entry_date.date()).days
        if days_held >= cfg.max_hold_days:
            return True, "MAX_HOLD"

    return False, ""


def trail_reach(position, cfg: Config) -> dict:
    """AUDIT F-18 (2026-10-02): can THIS position's trailing stop ever fire?

    Uses the position's own snapshotted stop and target (the k x ATR target
    since 2026-09-10, an owner's custom level, or a danger-tightened stop)
    and the trail of the config handed in — pass the position's own mode
    config. Report only; it changes no exit. Positions with no snapshot fall
    back to the config's percentages, exactly as check_exit does."""
    entry = float(position.entry_price or 0.0)
    sp = float(getattr(position, "stop_price", 0.0) or 0.0)
    tp = float(getattr(position, "target_price", 0.0) or 0.0)
    stop = (sp / entry - 1.0) if (entry > 0 and sp > 0) else float(cfg.stop_loss_pct)
    target = (tp / entry - 1.0) if (entry > 0 and tp > 0) else float(cfg.take_profit_pct)
    live, need = cfg.trail_is_reachable(stop=stop, target=target)
    return {"ticker": position.ticker, "live": bool(live),
            "stop_pct": round(stop * 100, 2), "target_pct": round(target * 100, 2),
            "trail_pct": round(float(cfg.trailing_stop_pct) * 100, 1),
            "need_pct": round(need * 100, 2)}
