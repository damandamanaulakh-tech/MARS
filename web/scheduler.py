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
import threading
from datetime import datetime

log = logging.getLogger(__name__)

_SCHED = None


def start_if_enabled() -> None:
    """Called from FastAPI startup. Idempotent.

    Daily auto-trading is ON by default (paper-only). Set DAILY_SCHEDULER=0
    in Render env to disable. Intraday tick remains opt-in.
    """
    global _SCHED
    intraday = os.environ.get("INTRADAY_SCHEDULER") == "1"
    daily = os.environ.get("DAILY_SCHEDULER", "1") != "0"
    if not (intraday or daily):
        log.info("Schedulers disabled (DAILY_SCHEDULER=0 and INTRADAY_SCHEDULER unset)")
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
    # AUDIT E (2026-08-09): APScheduler's default misfire grace is ~1s — a GC
    # pause on the 512MB box at 15:25 could silently DISCARD the square-off
    # job. 5-minute grace + coalesce, so a late wake still runs once.
    _SCHED = BackgroundScheduler(
        timezone=ist,
        job_defaults={"misfire_grace_time": 300, "coalesce": True})

    if intraday:
        _SCHED.add_job(
            _intraday_tick, "cron",
            day_of_week="mon-fri", hour="9-15", minute="*/5",
            id="intraday_tick",
        )
    if daily or intraday:
        # 15:25 IST intraday square-off. Registered on the ALWAYS-ON daily
        # track (was intraday-toggle-only, which is off on Render — one of
        # two reasons intraday positions survived overnight).
        _SCHED.add_job(
            _square_off_pass, "cron",
            day_of_week="mon-fri", hour=15, minute=25,
            id="square_off",
        )

    if daily:
        # 08:50 IST: refresh the NSE universe master from Upstox's public
        # instrument file BEFORE the 09:20 scan — new listings enter the
        # rotation same day, delisted names drop (user ask 2026-07-21).
        _SCHED.add_job(
            _universe_refresh_job, "cron",
            day_of_week="mon-fri", hour=8, minute=50,
            id="universe_refresh",
        )
        # 09:05 IST: filings feed v1 — company announcements for held +
        # watchlist names (owner-approved 2026-07-26). Before the scan so
        # the desk shows the morning's news next to the picks.
        _SCHED.add_job(
            _filings_job, "cron",
            day_of_week="mon-fri", hour=9, minute=5,
            id="filings_refresh",
        )
        # Daily morning scan across all modes (one pass each).
        _SCHED.add_job(
            _daily_multi_mode_scan, "cron",
            day_of_week="mon-fri", hour=9, minute=20,
            id="daily_multi_mode_scan",
        )
        # Afternoon top-up: if deployment below 60%, scan again to fill book.
        _SCHED.add_job(
            _deployment_topup_scan, "cron",
            day_of_week="mon-fri", hour=14, minute=30,
            id="deployment_topup_scan",
        )
        # End-of-day refresh: mark positions + fire exit rules + journal holds.
        # Weekly data freshness (owner order 2026-07-27): Friday 16:30 IST,
        # market closed, morning login token still alive. Tops up the stored
        # OHLCV research store — merge, never replace.
        _SCHED.add_job(
            _weekly_history_topup,
            "cron",
            day_of_week="fri", hour=16, minute=30,
            id="weekly_history_topup",
            replace_existing=True,
        )
        _SCHED.add_job(
            _eod_refresh, "cron",
            day_of_week="mon-fri", hour=15, minute=35,
            id="eod_refresh",
        )

    if daily and os.environ.get("SB_DISABLE_CATCHUP") != "1":
        # Deploy-restart healing: if the process boots after a missed 09:20
        # (deploys kill in-flight jobs), run the day's scan once, 2 min in.
        from datetime import datetime as _dt, timedelta as _td
        _SCHED.add_job(lambda: catchup_scan_if_missed("startup"), "date",
                       run_date=_dt.now() + _td(seconds=120),
                       id="startup_catchup")

    _SCHED.start()
    log.info("Schedulers started (intraday=%s, daily=%s)", intraday, daily)


