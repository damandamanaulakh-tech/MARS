"""Daily auto-scan scheduler (APScheduler).

Two job tracks:
  1. Intraday tick — every 5 min, 9:15-15:25 IST (only when mode=intraday)
  2. Daily multi-mode scan — once at 09:20 IST: runs scan in intraday,
     short, swing, and positional in sequence. Opens whatever passes the
     gates in each mode. Target 60-70% weekly capital deployment.

The daily track is ON by default (paper-only). Set DAILY_SCHEDULER=0 in
Render env to switch it off. The legacy INTRADAY_SCHEDULER=1 toggle is
still honoured, and the intraday track stays opt-in.

TRUTH-PASS 2026-08-29: this header used to say "Disabled by default. Set
DAILY_SCHEDULER=1 to switch on", contradicting both the code below and
start_if_enabled's own docstring. Read daily_enabled() — never re-derive
the predicate.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

_SCHED = None


def daily_enabled() -> bool:
    """Is the daily auto-scan track on? THE single source of truth.

    UNSET MEANS ON. This exists because the predicate was written out by
    hand in three places and one of them disagreed: the dashboard read
    `DAILY_SCHEDULER != "1"` and told the owner "Daily auto-scan is OFF"
    whenever the variable was simply absent — which is the default, and
    which render.yaml never sets. So the screen reported the engine idle
    while it scanned every morning. Lying-screen class (audit B batch);
    caught 2026-08-29 while auditing the external review's F-012.

    Every reader must call this. Never re-derive it from os.environ.
    """
    return os.environ.get("DAILY_SCHEDULER", "1") != "0"


def start_if_enabled() -> None:
    """Called from FastAPI startup. Idempotent.

    Daily auto-trading is ON by default (paper-only). Set DAILY_SCHEDULER=0
    in Render env to disable. Intraday tick remains opt-in.
    """
    global _SCHED
    intraday = os.environ.get("INTRADAY_SCHEDULER") == "1"
    daily = daily_enabled()
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
        # Weekly data freshness (owner order 2026-07-27): 16:30 IST, market
        # closed. Tops up the stored OHLCV research store — merge, never
        # replace — and runs the six short data feeds first.
        #
        # SELF-HEALING SLOT 2026-08-29 (owner: "if Friday run is not done, it
        # should run on Monday not next Friday"). Registered mon-fri, not
        # fri: weekly_block_due() keeps the cadence weekly and returns
        # immediately on a day it already ran, so an ordinary Monday costs
        # one settings read. A cron job that misses its slot is otherwise
        # simply missed — this block had no catch-up at all, and a restart
        # across 16:30 on a Friday cost a whole week of data.
        _SCHED.add_job(
            _weekly_history_topup,
            "cron",
            day_of_week="mon-fri", hour=16, minute=30,
            id="weekly_history_topup",
            replace_existing=True,
        )
        _SCHED.add_job(
            _eod_refresh, "cron",
            day_of_week="mon-fri", hour=15, minute=35,
            id="eod_refresh",
        )
        # Node 10: nightly register precompute over the full store — after
        # the close and the 15:35 refresh, when the box is otherwise idle.
        _SCHED.add_job(
            run_register_precompute, "cron",
            day_of_week="mon-fri", hour=16, minute=45,
            id="register_precompute",
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

    if (not daily_enabled()
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
# OWNER ORDER 2026-08-30 ("make sure category — Intraday, short, swing &
# positional & N200 momentum execute the trades"): all FIVE categories are
# back in the daily rotation. This supersedes their 2026-07-27 decision
# ("u can remove intraday but but in 3 sections") which had dropped intraday.
#
# Intraday's own preset (±2.5/1.5, 1-day max hold) is unchanged, and the
# 15:25 square-off job still closes every intraday-tagged position the same
# day — so putting it back cannot leave an intraday trade running overnight.
# Momentum stays FIRST so the primary strategy gets first call on the cash.
_DAILY_MODE_ORDER = ["nifty200_momentum", "intraday", "short", "swing",
                     "positional"]

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


def run_register_precompute(where: str = "manual") -> dict:
    """Node 10 (owner order 2026-08-24: "continue with node 10"): the
    register's MARKET view precomputed nightly over the FULL stored panel.

    THE F5 FLAW THIS LIFTS: /register computed its market-level items (PX
    panic signatures, breadth, dispersion, tails) at page-render time over
    a 50-member, 320-bar sample — the 512MB RAM guard — so every panic
    number was panel-relative to whichever 50 names loaded first, and the
    page paid the compute cost on every open.

    The precompute reads ONLY closes, via the same streamed Mongo $slice
    tail query the F3 breadth fix uses — the full ~2,700-name store costs a
    few MB, not the OOM the cap guarded against. The result is stored on
    `portfolio.register_market` (JSON+Mongo both ways); the page reads it
    while fresh and falls back to the old capped live compute otherwise —
    degrade-to-before, never to something new.

    Thin store (< min names — sandbox, Mongo down, young store) journals
    REGISTER_PRECOMPUTE_SKIPPED and stores nothing: a 60-name "full panel"
    wearing the wide label is the exact lie F5's disclosure existed to
    prevent.
    """
    from datetime import datetime as _dt

    from . import db, state
    p = state.portfolio()

    def _journal(action, why):
        try:
            p.decision_journal.append(
                {"ts": _dt.utcnow().isoformat(), "action": action, "why": why})
            p.save()
        except Exception:
            pass

    MIN_NAMES = 150       # below this the capped live view is just as honest
    try:
        import pandas as pd

        import trading.register_v4 as rv
        from trading.universes import get_nifty_200_universe
        panel: dict = {}
        newest = ""
        for doc in db.history_tails(340):
            candles = doc.get("candles") or []
            t = doc.get("ticker") or ""
            if not t or len(candles) < 60:
                continue
            try:
                # ALL FIVE columns — register_v4's normalizer (the 2026-08-09
                # lowercase-fixture lesson) rejects close-only frames, and a
                # rejected member silently drops out of the panel. My first
                # cut here built Close-only and every PX item read NA with
                # 2,700 names loaded; the $slice docs carry o/h/l/c/v anyway.
                idx = pd.to_datetime([c[0] for c in candles])
                panel[t] = pd.DataFrame({
                    "Open": [float(c[1]) for c in candles],
                    "High": [float(c[2]) for c in candles],
                    "Low": [float(c[3]) for c in candles],
                    "Close": [float(c[4]) for c in candles],
                    "Volume": [float(c[5]) for c in candles],
                }, index=idx)
            except (TypeError, ValueError, IndexError):
                continue
            end = str(doc.get("end") or "")
            if end > newest:
                newest = end
        if len(panel) < MIN_NAMES:
            log.info("[register:%s] store too thin (%d names) — page keeps "
                     "its capped live view", where, len(panel))
            return {}
        _, sector_map = get_nifty_200_universe()
        inputs = rv.load_market_inputs()
        mv = rv.market_view(panel, index_close=inputs.get("index"),
                            vix_close=inputs.get("vix"),
                            bond_yield=inputs.get("bond"),
                            flows=inputs.get("flows"),
                            pwoi=inputs.get("pwoi"),
                            sectors=sector_map,
                            damage_store=p.damage_store)
        rows = {rid: {"state": r.state,
                      "value": None if r.value is None else float(r.value),
                      "note": r.note}
                for rid, r in mv.items()}
        p.register_market = {"asof": newest,
                             "ts": _dt.utcnow().isoformat(),
                             "sample": len(panel), "rows": rows}
        p.save()
        _journal("REGISTER_PRECOMPUTE",
                 f"Register market view precomputed over the FULL store: "
                 f"{len(panel)} names, closes to {newest}, "
                 f"{len(rows)} items. /register now reads this instead of "
                 "the 50-name render-time sample.")
        log.info("[register:%s] %d names, %d items, closes to %s", where,
                 len(panel), len(rows), newest)
        return p.register_market
    except Exception as e:
        log.exception("register precompute failed: %s", e)
        try:
            _journal("REGISTER_PRECOMPUTE_FAILED",
                     f"Register precompute crashed: {e} — the page keeps "
                     "its capped live view.")
        except Exception:
            pass
        return {}


def run_option_chain_snapshot(where: str = "manual") -> dict:
    """Node 8 (owner order 2026-08-23: "continue with node 8"): one option-
    chain snapshot per trading day, through the owner's own Upstox token.

    Scope = the custom universe plus held names (the index rides along as
    ^NSEI). Feeds T12W_0102 (the LAST unwired trigger), W-022 true PCR and
    W-007 per-stock IV. HONESTLY TOKEN-DEPENDENT (audit F8): with no login
    there is no chain — journaled OPTION_CHAIN_SKIPPED, every consumer
    reads DATA_NEEDED. Runs from the 09:20 job and again on Upstox connect;
    the once-per-day guard makes the second arrival a no-op.
    """
    from datetime import date as _date, datetime as _dt

    from . import state
    p = state.portfolio()

    def _journal(action, why):
        try:
            p.decision_journal.append(
                {"ts": _dt.utcnow().isoformat(), "action": action, "why": why})
            p.save()
        except Exception:
            pass

    try:
        today = _date.today().isoformat()
        if (p.option_chain or {}).get("asof") == today:
            return p.option_chain            # already snapped today
        eng = state.engine()
        broker = getattr(eng, "broker", None)
        if broker is None or not getattr(broker, "is_connected",
                                         lambda: False)():
            # once per day, not once per attempt — the 09:20 miss and the
            # catch-up miss are the same fact
            if (p.option_chain or {}).get("skip_noted") != today:
                p.option_chain = {**(p.option_chain or {}),
                                  "skip_noted": today}
                _journal("OPTION_CHAIN_SKIPPED",
                         "Option-chain snapshot skipped: the option "
                         "endpoints need the daily Upstox login (unlike "
                         "historical candles). T12W_0102 / W-022 / W-007 "
                         "read DATA_NEEDED today.")
            return {}
        from trading.brokers.upstox_instruments import to_instrument_keys
        from trading.options_chain import HISTORY_KEEP, snapshot_universe
        cfg = state.cfg()
        tickers = sorted(set(getattr(cfg, "custom_universe", ()) or ())
                         | set(p.positions.keys()))
        keys = to_instrument_keys(tickers)
        snap = snapshot_universe(broker, tickers, keys.get,
                                 _date.today())
        if not snap["rows"]:
            _journal("OPTION_CHAIN_FAILED",
                     f"Option-chain snapshot returned nothing "
                     f"({len(snap['failed'])} failed, "
                     f"{len(snap['skipped_no_fo'])} without options) — "
                     "consumers keep DATA_NEEDED.")
            return {}
        # bounded per-ticker history — the forward archive T12W_0102's
        # deterioration leg needs
        hist = dict((p.option_chain or {}).get("history") or {})
        for t, r in snap["rows"].items():
            rows = list(hist.get(t) or [])
            rows = [x for x in rows if x[0] != snap["asof"]]
            rows.append((snap["asof"], r.get("pcr_oi"), r.get("iv_skew")))
            hist[t] = rows[-HISTORY_KEEP:]
        p.option_chain = {"asof": snap["asof"], "ts": snap["ts"],
                          "rows": snap["rows"], "history": hist,
                          "skipped_no_fo": snap["skipped_no_fo"],
                          "failed": snap["failed"]}
        p.save()
        _journal("OPTION_CHAIN_SNAPSHOT",
                 f"Option chain snapped for {len(snap['rows'])} name(s) "
                 f"incl. ^NSEI ({len(snap['skipped_no_fo'])} without "
                 f"options, {len(snap['failed'])} failed). Feeds "
                 "T12W_0102, W-022, W-007.")
        log.info("[chain:%s] %d rows, %d no-F&O, %d failed", where,
                 len(snap["rows"]), len(snap["skipped_no_fo"]),
                 len(snap["failed"]))
        return p.option_chain
    except Exception as e:
        log.exception("option chain snapshot failed: %s", e)
        try:
            _journal("OPTION_CHAIN_FAILED",
                     f"Option-chain snapshot crashed: {e}")
        except Exception:
            pass
        return {}


def run_market_fii_snapshot(where: str = "manual") -> bool:
    """Owner order 2026-08-24 ("with upstox key u can wire the FII data"):
    one daily pull of the Upstox Market Information FII + DII endpoints.

    FII derivatives (index/stock futures + options, segment-level — no
    feed anywhere serves daily PER-STOCK FII) extend the committed PWOI
    panel, so FII-04/05 on /register stop being frozen at the 2026-08-07
    Colab fetch. DII (NSE Cash) is the first LIVE DII source since the
    NSDL DII archive proved OTP-walled (F7) — rows merge onto fii_daily.

    AUTH: the daily login token, or UPSTOX_ANALYTICS_TOKEN in Render (the
    owner can generate one on the Upstox Developer Apps page — that keeps
    this feed alive even before the day's login). Response shape is
    UNVERIFIED until the first authenticated call: the raw payload is
    archived either way, the mappers refuse rows they can't prove, and a
    shape miss journals instead of guessing.
    """
    import os
    from datetime import date as _date

    from . import db, state
    try:
        if not db.is_connected():
            return False                 # nowhere to store; weekly jobs journal Mongo-down
        today = _date.today().isoformat()
        guard = db.load_settings(doc_id="data_refresh")
        if guard.get("market_fii_day") == today:
            return True
        eng = state.engine()
        broker = getattr(eng, "broker", None)
        if broker is None or not getattr(broker, "is_connected",
                                         lambda: False)():
            from trading import data as tdata
            broker = tdata.history_broker()
        has_env = bool(os.environ.get("UPSTOX_ANALYTICS_TOKEN", "").strip())
        has_token = bool(broker is not None
                         and getattr(broker, "access_token", None))
        if broker is None or not (has_token or has_env):
            if guard.get("market_fii_skip_noted") != today:
                db.save_settings({"market_fii_skip_noted": today},
                                 doc_id="data_refresh")
                _refresh_journal(
                    "MARKET_FII_SKIPPED",
                    "Upstox FII/DII snapshot skipped: needs the daily "
                    "login token or UPSTOX_ANALYTICS_TOKEN in Render. "
                    "FII-04/05 read the committed panel today.")
            return False
        from trading import data_refresh as dr
        fii_raw = broker.get_market_fii(list(dr.FII_DATA_TYPES))
        dii_raw = broker.get_market_dii()
        if fii_raw is None and dii_raw is None:
            _refresh_journal("MARKET_FII_FAILED",
                             "Upstox FII/DII snapshot: both endpoints "
                             "returned nothing — consumers keep the "
                             "committed panels.")
            return False
        db.save_market_info_raw("fii", today, fii_raw)
        db.save_market_info_raw("dii", today, dii_raw)
        pwoi_rows = dr.map_market_fii(fii_raw) if fii_raw else []
        dii_rows = dr.map_market_dii(dii_raw) if dii_raw else []
        n_p = db.save_pwoi_rows(pwoi_rows)
        n_d = db.update_dii_rows(dii_rows)
        db.save_settings({"market_fii_day": today}, doc_id="data_refresh")
        parts = []
        if n_p:
            parts.append(f"{n_p} FII derivative day-row(s) -> FII-04/05")
        elif fii_raw is not None:
            parts.append("FII payload archived but UNMAPPED (shape "
                         "differs from the documented fields — raw kept, "
                         "mapper needs one look at it)")
        if n_d:
            parts.append(f"{n_d} DII cash day-row(s) (first live DII "
                         "since F7)")
        elif dii_raw is not None:
            parts.append("DII payload archived but UNMAPPED")
        _refresh_journal("MARKET_FII_SNAPSHOT",
                         "Upstox market FII/DII: " + "; ".join(parts) + ".")
        log.info("[market-fii:%s] pwoi=%d dii=%d", where, n_p, n_d)
        return True
    except Exception as e:
        log.exception("market FII/DII snapshot failed: %s", e)
        try:
            _refresh_journal("MARKET_FII_FAILED",
                             f"Upstox FII/DII snapshot crashed: {e}")
        except Exception:
            pass
        return False


def _weekly_fii_refresh() -> None:
    """Node 3 (owner order 2026-08-23): weekly NSDL FPI tail into Mongo.

    Runs inside the Friday 16:30 job. Fetches the last two calendar months
    of daily FPI equity cash (report-date shift + cumulative-row guard
    handled in trading/signals/fii_nsdl.py), upserts them into the
    `fii_daily` collection keyed by date, and journals the outcome either
    way — a silent skip is forbidden. Failure changes nothing: get_flows
    keeps its other two sources and the staleness guard stays in charge.
    """
    from datetime import datetime as _dt

    from . import db, state
    p = state.portfolio()

    def _journal(action, why):
        try:
            p.decision_journal.append(
                {"ts": _dt.utcnow().isoformat(), "action": action, "why": why})
            p.save()
        except Exception:
            pass

    if not db.is_connected():
        _journal("DATA_REFRESH_FII_SKIPPED",
                 "Weekly FII refresh skipped: Mongo offline (nowhere to "
                 "store the rows).")
        return
    try:
        from trading.signals.fii_nsdl import recent_rows
        rows = recent_rows(months=2)
        if not rows:
            _journal("DATA_REFRESH_FII_FAILED",
                     "Weekly FII refresh: NSDL archive returned nothing — "
                     "flows keep their committed-CSV + live-API sources.")
            return
        n = db.save_fii_rows(rows)
        _journal("DATA_REFRESH_FII",
                 f"Weekly FII refresh: {n} NSDL rows stored "
                 f"({rows[0][0]} → {rows[-1][0]}, settlement basis, "
                 "trading-dated). The 5-day FII sum stays live.")
        log.info("[fri 16:30] FII refresh: %d rows to %s", n, rows[-1][0])
    except Exception as e:
        log.exception("weekly FII refresh failed: %s", e)
        _journal("DATA_REFRESH_FII_FAILED", f"Weekly FII refresh crashed: {e}")


def _refresh_journal(action: str, why: str) -> None:
    """Append-only journal line for the Node 3 refresh jobs."""
    from datetime import datetime as _dt

    from . import state
    try:
        p = state.portfolio()
        p.decision_journal.append(
            {"ts": _dt.utcnow().isoformat(), "action": action, "why": why})
        p.save()
    except Exception:
        pass


def _weekly_index_refresh() -> None:
    """Index-daily tail — DAILY since 2026-08-26 (owner fault report:
    "still on sensex and all, it show 7 august" — the weekly Friday slot
    meant the first run had never fired). Runs before the 09:20 scans,
    at boot catch-up, and in the Friday block; idempotent, quiet when
    already current.

    Fetches the four dashboard indices' candles PAST the newest date we
    hold (committed CSV or stored tail, whichever is later) via the public
    Upstox candle endpoint — tokenless, the F1 fact — and upserts them into
    the `index_daily` collection. Consumers (dashboard sparklines, register
    index/VIX inputs, the T12W_0095 index overlay in the danger layer)
    merge this tail onto the committed CSV; Mongo down or fetch dead =
    the committed file alone, exactly the pre-Node-3 world.
    """
    from . import db
    if not db.is_connected():
        _refresh_journal("DATA_REFRESH_INDEX_SKIPPED",
                         "Weekly index refresh skipped: Mongo offline "
                         "(nowhere to store the tail).")
        return
    try:
        from trading import data as tdata, data_refresh as dr
        last = dr.committed_index_last()
        stored = dr.stored_index_rows()
        if stored:
            last = max(last, str(stored[-1].get("date") or ""))
        if not last:
            _refresh_journal("DATA_REFRESH_INDEX_FAILED",
                             "Weekly index refresh: committed index CSV "
                             "unreadable — nothing to extend from.")
            return
        broker = tdata.history_broker()
        if broker is None:
            _refresh_journal("DATA_REFRESH_INDEX_SKIPPED",
                             "Weekly index refresh skipped: no Upstox "
                             "client object (API keys unset).")
            return
        rows = dr.fetch_index_tail(broker, last)
        if not rows:
            # daily since 2026-08-26 — an already-current day is the normal
            # case and must not write a journal line every morning
            log.info("index refresh: already current to %s", last)
            return
        n = db.save_index_rows(rows)
        _refresh_journal("DATA_REFRESH_INDEX",
                         f"Weekly index refresh: {n} day(s) stored "
                         f"({rows[0]['date']} → {rows[-1]['date']}). "
                         "Dashboard sparklines, register index/VIX and the "
                         "T12W_0095 overlay now read past the committed "
                         "CSV.")
        log.info("[fri 16:30] index refresh: %d rows to %s", n,
                 rows[-1]["date"])
    except Exception as e:
        log.exception("weekly index refresh failed: %s", e)
        _refresh_journal("DATA_REFRESH_INDEX_FAILED",
                         f"Weekly index refresh crashed: {e}")


def _monthly_constituents_snapshot() -> None:
    """Node 3: monthly Nifty 50/200/500 constituents re-snapshot — the
    forward point-in-time record data/index_constituents/ (2026-08-09)
    started. Month-guarded via the data_refresh settings doc; a quiet skip
    inside the month is by design (it ran already), only failures journal.
    """
    from datetime import date as _date

    from . import db
    if not db.is_connected():
        return                      # weekly retry; the index job journaled
    month = _date.today().isoformat()[:7]
    try:
        if db.load_settings(doc_id="data_refresh").get(
                "constituents_month") == month:
            return
        from trading import data_refresh as dr
        lists = dr.fetch_constituents()
        if not lists:
            _refresh_journal("DATA_REFRESH_CONSTITUENTS_FAILED",
                             "Monthly constituents snapshot: niftyindices "
                             "returned nothing usable — will retry next "
                             "Friday.")
            return
        saved = [n for n, rows in lists.items()
                 if db.save_constituent_snapshot(month, n, rows)]
        if not saved:
            _refresh_journal("DATA_REFRESH_CONSTITUENTS_FAILED",
                             "Monthly constituents snapshot: fetched but "
                             "Mongo refused the docs.")
            return
        db.save_settings({"constituents_month": month},
                         doc_id="data_refresh")
        counts = ", ".join(f"{n.replace('ind_', '').replace('list', '')} "
                           f"{len(lists[n])}" for n in saved)
        _refresh_journal("DATA_REFRESH_CONSTITUENTS",
                         f"Monthly constituents snapshot stored for {month}: "
                         f"{counts}. The forward PIT record grows.")
        log.info("[fri 16:30] constituents snapshot %s: %s", month, saved)
    except Exception as e:
        log.exception("constituents snapshot failed: %s", e)
        _refresh_journal("DATA_REFRESH_CONSTITUENTS_FAILED",
                         f"Monthly constituents snapshot crashed: {e}")


def _monthly_fundamentals_refresh() -> None:
    """Node 3: monthly fundamentals refresh (Yahoo direct timeseries — the
    raw endpoint, since the yfinance wrapper returns empty where the
    endpoint has data). Scope = custom universe + held names, bounded.
    register_v4.load_fundamentals overlays the stored rows on the committed
    CSV; the cache is cleared after a successful store so the page and the
    nightly precompute see the new quarters without a restart."""
    from datetime import date as _date

    from . import db, state
    if not db.is_connected():
        return                      # weekly retry; the index job journaled
    month = _date.today().isoformat()[:7]
    try:
        if db.load_settings(doc_id="data_refresh").get(
                "fundamentals_month") == month:
            return
        from trading import data_refresh as dr
        cfg = state.cfg()
        p = state.portfolio()
        syms = sorted({t.replace(".NS", "")
                       for t in (tuple(cfg.custom_universe or ())
                                 + tuple((p.positions or {}).keys()))})
        if not syms:
            return
        rows, n_ok = dr.fetch_fundamentals(syms)
        if not rows:
            _refresh_journal("DATA_REFRESH_FUNDAMENTALS_FAILED",
                             f"Monthly fundamentals refresh: Yahoo returned "
                             f"nothing for all {len(syms)} symbols — will "
                             "retry next Friday.")
            return
        n = db.save_fundamentals_rows(rows)
        db.save_settings({"fundamentals_month": month},
                         doc_id="data_refresh")
        try:
            import trading.register_v4 as rv
            rv.clear_fundamentals_cache()
        except Exception:
            log.exception("fundamentals cache clear failed")
        _refresh_journal("DATA_REFRESH_FUNDAMENTALS",
                         f"Monthly fundamentals refresh for {month}: "
                         f"{n} datapoints stored, {n_ok}/{len(syms)} "
                         "symbols answered. /register H/W/X items read the "
                         "fresh quarters.")
        log.info("[fri 16:30] fundamentals refresh: %d rows, %d/%d ok",
                 n, n_ok, len(syms))
    except Exception as e:
        log.exception("fundamentals refresh failed: %s", e)
        _refresh_journal("DATA_REFRESH_FUNDAMENTALS_FAILED",
                         f"Monthly fundamentals refresh crashed: {e}")


def _weekly_sebi_refresh() -> None:
    """Owner order 2026-08-25 ("wire the SEBI enforcement orders feed
    too"): weekly pull of SEBI's live enforcement-orders listing, matched
    by exact title word against EVERY custom-universe + held company
    name (SEBI hits any sector). Most orders concern individuals — an
    empty week means no order NAMED these companies, and the journal
    carries the scanned count so absence is dated evidence. Display only:
    no order ever blocks a buy or moves a stop."""
    from . import db, state
    if not db.is_connected():
        _refresh_journal("DATA_REFRESH_SEBI_SKIPPED",
                         "Weekly SEBI refresh skipped: Mongo offline "
                         "(nowhere to store orders).")
        return
    try:
        from trading import sebi_feed
        cfg = state.cfg()
        p = state.portfolio()
        tickers = set(tuple(cfg.custom_universe or ())) | \
            set(p.positions or {})
        import csv as _csv
        from pathlib import Path as _Path
        uf = _Path(__file__).resolve().parents[1] / "data" / \
            "upstox_nse_equities.csv"
        with open(uf) as f:
            rows = {r["ticker"]: r.get("name", "")
                    for r in _csv.DictReader(f)}
        name_map = {t: rows[t] for t in sorted(tickers) if rows.get(t)}
        if not name_map:
            return
        orders = sebi_feed.fetch_orders()
        if not orders:
            _refresh_journal("DATA_REFRESH_SEBI_FAILED",
                             "Weekly SEBI refresh: the listing endpoint "
                             "returned no parseable orders — will retry "
                             "next Friday.")
            return
        matched = sebi_feed.match_orders(orders, name_map)
        n_new = db.save_sebi_orders(matched)
        hits = sorted({m["ticker"] for m in matched})
        _refresh_journal(
            "DATA_REFRESH_SEBI",
            f"SEBI weekly: {len(orders)} order(s) scanned against "
            f"{len(name_map)} name(s); {len(matched)} matched, {n_new} "
            "new." + (f" Named: {', '.join(hits)}." if hits
                      else " No order named these companies."))
        log.info("[fri 16:30] SEBI: %d orders, %d matched, %d new",
                 len(orders), len(matched), n_new)
    except Exception as e:
        log.exception("weekly SEBI refresh failed: %s", e)
        _refresh_journal("DATA_REFRESH_SEBI_FAILED",
                         f"Weekly SEBI refresh crashed: {e}")


def refresh_breadth_store(where: str = "manual") -> dict:
    """F3 fix (owner order 2026-08-20): compute market breadth over the FULL
    Mongo OHLCV store (~2,700 names, one streamed $slice query) and persist
    the reading on the portfolio for the A3 gate to prefer.

    The gate used to read the scan's own ~50-name bucket — ±7pp sampling
    noise against a <=50% line, so buy-or-block flapped on which 50 names the
    rotation served that day. The full-store reading has ~±1pp noise and
    carries its as-of date (store closes can trail up to a week between
    Friday top-ups; the F1 fix made those reliable).

    Fail-safe: any failure leaves the previous reading in place and the gate
    falls back to the bucket sample once the reading is stale — behaviour
    then degrades to exactly the pre-fix gate, never to something new.
    """
    from datetime import datetime as _dt

    from trading.breadth import store_breadth
    from . import db, state
    p = state.portfolio()
    try:
        dma = 200
        reading = store_breadth(db.history_tails(dma + 5), dma=dma)
        if reading is None:
            log.info("[breadth:%s] no usable full-store reading "
                     "(store thin or Mongo down) — gate keeps its fallback",
                     where)
            return {}
        reading["computed"] = _dt.utcnow().isoformat()
        p.breadth_store = reading
        p.save()
        log.info("[breadth:%s] %.1f%% of %d above 200DMA (closes to %s, "
                 "%d stale + %d short excluded)", where,
                 reading["pct"] * 100, reading["sample"], reading["asof"],
                 reading["excluded_stale"], reading["excluded_short"])
        return reading
    except Exception as e:
        log.exception("full-store breadth refresh failed: %s", e)
        return {}


# The weekly data block's completion ledger, in the settings doc the other
# day/month guards already use. Written only AFTER the block finishes — the
# guard-set-before-the-work fault this file has been bitten by before.
WEEKLY_BLOCK_KEY = "weekly_block_date"


def weekly_block_due(today=None) -> tuple[bool, str]:
    """Should the weekly data block run today? (bool, plain-language reason)

    ADOPTED 2026-08-29 (owner order "go ... 3") from the AshStocks review,
    whose continuous market-hours loop self-heals a missed slot where a cron
    job simply misses it. Copying that loop wholesale is wrong here — the
    block pulls ~2,400 names and the box has 512 MB — so the self-healing is
    taken without the loop: the job is registered mon-fri at the SAME 16:30
    slot (market closed, nothing competing) and returns immediately unless
    it is actually due.

    This is also the owner's own decision, verbatim: "If Friday run is not
    done, it should run on Monday not next Friday."

    Friday remains the slot. The 4-day arm is what returns the cadence TO
    Friday after a Monday catch-up — without it a single miss would move the
    block to Mondays for ever.
    """
    from datetime import date as _date
    today = today or _date.today()
    try:
        from . import db
        last_iso = (db.load_settings(doc_id="data_refresh")
                    or {}).get(WEEKLY_BLOCK_KEY)
    except Exception:
        log.exception("weekly block ledger unreadable")
        last_iso = None
    if not last_iso:
        return True, "the weekly data block has no recorded completion yet"
    try:
        last = _date.fromisoformat(str(last_iso)[:10])
    except ValueError:
        return True, f"unreadable last-completion stamp {last_iso!r}"
    days = (today - last).days
    if days < 0:
        return True, f"last completion {last} is in the future — clock skew"
    if today.weekday() == 4:                       # Friday, the normal slot
        return (days >= 4,
                f"Friday slot, {days} day(s) since the last completion")
    return (days >= 7,
            f"catch-up check, {days} day(s) since the last completion "
            f"({last}) — Friday's run did not complete")


def _mark_weekly_block_done() -> None:
    """Record completion. Only reached after the block's own sequence ran."""
    from datetime import date as _date
    try:
        from . import db
        db.save_settings({WEEKLY_BLOCK_KEY: _date.today().isoformat()},
                         doc_id="data_refresh")
    except Exception:
        log.exception("could not record the weekly block completion")


def _run_weekly_step(label: str, fn, journal) -> bool:
    """Run ONE Friday sub-job so its failure cannot take out the others.

    FIX 2026-08-29 (owner order "fix it"): every weekly sub-job used to sit
    in a SINGLE try block, so the first crash silently skipped every feed
    below it and the only trace was one DATA_REFRESH_FAILED line naming the
    OHLCV pull. Most sub-jobs already catch their own exceptions; this is
    the outer guarantee that an import error or an unexpected escape in one
    feed cannot cost the owner the other seven.
    """
    try:
        fn()
        return True
    except Exception as e:                      # noqa: BLE001 - isolation
        log.exception("weekly sub-job %s failed: %s", label, e)
        journal("DATA_REFRESH_STEP_FAILED",
                f"Friday data block: {label} crashed ({e}) — the other "
                f"weekly feeds still ran.")
        return False


def _weekly_history_topup() -> None:
    """Friday 16:30 IST: merge fresh candles into the Mongo OHLCV store.

    Runs with or without the daily Upstox login — historical candles are a
    public endpoint (F1 fix 2026-08-20); only a missing client object or
    Mongo offline skips, and both journal DATA_REFRESH_SKIPPED instead of
    silently doing nothing.

    ORDER (2026-08-29): journal DATA_REFRESH_WEEKLY_START, then the six
    short store-independent feeds each isolated, THEN the OHLCV pull
    (journals DATA_REFRESH_WEEKLY with the counts), then the two
    store-derived recomputes. A crashed or interrupted pull can no longer
    cost the week's FII/constituents/fundamentals/SEBI data.

    Scope of the pull = tickers already in the store, plus anything held,
    plus the full universe file (~2,400 names since PR #68 — the store
    could not grow before that). Merged one name at a time, so the 512 MB
    box never holds more than one frame."""
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

    # AUDIT F1 FIX (2026-08-20, owner: "fix F1"): this job used to also
    # require broker.is_connected() — but Upstox HISTORICAL candles are a
    # PUBLIC endpoint (get_daily_candles now works tokenless), so gating a
    # Friday-evening data refresh on the 03:30-dead login token skipped the
    # top-up any week the owner hadn't logged in, for no reason. Only a
    # missing broker OBJECT (no API keys configured at all) still skips,
    # because there is then no client to fetch with.
    from trading.data import history_broker
    broker = getattr(eng, "broker", None) or history_broker()
    if broker is None:
        _journal("DATA_REFRESH_SKIPPED",
                 "Weekly OHLCV top-up skipped: no Upstox client configured "
                 "(historical candles need no login, but they do need the "
                 "API client to exist — set the API keys in Render).")
        return
    if not db.is_connected():
        _journal("DATA_REFRESH_SKIPPED",
                 "Weekly OHLCV top-up skipped: Mongo offline.")
        return
    # Self-healing slot (owner order 2026-08-29: "if Friday run is not done,
    # it should run on Monday not next Friday"). Registered mon-fri now, so
    # this is the gate that keeps it weekly. Cheap: one settings read.
    due, why = weekly_block_due()
    if not due:
        log.info("[16:30] weekly data block not due — %s", why)
        return
    # FIX 2026-08-29 (owner order "fix it", after they asked "is the Friday
    # trigger captured"): the SHORT, store-independent feeds now run FIRST,
    # each isolated, BEFORE the long OHLCV pull.
    #
    # WHY: all eight sub-jobs used to run in one try block AFTER a
    # ~2,400-name top-up that takes the better part of an hour. A crash —
    # or a container restart mid-pull, which NO try block can catch —
    # silently skipped everything below it, and the weekly block has no
    # catch-up, so the miss cost a full week. Three of the eight have a
    # daily counterpart and survived that (index tail, breadth, register
    # precompute); FOUR do not (FII tide, constituents, fundamentals,
    # SEBI). Measured on the live box 2026-08-28: the store held
    # 573 of 2,428 names after two full-scope Fridays — the pull was not
    # finishing, so those five were the ones paying for it.
    _journal("DATA_REFRESH_WEEKLY_START",
             f"Weekly data block started ({why}): short feeds first, then "
             f"the full-universe OHLCV top-up.")

    # Independent of the store's freshness — cheap, so they go first and
    # are done long before anything can interrupt the pull.
    for _label, _fn in (
        ("FII tide (NSDL)", _weekly_fii_refresh),
        ("index tail", _weekly_index_refresh),
        ("constituents snapshot", _monthly_constituents_snapshot),
        ("fundamentals refresh", _monthly_fundamentals_refresh),
        ("SEBI orders", _weekly_sebi_refresh),
    ):
        _run_weekly_step(_label, _fn, _journal)

    try:
        from trading.brokers.upstox_instruments import to_instrument_keys
        from trading.history_store import top_up_history
        tickers = {m["ticker"] for m in db.list_history_meta() if m.get("ticker")}
        tickers |= set(eng.portfolio.positions.keys())
        # CODEX REVIEW FIX (PR #68): the scope used to be "already stored +
        # held", so the store could never GROW — it sat at the original 191
        # names, which also starved the F3 breadth reading of the wide
        # sample it claims. The full universe file (~2,400 tradeable names)
        # is now included: tokenless since F1, one bounded request per
        # stale name, off-hours on Friday, merged one doc at a time so the
        # 512 MB box never holds more than one name's frame.
        try:
            from trading.universes import load_full_universe
            tickers |= set(load_full_universe())
        except Exception:
            log.exception("weekly top-up: universe file unreadable — "
                          "refreshing stored+held only")
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

    # These two READ the store, so they can only follow the top-up. They are
    # isolated from it and from each other: a failed or partial pull still
    # leaves closes worth recomputing over, and before this fix a crashed
    # pull skipped both. Each also runs daily, so neither is a single point
    # of failure — unlike the six above.
    for _label, _fn in (
        # F3: fresh closes -> recompute the full-store breadth reading the
        # A3 gate prefers.
        ("breadth store", lambda: refresh_breadth_store("post-topup")),
        # Node 10: refresh the precomputed register view over the store.
        ("register precompute", lambda: run_register_precompute("post-topup")),
    ):
        _run_weekly_step(_label, _fn, _journal)

    # Completion is recorded LAST, and deliberately counts the SEQUENCE, not
    # the pull's internal result: the OHLCV top-up is incremental by nature
    # (it skips names already fresh and journals its own failures), so tying
    # the ledger to it would re-run an hour-long pull every single evening
    # whenever a handful of names failed. A block that never reaches here —
    # a crash, or a container restart mid-run — leaves no stamp, so the next
    # trading day picks it up. That is the owner's Monday rule.
    _mark_weekly_block_done()


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
    # F3: refresh the full-store breadth reading BEFORE the scans, so the A3
    # gate judges today's entries on ~2,700 names instead of the day's
    # ~50-name bucket. Isolated — a failure leaves the previous reading and
    # the gate's bucket fallback intact, and can never stop the scan.
    try:
        refresh_breadth_store("09:20")
    except Exception:
        log.exception("breadth refresh failed before daily scan")
    # OWNER FAULT REPORT 2026-08-26 ("still on sensex and all, it show
    # 7 august"): the index tail was wired WEEKLY (Friday 16:30) on a
    # Sunday — so no run had ever happened and every page read the CSV
    # frozen at its commit date. The refresh is idempotent and cheap
    # (4 public candle calls, only dates past the newest held row), so it
    # now runs DAILY here and at boot catch-up; Friday keeps its slot.
    try:
        _weekly_index_refresh()
    except Exception:
        log.exception("index refresh failed before daily scan")
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
    # OWNER ORDER 2026-08-20 ("all fresh"): with the custom 50-name universe
    # live, top up the STORE for exactly those names every scan day —
    # tokenless since F1 — so charts/triggers/news read today-fresh candles,
    # not last Friday's. Runs BEFORE the trigger layer so the layer already
    # sees the fresh bars. Isolated: a failure journals and cannot stop the
    # trigger pass.
    try:
        _daily_universe_topup()
    except Exception:
        log.exception("daily custom-universe top-up failed")
    run_trigger_layer("09:20")
    _push_phone_alerts("09:20")
    log.info("[09:20] Daily multi-mode scan complete: %s", " · ".join(summary))


def _daily_universe_topup() -> None:
    """Merge fresh daily candles into the Mongo store for the owner's custom
    universe + held names. Daily counterpart of the Friday job, bounded to
    ~50 names so it is cheap; skips names already fresh to yesterday. Only
    runs when a custom universe is set — the rotating full-NSE mode keeps
    the weekly cadence (2,700 names daily would not fit the 512 MB box)."""
    from datetime import date, datetime as _dt

    from . import db, state
    cfg = state.cfg()
    custom = tuple(getattr(cfg, "custom_universe", ()) or ())
    if not custom:
        return
    eng = state.engine()
    from trading.data import history_broker
    broker = getattr(eng, "broker", None) or history_broker()
    if broker is None or not db.is_connected():
        log.info("[daily-topup] skipped: %s",
                 "no Upstox client" if broker is None else "Mongo offline")
        return
    try:
        from trading.brokers.upstox_instruments import to_instrument_keys
        from trading.history_store import top_up_history
        names = sorted(set(custom) | set(eng.portfolio.positions.keys()))
        keys = to_instrument_keys(names)
        summ = top_up_history(
            broker, keys,
            load_doc=db.load_history_doc,
            save_doc=db.save_history_doc,
            today=date.today(),
            fresh_within_days=1,
        )
        log.info("[daily-topup] custom universe: %s", summ)
        if summ.get("failed"):
            eng.portfolio.decision_journal.append({
                "ts": _dt.utcnow().isoformat(), "action": "DATA_REFRESH_DAILY",
                "why": (f"Daily top-up for the custom universe: {summ['ok']} "
                        f"refreshed, {summ['skipped_fresh']} already fresh, "
                        f"{summ['failed']} FAILED of {summ['total']}."),
            })
            eng.portfolio.save()
    except Exception as e:
        log.exception("daily universe top-up failed: %s", e)


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

        # News runs FIRST, over the same filtered list (owner's funnel: "u
        # will run the feed that time only and it kept on that stock"). It has
        # to precede the trigger pass because nine of the owner's triggers
        # read a news flag as one of their two legs. Isolated: if the feed is
        # down those nine stay DATA_NEEDED and every other trigger is
        # unaffected — a news outage must never change a price verdict.
        news_rows: dict = {}
        try:
            news_rows = (run_news_feed(where, tickers=tickers) or {}).get(
                "rows") or {}
        except Exception:
            log.exception("news feed pass failed before trigger layer")

        # Node 8: today's option-chain snapshot (token-dependent; a skip is
        # journaled and T12W_0102 reads DATA_NEEDED). Runs before the
        # trigger pass for the same reason news does — a consumer must not
        # run before its feed.
        chain: dict = {}
        try:
            chain = run_option_chain_snapshot(where) or {}
        except Exception:
            log.exception("option chain snapshot failed before trigger layer")
        # Owner order 2026-08-24: daily Upstox FII/DII pull (same auth
        # story as the chain; once-per-day guard inside; failure isolated).
        try:
            run_market_fii_snapshot(where)
        except Exception:
            log.exception("market FII/DII snapshot failed before trigger layer")
        # External panels (sector/RS, delivery, F&O OI, deals) — ONE batch
        # load for the whole run: the delivery file is 3.5M rows and a
        # per-stock pass would be 80 passes. A failure here leaves ext None,
        # which keeps those triggers at DATA_NEEDED instead of breaking the
        # layer (owner order 2026-08-13 wired them; they must never be able to
        # take the job down).
        ext_batch = None
        try:
            from trading import external_panels
            ext_batch = external_panels.prepare(tickers, news=news_rows,
                                                chain=chain)
        except Exception as e:
            log.warning("[triggers] external panels unavailable: %s", e)

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
            view = stock_view(df, asof=asof,
                              ext=(ext_batch.for_ticker(t) if ext_batch
                                   else None))
            gates_passed = [g["id"] for g in view["gates"]
                            if g["status"] == "PASS"]
            # The owner's selection sequence (their named gap, 2026-08-12
            # audit Node 1): one rung on the quiet→compressing→accumulating→
            # reclaim→entry-window→post-entry-watch ladder, derived from the
            # numbers just computed. Watchlist-only, like everything here.
            from trading.selection_sequence import stage_for
            stage = stage_for(view["family_counts"], gates_passed,
                              held=t in p.positions)
            rows[t] = {
                "asof": view["asof"],
                "family_counts": view["family_counts"],
                "scores": view["scores"],
                "data_quality": view["data_quality"],
                "gates_passed": gates_passed,
                "fired": [f["id"] for f in view["fired"]],
                "watch": view["watch"],
                "stage": stage,
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


def run_news_feed(where: str = "manual", limit: int = 60,
                  tickers: Optional[list[str]] = None) -> dict:
    """📰 The owner's news pass — their design, on their order.

    Owner 2026-08-13: "when i will fetch new universe for stocks, u will run
    the feed that time only and it kept on that stock ... if it value it
    trigger or it pass", then "no block just show".

    So this runs ONCE per daily cycle (called at the START of the trigger
    layer, which follows the 09:20 scan), over the names the scan already
    filtered plus everything held — never on a separate schedule, and never
    over the whole universe. The verdict is stored per stock in
    `portfolio.news_watch` and shown; it blocks nothing, sizes nothing and
    cannot place an order.

    It runs BEFORE the trigger pass because nine of the owner's own triggers
    take a news flag as one of their two legs (T12W_0097/0099/0103/0104/0106/
    0107/0108/0109/0110). `tickers` lets the trigger layer hand over the exact
    list it is about to score, so the two passes can never disagree about
    which names were looked at.

    A crash here can no more break the scan than the trigger layer can.
    """
    from datetime import datetime as _dt
    from . import state
    p = state.portfolio()
    try:
        from trading import news_feed
        if tickers is None:
            tickers = []
            seen: set[str] = set()
            # held first — the owner's own money is the priority read
            for t in list(p.positions.keys()) \
                    + [pk.get("ticker") for pk in (p.last_scan_picks or [])] \
                    + list(((p.scan_matrix or {}).get("rows") or {}).keys()):
                if t and t not in seen:
                    seen.add(t)
                    tickers.append(t)
        if not tickers:
            return {}
        readings = news_feed.run(tickers, limit=limit)
        rows = {t: r.to_dict() for t, r in readings.items()}
        triggered = sorted(t for t, r in readings.items()
                           if r.verdict == "TRIGGER")
        unknown = sorted(t for t, r in readings.items() if r.fetch_failed)
        prev = set((p.news_watch or {}).get("triggered") or [])
        p.news_watch = {
            "ts": _dt.utcnow().isoformat(),
            "rows": rows,
            "triggered": triggered,
            "fetch_failed": unknown,
            "scanned": len(rows),
            "lookback_days": news_feed.LOOKBACK_DAYS,
            "note": ("display only — a news verdict never blocks a buy or "
                     "moves a stop; news cannot be calibrated (no archive)"),
        }
        for t in triggered:
            if t not in prev:                  # journal each name once
                r = rows[t]
                p.decision_journal.append({
                    "ts": _dt.utcnow().isoformat(),
                    "action": "NEWS_TRIGGER",
                    "ticker": t,
                    "tags": r["tags"],
                    "why": (f"News verdict TRIGGER: {r['why']}"),
                })
        if unknown:
            p.decision_journal.append({
                "ts": _dt.utcnow().isoformat(),
                "action": "NEWS_FETCH_FAILED",
                "why": (f"{len(unknown)} name(s) could not be fetched "
                        f"({', '.join(unknown[:6])}) — recorded as UNKNOWN, "
                        "never as 'no news'."),
            })
        p.save()
        log.info("[news:%s] %d read, %d TRIGGER, %d unfetchable",
                 where, len(rows), len(triggered), len(unknown))
        return p.news_watch
    except Exception as e:
        log.exception("News feed pass failed: %s", e)
        try:
            p.decision_journal.append({
                "ts": _dt.utcnow().isoformat(),
                "action": "NEWS_FEED_FAILED",
                "err": f"{type(e).__name__}: {str(e)[:120]}",
                "why": "News pass crashed — scan and trigger layer unaffected.",
            })
            p.save()
        except Exception:
            pass
        return {}
