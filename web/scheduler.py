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
import time
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

_SCHED = None


def _job_end_mask() -> int:
    from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
    return EVENT_JOB_EXECUTED | EVENT_JOB_ERROR


def _release_after_job(event) -> None:
    """Scheduler listener: give memory back after every job. Never raises."""
    try:
        from .memory_relief import release
        release(f"job {getattr(event, 'job_id', '?')}")
    except Exception:                                      # noqa: BLE001
        pass


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
    # 2026-10-05 (owner: "go with memory fix"): every job hands its freed
    # memory back to the OS when it finishes — success or crash. Without
    # this the 512 MiB box ratcheted up job after job (353 MiB held after
    # the 09:20 rotation, 494 after 14:30) and Render killed it at 10:09 IST.
    _SCHED.add_listener(_release_after_job, _job_end_mask())

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
        # 08:30 IST: top up the price store BEFORE the 09:20 scan (owner
        # order 2026-10-02, audit item 8) — ~35 min for ~1,200 names, so it
        # finishes before the scan reads the store.
        _SCHED.add_job(
            _morning_topup_job, "cron",
            day_of_week="mon-fri", hour=8, minute=30,
            id="morning_topup",
        )
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
        # Afternoon: a second pass of all five categories (owner order
        # 2026-10-04, "14:30 scan as well"); each spends only its own share.
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
        # OWNER ORDER 2026-09-10: price + exits + rotation every 30 minutes
        # inside the session. `minute="*/30"` over hours 9-15 gives 09:00,
        # 09:30, ... 15:30; the pass no-ops before 09:15 via is_market_open.
        _mins = 30
        try:
            from . import state as _st
            _mins = max(5, int(getattr(_st.cfg(), "intraday_refresh_minutes", 30)))
        except Exception:
            pass
        _SCHED.add_job(
            _session_refresh, "cron",
            day_of_week="mon-fri", hour="9-15", minute=f"*/{_mins}",
            id="session_refresh", replace_existing=True,
        )
        # Node 10: nightly register precompute over the full store — after
        # the close and the 15:35 refresh, when the box is otherwise idle.
        _SCHED.add_job(
            run_register_precompute, "cron",
            day_of_week="mon-fri", hour=16, minute=45,
            id="register_precompute",
        )
        # Item 19 (owner order 2026-10-21): the valuation records the
        # engine's exit target reads — after the register precompute, on a
        # quiet box. The 09:20 chain gets NO new load: a buy reads last
        # evening's record (the study used month-old valuations).
        _SCHED.add_job(
            _valuation_precompute_job, "cron",
            day_of_week="mon-fri", hour=16, minute=50,
            id="valuation_precompute",
        )

    if daily and os.environ.get("SB_DISABLE_CATCHUP") != "1":
        # Deploy-restart healing: if the process boots after a missed 09:20
        # (deploys kill in-flight jobs), run the day's scan once, 2 min in.
        #
        # 2026-09-28 (owner's Render log: 'Run time of job ... was missed by
        # 5:28:00' on EVERY boot): the run_date used to be a NAIVE
        # datetime.now(), which on Render is the UTC clock; APScheduler reads
        # a naive run_date in the scheduler's own timezone (IST), so the job
        # was born 5h28m overdue, past the 300 s grace, and DROPPED — this
        # catch-up had never once run on Render. Same clock class as the
        # 2026-09-16 exit drain. The clock must be IST-AWARE; a test pins it.
        from datetime import timedelta as _td
        _SCHED.add_job(lambda: catchup_scan_if_missed("startup"), "date",
                       run_date=startup_catchup_run_date(ist, _td(seconds=120)),
                       id="startup_catchup")
        # Item 19: stale valuation records are rebuilt after a boot — only
        # in the evening (>= 15:45 IST) or on a holiday, on a quiet box.
        _SCHED.add_job(lambda: valuation_catchup_if_stale("catchup"), "date",
                       run_date=startup_catchup_run_date(ist, _td(seconds=300)),
                       id="valuation_catchup")

    _SCHED.start()
    log.info("Schedulers started (intraday=%s, daily=%s)", intraday, daily)


def startup_catchup_run_date(tz, delay):
    """The boot catch-up's fire time: `delay` from NOW on the scheduler's
    OWN clock (tz-aware). Never a naive datetime — APScheduler reads a naive
    run_date in the scheduler's timezone, and on Render the naive clock is
    UTC, so the job would be born 5h28m in the past and discarded."""
    from datetime import datetime as _dt
    return _dt.now(tz) + delay


def catchup_scan_if_missed(reason: str = "catchup") -> bool:
    """If today's multi-mode scan never completed (dead Upstox token at
    09:20, deploy restart mid-run, crash), run it now in a background
    thread. Fired at startup and on every Upstox connect. Trading days
    only; no-op when today's scan already ran."""
    import os
    import threading
    from datetime import datetime as _dt

    from trading.calendars import is_market_open, is_trading_day

    if (not daily_enabled()
            or os.environ.get("SB_DISABLE_CATCHUP") == "1"
            or not is_trading_day()):
        return False
    # OWNER ORDER 2026-09-10: "trade are prohibited from now after market
    # hours". THIS was the hole. The guard above reads the DAY and never the
    # CLOCK, and this job fires ~2 minutes after EVERY boot — so a Render
    # restart at 21:00 on a Tuesday, or the owner logging in at night, ran a
    # full multi-mode scan that could open positions at prices nobody could
    # have traded. The scan still RUNS outside hours (they asked to keep
    # reviewing buy/watch/block lists); engine.scan simply opens nothing and
    # journals MARKET_CLOSED_NO_ENTRY. This line makes the intent explicit
    # and keeps the log honest about which it was.
    if not is_market_open():
        log.info("[catchup:%s] market closed — scan will score and show, "
                 "but open nothing (owner order 2026-09-10)", reason)
    from . import state
    p = state.portfolio()
    last = (p.last_scan or "")[:10]
    last_live = (getattr(p, "last_scan_live", None) or "")[:10]
    today = _dt.utcnow().date().isoformat()
    # AUDIT S-5 (2026-10-02): a look-only scan before 09:15 (a boot or a
    # login) used to stamp the day as done, so when the app then slept
    # through 09:20 the catch-up skipped and no scan that could BUY ran that
    # day. Done = a scan ran inside market hours today. Outside hours, any
    # scan today is enough (it only scores and shows; re-running it on every
    # boot would cost memory for nothing).
    if last_live >= today:
        return False
    if not is_market_open() and last >= today:
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
        # Owner order 2026-10-21: held names absorb today's split/bonus
        # BEFORE the scan's exit pass can read a halved quote (BLSE).
        apply_corporate_actions_job("catchup")
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
    # Owner order 2026-10-21 (the BLSE split): the keys the held names had
    # BEFORE today's master lands, so a changed ISIN is seen and journaled.
    before_keys = _held_instrument_keys(p)
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
    # Owner order 2026-10-21: an ISIN change on a held name (a face-value
    # change re-lists the share under a new key; Upstox refuses the old one)
    # is journaled, the fresh key preferred everywhere, the name's history
    # re-fetched and its corporate actions fetched now; then today's NSE
    # corporate-action rows for every held name land in Mongo for the
    # ex-date adjuster. Isolated: a failure here never costs the universe.
    try:
        changed = _note_isin_changes(p, before_keys)
        _corporate_actions_fetch_job("08:50", force=changed)
        _warn_isin_change_without_record(p, changed)
    except Exception:
        log.exception("[08:50] corporate-action fetch failed")
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


# ---------------------------------------------------------------------------
# Corporate actions on held names (owner order 2026-10-21, the BLSE split)
# ---------------------------------------------------------------------------
# Tickers whose OHLCV doc must be REPLACED by a fresh full fetch: a split /
# bonus the adjuster applied, or an ISIN change the 08:50 refresh saw. The one
# documented exception to merge-never-replace — Upstox back-adjusts the whole
# series, so merging new adjusted bars onto old raw ones leaves a 2× step.
_HISTORY_REFETCH_DUE: set = set()
CA_FETCH_PACE_S = 0.5
# After this many NSE failures in a row the rest of the held names are not
# asked this run (a blocked or hanging NSE would otherwise cost ~15 s per
# name, ≤ 60 names, inside the 08:50 / catch-up chain). They fall back to
# the store + committed file and are named in the journal.
CA_FETCH_MAX_FAILS = 3
HISTORY_REFETCH_YEARS = 12
# The IST day the held-name NSE fetch last completed in THIS process; the
# adjuster job runs it first when the 08:50 slot was slept through.
_CA_FETCH_DAY: str = ""


def _ca_pace_s() -> float:
    """SB_CA_FETCH_PACE_S (tests set 0), default CA_FETCH_PACE_S."""
    import os
    try:
        return float(os.environ.get("SB_CA_FETCH_PACE_S", CA_FETCH_PACE_S))
    except ValueError:
        return CA_FETCH_PACE_S


def _ist_today_iso() -> str:
    from datetime import datetime as _dt, timedelta as _td
    return (_dt.utcnow() + _td(hours=5, minutes=30)).date().isoformat()


def _held_instrument_keys(p) -> dict:
    """{ticker: instrument_key} for the held names from the universe FILE
    (the thing the 08:50 refresh rewrites), {} when unreadable."""
    try:
        from trading.universe_refresh import default_universe_path, read_existing
        rows = read_existing(default_universe_path())
        return {t: (rows.get(t) or {}).get("instrument_key", "")
                for t in p.positions.keys()}
    except Exception as e:                      # noqa: BLE001
        log.warning("held instrument keys unreadable: %s", e)
        return {}


def _note_isin_changes(p, before_keys: dict) -> list:
    """Compare the held names' keys before/after the refresh. A change →
    journal INSTRUMENT_ISIN_CHANGED, prefer the fresh key everywhere, queue
    the history re-fetch. Returns the changed tickers."""
    from datetime import datetime as _dt
    if not before_keys:
        return []
    after = _held_instrument_keys(p)
    changed = []
    for t, old in before_keys.items():
        new = after.get(t, "")
        if old and new and old != new:
            changed.append(t)
    if not changed:
        return []
    try:
        from trading.brokers.upstox_instruments import prefer_keys
        prefer_keys({t: after[t] for t in changed})
    except Exception:                           # noqa: BLE001
        log.exception("prefer_keys failed")
    # A restart puts the repo copy of the universe file (old key) back, so
    # the next 08:50 sees the same change again: journal + re-fetch once per
    # (ticker, new key), the key preference above every time.
    tail = p.decision_journal[-2000:]
    seen = {(e.get("ticker"), e.get("new_key")) for e in tail
            if e.get("action") == "INSTRUMENT_ISIN_CHANGED"}
    refetched = {(e.get("ticker"), e.get("key")) for e in tail
                 if e.get("action") == "HISTORY_REFETCHED"}
    for t in changed:
        # the re-fetch is queued until one has landed on the NEW key (a
        # restart between the journal line and the re-fetch must not lose it)
        if (t, after.get(t)) not in refetched:
            _HISTORY_REFETCH_DUE.add(t)
    changed = [t for t in changed if (t, after.get(t)) not in seen]
    for t in changed:
        p.decision_journal.append({
            "ts": _dt.utcnow().isoformat(), "action": "INSTRUMENT_ISIN_CHANGED",
            "ticker": t, "old_key": before_keys.get(t), "new_key": after.get(t),
            "why": ("Upstox now lists this held name under a new ISIN (a face-value "
                    "change re-lists the share). The fresh key is used from now; the "
                    "stored history is re-fetched; its corporate actions are fetched "
                    "now so the ex-date adjuster can apply the split/bonus."),
        })
    try:
        p.save()
    except Exception:                           # noqa: BLE001
        pass
    return changed