def catchup_scan_if_missed(reason: str = "catchup") -> bool:
    """If today's multi-mode scan never completed (dead Upstox token at
    09:20, deploy restart mid-run, crash), run it now in a background
    thread. Fired at startup and on every Upstox connect. Trading days
    only; no-op when today's scan already ran."""
    import os
    import threading
    from datetime import datetime as _dt

    from trading.calendars import is_trading_day

    if (os.environ.get("DAILY_SCHEDULER", "1") == "0"
            or os.environ.get("SB_DISABLE_CATCHUP") == "1"
            or not is_trading_day()):
        return False
    from . import state
    p = state.portfolio()
    last = (p.last_scan or "")[:10]
    today = _dt.utcnow().date().isoformat()
    if last >= today:
        return False
    log.info("[catchup:%s] last_scan=%s < today — running missed multi-mode scan",
             reason, last or "never")
    p.decision_journal.append({
        "ts": _dt.utcnow().isoformat(), "action": "SCAN_CATCHUP",
        "why": (f"Today's 09:20 scan missing (last scan: {last or 'never'}); "
                f"triggered by {reason}. Running it now."),
    })

    def _catchup_run():
        # A boot after 08:50 also missed the universe refresh — do it first
        # so the catch-up scan sees today's market, not a stale list.
        from trading.universe_refresh import refreshed_today
        if not refreshed_today():
            _universe_refresh_job()
        _daily_multi_mode_scan()

    threading.Thread(target=_catchup_run, daemon=True).start()
    return True


def _universe_refresh_job() -> None:
    """08:50 IST: rebuild data/upstox_nse_equities.csv from Upstox's public
    NSE instrument master. Fail-safe: any error keeps the old file and goes
    into the journal — a stale universe beats an empty one, and silent
    failures are forbidden."""
    from datetime import datetime as _dt

    from trading.calendars import is_trading_day
    if not is_trading_day():
        return
    from . import state
    p = state.portfolio()
    try:
        from trading.universe_refresh import refresh_universe
        s = refresh_universe()
        entry = {
            "ts": _dt.utcnow().isoformat(), "action": "UNIVERSE_REFRESH",
            "why": (f"Fresh NSE list from Upstox: {s['total']} stocks "
                    f"(+{s['added']} new, -{s['removed']} delisted/moved)."),
            "total": s["total"], "added": s["added_names"],
            "removed": s["removed_names"],
        }
        log.info("[08:50] %s", entry["why"])
    except Exception as e:
        entry = {
            "ts": _dt.utcnow().isoformat(), "action": "UNIVERSE_REFRESH_FAILED",
            "err": f"{type(e).__name__}: {str(e)[:120]}",
            "why": "Master download/parse failed — yesterday's list stays in use.",
        }
        log.exception("[08:50] Universe refresh failed: %s", e)
    try:
        p.decision_journal.append(entry)
        p.save()
        _push_phone_alerts("08:50")
    except Exception:
        pass
    # N1 series-watch (owner-approved 2026-07-26): refresh the BE/T2T
    # shadow list with real 6m returns. Slow (per-name fetch) — runs after
    # the main list so a failure here never blocks the universe.
    try:
        from trading.universe_refresh import refresh_series_watch
        sw = refresh_series_watch()
        p.decision_journal.append({
            "ts": _dt.utcnow().isoformat(), "action": "SERIES_WATCH_REFRESH",
            "why": (f"Shadow list rebuilt: {sw['total']} BE/T2T names, "
                    f"{sw['scored']} with live 6m returns."),
        })
        p.save()
    except Exception as e:
        try:
            p.decision_journal.append({
                "ts": _dt.utcnow().isoformat(), "action": "SERIES_WATCH_FAILED",
                "err": f"{type(e).__name__}: {str(e)[:120]}",
                "why": "Shadow list refresh failed — yesterday's stays.",
            })
            p.save()
        except Exception:
            pass


