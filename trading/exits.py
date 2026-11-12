"""Exit logic: hard stop, take profit, trailing stop, score decay, max hold."""

from __future__ import annotations

from datetime import datetime

from .config import Config
from . import decision_evidence as de


def retag_positions_per_mode(portfolio, now: datetime | None = None) -> int:
    """Policy C (user-approved 2026-07-16): exits ON with per-mode stops.

    Re-snapshot every open position's stop/target from ITS OWN mode's preset
    (intraday ±2.5/1.5 … momentum +30/−12). Blank/unknown tags get the
    momentum preset. An overdue intraday holding keeps its own protection:
    its saved square-off deadline remains unfinished work after a restart.
    Every change is journaled; reruns are no-ops.
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
        retagged = not tag or tag not in PRESETS
        if retagged:
            tag = "nifty200_momentum"
        preset = PRESETS[tag]
        old_stop = getattr(pos, "stop_price", 0.0) or 0.0
        old_target = getattr(pos, "target_price", 0.0) or 0.0
        # OWNER ORDER 2026-10-05 ("Go for A"): a momentum buy's stop is set
        # at entry as the wider of the category stop and 2 x its own ATR
        # (engine._stop_for). This retag runs on EVERY boot and used to
        # re-snapshot the flat preset stop over it — the exact fault audit
        # item S-2 fixed for the TARGET on 2026-10-02. Same rule now for the
        # stop: a position whose category is still valid keeps the stop it
        # was opened with; only a blank/unknown tag, or a
        # missing stop, gets the preset. A Settings change to a stop applies
        # to NEW trades.
        if retagged or old_stop <= 0:
            new_stop = round(pos.entry_price * (1 + preset.stop_loss_pct), 2)
        else:
            new_stop = round(old_stop, 2)
        # OWNER ORDER 2026-10-02 (audit item 6): since 2026-09-10 a target is
        # k x ATR per stock, set at entry (engine._target_for). This retag
        # runs on EVERY boot and used to overwrite it with the flat preset
        # percentage — and the app restarts many times a day. A position
        # whose category is still valid keeps the target it was opened with.
        # Only a blank or unknown tag (the case this retag
        # exists for) or a missing target gets the preset target.
        # Item 19 (owner order 2026-10-21): a valuation target (set at entry
        # or by the re-target) is kept even on a blank legacy tag — the
        # preset target would undo it on every boot.
        val_src = (getattr(pos, "val_target", None) or {}).get("source")
        keep_val = val_src in ("valuation", "retarget") and old_target > 0
        if (retagged and not keep_val) or old_target <= 0:
            new_target = round(pos.entry_price * (1 + preset.take_profit_pct), 2)
        else:
            new_target = round(old_target, 2)
        if abs(new_stop - old_stop) < 0.01 and abs(new_target - old_target) < 0.01:
            continue
        before = de.protection(pos)
        pos.stop_price = new_stop
        pos.target_price = new_target
        de.amendment(pos, before, "RETAG_EXITS", now, portfolio=portfolio)
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
                    "from the per-mode table (unknown/blank tag -> momentum)."),
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

    ctx = de.current()
    if ctx is not None:
        ctx["exit_rules"] = {"disable_exits": cfg.disable_exits,
            "score_exempt": score_exempt, "owner_pick": owner_pick, "ocean": ocean,
            "trailing_stop_pct": cfg.trailing_stop_pct, "score_decay_exit": cfg.score_decay_exit,
            "max_hold_days": cfg.max_hold_days}
    score_available = ctx is None or ctx.get("score_available", True)
    def score_rule():
        hit = score_available and current_score < cfg.score_decay_exit and not score_exempt
        de.record("score_decay", current_score, cfg.score_decay_exit, passed=hit,
                  state="skipped" if score_exempt else "unavailable" if not score_available else None,
                  reason="Owner/Ocean exemption" if score_exempt else "Entry-score fallback; current score unavailable" if not score_available else "Exit when score is below floor",
                  role="exit_trigger")
        return hit
    if getattr(cfg, "disable_exits", False):
        position.peak_price = max(position.peak_price, current_price)
        for rule in ("stop", "target", "trailing", "max_hold"):
            de.record(rule, state="skipped", reason="Buy-and-hold policy disables this exit", role="exit_trigger")
        if score_rule():
            return True, "SCORE_DECAY"
        return False, ""

    entry = position.entry_price
    peak = max(position.peak_price, current_price)
    position.peak_price = peak
    stop = (getattr(position, "stop_price", 0.0) or 0.0)
    target = (getattr(position, "target_price", 0.0) or 0.0)
    ret = (current_price - entry) / entry if entry else 0.0
    stop_hit = current_price <= stop if stop > 0 else ret <= cfg.stop_loss_pct
    target_hit = current_price >= target if target > 0 else ret >= cfg.take_profit_pct
    stop = stop if stop > 0 else entry * (1 + cfg.stop_loss_pct)
    target = target if target > 0 else entry * (1 + cfg.take_profit_pct)
    de.record("stop", current_price, stop, passed=stop_hit,
              reason="Exit at or below stop", role="exit_trigger")
    if stop_hit:
        return True, "STOP_LOSS"
    de.record("target", current_price, target, passed=target_hit,
              reason="Exit at or above target", role="exit_trigger")
    if target_hit:
        return True, "TAKE_PROFIT"
    if peak > entry:
        trail_ret = (current_price - peak) / peak
        trail_pct = cfg.trailing_stop_pct
        tightened = score_available and not ocean and current_score < (position.entry_score - 15.0)
        if tightened:
            trail_pct *= 0.6
        de.record("trailing", {"return_from_peak": trail_ret, "peak": peak,
            "score": current_score, "score_at_entry": position.entry_score,
            "tightened": tightened, "price_level": peak * (1 + trail_pct)},
            {"fraction": trail_pct, "tighten_score_below": position.entry_score - 15.0},
            passed=trail_ret <= trail_pct, role="exit_trigger")
        if trail_ret <= trail_pct:
            return True, "TRAILING_STOP"
    else:
        de.record("trailing", peak, entry, state="skipped", reason="Peak has not exceeded entry", role="exit_trigger")
    if score_rule():
        return True, "SCORE_DECAY"
    if now is not None:
        days_held = (now.date() - position.entry_date.date()).days
        de.record("max_hold", days_held, cfg.max_hold_days, passed=days_held >= cfg.max_hold_days, role="exit_trigger")
        if days_held >= cfg.max_hold_days:
            return True, "MAX_HOLD"
    else:
        de.record("max_hold", state="unavailable", reason="No evaluation clock", role="exit_trigger")
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