def _warn_isin_change_without_record(p, changed: list) -> list:
    """A held name re-listed under a new ISIN is a face-value change; when no
    split/bonus row for it is reachable (NSE refused, the committed file is
    older), the ex-date adjuster cannot restate it and the next exit pass
    reads a halved quote as a -50% crash — the BLSE fault. Journaled loudly
    (CORPORATE_ACTION_RECORD_MISSING); nothing is sold or changed here."""
    from datetime import date as _date, datetime as _dt, timedelta as _td

    from trading import corporate_actions as tca
    if not changed:
        return []
    since = (_date.today() - _td(days=21)).isoformat()
    missing = []
    for t in changed:
        try:
            acts = tca.actions_for(t)
        except Exception:                       # noqa: BLE001
            acts = []
        if any(a.get("ratio") and a["kind"].split("+")[0] in tca.APPLIED_KINDS
               and a["ex_date"] >= since for a in acts):
            continue
        missing.append(t)
        p.decision_journal.append({
            "ts": _dt.utcnow().isoformat(), "action": "CORPORATE_ACTION_RECORD_MISSING",
            "ticker": t,
            "why": ("This HELD name was re-listed under a new ISIN (a face-value change) but "
                    "no split/bonus record is reachable — NSE's corporate-action API did not "
                    "answer and the committed file is older. The ex-date adjuster cannot "
                    "restate the position, so if the quote has halved the stop will read it "
                    "as a crash. Check the position by hand on /positions (DATA_NEEDED)."),
        })
    if missing:
        try:
            p.save()
        except Exception:                       # noqa: BLE001
            pass
    return missing


def _corporate_actions_fetch_job(where: str = "manual", force=None,
                                 fetch=None, symbols=None) -> dict:
    """Fetch NSE's corporate-action rows for every HELD name (≤ ~60, paced),
    store them in Mongo `corporate_actions` (keyed symbol|ex_date|subject).
    Journals CORPORATE_ACTIONS_FETCHED only when a NEW split/bonus/rights row
    landed or a fetch failed; a quiet day costs no journal line. Mongo down
    = rows kept in process only (the engine falls back to the committed
    file). `force` names tickers fetched even when today's fetch already ran."""
    import time as _time
    from datetime import datetime as _dt

    from trading import corporate_actions as tca
    from . import db, state
    p = state.portfolio()
    held = sorted(set(symbols or []) | set(force or []) | set(p.positions.keys()))
    if not held:
        return {"fetched": 0, "new": 0, "failed": [], "skipped": "no held names"}
    fetch = fetch or tca.fetch_nse_actions
    live = fetch is tca.fetch_nse_actions        # pace the real NSE calls only
    pace = _ca_pace_s()
    new_rows, failed, skipped, fetched, n_new = [], [], [], 0, 0
    fails_in_row = 0
    for i, t in enumerate(held):
        sym = tca.bare(t)
        if fails_in_row >= CA_FETCH_MAX_FAILS:
            skipped.append(sym)                  # NSE not answering: stop asking
            continue
        rows = fetch(sym)
        if rows is None:
            failed.append(sym)
            fails_in_row += 1
        else:
            fails_in_row = 0
            fetched += 1
            # kept in process too: a Mongo outage must not hide the split
            tca.remember_rows(sym, rows)
            n = db.save_corporate_actions(rows) if db.is_connected() else 0
            if n:
                n_new += n
                new_rows += tca.parse_rows(rows, source="nse_api")
        if live and i + 1 < len(held) and pace > 0:
            _time.sleep(pace)
    out = {"fetched": fetched, "new": n_new, "failed": failed, "skipped": skipped,
           "names": held}
    if symbols is None:
        # one attempt a day over the whole held list: a blocked NSE must not
        # cost 45 s on every 30-minute exit pass (the 08:50 slot and an ISIN
        # change still ask again)
        global _CA_FETCH_DAY
        _CA_FETCH_DAY = _ist_today_iso()
    if new_rows or failed or skipped:
        p.decision_journal.append({
            "ts": _dt.utcnow().isoformat(), "action": "CORPORATE_ACTIONS_FETCHED",
            "where": where, "fetched": fetched, "failed": failed, "skipped": skipped,
            "new": [{"symbol": c["symbol"], "ex_date": c["ex_date"], "kind": c["kind"],
                     "ratio": c["ratio"], "subject": c["subject"]} for c in new_rows[:40]],
            "why": (f"NSE corporate actions for {fetched} of {len(held)} held names"
                    + (f"; {n_new} new row(s) stored, split/bonus/rights on file for "
                       f"those names: {len(new_rows)}" if n_new else "")
                    + (f"; FAILED for {', '.join(failed[:10])}" if failed else "")
                    + (f"; NOT ASKED after {CA_FETCH_MAX_FAILS} failures in a row: "
                       f"{', '.join(skipped[:10])}" if skipped else "")
                    + ("; the store and the committed file stand in" if failed or skipped
                       else "") + "."),
        })
        try:
            p.save()
        except Exception:                       # noqa: BLE001
            pass
    log.info("[%s] corporate actions: %s", where, out)
    return out


def _refetch_history(ticker: str, where: str, broker=None, today=None) -> bool:
    """REPLACE the stored OHLCV doc of one name with a fresh full (adjusted)
    fetch on its current key. Journals HISTORY_REFETCHED / _FAILED."""
    from datetime import date, datetime as _dt

    from . import db, state
    p = state.portfolio()
    eng = state.engine()
    from trading.data import history_broker
    broker = broker or getattr(eng, "broker", None) or history_broker()
    try:
        if broker is None:
            raise RuntimeError("no Upstox client")
        from trading.brokers.upstox_instruments import to_instrument_key
        from trading.history_store import df_to_records, download_ticker_history
        key = to_instrument_key(ticker)
        if not key:
            raise RuntimeError("no instrument key")
        if not db.is_connected():
            # save_history_doc is silent when Mongo is down — never journal a
            # replacement that did not land
            raise RuntimeError("Mongo offline — the stored history cannot be replaced")
        df = download_ticker_history(broker, key, HISTORY_REFETCH_YEARS, today or date.today())
        if df is None or df.empty:
            raise RuntimeError("Upstox returned no candles")
        doc = df_to_records(ticker, df)
        db.save_history_doc(doc)                # replace_one: the whole series
        p.decision_journal.append({
            "ts": _dt.utcnow().isoformat(), "action": "HISTORY_REFETCHED",
            "ticker": ticker, "key": key, "bars": doc["n"], "start": doc["start"],
            "end": doc["end"], "where": where,
            "why": ("Stored history REPLACED by a fresh full fetch on the current key "
                    "(Upstox back-adjusts the series for the split/bonus; merging new "
                    "adjusted bars onto old raw ones would leave a step). The one "
                    "documented exception to merge-never-replace."),
        })
        _HISTORY_REFETCH_DUE.discard(ticker)
        return True
    except Exception as e:                      # noqa: BLE001
        log.warning("history re-fetch failed for %s: %s", ticker, e)
        # retried every exit pass until it lands; journaled once a day a name
        day = _dt.utcnow().date().isoformat()
        if not any(x.get("action") == "HISTORY_REFETCH_FAILED" and x.get("ticker") == ticker
                   and str(x.get("ts", "")).startswith(day)
                   for x in p.decision_journal[-300:]):
            p.decision_journal.append({
                "ts": _dt.utcnow().isoformat(), "action": "HISTORY_REFETCH_FAILED",
                "ticker": ticker, "err": f"{type(e).__name__}: {str(e)[:120]}", "where": where,
                "why": "Stored history NOT replaced; retried at the next run.",
            })
        return False


REFETCH_OWED_DAYS = 10


def _refetch_owed_from_journal(p) -> set:
    """Tickers the journal shows adjusted (CORPORATE_ACTION_APPLIED) with no
    HISTORY_REFETCHED after it, within REFETCH_OWED_DAYS. The in-process
    queues die with a restart; the journal does not, so an adjustment made
    just before a restart still gets its history replaced (a stored series
    with old raw bars under new adjusted ones reads as a -50% crash to the
    momentum score and the danger tripwires)."""
    from datetime import datetime as _dt, timedelta as _td
    cutoff = (_dt.utcnow() - _td(days=REFETCH_OWED_DAYS)).isoformat()
    last_applied: dict = {}
    last_refetch: dict = {}
    for e in (p.decision_journal or [])[-3000:]:
        act = e.get("action")
        ts = str(e.get("ts") or "")
        if act == "CORPORATE_ACTION_APPLIED" and ts >= cutoff:
            last_applied[e.get("ticker")] = max(ts, last_applied.get(e.get("ticker"), ""))
        elif act == "HISTORY_REFETCHED":
            last_refetch[e.get("ticker")] = max(ts, last_refetch.get(e.get("ticker"), ""))
    return {t for t, ts in last_applied.items() if t and last_refetch.get(t, "") < ts}


def apply_corporate_actions_job(where: str = "manual", today=None) -> list:
    """The ex-date adjuster, run BEFORE any exit pass: engine.apply_corporate_
    actions (qty × ratio, entry ÷ ratio, levels ÷ ratio, journaled, no sale),
    then the OHLCV re-fetch for every adjusted name and every ISIN change
    the 08:50 refresh queued. Never raises — a fault here must not stop the
    scan or the exit pass that follows."""
    from . import state
    # The 08:50 slot is often slept through (free plan): the held names'
    # NSE rows are fetched first, once a day per process, so the adjuster
    # never runs on yesterday's knowledge on an ex-date.
    import os
    try:
        if (os.environ.get("SB_CA_FETCH_ON_APPLY", "1") != "0"
                and state.portfolio().positions and _CA_FETCH_DAY != _ist_today_iso()):
            _corporate_actions_fetch_job(where)
    except Exception:                           # noqa: BLE001
        log.exception("[%s] corporate-action fetch before the adjuster failed", where)
    try:
        eng = state.engine()
        applied = eng.apply_corporate_actions(today=today)
    except Exception as e:                      # noqa: BLE001
        log.exception("[%s] corporate-action apply failed: %s", where, e)
        applied = []
    # names the engine adjusted on its own (top of refresh()/scan()) since
    # the last run also need their history replaced
    try:
        from trading.engine import CA_REFETCH_DUE
        engine_due = set()
        while CA_REFETCH_DUE:                   # pop, not copy+clear: an add from
            engine_due.add(CA_REFETCH_DUE.pop())  # another thread is never lost
    except Exception:                           # noqa: BLE001
        engine_due = set()
    due = ({a["ticker"] for a in applied} | set(_HISTORY_REFETCH_DUE) | engine_due
           | _refetch_owed_from_journal(state.portfolio()))
    for t in sorted(due):
        try:
            if not _refetch_history(t, where, today=today):
                _HISTORY_REFETCH_DUE.add(t)     # retried at the next run
        except Exception:                       # noqa: BLE001
            _HISTORY_REFETCH_DUE.add(t)
            log.exception("history re-fetch crashed for %s", t)
    if applied or due:
        try:
            state.portfolio().save()
        except Exception:                       # noqa: BLE001
            pass
    return applied


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
    from trading.modes import holding_mode_lock
    try:
        with holding_mode_lock():           # audit S-6
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

def _deployment_topup_scan() -> None:
    """14:30 IST: a second pass of ALL FIVE categories (owner order
    2026-10-04, "14:30 scan as well").

    Before this it scanned ONE category — whatever `cfg.mode` held after the
    09:20 rotation restored it, normally momentum — and skipped entirely
    once 60% of the STARTING capital was deployed, so intraday, short, swing
    and positional got exactly one chance a day. Now it runs the same
    rotation as 09:20 (`_run_category_rotation`), and the old 60% skip is
    gone: since the same day's order each category spends only its own cash
    share (trading/category_budget.py), so `risk.can_open` already stops a
    category that has spent its share, and a book-wide 60% line would only
    stop the categories that still have room."""
    from trading.calendars import is_trading_day
    if not is_trading_day():
        return
    from . import state
    if _degraded_halt("14:30"):
        return
    if not _ROTATION_LOCK.acquire(blocking=False):
        log.warning("[14:30] a category rotation is still running — skipped")
        _check_pending_orders("top-up-skip")
        return
    try:
        # Owner order 2026-10-21 (BLSE): a process woken after the morning
        # (free plan) meets the ex-date here first — absorb the split and
        # replace the history before the rotation's exit pass reads either.
        apply_corporate_actions_job("14:30")
        summary = _run_category_rotation(state.engine(), state.cfg(), "14:30")
        log.info("[14:30] Top-up rotation complete: %s", " · ".join(summary))
    finally:
        _ROTATION_LOCK.release()
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


#: Item 19 (2026-10-21): the register precompute and the valuation
#: precompute never run at the same time. On the live box the register
#: precompute peaks ~350 MiB between 16:45 and 16:55; the valuation job
#: starts at 16:50 and waits for it instead of stacking on top.
_PRECOMPUTE_LOCK = threading.Lock()
#: The register precompute waits at most this long for the lock, then runs
#: anyway (the old behaviour) — a hung holder must never stall the weekly
#: block behind it.
REGISTER_WAIT_S = 900.0