def _filings_job() -> None:
    """Filings feed v1: BSE announcements matched to held + watchlist.
    Fail-visible; an empty feed day journals 0 matches, never invents."""
    import csv as _csv
    import os as _os
    from datetime import datetime as _dt

    from trading.calendars import is_trading_day
    if not is_trading_day():
        return
    from . import state
    p = state.portfolio()
    try:
        from trading.filings import build_watch_map, refresh_filings
        held = set(p.positions.keys())
        picks = {pk.get("ticker") for pk in (p.last_scan_picks or [])}
        tickers = held | {t for t in picks if t}
        uni = _os.path.join(_os.path.dirname(__file__), "..", "data",
                            "upstox_nse_equities.csv")
        with open(uni, newline="") as fh:
            rows = list(_csv.DictReader(fh))
        wm = build_watch_map(rows, tickers)
        s = refresh_filings(p, wm)
        p.decision_journal.append({
            "ts": _dt.utcnow().isoformat(), "action": "FILINGS_REFRESH",
            "why": (f"Exchange announcements: {s['scanned']} scanned, "
                    f"{s['matched']} matched held/watch, {s['new']} new."),
        })
        p.save()
    except Exception as e:
        try:
            p.decision_journal.append({
                "ts": _dt.utcnow().isoformat(), "action": "FILINGS_FAILED",
                "err": f"{type(e).__name__}: {str(e)[:120]}",
                "why": "Filings fetch failed — no fake news, list unchanged.",
            })
            p.save()
        except Exception:
            pass
    _push_phone_alerts("09:05")


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
    _check_pending_orders("intraday-tick")


def _square_off_pass() -> None:
    """At 15:25 IST: force-close all INTRADAY-mode positions.

    BUG FIX: previously gated on cfg.mode == "intraday" (the app's GLOBAL
    mode) and delegated to refresh(), which honours disable_exits — so
    intraday picks opened by the 09:20 rotation survived overnight while
    the app ran in momentum mode. Now closes by position tag, always.
    """
    from trading.calendars import is_trading_day
    if not is_trading_day():
        return
    from . import state
    try:
        closed = state.engine().square_off_intraday()
        if closed:
            log.info("[15:25] Square-off closed %d intraday positions: %s",
                     len(closed), ", ".join(closed))
        else:
            log.info("[15:25] Square-off: no intraday positions")
    except Exception as e:
        log.exception("Square-off pass failed: %s", e)
    # Day validity for owner limit orders ends here, like a real broker.
    try:
        from trading.orders import expire_day_orders
        n = expire_day_orders(state.portfolio())
        if n:
            log.info("[15:25] Expired %d unfilled owner limit orders", n)
    except Exception as e:
        log.exception("Order expiry failed: %s", e)
    _push_phone_alerts("15:25")


# Daily mode rotation. Intraday runs first (entered + squared-off same day),
# then short, swing, positional fire scans in sequence to fill the book.
# P1/F1 fix: the primary momentum strategy was MISSING from the morning
# rotation (book filled only via 14:30 top-up). Momentum now fills first.
# Owner decision 2026-07-27 ("u can remove intraday but but in 3 sections"):
# intraday removed from the daily auto-rotation — its ±2.5/1.5 same-day churn
# ends; momentum + the 3 swing-family sections remain. The intraday preset
# stays available in Settings for manual use, and the 15:25 square-off job
# keeps running for any intraday-tagged position however it was opened.
_DAILY_MODE_ORDER = ["nifty200_momentum", "short", "swing", "positional"]

DEPLOYMENT_TARGET_PCT = 60.0   # weekly target gross deployment


