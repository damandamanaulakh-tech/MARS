"""Daily auto-scan scheduler (APScheduler).

Two job tracks:
  1. Intraday tick — every 5 min, 9:15-15:25 IST (only when mode=intraday)
  2. Daily multi-mode scan — once at 09:20 IST: runs scan in intraday,
     short, swing, and positional in sequence. Opens whatever passes the
     gates in each mode. Target 60-70% weekly capital deployment.

Disabled by default. Set DAILY_SCHEDULER=1 in Render env to switch on.
The legacy INTRADAY_SCHEDULER=1 toggle is still honoured.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

log = logging.getLogger(__name__)

_SCHED = None


def start_if_enabled() -> None:
    """Called from FastAPI startup. Idempotent."""
    global _SCHED
    intraday = os.environ.get("INTRADAY_SCHEDULER") == "1"
    daily = os.environ.get("DAILY_SCHEDULER") == "1"
    if not (intraday or daily):
        log.info("Schedulers disabled (set DAILY_SCHEDULER=1 and/or INTRADAY_SCHEDULER=1)")
        return
    if _SCHED is not None:
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from pytz import timezone
    except ImportError:
        log.warning("apscheduler/pytz missing — scheduler skipped")
        return

    ist = timezone("Asia/Kolkata")
    _SCHED = BackgroundScheduler(timezone=ist)

    if intraday:
        _SCHED.add_job(
            _intraday_tick, "cron",
            day_of_week="mon-fri", hour="9-15", minute="*/5",
            id="intraday_tick",
        )
        _SCHED.add_job(
            _square_off_pass, "cron",
            day_of_week="mon-fri", hour=15, minute=25,
            id="square_off",
        )

    if daily:
        # Daily morning scan across all modes (one pass each).
        _SCHED.add_job(
            _daily_multi_mode_scan, "cron",
            day_of_week="mon-fri", hour=9, minute=20,
            id="daily_multi_mode_scan",
        )

    _SCHED.start()
    log.info("Schedulers started (intraday=%s, daily=%s)", intraday, daily)


def _intraday_tick() -> None:
    """Run scan + refresh if mode is intraday. Logs anything material."""
    from . import state  # local import to avoid circular at module load
    cfg = state.cfg()
    if cfg.mode != "intraday":
        return
    eng = state.engine()
    log.info("[%s] Intraday tick — scanning + refreshing",
             datetime.now().strftime("%H:%M:%S"))
    try:
        eng.refresh()
        eng.scan()
    except Exception as e:
        log.exception("Intraday tick failed: %s", e)


def _square_off_pass() -> None:
    """At 15:25 IST: force-close all intraday positions."""
    from . import state
    cfg = state.cfg()
    if cfg.mode != "intraday":
        return
    portfolio = state.portfolio()
    if not portfolio.positions:
        log.info("[15:25] Square-off: no positions to close")
        return
    log.info("[15:25] Square-off pass: closing %d positions", len(portfolio.positions))
    try:
        state.engine().refresh()
    except Exception as e:
        log.exception("Square-off pass failed: %s", e)


# Daily mode rotation. Intraday runs first (entered + squared-off same day),
# then short, swing, positional fire scans in sequence to fill the book.
_DAILY_MODE_ORDER = ["intraday", "short", "swing", "positional"]


def _daily_multi_mode_scan() -> None:
    """Once a day at 09:20 IST: scan in each mode and open whatever passes.

    Target: 60-70% capital deployed across the week. Each scan is fully
    gated by the engine's risk controls, so this only opens picks that
    pass score + timing + correlation + drawdown + liquidity + earnings
    blackouts. Aborted modes (e.g. blacklisted regime) are skipped silently.
    """
    from . import state
    eng = state.engine()
    cfg = state.cfg()
    original_mode = getattr(cfg, "mode", "swing")
    log.info("[09:20] Daily multi-mode scan starting (target 60-70%% deployed)")
    summary = []
    for m in _DAILY_MODE_ORDER:
        try:
            cfg.mode = m
            cfg.apply_mode_preset()
            res = eng.scan()
            summary.append(f"{m}={len(res.opened)} opened/{len(res.skipped)} skipped")
        except Exception as e:
            log.exception("Daily scan for mode=%s failed: %s", m, e)
            summary.append(f"{m}=error")
    # Restore the active mode the user selected in Settings.
    cfg.mode = original_mode
    cfg.apply_mode_preset()
    log.info("[09:20] Daily multi-mode scan complete: %s", " · ".join(summary))