def run_register_precompute(where: str = "manual") -> dict:
    """The register precompute (see _register_precompute_body), one at a
    time with the valuation precompute (item 19)."""
    got = _PRECOMPUTE_LOCK.acquire(timeout=REGISTER_WAIT_S)
    if not got:
        log.warning("[register:%s] the shared precompute lock was held for "
                    "%.0f s — running anyway", where, REGISTER_WAIT_S)
    try:
        return _register_precompute_body(where)
    finally:
        if got:
            _PRECOMPUTE_LOCK.release()


def _register_precompute_body(where: str = "manual") -> dict:
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
        from trading.universes import fund_unit_tickers, get_nifty_200_universe
        # 2026-10-16: fund units (ETFs / liquid funds) are not company
        # shares; they leave the market panel as they left breadth.
        funds = fund_unit_tickers()
        panel: dict = {}
        newest = ""
        for doc in db.history_tails(340):
            candles = doc.get("candles") or []
            t = doc.get("ticker") or ""
            if not t or len(candles) < 60 or t in funds:
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


VALUATION_PRECOMPUTE_MAX = 120
VALUATION_PRECOMPUTE_BUDGET_S_DEFAULT = 120.0
VALUATION_PRECOMPUTE_CHUNK = 25        # names per price query (the Den's row cap)
VALUATION_CATCHUP_AFTER_IST = (15, 45)  # the boot catch-up never runs before this
#: How long the 16:50 job / the catch-up wait for a running register
#: precompute (it ran ~10 min on the live box). The owner's button never waits.
VALUATION_WAIT_FOR_REGISTER_S = 1200.0
#: How long it waits for a Den §12 open that is computing rows (its own
#: budget is 20 s).
VALUATION_WAIT_FOR_DEN_S = 30.0
_VALUATION_LOCK = threading.Lock()


def valuation_precompute_budget_s() -> float:
    """Seconds the valuation precompute may spend (env
    SB_VALUATION_PRECOMPUTE_BUDGET_S; <= 0 = no budget)."""
    try:
        return float(os.environ.get("SB_VALUATION_PRECOMPUTE_BUDGET_S",
                                    VALUATION_PRECOMPUTE_BUDGET_S_DEFAULT))
    except ValueError:
        return VALUATION_PRECOMPUTE_BUDGET_S_DEFAULT


def _valuation_busy() -> tuple[str, str]:
    """(kind, why) the precompute must not run now; ("", "") = free."""
    if _ROTATION_LOCK.locked():
        return "busy", "a scan rotation is running"
    from . import valuation_chain_view as cv
    ceiling = cv.rss_ceiling_mb()
    if ceiling > 0:
        try:
            from .memory_relief import rss_mb
            r = rss_mb()
        except Exception:                               # noqa: BLE001
            r = None
        if r is not None and r >= ceiling:
            return "memory", (f"the app is using {r:.0f} MiB (the valuation "
                              f"precompute runs below {ceiling:.0f})")
    return "", ""


def run_valuation_precompute(where: str = "manual") -> dict:
    """ITEM 19 (owner order 2026-10-21, "Wire A3" + "Exit as tested"): the
    valuation RECORDS the engine's exit target reads (trading/
    valuation_exit.py), one per name in scope — held names, the momentum
    shortlist by score, the last scan picks, the Den §11 targets (capped
    VALUATION_PRECOMPUTE_MAX, held first).

    Each record is the Den §12 chain row (valuation_chain_view.row_for, the
    stored peer base, closes in one query per chunk, DEFAULT assumptions —
    the tested rule) reduced to the RECORD shape, plus beta on the Sensex
    (252 sessions) and the sector index. A time budget stops new names
    (they keep their previous record, or none); nothing runs while a scan
    rotation holds the lock or the process is above the Den's memory
    ceiling (checked again before every chunk of names) — journaled
    VALUATION_PRECOMPUTE_SKIPPED with the reason. It waits for a running
    register precompute (the owner's button does not wait) and for a Den
    §12 open that is computing, so the evening jobs never stack in memory.

    Stored in settings doc "valuation_records" + the in-process copy."""
    from datetime import datetime as _dt

    from . import state
    from . import valuation_chain_view as cv

    p = state.portfolio()
    cfg = state.cfg()

    def _journal(action: str, fields: dict) -> None:
        try:
            p.decision_journal.append({"ts": _dt.utcnow().isoformat(),
                                       "action": action, **fields})
            p.save()
        except Exception:                               # noqa: BLE001
            pass

    def _skip(kind: str, why: str) -> dict:
        _journal("VALUATION_PRECOMPUTE_SKIPPED", {
            "where": where, "reason": kind,
            "why": f"Valuation records not rebuilt: {why}. The engine keeps "
                   "reading the records it has (k x ATR where none qualifies)."})
        return {"status": "skipped", "reason": kind, "why": why}

    kind, why = _valuation_busy()
    if kind:
        return _skip(kind, why)
    if not _VALUATION_LOCK.acquire(blocking=False):
        return {"status": "skipped", "reason": "running",
                "why": "a valuation precompute is already running"}
    held: list = []
    try:
        # never at the same time as the register precompute (16:45) or a
        # Den §12 open that is computing — the three would stack in memory
        wait = 0.0 if where == "owner-button" else VALUATION_WAIT_FOR_REGISTER_S
        got = (_PRECOMPUTE_LOCK.acquire(timeout=wait) if wait > 0
               else _PRECOMPUTE_LOCK.acquire(blocking=False))
        if not got:
            return _skip("register", "the register precompute is still running")
        held.append(_PRECOMPUTE_LOCK)
        if not cv._SECTION_LOCK.acquire(timeout=VALUATION_WAIT_FOR_DEN_S):
            return _skip("den", "a Den §12 open is computing valuation rows")
        held.append(cv._SECTION_LOCK)
        kind, why = _valuation_busy()          # again, after any wait
        if kind:
            return _skip(kind, why)
        return _valuation_precompute_body(where, p, cfg, _journal)
    finally:
        for lock in reversed(held):
            lock.release()
        _VALUATION_LOCK.release()
        try:
            from .memory_relief import release
            release("valuation precompute")
        except Exception:                               # noqa: BLE001
            pass


def _valuation_precompute_body(where: str, p, cfg, _journal) -> dict:
    """The records loop (run_valuation_precompute holds the locks)."""
    import time as _time
    from datetime import datetime as _dt

    from . import valuation_chain_view as cv
    from . import valuation_exit_feed as feed

    t0 = _time.monotonic()
    try:
        names, cut = feed.precompute_scope(p, VALUATION_PRECOMPUTE_MAX)
        prev_rows = dict(feed.current_doc().get("rows") or {})
        budget = valuation_precompute_budget_s()
        ctx = cv._Render(budget, t0)
        prev_ctx = getattr(cv._RENDER, "ctx", None)
        cv._RENDER.ctx = ctx
        rows: dict = {}
        kept: list = []
        failed: list = []
        stopped = ""

        def _keep(t: str) -> None:
            if t in prev_rows:
                rows[t] = prev_rows[t]
            kept.append(t)
        try:
            sensex = cv._index_series("sensex_close")
            for i in range(0, len(names), VALUATION_PRECOMPUTE_CHUNK):
                chunk = names[i:i + VALUATION_PRECOMPUTE_CHUNK]
                if not stopped and not ctx.budget_spent():
                    # a rotation that starts, or memory that climbs, while
                    # this runs stops it at the next chunk
                    kind, why = _valuation_busy()
                    if kind:
                        stopped = why
                pre = {} if (stopped or ctx.budget_spent()) else cv._prefetch_closes(chunk)
                for t in chunk:
                    if not stopped and ctx.budget_spent():
                        stopped = f"the {budget:g} s budget"
                    if stopped:
                        _keep(t)
                        continue
                    if t in pre:
                        closes, src = pre[t], "store"
                    else:
                        closes, src = cv._zip_close_series(t), "repo copy"
                    try:
                        rec = cv.row_for(t, cfg, p, closes=closes, price_source=src)
                    except cv._BudgetSpent:
                        stopped = stopped or f"the {budget:g} s budget"
                        _keep(t)
                        continue
                    except Exception as e:              # noqa: BLE001
                        log.warning("valuation precompute: %s failed: %s", t, e)
                        failed.append(t)
                        continue
                    rows[t] = feed.record_from_row(t, rec, closes, sensex, cfg, where)
                pre = None
                try:
                    from .memory_relief import release
                    release("valuation precompute")
                except Exception:                       # noqa: BLE001
                    pass
        finally:
            cv._RENDER.ctx = prev_ctx
        judged = sorted(t for t, r in rows.items()
                        if r.get("verdict") in ("attractive", "watch")
                        and r.get("range_status") == "OK")
        asofs = [r.get("asof") for r in rows.values() if r.get("asof")]
        elapsed = round(_time.monotonic() - t0, 1)
        doc = {"rows": rows, "built": _dt.utcnow().isoformat(),
               "asof": max(asofs) if asofs else "", "n": len(rows),
               "n_judged": len(judged), "where": where, "elapsed_s": elapsed,
               "stopped": stopped,
               "skipped": {"budget": len(kept), "failed": len(failed),
                           "cap": len(cut)}}
        stored = feed.store(doc)
        _journal("VALUATION_PRECOMPUTE", {
            "where": where, "n": len(rows), "n_judged": len(judged),
            "skipped": doc["skipped"], "elapsed_s": elapsed,
            "stopped": stopped or None, "stored": stored,
            "why": (f"Valuation records rebuilt for {len(rows)} name(s) "
                    f"({len(judged)} judged: {', '.join(judged[:12])}"
                    f"{' …' if len(judged) > 12 else ''}) in {elapsed} s"
                    + (f"; STOPPED on {stopped}, {len(kept)} name(s) kept their "
                       "previous record (or none)" if stopped else "")
                    + (f"; {len(failed)} failed" if failed else "")
                    + (f"; {len(cut)} past the {VALUATION_PRECOMPUTE_MAX}-name cap"
                       if cut else "")
                    + ("" if stored else "; NOT saved to the database (write "
                       "refused or no database) — this process keeps them in "
                       "memory until it restarts") + ".")})
        log.info("[valuation:%s] %d records, %d judged, %.1fs", where,
                 len(rows), len(judged), elapsed)
        return {"status": "ok", **doc}
    except Exception as e:                              # noqa: BLE001
        log.exception("valuation precompute failed: %s", e)
        _journal("VALUATION_PRECOMPUTE_FAILED", {
            "where": where,
            "why": f"Valuation precompute crashed: {e} — the previous records stand."})
        return {"status": "failed", "why": str(e)}


def _valuation_precompute_job() -> None:
    """16:50 IST mon-fri: the records on a trading day."""
    from trading.calendars import is_trading_day
    if not is_trading_day():
        return
    run_valuation_precompute("16:50")