def _deployment_topup_scan() -> None:
    """14:30 IST: if deployed < target, run another scan pass to fill book."""
    from trading.calendars import is_trading_day
    if not is_trading_day():
        return
    from . import state
    if _degraded_halt("14:30"):
        return
    eng = state.engine()
    p = state.portfolio()
    cfg = state.cfg()
    prices = {pos.ticker: pos.entry_price for pos in p.positions.values()}
    deployed = p.deployed_value(prices)
    deployed_pct = deployed / cfg.starting_capital * 100 if cfg.starting_capital else 0
    if deployed_pct >= DEPLOYMENT_TARGET_PCT:
        log.info("[14:30] Top-up skip: already %.0f%% deployed", deployed_pct)
        return
    log.info("[14:30] Top-up scan: %.0f%% deployed < %.0f%% target",
             deployed_pct, DEPLOYMENT_TARGET_PCT)
    try:
        res = eng.scan()
        log.info("[14:30] Top-up result: %d opened, %d skipped",
                 len(res.opened), len(res.skipped))
    except Exception as e:
        log.exception("Top-up scan failed: %s", e)
    _push_phone_alerts("14:30")
    _check_pending_orders("top-up")


def _check_pending_orders(where: str) -> None:
    """Owner limit orders: fill the touched ones wherever the engine
    already looks at live prices. Never lets an order error break a scan."""
    from . import state
    try:
        from trading.orders import process_open_orders
        filled = process_open_orders(state.engine())
        if filled:
            log.info("[orders:%s] filled: %s", where, ", ".join(filled))
    except Exception as e:
        log.exception("Pending-order pass failed (%s): %s", where, e)


def _push_phone_alerts(where: str) -> None:
    """Batch any new push-worthy journal events to the owner's phone.

    Called after every pass that can buy, sell or fail. Must never break
    the pass itself — push_ntfy.send already swallows transport errors,
    this guards everything else."""
    try:
        from . import push_ntfy
        n = push_ntfy.push_journal_events()
        if n:
            log.info("[%s] pushed %d event(s) to phone", where, n)
    except Exception as e:
        log.warning("[%s] phone-alert push failed: %s", where, e)


def _weekly_history_topup() -> None:
    """Friday 16:30 IST: merge fresh candles into the Mongo OHLCV store.

    Fail-visible: no broker/token or Mongo offline journals
    DATA_REFRESH_SKIPPED instead of silently doing nothing; a run journals
    DATA_REFRESH_WEEKLY with the counts. Scope = tickers already in the
    store plus anything currently held (bounded, safe on 512 MB)."""
    from datetime import date, datetime as _dt

    from trading.calendars import is_trading_day
    if not is_trading_day():
        return          # AUDIT E: every other job checks; this one didn't
    from . import db, state
    eng = state.engine()

    def _journal(action, why):
        try:
            eng.portfolio.decision_journal.append(
                {"ts": _dt.utcnow().isoformat(), "action": action, "why": why})
            eng.portfolio.save()
        except Exception:
            pass

    broker = getattr(eng, "broker", None)
    if broker is None or not getattr(broker, "is_connected", lambda: False)():
        _journal("DATA_REFRESH_SKIPPED",
                 "Weekly OHLCV top-up skipped: Upstox not connected (log in).")
        return
    if not db.is_connected():
        _journal("DATA_REFRESH_SKIPPED",
                 "Weekly OHLCV top-up skipped: Mongo offline.")
        return
    try:
        from trading.brokers.upstox_instruments import to_instrument_keys
        from trading.history_store import top_up_history
        tickers = {m["ticker"] for m in db.list_history_meta() if m.get("ticker")}
        tickers |= set(eng.portfolio.positions.keys())
        keys = to_instrument_keys(sorted(tickers))
        summ = top_up_history(
            broker, keys,
            load_doc=db.load_history_doc,
            save_doc=db.save_history_doc,
            today=date.today(),
        )
        _journal("DATA_REFRESH_WEEKLY",
                 f"Weekly OHLCV top-up: {summ['ok']} refreshed, "
                 f"{summ['skipped_fresh']} already fresh, "
                 f"{summ['failed']} failed of {summ['total']}.")
        log.info("[fri 16:30] weekly history top-up: %s", summ)
    except Exception as e:
        log.exception("weekly history top-up failed: %s", e)
        _journal("DATA_REFRESH_FAILED", f"Weekly OHLCV top-up crashed: {e}")


def _eod_refresh() -> None:
    """15:35 IST: mark all positions, fire exits, journal HOLD decisions."""
    from trading.calendars import is_trading_day
    if not is_trading_day():
        return
    from . import state
    try:
        state.engine().refresh()
        log.info("[15:35] EOD refresh done")
    except Exception as e:
        log.exception("EOD refresh failed: %s", e)
    _push_phone_alerts("15:35")


_ROTATION_LOCK = threading.Lock()


def _degraded_halt(where: str) -> bool:
    """AUDIT FIX C1: a boot during a Mongo outage used to trade a phantom
    fresh book all day, and those trades vanished at the next restart. While
    Mongo is expected-but-down, scans do NOT open positions; the halt is
    journaled once per day and pushed to the phone."""
    from datetime import datetime as _dt

    from . import state
    state.heal_after_reconnect()
    if not state.mongo_degraded():
        return False
    p = state.portfolio()
    today = _dt.utcnow().date().isoformat()
    if not any(e.get("action") == "MONGO_OFFLINE_HALT"
               and str(e.get("ts", "")).startswith(today)
               for e in p.decision_journal[-100:]):
        p.decision_journal.append({
            "ts": _dt.utcnow().isoformat(), "action": "MONGO_OFFLINE_HALT",
            "why": (f"[{where}] MongoDB is configured but unreachable — new "
                    "entries halted so a phantom book can't trade trades that "
                    "vanish at the next restart. Exits still run."),
        })
        p.save()
    log.error("[%s] Mongo expected but down — scan entries halted", where)
    return True


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
    from trading.calendars import is_trading_day
    if not is_trading_day():
        log.info("[09:20] NSE holiday — daily scan skipped")
        return
    if _degraded_halt("09:20"):
        return
    # AUDIT FIX C3: the startup/login catch-up thread and the 09:20 cron
    # could run this concurrently — two loops fighting over cfg.mode,
    # double entries, double journals. One at a time, second one skips.
    if not _ROTATION_LOCK.acquire(blocking=False):
        log.warning("[09:20] another multi-mode scan is already running — skipped")
        return
    log.info("[09:20] Daily multi-mode scan starting (target 60-70%% deployed)")
    summary = []
    import gc
    from trading.modes import MODE_LOCK
    try:
        for m in _DAILY_MODE_ORDER:
            try:
                with MODE_LOCK:
                    cfg.mode = m
                    cfg.apply_mode_preset()
                    res = eng.scan()
                summary.append(f"{m}={len(res.opened)} opened/{len(res.skipped)} skipped")
            except Exception as e:
                log.exception("Daily scan for mode=%s failed: %s", m, e)
                summary.append(f"{m}=error")
                # Silent dead mornings are forbidden: the failure goes into the
                # journal so the dashboard trigger card SHOWS it the same day.
                try:
                    from datetime import datetime as _dt
                    eng.portfolio.decision_journal.append({
                        "ts": _dt.utcnow().isoformat(), "action": "SCAN_FAILED",
                        "mode": m,
                        "err": f"{type(e).__name__}: {str(e)[:120]}",
                        "why": "09:20 scan pass failed — check Upstox login/data.",
                    })
                    eng.portfolio.save()
                except Exception:
                    pass
            finally:
                # 512 MB guard: drop each mode's OHLCV panels before the next
                # mode downloads its own — otherwise five scans' worth of frames
                # can overlap in RAM at 09:20.
                gc.collect()
    finally:
        # AUDIT FIX C3: the restore is in a finally now — an escape mid-loop
        # used to leave cfg.mode stuck on whatever mode the loop died in.
        with MODE_LOCK:
            cfg.mode = original_mode
            cfg.apply_mode_preset()
        _ROTATION_LOCK.release()
    _check_pending_orders("post-scan")
    run_trigger_layer("09:20")
    _push_phone_alerts("09:20")
    log.info("[09:20] Daily multi-mode scan complete: %s", " · ".join(summary))