def valuation_catchup_if_stale(where: str = "catchup", now_ist=None) -> bool:
    """After a boot: rebuild the valuation records when they are older than
    one trading day — only in the evening (>= 15:45 IST) or on a holiday,
    so the 09:20 and 14:30 chains never carry this load. The precompute's
    own guards (rotation lock, memory ceiling) still apply."""
    from trading.calendars import is_trading_day

    from . import valuation_exit_feed as feed
    if not daily_enabled() or os.environ.get("SB_DISABLE_CATCHUP") == "1":
        return False
    if now_ist is None:
        from datetime import datetime as _dt
        try:
            from pytz import timezone
            now_ist = _dt.now(timezone("Asia/Kolkata"))
        except Exception:                               # noqa: BLE001
            return False
    today = now_ist.date()
    if is_trading_day(today) and (now_ist.hour, now_ist.minute) < VALUATION_CATCHUP_AFTER_IST:
        return False
    if not feed.records_stale(feed.built_date(), today):
        return False
    return run_valuation_precompute(where).get("status") == "ok"


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
        # Already snapped today — but only a UNIVERSE snapshot counts. The
        # owner's one-stock "read the chain now" button (2026-09-08) also
        # stamps today's date with scope "single"; that must not stop the
        # daily universe pass.
        if ((p.option_chain or {}).get("asof") == today
                and (p.option_chain or {}).get("scope", "universe") == "universe"):
            return p.option_chain
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
        from trading.options_chain import (HISTORY_KEEP, OPTION_CHAIN_MAX_TICKERS,
                                           OPTION_CHAIN_PACE_S, snapshot_universe)
        cfg = state.cfg()
        # SCOPE BOUNDED 2026-09-24 (owner: "upstox login issue as well / its
        # hitting 512+ / layoff unwanted"). This used to be custom_universe +
        # held = ~1,200 names since the liquidity rebuild, i.e. TWO
        # authenticated calls per name = ~2,400 in one burst, fired at the
        # moment of login beside the 5-mode scan and the FII pull. Upstox
        # allows 1,000 calls per 30 minutes: the back ~1,900 silently 429'd
        # while the box hammered for twenty minutes at its memory ceiling.
        #
        # The chain informs T12W_0102 on a held or watched name, and W-022 /
        # W-007 per stock on the register — none of that needs the whole
        # scanned universe. Scope = held + today's picks + the trigger/news
        # watchlist + the owner's hand-ordered list, capped, paced.
        from trading.universes import OWNER_UNIVERSE_2026_08_20 as _OWNER
        wanted = (set(p.positions.keys())
                  | {pk.get("ticker") for pk in (p.last_scan_picks or [])
                     if pk.get("ticker")}
                  | _watchlist_names(p)
                  | set(_OWNER))
        tickers = sorted(wanted)[:OPTION_CHAIN_MAX_TICKERS]
        keys = to_instrument_keys(tickers)
        snap = snapshot_universe(broker, tickers, keys.get,
                                 _date.today(), pace_s=OPTION_CHAIN_PACE_S)
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
                          "failed": snap["failed"], "scope": "universe"}
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


def read_option_chain_now(ticker: str, where: str = "owner-button") -> dict:
    """Owner button (order 2026-09-08: "build 1"): read ONE stock's option
    chain right now through the stored Upstox login and keep it beside the
    daily snapshot. Display only — feeds the same three consumers
    (T12W_0102 / W-022 / W-007) the daily job feeds; nothing here touches
    an entry or an exit.

    Returns {"status": ok | needs_login | no_fo | failed, ...}. The reason a
    read cannot happen is returned, never hidden: the option endpoints need
    the daily token (unlike candles), and a stock without an options market
    has nothing to read.

    Storage rule: if today's snapshot already exists, the row is merged into
    it (scope kept). If the stored snapshot is older, a NEW today's dict with
    this one row replaces it and is stamped scope "single" — older rows are
    dropped rather than left wearing today's as-of (the stale-chain fault
    attach_chain exists to prevent), and the "single" scope tells the 09:20
    universe job it still has to run.
    """
    from datetime import date as _date, datetime as _dt

    from . import state
    p = state.portfolio()
    t = (ticker or "").strip().upper()
    if t and not t.endswith(".NS"):
        t += ".NS"

    def _journal(action, why):
        try:
            p.decision_journal.append(
                {"ts": _dt.utcnow().isoformat(), "action": action,
                 "ticker": t, "why": why})
            p.save()
        except Exception:
            pass

    eng = state.engine()
    broker = getattr(eng, "broker", None)
    if broker is None or not getattr(broker, "is_connected",
                                     lambda: False)():
        return {"status": "needs_login", "ticker": t}
    try:
        from trading.brokers.upstox_instruments import to_instrument_keys
        from trading.options_chain import HISTORY_KEEP, snapshot_universe
        today = _date.today()
        keys = to_instrument_keys([t])
        snap = snapshot_universe(broker, [t], keys.get, today,
                                 include_index=False)
    except Exception as e:
        log.exception("option chain read failed for %s: %s", t, e)
        _journal("OPTION_CHAIN_READ_FAILED",
                 f"Owner chain read for {t} crashed: {e}")
        return {"status": "failed", "ticker": t, "why": str(e)}
    if t in snap["skipped_no_fo"]:
        return {"status": "no_fo", "ticker": t}
    if t not in snap["rows"]:
        _journal("OPTION_CHAIN_READ_FAILED",
                 f"Owner chain read for {t}: Upstox answered but the chain "
                 "carried no usable open interest — DATA_NEEDED, not zero.")
        return {"status": "failed", "ticker": t}
    row = snap["rows"][t]
    asof = today.isoformat()
    cur = dict(p.option_chain or {})
    if cur.get("asof") == asof:
        rows = dict(cur.get("rows") or {})
        rows[t] = row
        cur.update({"rows": rows, "ts": snap["ts"]})
        cur["skipped_no_fo"] = [x for x in (cur.get("skipped_no_fo") or [])
                                if x != t]
        cur["failed"] = [x for x in (cur.get("failed") or []) if x != t]
    else:
        cur = {"asof": asof, "ts": snap["ts"], "rows": {t: row},
               "history": dict(cur.get("history") or {}),
               "skipped_no_fo": [], "failed": [], "scope": "single"}
    hist = dict(cur.get("history") or {})
    kept = [x for x in (hist.get(t) or []) if x[0] != asof]
    kept.append((asof, row.get("pcr_oi"), row.get("iv_skew")))
    hist[t] = kept[-HISTORY_KEEP:]
    cur["history"] = hist
    p.option_chain = cur
    p.save()
    _journal("OPTION_CHAIN_READ",
             f"Owner read the option chain for {t} ({where}): PCR(OI) "
             f"{row.get('pcr_oi')}, IV skew {row.get('iv_skew')}, expiry "
             f"{row.get('expiry')}; {len(hist[t])} recording(s) held for "
             "the 15-day pattern. Display only.")
    return {"status": "ok", "ticker": t, "row": row, "days": len(hist[t])}


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
    this feed alive even before the day's login). The FII shape was
    verified from the live stored payload on 2026-10-04 and the mappers
    fixed to it (time_stamp in ms; DII needs data_type=NSE_EQ|CASH). The
    raw payload is still archived either way, the mappers still refuse rows
    they can't prove, and a shape miss journals instead of guessing.
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


def _sector_index_refresh() -> None:
    """Real NSE sector-index daily closes (owner order 2026-09-09 "fix it":
    Nifty IT fell 10% in six sessions and no screen in the app said so).

    Runs beside the index tail — before the 09:20 scans, at boot catch-up,
    in the 15:35 EOD pass (so TODAY'S close lands today) and in the Friday
    block. Tokenless public candles; idempotent; quiet when already current.

    DISPLAY ONLY, deliberately. The calibration-VERIFIED danger tripwires
    T12W_0094/0095 keep their OWN equal-weight sector construction: their
    lift was measured on that definition, and swapping the input under a
    verified trigger would silently void its evidence.
    """
    from . import db
    if not db.is_connected():
        _refresh_journal("DATA_REFRESH_SECTOR_SKIPPED",
                         "Sector index refresh skipped: Mongo offline.")
        return
    try:
        from trading import data as tdata, data_refresh as dr
        stored = db.load_sector_rows()
        last = max((str(r.get("date") or "") for r in stored), default="")
        broker = tdata.history_broker()
        if broker is None:
            _refresh_journal("DATA_REFRESH_SECTOR_SKIPPED",
                             "Sector index refresh skipped: no Upstox client "
                             "object (API keys unset).")
            return
        rows = dr.fetch_sector_tail(broker, last)
        if not rows:
            log.info("sector refresh: already current to %s", last or "(empty)")
        else:
            n = db.save_sector_rows(rows)
            got = sorted({r["sector"] for r in rows})
            missing = [s for s in dr.SECTOR_INDEX_KEYS if s not in got]
            _refresh_journal(
                "DATA_REFRESH_SECTOR",
                f"Sector index refresh: {n} row(s) stored for {len(got)} of "
                f"{len(dr.SECTOR_INDEX_KEYS)} sectors, to {max(r['date'] for r in rows)}."
                + (f" No data for: {', '.join(missing)}." if missing else ""))
            log.info("sector refresh: %d rows, %d sectors", n, len(got))
    except Exception as e:
        log.exception("sector index refresh failed: %s", e)
        _refresh_journal("DATA_REFRESH_SECTOR_FAILED",
                         f"Sector index refresh crashed: {e}")
    # The valuation context stage (owner order 2026-10-12) reads the sector
    # index at the VALUATION DATE and regresses a β over 252 sessions; the
    # 30-day seed above cannot serve either. One-time, idempotent backfill.
    try:
        backfill_sector_history()
    except Exception:
        log.exception("sector history backfill failed")


def _num_or_none(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _macro_new_rows(rows: list, stored: list) -> list:
    """Rows that add a date or change a stored value (quiet-when-current)."""
    have = {(r.get("series"), r.get("date")): r.get("value") for r in stored}
    return [r for r in rows
            if have.get((r["series"], r["date"]), object()) != r["value"]]


def _macro_feeds_refresh() -> None:
    """Valuation macro feeds (owner order 2026-10-21, VALFIX batch 2 item 10):
    the 10-year G-sec yield from Upstox's public candles (owner chose Upstox
    only), and the policy repo rate + USD/INR reference rate from RBI's home
    page. DISPLAY ONLY — the valuation chain reads them; no gate, stop or
    size does.

    Same slots as the sector refresh (the 09:20 job — AFTER the rotation,
    reviewer defect 6 — and the 15:35 EOD pass, so the 1.30pm reference
    rate lands the same day). Each leg is isolated; a failure journals
    MACRO_FEEDS_FAILED and never costs the job. Quiet when current: a
    journal line only when a new date or a changed value lands — and the
    repo rate, dated the day it is read, is journaled only when the RATE
    changes (the daily row is still stored; reviewer defect 7).
    """
    from . import db
    if not db.is_connected():
        log.warning("macro feeds skipped: Mongo offline (nowhere to store)")
        return
    from trading import macro_feeds as mf
    stored = db.load_macro_rows() or []
    repo_prev = max((r for r in stored if r.get("series") == "rbi_repo"),
                    key=lambda r: str(r.get("date") or ""), default=None)
    repo_prev_v = _num_or_none(repo_prev.get("value")) if repo_prev else None
    landed: list = []
    failed: list = []

    # Leg 1: 10-year G-sec yield (tokenless Upstox candles).
    try:
        from trading import data as tdata
        g_old = [r for r in stored if r.get("series") == "gsec10y_yield"]
        last = max((str(r.get("date") or "") for r in g_old), default="")
        rows = mf.fetch_gsec10y(tdata.history_broker(), last)
        new = _macro_new_rows(rows, stored)
        if new:
            db.save_macro_rows(new)
            landed += new
        elif not last or mf.is_stale(last):
            failed.append("10-year yield: no Upstox candles for any 9-11-year "
                          "G-sec" + (f" since {last}" if last else ""))
    except Exception as e:                                     # noqa: BLE001
        log.exception("macro feed (G-sec) failed")
        failed.append(f"10-year yield crashed: {e}")

    # Leg 2: RBI home page (repo + USD/INR). Skipped once both are read
    # today (the 15:35 pass still runs when only yesterday's rate is held).
    try:
        today = mf._today_ist().isoformat()
        have_today = {r.get("series") for r in stored if r.get("date") == today}
        if not {"rbi_repo", "rbi_usdinr"} <= have_today:
            rows = mf.fetch_rbi_home()
            got = {r["series"] for r in rows}
            missing = [s for s in ("rbi_repo", "rbi_usdinr") if s not in got]
            if missing:
                failed.append("RBI home page answered without "
                              + " and ".join(missing))
            new = _macro_new_rows(rows, stored)
            if new:
                db.save_macro_rows(new)
                landed += new
    except Exception as e:                                     # noqa: BLE001
        log.exception("macro feed (RBI home) failed")
        failed.append(f"RBI home page fetch failed: {e}")

    if landed:
        parts = []
        g = [r for r in landed if r["series"] == "gsec10y_yield"]
        if g:
            r = g[-1]
            parts.append(f"10-year yield {r['value']:.2f}% ({r.get('bond')}, "
                         f"close {r.get('price')} on {r['date']}, Upstox; "
                         f"{len(g)} day(s))")
        for s, label in (("rbi_repo", "RBI repo"), ("rbi_usdinr", "USD/INR")):
            x = [r for r in landed if r["series"] == s]
            if s == "rbi_repo" and repo_prev_v is not None:
                # an unchanged repo is today's row, not news (defect 7)
                x = [r for r in x if _num_or_none(r.get("value")) != repo_prev_v]
            if x:
                parts.append(f"{label} {x[-1]['value']} ({x[-1]['date']})")
        b = [r for r in landed if r["series"] == "rbi_gsec10y"]
        if b:
            parts.append(f"RBI 10-year benchmark {float(b[-1]['value']):.2f}% "
                         f"({b[-1].get('bond') or 'bond not named'}, {b[-1]['date']}; "
                         "display only beside the Upstox yield)")
        if parts:
            _refresh_journal("MACRO_FEEDS",
                             "Macro feeds stored: " + "; ".join(parts)
                             + ". Display only (valuation chain).")
    if failed:
        _refresh_journal("MACRO_FEEDS_FAILED",
                         "Macro feeds: " + "; ".join(failed)
                         + ". The valuation chain keeps the last stored "
                         "reading, dated.")


SECTOR_HISTORY_DAYS = 760          # ~2 years + margin for the 252-session β
#: One attempt per process per day PER DEPTH: {"day": iso, "from": the
#: deepest start date already attempted that day}. A request no deeper than
#: one already tried today returns "already_tried_today" WITHOUT reading the
#: store; a deeper one (an older valuation date typed on the Den) gets one
#: attempt of its own.
_SECTOR_BACKFILL_DAY: dict = {}


def backfill_sector_history(days: int = SECTOR_HISTORY_DAYS,
                            force: bool = False) -> dict:
    """Fill the sector-index store BACKWARDS to ~`days` calendar days once
    (owner order 2026-10-12, the valuation context stage). Idempotent: when
    the oldest stored row already reaches back that far nothing is fetched;
    when it does not, every sector's missing window is fetched in ONE
    public-candle call per index (16 calls, tokenless) and stored. One
    attempt per process per day (per depth) unless `force` — a dead feed
    must not cost 16 calls on every owner tap. Returns a small report for
    the caller (the Den's valuation POST calls this, with the days the
    typed valuation date needs).

    2026-10-15: the day guard is set BEFORE the store read. It used to be
    set only when a fetch happened, so a store that was already deep enough
    was read in FULL (the whole sector_index collection) on every 09:20 /
    15:35 refresh and on every Den save."""
    from datetime import date as _date, timedelta as _td

    from trading import data as tdata, data_refresh as dr
    from . import db
    today = _date.today().isoformat()
    rep = {"fetched": 0, "stored": 0, "skipped": "", "oldest": ""}
    if not db.is_connected():
        rep["skipped"] = "mongo_offline"
        return rep
    want_from = (_date.today() - _td(days=int(days))).isoformat()
    tried = (_SECTOR_BACKFILL_DAY.get("from")
             if _SECTOR_BACKFILL_DAY.get("day") == today else None)
    if not force and tried is not None and tried <= want_from:
        rep["skipped"] = "already_tried_today"
        return rep
    broker = tdata.history_broker()
    if broker is None:
        rep["skipped"] = "no_history_client"
        return rep
    # BEFORE the read: the attempt counts even when the store turns out deep
    # enough (keep the deepest start tried today, a forced shallow call
    # must not re-open a deeper one).
    _SECTOR_BACKFILL_DAY.update(
        day=today, **{"from": min(tried, want_from) if tried else want_from})
    stored = db.load_sector_rows()
    oldest = min((str(r.get("date") or "") for r in stored
                  if r.get("date")), default="")
    rep["oldest"] = oldest
    if oldest and oldest <= want_from:
        rep["skipped"] = "deep_enough"
        return rep
    end = ((_date.fromisoformat(oldest) - _td(days=1)).isoformat()
           if oldest else today)
    rows = dr.fetch_sector_history(broker, want_from, end)
    rep["fetched"] = len(rows)
    if not rows:
        _refresh_journal("DATA_REFRESH_SECTOR_FAILED",
                         f"Sector history backfill fetched nothing for "
                         f"{want_from} → {end}.")
        return rep
    rep["stored"] = db.save_sector_rows(rows)
    got = sorted({r["sector"] for r in rows})
    _refresh_journal(
        "DATA_REFRESH_SECTOR",
        f"Sector history backfill: {rep['stored']} row(s) stored for "
        f"{len(got)} of {len(dr.SECTOR_INDEX_KEYS)} sectors, "
        f"{want_from} → {end} (valuation context stage).")
    return rep


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


# Owner order 2026-10-12 ("add a time budget on the fundamentals step").
# The step used to loop every scanned name with one 20 s-timeout Yahoo GET
# and no total bound: ~1,205 names since the liquidity universe, so when
# Yahoo hung the weekly block sat in it for 89 minutes (Fri 10-09) and
# again Mon 10-12, and the universe rebuild + OHLCV pull behind it never
# ran. Now: a per-run budget (seconds; env SB_FUNDAMENTALS_BUDGET_S), the
# names already attempted this month kept in the ledger, and an unfinished
# run keeps the weekly block due the NEXT trading day — the OHLCV pull's own
# resume pattern (PR #103). Display-only feed: nothing here touches entries
# or exits.
FUNDAMENTALS_BUDGET_S_DEFAULT = 600.0
FUNDAMENTALS_DONE_KEY = "fundamentals_done"        # {"month", "symbols": [...]}
FUNDAMENTALS_LEFT_KEY = "fundamentals_remaining"   # int; > 0 = block due tomorrow


def fundamentals_time_budget_s() -> "float | None":
    """Seconds one fundamentals run may spend (SB_FUNDAMENTALS_BUDGET_S,
    default 600). None = unbounded, chosen only by setting it <= 0."""
    raw = os.environ.get("SB_FUNDAMENTALS_BUDGET_S", "")
    try:
        v = float(raw) if raw.strip() else FUNDAMENTALS_BUDGET_S_DEFAULT
    except ValueError:
        v = FUNDAMENTALS_BUDGET_S_DEFAULT
    return v if v > 0 else None


def _fundamentals_complete(db, month: str) -> None:
    """Stamp the month done and clear the resume state."""
    db.save_settings({"fundamentals_month": month, FUNDAMENTALS_LEFT_KEY: 0},
                     doc_id="data_refresh")
    try:
        db.unset_settings_keys([FUNDAMENTALS_DONE_KEY], doc_id="data_refresh")
    except Exception:
        log.exception("fundamentals done-list could not be cleared")


def _monthly_fundamentals_refresh() -> None:
    """Node 3: monthly fundamentals refresh (Yahoo direct timeseries — the
    raw endpoint, since the yfinance wrapper returns empty where the
    endpoint has data). Scope = custom universe + held names.
    register_v4.load_fundamentals overlays the stored rows on the committed
    CSV; the cache is cleared after a successful store so the page and the
    nightly precompute see the new quarters without a restart.

    TIME-BUDGETED AND RESUMABLE (owner order 2026-10-12): each run spends
    at most `fundamentals_time_budget_s()` on Yahoo, saves what it got,
    records the names it attempted under FUNDAMENTALS_DONE_KEY, and — when
    names are left — sets FUNDAMENTALS_LEFT_KEY so `weekly_block_due` brings
    the block back tomorrow to continue. The month is stamped only once
    every name has been attempted. A run Yahoo refuses entirely stores
    nothing, counts nothing as done, and does NOT ask for a next-day rerun
    (that would be a 10-minute hang every evening); it waits for the next
    weekly slot as before."""
    from datetime import date as _date

    from . import db, state
    if not db.is_connected():
        return                      # weekly retry; the index job journaled
    month = _date.today().isoformat()[:7]
    try:
        ledger = db.load_settings(doc_id="data_refresh") or {}
        if ledger.get("fundamentals_month") == month:
            return
        from trading import data_refresh as dr
        cfg = state.cfg()
        p = state.portfolio()
        syms = sorted({t.replace(".NS", "")
                       for t in (tuple(cfg.custom_universe or ())
                                 + tuple((p.positions or {}).keys()))})
        if not syms:
            return
        done_doc = ledger.get(FUNDAMENTALS_DONE_KEY) or {}
        done = (set(done_doc.get("symbols") or [])
                if done_doc.get("month") == month else set())
        todo = [s for s in syms if s not in done]
        if not todo:
            # every name was attempted on earlier runs this month (the list
            # shrank since) — nothing left to fetch, stamp and clear
            _fundamentals_complete(db, month)
            log.info("[fri 16:30] fundamentals refresh %s: nothing left", month)
            return
        budget = fundamentals_time_budget_s()
        res = dr.fetch_fundamentals_budgeted(todo, time_budget_s=budget)
        rows, n_ok = res["rows"], res["n_ok"]
        attempted = list(res["attempted"])
        left = len(res["remaining"])
        if not rows:
            # Nothing came back: keep the progress already made, count none
            # of these as done, and do not re-run tomorrow for it.
            db.save_settings({FUNDAMENTALS_LEFT_KEY: 0}, doc_id="data_refresh")
            _refresh_journal("DATA_REFRESH_FUNDAMENTALS_FAILED",
                             f"Monthly fundamentals refresh: Yahoo returned "
                             f"nothing for all {len(attempted)} symbols tried "
                             f"in {res['elapsed_s']}s ({len(done)} of "
                             f"{len(syms)} already done this month) — will "
                             "retry at the next weekly slot.")
            return
        n = db.save_fundamentals_rows(rows)
        done |= set(attempted)
        if left:
            db.save_settings({FUNDAMENTALS_DONE_KEY: {"month": month,
                                                      "symbols": sorted(done)},
                              FUNDAMENTALS_LEFT_KEY: left},
                             doc_id="data_refresh")
        else:
            _fundamentals_complete(db, month)
        try:
            import trading.register_v4 as rv
            rv.clear_fundamentals_cache()
        except Exception:
            log.exception("fundamentals cache clear failed")
        budget_txt = f"{int(budget)} s" if budget else "no"
        _refresh_journal("DATA_REFRESH_FUNDAMENTALS",
                         f"Monthly fundamentals refresh for {month}: "
                         f"{n} datapoints stored, {n_ok}/{len(attempted)} "
                         f"symbols answered in {res['elapsed_s']}s"
                         + (f". STOPPED on the {budget_txt} time budget with "
                            f"{left} name(s) left ({len(done)}/{len(syms)} "
                            "done this month) — the weekly block runs again "
                            "next trading day and resumes from there."
                            if left else
                            f". Complete: {len(done)}/{len(syms)} names this "
                            "month. /register H/W/X items read the fresh "
                            "quarters."))
        log.info("[fri 16:30] fundamentals refresh: %d rows, %d/%d ok, "
                 "%d left", n, n_ok, len(attempted), left)
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
    """F3 fix (owner order 2026-08-20): compute market breadth from the
    Mongo OHLCV store (one streamed $slice query) and persist the reading on
    the portfolio for the A3 gate to prefer.

    2026-10-16 ("new position are not added ... relook n fix"): the reading
    the gate prefers is now the 191-name STUDY PANEL the 40-65% band was
    measured on, with the whole-store number beside it as context — the gate
    had been comparing the band's edges with a different population (see
    trading.breadth.gate_breadth). The paragraph below is the F3 history.

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

    from trading.breadth import gate_breadth
    from . import db, state
    p = state.portfolio()
    try:
        dma = 200
        from trading.universes import fund_unit_tickers
        # 2026-10-16: ONE pass over the store gives the reading the gate
        # reads — the study panel the 40-65% band was measured on — with the
        # whole-store number (fund units left out) beside it as context.
        reading = gate_breadth(db.history_tails(dma + 5), dma=dma,
                               exclude=fund_unit_tickers())
        if reading is None:
            log.info("[breadth:%s] no usable full-store reading "
                     "(store thin or Mongo down) — gate keeps its fallback",
                     where)
            return {}
        reading["computed"] = _dt.utcnow().isoformat()
        p.breadth_store = reading
        p.save()
        log.info("[breadth:%s] %s: %.1f%% of %d above 200DMA (closes to "
                 "%s, %d stale + %d short excluded; whole store %s%%)", where,
                 reading.get("population"), reading["pct"] * 100,
                 reading["sample"], reading["asof"],
                 reading["excluded_stale"], reading["excluded_short"],
                 (None if reading.get("full_pct") is None
                  else round(reading["full_pct"] * 100, 1)))
        return reading
    except Exception as e:
        log.exception("full-store breadth refresh failed: %s", e)
        return {}


# The weekly data block's completion ledger, in the settings doc the other
# day/month guards already use. Written only AFTER the block finishes — the
# guard-set-before-the-work fault this file has been bitten by before.
WEEKLY_BLOCK_KEY = "weekly_block_date"
# How many names the bounded OHLCV pull still owes (2026-09-11). While
# this is above zero the block is due again the NEXT trading day, not
# in seven — otherwise a pull that stops on its time budget would stamp
# "done" and leave the store short for a week, which is the shape of
# the bug this whole round is fixing.
WEEKLY_BLOCK_LEFT_KEY = "weekly_block_remaining"


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
        ledger = db.load_settings(doc_id="data_refresh") or {}
    except Exception:
        log.exception("weekly block ledger unreadable")
        ledger = {}
    last_iso = ledger.get(WEEKLY_BLOCK_KEY)
    try:
        left = int(ledger.get(WEEKLY_BLOCK_LEFT_KEY) or 0)
    except (TypeError, ValueError):
        left = 0
    if left > 0:
        return True, (f"the OHLCV pull still owes {left} name(s) — it stopped "
                      f"on its time budget, so it resumes today")
    # Owner order 2026-10-12: the fundamentals step is time-budgeted and
    # resumable the same way — an unfinished month brings the block back
    # tomorrow (the other feeds are cheap and idempotent; the pull skips
    # fresh names).
    try:
        fleft = int(ledger.get(FUNDAMENTALS_LEFT_KEY) or 0)
    except (TypeError, ValueError):
        fleft = 0
    if fleft > 0:
        return True, (f"the fundamentals refresh still owes {fleft} name(s) "
                      f"— it stopped on its time budget, so it resumes today")
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


def _mark_weekly_block_done(remaining: int = 0) -> None:
    """Record completion, and what the bounded pull still owes.

    `remaining > 0` keeps the block due tomorrow instead of in seven days —
    the pull is incremental, so an unfinished store is a reason to run again,
    not a failure to report.
    """
    from datetime import date as _date
    try:
        from . import db
        db.save_settings({WEEKLY_BLOCK_KEY: _date.today().isoformat(),
                          WEEKLY_BLOCK_LEFT_KEY: int(remaining or 0)},
                         doc_id="data_refresh")
    except Exception:
        log.exception("could not record the weekly block completion")


def _watchlist_names(p) -> set:
    """Every name the app is actively watching, from both watch surfaces.

    `trigger_watch["watchlist"]` is the owner's own 112-trigger gate result;
    `news_watch["triggered"]` is a stock a news event named. Both are display
    surfaces, but the NAMES are work the owner accumulated and must survive a
    universe rebuild. Defensive about shape: these dicts are persisted and
    hydrated from Mongo, so a partial doc must degrade to "no watchlist"
    rather than raise inside the Friday block.
    """
    out: set = set()
    for doc, key in ((getattr(p, "trigger_watch", None), "watchlist"),
                     (getattr(p, "news_watch", None), "triggered")):
        if not isinstance(doc, dict):
            continue
        names = doc.get(key)
        if isinstance(names, dict):
            names = list(names.keys())
        if not isinstance(names, (list, tuple, set)):
            continue
        out |= {str(t) for t in names if t}
    return out


def refresh_liquid_universe(where: str = "weekly") -> dict:
    """Rebuild the tradeable universe as the top-N names by rupee turnover.

    OWNER ORDER 2026-08-30: "No automation for weekly new batch of stocks /
    batch size also reduce to 500 from 2300+", and their pick from the
    options card: "Top 500 by liquidity/turnover, weekly, replaces the 50".

    This retires the hand-typed 50-name list of 2026-08-20 as the *source*
    of the universe — the owner no longer has to refresh it by hand, which
    was their own stated workflow and the thing that went stale ("system
    still use old stocks"). Held names always ride along, so a position can
    never fall out of its own scan universe.

    Ranked from the OHLCV store, which the Friday pull grows, so the list
    converges on a true market top-500 as the store fills. Until it does the
    list is the top of what is stored, and the journal SAYS the sample size
    rather than presenting a short list as a full 500.
    """
    from datetime import datetime as _dt

    from . import db, state
    from trading import universe_select as _us
    from trading.universe_select import DEFAULT_TARGET, select_liquid_universe

    cfg, p = state.cfg(), state.portfolio()

    def _journal(action, why, **extra):
        try:
            p.decision_journal.append(
                {"ts": _dt.utcnow().isoformat(), "action": action,
                 "why": why, **extra})
            p.save()
        except Exception:
            log.exception("universe journal failed")

    if not db.is_connected():
        _journal("UNIVERSE_BATCH_SKIPPED",
                 "Weekly liquidity universe skipped: Mongo offline, so the "
                 "OHLCV store could not be read. The previous list stands.")
        return {}
    try:
        target = int(getattr(cfg, "universe_target_size", DEFAULT_TARGET) or
                     DEFAULT_TARGET)
        # OWNER ORDER 2026-09-10, item 8: "you add keep the entire watch list
        # in new download on friday (x+watch list =2400), so some current work
        # done on watch list must be kept ... also in future i can track the n
        # see why its in watch and check many other parameter".
        #
        # THE GAP THIS CLOSES. always_include carried HELD names only, so a
        # name the trigger layer had flagged WATCH — or one the news module
        # had marked TRIGGER — dropped out of the scanned universe the moment
        # it fell below the turnover cut, taking its accumulated reasoning
        # with it. The owner watches names precisely because they are not
        # positions yet; losing them on a Friday is losing the work.
        #
        # Their clarification the same day settles the other half: "universe
        # means the stocks app will analysis for trade - do not even think or
        # touch or dlt the years data". Nothing here reads or writes stored
        # candles; this rebuilds the LIST only.
        keep = _watchlist_names(p)
        # OWNER ORDER 2026-09-16 ("add defence, shipbuilding, pharma names to
        # the scan list") meets OWNER ORDER 2026-09-24 ("update scan to
        # 1200"). Both stand: the liquidity rebuild fills the list to the
        # 1,200 target, and the names the owner put on the list BY HAND ride
        # along whatever their turnover rank — the same rule held and watched
        # names already get. Without this line the first Friday after 09-16
        # could silently drop a hand-ordered defence or pharma name that
        # ranked below the cut, and "why is HAL not scanned" would have had
        # no visible answer.
        from trading.universes import OWNER_UNIVERSE_2026_08_20 as _OWNER
        from trading.universes import fund_unit_tickers
        owner_list = set(_OWNER)
        # 2026-10-16: fund units (INF ISIN) are not stocks. NONE is ranked —
        # a held one included, or it would take one of the `target` stock
        # slots by turnover — and none rides in on the watchlist or owner
        # rule. A HELD fund unit is still CARRIED (always_include) so its
        # exits keep running, outside the ranked count.
        funds = set(fund_unit_tickers())
        held = set(p.positions.keys())
        carry = held | ((keep | owner_list) - funds)
        res = select_liquid_universe(
            db.history_tails(40), target=target,
            blocklist=getattr(cfg, "ticker_blocklist", ()) or (),
            always_include=sorted(carry),
            exclude=funds)
        picked = res["tickers"]
        # THE GUARD MUST READ WHAT THE STORE CONTRIBUTED, not the union.
        # Caught by test_an_empty_result_never_empties_the_universe the day
        # the owner list joined always_include (2026-09-24): with 83 names
        # always riding along, `picked` is never empty, so on a Friday when
        # Mongo answers nothing the rebuild would have REPLACED a ~1,200-name
        # universe with those 83 — the exact "emptied the universe" failure
        # this branch exists to prevent, at 83 instead of 0. "Nothing ranked"
        # = every name in the result got there by rule, none by turnover.
        ranked_by_store = [t for t in picked if t not in carry]
        if not ranked_by_store:
            _journal("UNIVERSE_BATCH_FAILED",
                     "Weekly liquidity universe produced NO names — the "
                     "previous list stands rather than emptying the "
                     "universe. Store sample was "
                     f"{res.get('sample', 0)} names.")
            return res
        before = list(getattr(cfg, "custom_universe", ()) or ())
        picked_set = set(picked)
        cfg.custom_universe = tuple(picked)
        db.save_settings({"custom_universe": list(picked)})
        added = sorted(set(picked) - set(before))
        dropped = sorted(set(before) - set(picked))
        # Owner ask 2026-09-04 ("where is details for new batch"): keep the
        # whole story on the portfolio (persisted JSON + Mongo, the risk_day
        # lesson) so /scan can show it without digging through the journal.
        p.universe_batch = {
            "ts": _dt.utcnow().isoformat(), "where": where,
            "count": len(picked), "target": target,
            "sample": res["sample"], "judged": res["judged"],
            "short_by": res["short_by"],
            "skipped_thin": res["skipped_thin"],
            "skipped_illiquid": res["skipped_illiquid"],
            "skipped_fund": res.get("skipped_fund", 0),
            "added": added, "dropped": dropped,
            "held_added": res.get("held_added", []),
            # Owner order 2026-09-10 item 8: which watched names were carried
            # through the rebuild, so the owner can SEE their work survived
            # rather than trusting that it did.
            "watchlist_kept": sorted(keep & picked_set),
            # The hand-ordered list (2026-09-16) is protected the same way;
            # this is how many of those names are in today's universe (all
            # of them, unless one left the tradeable file) and how many rode
            # in BELOW the turnover cut on the owner's rule alone.
            "owner_list_kept": len(owner_list & picked_set),
            "owner_list_carried": sorted(
                owner_list & set(res.get("held_added", ()))),
            # Carried = in the universe but NOT by rank, i.e. it would have
            # been dropped without the owner's rule. `picked` is already the
            # FINAL list (top-N plus always_include), so the ones added
            # outside the rank are exactly res["held_added"].
            "watchlist_carried": sorted(keep & set(res.get("held_added", ()))),
        }
        # OWNER 2026-10-11 item 15: "when the shares are moved from the
        # universe of 1200, verify that some part do things in code, but
        # some you keep quiet". What was quiet: the per-name REASON and DATE.
        # One ledger row per move, kept on the portfolio (JSON + Mongo both
        # ways), capped at UNIVERSE_LEDGER_CAP rows.
        try:
            from trading.paper import UNIVERSE_LEDGER_CAP
            rank_of = {r["ticker"]: (i + 1, r["turnover"])
                       for i, r in enumerate(res.get("ranked") or [])}
            always_set = carry
            stamp = _dt.utcnow().isoformat()
            rows = []
            for t in added:
                rk, tv = rank_of.get(t, (None, None))
                if t in always_set and rk is None:
                    why = ("held" if t in p.positions else
                           "owner list" if t in owner_list else "watchlist")
                    reason = f"carried in by rule: {why}"
                elif rk is not None:
                    reason = f"ranked {rk} of {target} by median turnover"
                else:
                    reason = "in the final list (no rank recorded)"
                rows.append({"ts": stamp, "ticker": t, "move": "in",
                             "turnover": tv, "rank": rk, "reason": reason})
            for t in dropped:
                if t in funds:
                    reason = ("a fund unit (ETF / liquid fund, ISIN INF...), "
                              "not a company share — fund units are never "
                              "scanned (2026-10-16)")
                elif t not in always_set:
                    reason = ("below the turnover cut (not in the top "
                              f"{target}) and not held / watched / "
                              "owner-listed")
                else:
                    reason = "left the tradeable file or blocklisted"
                rows.append({"ts": stamp, "ticker": t, "move": "out",
                             "turnover": None, "rank": None, "reason": reason})
            ledger = list(getattr(p, "universe_ledger", []) or []) + rows
            p.universe_ledger = ledger[-UNIVERSE_LEDGER_CAP:]
        except Exception:
            log.exception("universe ledger failed (batch card unaffected)")
        carried = sorted(keep & set(res.get("held_added", ())))
        kept_note = ("" if not keep else
                     f" Watchlist kept: {len(keep)} name(s), "
                     f"{len(carried)} of them carried in below the turnover "
                     f"cut so their accumulated reasoning survives.")
        note = ("" if not res["short_by"] else
                f" SHORT BY {res['short_by']}: only {res['judged']} of the "
                f"{res['sample']} stored names cleared the liquidity floor, "
                f"so this is the top of what is stored, not a full {target}.")
        _journal("UNIVERSE_BATCH",
                 f"[{where}] Tradeable universe rebuilt by median rupee "
                 f"turnover: {len(picked)} names (target {target}). "
                 f"{len(added)} added, {len(dropped)} dropped. "
                 f"{kept_note}"
                 f"{res['skipped_thin']} skipped for thin history, "
                 f"{res['skipped_illiquid']} below the Rs "
                 f"{_us.MIN_TURNOVER / 1e7:.0f} crore entry-gate floor, "
                 f"{res.get('skipped_fund', 0)} fund unit(s) (ETF / liquid "
                 f"fund) left out — not company shares."
                 + note,
                 added=added[:25], dropped=dropped[:25],
                 sample=res["sample"], short_by=res["short_by"])
        log.info("[%s] liquidity universe: %d names (+%d/-%d)",
                 where, len(picked), len(added), len(dropped))
        return res
    except Exception as e:
        log.exception("liquidity universe refresh failed: %s", e)
        _journal("UNIVERSE_BATCH_FAILED",
                 f"Weekly liquidity universe crashed ({e}) — the previous "
                 "list stands.")
        return {}


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
    finally:
        # 2026-10-05: each Friday feed hands its memory back before the next
        # one loads (the block's peak was 326 MiB on 09-30).
        from .memory_relief import release as release_memory
        release_memory(f"weekly {label}")


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
    _topup_remaining = 0
    _journal("DATA_REFRESH_WEEKLY_START",
             f"Weekly data block started ({why}): short feeds first, then "
             f"the full-universe OHLCV top-up.")

    # Independent of the store's freshness — cheap, so they go first and
    # are done long before anything can interrupt the pull.
    for _label, _fn in (
        ("FII tide (NSDL)", _weekly_fii_refresh),
        ("index tail", _weekly_index_refresh),
        ("sector indices", _sector_index_refresh),
        ("constituents snapshot", _monthly_constituents_snapshot),
        ("fundamentals refresh", _monthly_fundamentals_refresh),
        ("SEBI orders", _weekly_sebi_refresh),
        # MOVED IN FRONT OF THE PULL 2026-09-11 (owner: "why system still
        # stucking at 49"). It used to sit AFTER the ~hour-long OHLCV pull,
        # the exact position that cost five other feeds a week each and that
        # I moved THEM out of on 2026-08-29 — and unlike its two remaining
        # post-pull neighbours it has NO daily counterpart, so a pull cut
        # short by a container restart meant the universe was never rebuilt
        # at all. Measured evidence it never ran: the live store sat at 573
        # names for twelve days while custom_universe stayed the hand-typed
        # 49. It ranks whatever the store ALREADY holds and never needed the
        # pull to have just finished, so nothing is lost by running first —
        # the list is simply one pull behind, which beats never.
        ("liquidity universe", lambda: refresh_liquid_universe("weekly")),
    ):
        _run_weekly_step(_label, _fn, _journal)

    try:
        from trading.brokers.upstox_instruments import to_instrument_keys
        from trading.history_store import top_up_history
        tickers = {m["ticker"] for m in db.list_history_meta() if m.get("ticker")}
        tickers |= set(eng.portfolio.positions.keys())
        # REVIEW FIX (PR #68): the scope used to be "already stored +
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
        # BOUNDED (2026-09-11): 40 minutes, then stop and finish the block.
        # The store lacked 2,075 of the universe's names and the pull needs
        # ~1.8s each — over an hour in one go, on a box that restarts on
        # every deploy, so it never completed and everything sequenced after
        # it never ran. Each run now saves what it fetched and the next run
        # resumes; the ledger below records a real completion either way.
        summ = top_up_history(
            broker, keys,
            load_doc=db.load_history_doc,
            save_doc=db.save_history_doc,
            today=date.today(),
            time_budget_s=float(os.environ.get("SB_TOPUP_BUDGET_S", "2400")),
        )
        _left = summ.get("remaining", 0)
        _topup_remaining = _left
        _journal("DATA_REFRESH_WEEKLY",
                 f"Weekly OHLCV top-up: {summ['ok']} refreshed, "
                 f"{summ['skipped_fresh']} already fresh, "
                 f"{summ['failed']} failed of {summ['total']} "
                 f"in {summ.get('elapsed_s', 0)}s."
                 + (f" STOPPED on the {summ.get('stopped')} with {_left} "
                    f"name(s) left — the next run resumes from there, and "
                    f"the names already saved stay saved."
                    if _left else " Complete."))
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
        # Item 19: the valuation records over the fresh closes.
        ("valuation precompute", lambda: run_valuation_precompute("post-topup")),
        # The universe rebuild runs a SECOND time here, now that the pull has
        # added names: the pre-pull run guarantees it happens at all, this one
        # picks up whatever the pull just landed. Idempotent — same ranking
        # over a larger store.
        ("liquidity universe (post-pull)",
         lambda: refresh_liquid_universe("post-topup")),
    ):
        _run_weekly_step(_label, _fn, _journal)

    # Completion is recorded LAST, and deliberately counts the SEQUENCE, not
    # the pull's internal result: the OHLCV top-up is incremental by nature
    # (it skips names already fresh and journals its own failures), so tying
    # the ledger to it would re-run an hour-long pull every single evening
    # whenever a handful of names failed. A block that never reaches here —
    # a crash, or a container restart mid-run — leaves no stamp, so the next
    # trading day picks it up. That is the owner's Monday rule.
    #
    # The ONE thing the ledger does take from the pull is its REMAINDER
    # (2026-09-11): the pull is now time-bounded, so "the sequence ran" and
    # "the store is full" are different facts. A remainder keeps the block
    # due tomorrow rather than in seven days, so the store fills over
    # consecutive evenings instead of needing one uninterrupted hour.
    _mark_weekly_block_done(_topup_remaining)


def _session_refresh() -> None:
    """Every `cfg.intraday_refresh_minutes` inside the session (owner order
    2026-09-10, "A - instead of 20 u can do in 30").

    Updates prices, fires stops AND targets on the same clock, then checks
    the owner's resting limit orders (audit S-7, 2026-10-02 — before this
    they were only checked after the 09:20 and 14:30 scans, so an order the
    price touched at 11:00 waited hours, and above 60% deployed the 14:30
    check never ran at all). Rotation is NOT run here; it runs inside the
    scan, which is where the picks it funds come from (audit S-14).

    WHY STOPS TOO, stated because it is a real change: firing targets every
    30 minutes while stops waited for the 15:35 pass would take profits fast
    and losses slow, which is the classic way to lose money. The owner
    approved the symmetric version ("A- OK"). The -5% stop is now checked
    about thirteen times a day instead of twice, so intraday dips that used to
    recover by the close will now sell. UNTESTED live; the window judges it.

    engine.refresh() is itself gated on market hours, so this is safe to fire
    at any clock — but the job is registered in-session only so a closed
    market costs nothing.
    """
    from trading.calendars import is_market_open
    if not is_market_open():
        return
    from . import state
    from trading.modes import ModeBusy, holding_mode_lock
    # FIX 2026-10-04 (owner: "fix the 30 min exit pass too"): this used to
    # wait with NO timeout behind the 09:20 rotation's mode lock, and
    # APScheduler (max_instances 1) DROPPED every later run that fell due
    # while it waited — stop checks simply stopped for as long as the
    # rotation ran. Now: wait a short while for an ordinary holder (an owner
    # tap, one scan pass), and if a rotation still holds the lock, stand
    # down — the rotation runs the same exit pass itself between categories
    # (`_run_category_rotation`), so the check happens either way.
    try:
        with holding_mode_lock(timeout=SESSION_EXIT_LOCK_WAIT_S):
            # Owner order 2026-10-21: the 09:15 pass is the FIRST exit pass
            # of an ex-date — a split must be absorbed before it (BLSE).
            apply_corporate_actions_job("session")
            state.engine().refresh()
            _mark_exit_pass()
    except ModeBusy:
        log.info("[session] rotation running — its own exit pass covers this slot")
    except Exception as e:
        log.exception("session refresh failed: %s", e)
    _check_pending_orders("session")


# How long the 30-minute exit pass waits for the mode lock before standing
# down to the rotation's own exit pass. Short on purpose: anything longer
# than a single scan pass is a rotation, which checks exits itself.
SESSION_EXIT_LOCK_WAIT_S = 60.0
_LAST_EXIT_PASS: dict = {"t": None}


def _mark_exit_pass() -> None:
    _LAST_EXIT_PASS["t"] = time.monotonic()


def _exit_pass_due(cfg) -> bool:
    """True when the last in-session exit pass is at least one refresh
    interval old (or none has run since this process started)."""
    last = _LAST_EXIT_PASS["t"]
    if last is None:
        return True
    mins = max(5, int(getattr(cfg, "intraday_refresh_minutes", 30) or 30))
    return (time.monotonic() - last) >= mins * 60


def _run_category_rotation(eng, cfg, where: str) -> list:
    """Scan every category in `_DAILY_MODE_ORDER` under ONE held mode lock,
    restoring the owner's mode at the end. Shared by 09:20 and 14:30.

    Exit checks keep running during the rotation (owner order 2026-10-04):
    before each category pass, inside market hours, if the last exit pass
    is a refresh interval old, `engine.refresh()` runs here. It reads each
    position's OWN category rules (`engine._exit_cfg`), never the shared
    cfg.mode the rotation is flipping, so running it mid-rotation is safe.
    Caller holds `_ROTATION_LOCK`."""
    from trading.calendars import is_market_open
    from trading.modes import MODE_LOCK
    from .memory_relief import release as release_memory
    summary: list = []
    # Owner 2026-10-21 ("how do i know its blocked"): every pass is recorded
    # on portfolio.rotation_report and ONE ROTATION_SUMMARY line is journaled
    # (pushed to the phone) when the loop ends — web/why_no_buy.py.
    from datetime import datetime as _dt
    from .clock import ist_today_iso
    from . import why_no_buy as wnb
    rot: dict = {"date": ist_today_iso(), "where": where,
                 "started": _dt.utcnow().isoformat(), "order": list(_DAILY_MODE_ORDER),
                 "passes": {}, "opened_total": 0, "finished": None}
    # AUDIT S-6 (2026-10-02): the lock is held for the WHOLE rotation,
    # restore included. Releasing it between passes left cfg.mode on the
    # previous pass's category, so an owner buy that slipped in between read
    # (and restored) the wrong preset. Owner web actions wait up to 30 s and
    # then say a scan is running.
    MODE_LOCK.acquire()
    original_mode = getattr(cfg, "mode", "swing")
    try:
        for m in _DAILY_MODE_ORDER:
            if is_market_open() and _exit_pass_due(cfg):
                try:
                    eng.refresh()
                    _mark_exit_pass()
                except Exception:
                    log.exception("[%s] exit pass inside the rotation failed", where)
            j0 = len(eng.portfolio.decision_journal)
            try:
                cfg.mode = m
                cfg.apply_mode_preset()
                res = eng.scan()
                summary.append(f"{m}={len(res.opened)} opened/{len(res.skipped)} skipped")
                try:
                    rot["passes"][m] = wnb.pass_record(
                        m, res, eng.portfolio.decision_journal[j0:],
                        float(getattr(cfg, "min_score_to_enter", 0) or 0),
                        getattr(eng.portfolio, "breadth_today", None))
                except Exception:
                    log.exception("[%s] why-no-buy record for %s failed", where, m)
                # OWNER 2026-10-11 item 28: the watchlist capture is taken
                # RIGHT AFTER the momentum pass, while scan_matrix /
                # last_scan_skipped / last_scan_opened still hold THAT pass —
                # at the end of the rotation they hold positional's, and a
                # capture taken there mixed two passes (found by the
                # positions/reports agent 2026-10-12). Never raises.
                if m == "nifty200_momentum":
                    _capture_watchlist(eng, cfg, where)
            except Exception as e:
                log.exception("[%s] scan for mode=%s failed: %s", where, m, e)
                summary.append(f"{m}=error")
                rot["passes"][m] = wnb.failed_record(m, f"{type(e).__name__}: {str(e)[:120]}")
                # Silent dead mornings are forbidden: the failure goes into the
                # journal so the dashboard trigger card SHOWS it the same day.
                try:
                    from datetime import datetime as _dt
                    eng.portfolio.decision_journal.append({
                        "ts": _dt.utcnow().isoformat(), "action": "SCAN_FAILED",
                        "mode": m,
                        "err": f"{type(e).__name__}: {str(e)[:120]}",
                        "why": f"{where} scan pass failed — check Upstox login/data.",
                    })
                    eng.portfolio.save()
                except Exception:
                    pass
            finally:
                # 512 MB guard: drop each mode's OHLCV panels before the next
                # mode downloads its own — and hand the freed pages back to
                # the OS (2026-10-05: gc alone left the process holding
                # 353-494 MiB after a rotation; see web/memory_relief.py).
                release_memory(f"{where} {m}")
    finally:
        # AUDIT FIX C3: the restore is in a finally — an escape mid-loop
        # used to leave cfg.mode stuck on whatever mode the loop died in.
        try:
            cfg.mode = original_mode
            cfg.apply_mode_preset()
        finally:
            MODE_LOCK.release()
    _record_rotation(eng, rot)
    return summary


def _record_rotation(eng, rot: dict) -> None:
    """Store the rotation record and journal the ROTATION_SUMMARY line.
    Never raises — a failure here must not cost the scan."""
    from datetime import datetime as _dt
    from . import why_no_buy as wnb
    try:
        rot["finished"] = _dt.utcnow().isoformat()
        rot["opened_total"] = sum(int(r.get("opened") or 0) for r in rot["passes"].values())
        text = wnb.summary_text(rot)
        eng.portfolio.rotation_report = wnb.add_rotation(
            getattr(eng.portfolio, "rotation_report", None), rot)
        eng.portfolio.decision_journal.append({
            "ts": rot["finished"], "action": "ROTATION_SUMMARY",
            "where": rot["where"], "opened_total": rot["opened_total"],
            "categories": {m: wnb.stopped_by(r) for m, r in rot["passes"].items()},
            "why": text,
        })
        eng.portfolio.save()
    except Exception:
        log.exception("[%s] rotation record failed", rot.get("where"))


def _capture_watchlist(eng, cfg, where: str) -> dict:
    """Owner item 28 — one dated watchlist capture per rotation, taken right
    after the momentum pass (web/watchlist_capture.py, bounded). Journals
    WATCHLIST_CAPTURED or WATCHLIST_CAPTURE_FAILED so an empty Reports
    card is never a mystery. Returns the capture result (tests)."""
    from datetime import datetime as _dt
    res: dict = {}
    try:
        from . import db
        from .watchlist_capture import capture
        try:
            res = capture(eng.portfolio, cfg, _dt.utcnow(), db_module=db,
                          state_dir=getattr(cfg, "state_dir", None)) or {}
            action = "WATCHLIST_CAPTURED" if res.get("ok") else "WATCHLIST_CAPTURE_FAILED"
            why = (f"[{where}] {res.get('n_rows', 0)} rows captured at "
                   f"{res.get('ts') or '?'} · mongo={'yes' if res.get('mongo') else 'no'}"
                   f" · csv={'yes' if res.get('csv') else 'no'}"
                   + (f" · {res.get('error')}" if res.get("error") else "")
                   + (f" · {res.get('note')}" if res.get("note") else ""))
        except Exception as e:                           # noqa: BLE001
            log.exception("[%s] watchlist capture crashed", where)
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            action, why = "WATCHLIST_CAPTURE_FAILED", f"[{where}] {res['error']}"
        eng.portfolio.decision_journal.append(
            {"ts": _dt.utcnow().isoformat(), "action": action, "why": why,
             "n_rows": res.get("n_rows", 0)})
        eng.portfolio.save()
    except Exception:
        log.exception("[%s] watchlist capture journal failed", where)
    # No memory_relief.release() here on purpose: the capture is a few
    # hundred small dicts, the last category pass already released, and the
    # rotation pins count exactly one release per category (CI red on
    # 5181c89 when this had its own release).
    return res


def _eod_refresh() -> None:
    """15:35 IST: mark all positions and journal HOLD decisions.

    NOTE since 2026-09-10: this runs five minutes AFTER the close, so it no
    longer fires exits — engine.refresh() marks only outside 09:15-15:30.
    Exits moved to _session_refresh, inside the session, where a fill is a
    price that could actually have been got. The pass still marks the book,
    keeps the equity curve, and carries the two index refreshes below.
    """
    from trading.calendars import is_trading_day
    if not is_trading_day():
        return
    from . import state
    from trading.modes import holding_mode_lock
    try:
        with holding_mode_lock():           # audit S-6
            state.engine().refresh()
        log.info("[15:35] EOD refresh done")
    except Exception as e:
        log.exception("EOD refresh failed: %s", e)
    # OWNER FAULT REPORT 2026-08-30 ("grey line = stored shape to 27 Aug —
    # still it show old data"). The index tail refreshed DAILY, but only at
    # 09:20 — BEFORE the market opens — so it could only ever reach the
    # PREVIOUS day's close. Today's close waited until tomorrow morning, and
    # Friday's until Monday. This post-close run is what actually makes the
    # line current; the 09:20 run stays as the catch-up for a missed evening.
    # Isolated: a failed index pull must never cost the exit pass above.
    try:
        _weekly_index_refresh()
    except Exception:
        log.exception("index refresh failed after the close")
    try:
        _sector_index_refresh()
    except Exception:
        log.exception("sector refresh failed after the close")
    # VALFIX 2026-10-21: today's 1.30pm USD/INR reference rate and today's
    # bond close land here; isolated like the two refreshes above.
    try:
        _macro_feeds_refresh()
    except Exception:
        log.exception("macro feeds failed after the close")
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
    # Owner order 2026-10-21 (the BLSE split): the ex-date adjuster runs
    # FIRST — before the top-up merges adjusted bars and before any exit
    # pass compares an adjusted quote with an unadjusted entry.
    apply_corporate_actions_job("09:20")
    # OWNER ORDER 2026-10-02 (audit item 8): fresh bars FIRST. The top-up
    # used to run after the passes below, so they scored on bars one trading
    # day stale. Normally 08:30 already did it and this is one settings read.
    try:
        _topup_once_today("09:20")
    except Exception:
        log.exception("daily top-up failed before the scan")
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
    try:
        _sector_index_refresh()
    except Exception:
        log.exception("sector refresh failed before daily scan")
    try:
        summary = _run_category_rotation(eng, cfg, "09:20")
    finally:
        _ROTATION_LOCK.release()
    _check_pending_orders("post-scan")
    # Valuation macro feeds (display only) AFTER the rotation — reviewer
    # defect 6 (2026-10-21): their legs run in series with 30 s timeouts
    # each (RBI bond list, ~6 Upstox candle calls, the RBI home page), so
    # ahead of the scan a hanging RBI or Upstox could hold it back 4-5 min.
    try:
        _macro_feeds_refresh()
    except Exception:
        log.exception("macro feeds failed after daily scan")
    # The daily store top-up ("all fresh", owner order 2026-08-20) now runs
    # BEFORE the scans above (audit item 8, 2026-10-02).
    # 2026-10-05: the trigger layer loads its panels on top of whatever the
    # rotation left resident — that is what tipped the box over 512 MiB at
    # 10:09 IST. Give the memory back first, and again after.
    from .memory_relief import release as release_memory
    release_memory("09:20 before trigger layer")
    run_trigger_layer("09:20")
    release_memory("09:20 after trigger layer")
    _push_phone_alerts("09:20")
    log.info("[09:20] Daily multi-mode scan complete: %s", " · ".join(summary))


def _daily_universe_topup() -> None:
    """Merge fresh daily candles into the Mongo store for the scanned
    universe + held names, so the 09:20 scan reads yesterday's close from the
    store instead of the network. Runs at 08:30 and, if that did not
    complete, first thing in the 09:20 job (audit item 8, 2026-10-02 — it
    used to run AFTER the scans, a day late). Daily counterpart of the Friday job; skips names already
    fresh to yesterday. Only runs when a custom universe is set — the
    rotating full-NSE mode keeps the weekly cadence.

    SCALE, truth-passed 2026-09-24: this used to say "bounded to ~50 names".
    Since the liquidity rebuild (2026-08-30, target 1,200 since 2026-09-10)
    writes the scanned list into cfg.custom_universe, this pass covers up to
    ~1,200 names, one Upstox fetch each (~1.8 s), so ~35 minutes on a full
    day. It is TIME, not memory — each name is loaded, merged and saved
    before the next, so the working set stays one frame deep. It is also
    load-bearing: fetch_daily treats a stored doc older than
    data.STORE_MAX_AGE_DAYS as stale and falls back to the network, which is
    capped at MAX_NETWORK_FALLBACK names per scan — without this pass the
    scan would journal SCAN_COVERAGE_SHORT every day between Fridays."""
    from datetime import date, datetime as _dt

    from . import db, state
    cfg = state.cfg()
    custom = tuple(getattr(cfg, "custom_universe", ()) or ())
    if not custom:
        return False
    eng = state.engine()
    from trading.data import history_broker
    broker = getattr(eng, "broker", None) or history_broker()
    if broker is None or not db.is_connected():
        log.info("[daily-topup] skipped: %s",
                 "no Upstox client" if broker is None else "Mongo offline")
        return False
    try:
        from trading.brokers.upstox_instruments import to_instrument_keys
        from trading.history_store import top_up_history
        # 2026-10-16: the breadth gate reads the 191-name study panel, so
        # those names are kept fresh every day whatever the scan list holds.
        from trading.breadth import study_panel
        names = sorted(set(custom) | set(eng.portfolio.positions.keys())
                       | set(study_panel()))
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
        return True
    except Exception as e:
        log.exception("daily universe top-up failed: %s", e)
        return False


DAILY_TOPUP_KEY = "daily_topup_date"
# 09:20 WAITS on this while an 08:30 pass is still running, then finds the
# ledger written and skips — never two pulls of the same names at once.
_TOPUP_LOCK = threading.Lock()


def _topup_once_today(where: str) -> None:
    """Run the daily price top-up at most once per trading day (owner order
    2026-10-02, audit item 8).

    The top-up used to run AFTER the 09:20 scan passes, so every morning's
    scan read bars that ended the day before yesterday's close — one
    trading day stale. It now runs at 08:30 IST, and 09:20 calls this
    again BEFORE the scans: when 08:30 already completed it, this is one
    settings read; when the process was asleep at 08:30, the top-up runs
    first and the scans start after it. The ledger is written only after a
    pass completes, so a failed pass is retried by the next caller."""
    from datetime import date
    from . import db
    with _TOPUP_LOCK:
        today = date.today().isoformat()
        try:
            if db.is_connected() and (db.load_settings(doc_id="data_refresh")
                                      .get(DAILY_TOPUP_KEY) == today):
                return
        except Exception:
            pass
        if _daily_universe_topup():
            try:
                db.save_settings({DAILY_TOPUP_KEY: today},
                                 doc_id="data_refresh")
            except Exception:
                log.exception("[%s] could not record the daily top-up", where)


def _morning_topup_job() -> None:
    from trading.calendars import is_trading_day
    if not is_trading_day():
        return
    _topup_once_today("08:30")


TRIGGER_LAYER_MAX_TICKERS = 80    # bounded work on the 512 MB box


def _watch_scope(p) -> list:
    """Who the trigger layer and the news feed read, in priority order.

    AUDIT S-13 (owner: "go for next", 2026-10-02): held names first (the
    owner's own money), then the MOMENTUM pass's ranked top and its
    floor-clearing rows (`portfolio.momentum_shortlist`). This used to read
    `last_scan_picks` and the scan matrix, which every pass overwrites — and
    positional runs last, so the 09:20 trigger/news pass was watching the
    positional pass's names. Falls back to the old lists when no momentum
    record exists yet. Callers de-duplicate and cap."""
    ms = getattr(p, "momentum_shortlist", None) or {}
    out = list(p.positions.keys())
    if ms.get("ts"):
        out += list(ms.get("top") or [])
        out += list((ms.get("rows") or {}).keys())
    out += [pk.get("ticker") for pk in (p.last_scan_picks or [])]
    out += list(((p.scan_matrix or {}).get("rows") or {}).keys())
    return out


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
        for t in _watch_scope(p):
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
            # PROD_SOURCES = everything but the delivery file (owner
            # 2026-09-24, "hitting 512+ / layoff unwanted"): that one stream
            # peaked at +153 MB per run for a REJECTED trigger on data frozen
            # at 2026-08-07. See the note on PROD_SOURCES for the numbers.
            ext_batch = external_panels.prepare(
                tickers, news=news_rows, chain=chain,
                sources=external_panels.PROD_SOURCES)
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
            for t in _watch_scope(p):
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