TRIGGER_LAYER_MAX_TICKERS = 80    # bounded work on the 512 MB box


def run_trigger_layer(where: str = "manual") -> dict:
    """⚡ The owner's Pre-Rise/Fall trigger layer (wired on their order,
    2026-08-04: "wire it"). Runs AFTER the scan, over the day's scanned bucket
    + held + picked names, from STORED history — display and watchlist only.
    Touches no entry, no exit, no engine state beyond `portfolio.trigger_watch`
    and the journal. A crash here can never break the scan that preceded it.
    """
    from datetime import datetime as _dt
    from . import state
    p = state.portfolio()
    try:
        from trading.triggers import stock_view
        from .candle_chart import load_ohlcv
        tickers: list[str] = []
        seen: set[str] = set()
        for t in list(p.positions.keys()) \
                + [pk.get("ticker") for pk in (p.last_scan_picks or [])] \
                + list(((p.scan_matrix or {}).get("rows") or {}).keys()):
            if t and t not in seen:
                seen.add(t)
                tickers.append(t)
        tickers = tickers[:TRIGGER_LAYER_MAX_TICKERS]

        prev_watch = set((p.trigger_watch or {}).get("watchlist") or [])
        rows: dict[str, dict] = {}
        watchlist: list[str] = []
        no_history: list[str] = []
        for t in tickers:
            df = load_ohlcv(t, tail=420)
            if df is None or df.empty:
                no_history.append(t)          # DATA_NEEDED — never guessed
                continue
            asof = ""
            try:
                asof = str(df.index[-1].date() if hasattr(df.index[-1], "date")
                           else df.iloc[-1].get("date", ""))[:10]
            except Exception:
                pass
            view = stock_view(df, asof=asof)
            rows[t] = {
                "asof": view["asof"],
                "family_counts": view["family_counts"],
                "scores": view["scores"],
                "data_quality": view["data_quality"],
                "gates_passed": [g["id"] for g in view["gates"]
                                 if g["status"] == "PASS"],
                "fired": [f["id"] for f in view["fired"]],
                "watch": view["watch"],
            }
            if view["watch"]:
                watchlist.append(t)

        p.trigger_watch = {
            "ts": _dt.utcnow().isoformat(),
            "rows": rows,
            "watchlist": watchlist,
            "no_history": no_history,
            "scanned": len(rows),
        }
        for t in watchlist:
            if t not in prev_watch:            # journal each name once
                p.decision_journal.append({
                    "ts": _dt.utcnow().isoformat(),
                    "action": "TRIGGER_WATCHLIST",
                    "ticker": t,
                    "gates": rows[t]["gates_passed"],
                    "why": ("Owner's trigger layer: GATE_RISE_WATCH_01 passed "
                            "(compression>=2, accumulation>=2, distribution=0)."
                            " Watchlist only — no order placed."),
                })
        p.save()
        log.info("[triggers:%s] %d scored, %d on watchlist, %d no-history",
                 where, len(rows), len(watchlist), len(no_history))
        return p.trigger_watch
    except Exception as e:
        log.exception("Trigger layer pass failed: %s", e)
        try:
            p.decision_journal.append({
                "ts": _dt.utcnow().isoformat(),
                "action": "TRIGGER_LAYER_FAILED",
                "err": f"{type(e).__name__}: {str(e)[:120]}",
                "why": "Trigger-layer pass crashed — scan itself unaffected.",
            })
            p.save()
        except Exception:
            pass
        return {}
