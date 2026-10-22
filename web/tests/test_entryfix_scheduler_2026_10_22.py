"""Entry-fix batch 2026-10-22, BUILDER B (owner order: "review and find flaws,
fix them for app not buying or gates which are halting"; owner cards
D5 / D6 / D7 + XR-071 + the WHY NO BUY words).

Pins:
  B-1  a box asleep at 14:30 runs the afternoon rotation when it wakes
       between 14:30 and 15:10 IST; after 15:10 nothing.
  B-2  the rotation record is saved after EVERY pass (`finished` empty until
       the end); a catch-up runs ONLY the categories a killed rotation never
       reached and finishes THAT record; the card says "cut short after X".
  B-3  every weekly step has a total time cap (a hung step is abandoned,
       journaled DATA_REFRESH_STEP_TIMEOUT, and the next step runs); the
       universe rebuild runs first; the pull keeps its own longer budget.
  B-4  the 09:20 job no longer runs the trigger layer inline: a one-shot job
       runs it 25 min later below 380 MiB, retries +15 min twice, then
       journals TRIGGER_LAYER_DEFERRED.
  B-5  the entry halt sees writes Mongo REFUSES (XR-071), and lifts itself
       at the first save that lands.
  B-6  the WHY NO BUY words for the deploy cap and a full share.
Every scheduler job is called directly with a frozen IST clock; no cron
ever fires (the web conftest keeps the scheduler unstarted).
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from trading import category_budget as cb
from trading.config import Config
from trading.paper import Portfolio
from web import db, memory_relief, scheduler, state, why_no_buy as wnb
from web.clock import IST

ORDER = list(scheduler._DAILY_MODE_ORDER)
_REAL_WEEKLY_BLOCK_DUE = scheduler.weekly_block_due      # _wire_block patches it
DAY = "2026-10-22"                       # a Thursday, an NSE trading day


def _at(hh, mm):
    return datetime(2026, 10, 22, hh, mm, tzinfo=IST)


def _res(opened=()):
    return SimpleNamespace(ranked=[{"ticker": "A.NS", "score": 70.0}],
                           picks=[{"ticker": "A.NS"}], opened=list(opened),
                           skipped=[])


def _rot(where, passes, finished=None, rid="r1"):
    return {"rid": rid, "date": DAY, "where": where,
            "started": "2026-10-22T09:00:00", "order": list(ORDER),
            "passes": {m: wnb.pass_record(m, _res(), [], 62.0, None) for m in passes},
            "opened_total": 0, "finished": finished}


@pytest.fixture()
def book(tmp_path, monkeypatch):
    """A fresh book the catch-up reads, an in-hours scan already done today,
    the catch-up armed, and every started thread run inline."""
    cfg = Config(state_dir=str(tmp_path), mode="swing")
    cfg.apply_mode_preset()
    p = Portfolio.fresh(cfg)
    p.last_scan = p.last_scan_live = datetime.utcnow().isoformat()
    monkeypatch.setenv("SB_DISABLE_CATCHUP", "0")
    monkeypatch.setattr(scheduler, "daily_enabled", lambda: True)
    monkeypatch.setattr("trading.calendars.is_trading_day", lambda *a, **k: True)
    monkeypatch.setattr(state, "portfolio", lambda: p)
    monkeypatch.setattr(state, "cfg", lambda: cfg)
    monkeypatch.setattr(scheduler, "_universe_refresh_job", lambda: None)
    monkeypatch.setattr("trading.universe_refresh.refreshed_today", lambda: True)
    # raising=False: so the same file can be run against bdc82ec (which has
    # neither) and judged on behaviour, not on a missing name
    monkeypatch.setattr(scheduler, "_CURRENT_ROTATION", {}, raising=False)

    class _Inline:
        def __init__(self, target=None, daemon=None, **kw):
            self._t = target

        def start(self):
            self._t()
    monkeypatch.setattr("threading.Thread", _Inline)
    return p, cfg


def _clock(monkeypatch, hh, mm):
    monkeypatch.setattr(scheduler, "_ist_now", lambda: _at(hh, mm), raising=False)


# =========================================================== B-1 the 14:30 slot

def test_a_box_that_wakes_at_1440_runs_the_missed_afternoon_rotation(book, monkeypatch):
    """THE confirmed fault: the morning stamped last_scan_live, so the boot
    catch-up returned and the afternoon rotation was simply lost."""
    p, _ = book
    p.rotation_report = {"rotations": [_rot("09:20", ORDER, finished="2026-10-22T04:30:00")]}
    calls = []
    monkeypatch.setattr(scheduler, "_deployment_topup_scan",
                        lambda resume=None: calls.append(resume))
    _clock(monkeypatch, 14, 40)
    assert scheduler.catchup_scan_if_missed("startup") is True
    assert calls == [None]                        # the full five-category pass
    line = [e for e in p.decision_journal if e.get("action") == "SCAN_CATCHUP"][-1]
    assert line["slot"] == "14:30" and line["plan"] == "afternoon"


def test_after_1510_the_afternoon_catchup_runs_nothing(book, monkeypatch):
    p, _ = book
    calls = []
    monkeypatch.setattr(scheduler, "_deployment_topup_scan",
                        lambda resume=None: calls.append(resume))
    _clock(monkeypatch, 15, 12)
    assert scheduler.catchup_scan_if_missed("startup") is False
    p.rotation_report = {"rotations": [_rot("14:30", ORDER[:2])]}   # cut short
    assert scheduler.catchup_scan_if_missed("startup") is False
    assert calls == []


def test_a_finished_1430_rotation_is_never_run_again(book, monkeypatch):
    p, _ = book
    p.rotation_report = {"rotations": [_rot("14:30", ORDER, finished="2026-10-22T09:20:00")]}
    monkeypatch.setattr(scheduler, "_deployment_topup_scan",
                        lambda resume=None: pytest.fail("ran twice"))
    _clock(monkeypatch, 14, 55)
    assert scheduler.catchup_scan_if_missed("upstox_connect") is False


def test_a_rotation_running_in_this_process_is_never_doubled(book, monkeypatch):
    p, _ = book
    p.rotation_report = {"rotations": [_rot("14:30", ORDER[:1], rid="live")]}
    monkeypatch.setitem(scheduler.__dict__.setdefault("_CURRENT_ROTATION", {}), "rid", "live")
    monkeypatch.setattr(scheduler, "_deployment_topup_scan",
                        lambda resume=None: pytest.fail("doubled"))
    _clock(monkeypatch, 14, 45)
    assert scheduler.catchup_scan_if_missed("upstox_connect") is False


def test_a_job_holding_the_rotation_lock_is_never_doubled(book, monkeypatch):
    """The 14:30 cron holds the lock through its corporate-action step
    before the rotation marker exists."""
    monkeypatch.setattr(scheduler, "_deployment_topup_scan",
                        lambda resume=None: pytest.fail("doubled"))
    _clock(monkeypatch, 14, 31)
    assert scheduler._ROTATION_LOCK.acquire(blocking=False)
    try:
        assert scheduler.catchup_scan_if_missed("startup") is False
    finally:
        scheduler._ROTATION_LOCK.release()


def test_the_morning_catchup_is_unchanged_when_no_scan_ran_today(book, monkeypatch):
    p, _ = book
    p.last_scan = p.last_scan_live = None
    ran = []
    monkeypatch.setattr(scheduler, "_daily_multi_mode_scan", lambda resume=None: ran.append(resume))
    monkeypatch.setattr(scheduler, "apply_corporate_actions_job", lambda where: None)
    _clock(monkeypatch, 10, 0)
    assert scheduler.catchup_scan_if_missed("startup") is True
    assert ran == [None]


# =========================================================== B-2 killed rotation

class _Killed(BaseException):
    """Stands in for the OOM kill: no `except Exception` catches it, so the
    rotation stops exactly where a dead process would."""


def _fake_eng(cfg, p, calls, kill_at=None):
    def scan():
        calls.append(cfg.mode)
        if cfg.mode == kill_at:
            raise _Killed()
        return _res()
    return SimpleNamespace(portfolio=p, scan=scan, refresh=lambda: None)


def test_a_killed_rotation_leaves_its_finished_passes_on_the_saved_record(tmp_path):
    """Old code stored the record only after the loop: a kill left nothing."""
    cfg = Config(state_dir=str(tmp_path), mode="swing")
    cfg.apply_mode_preset()
    p = Portfolio.fresh(cfg)
    calls: list = []
    with pytest.raises(_Killed):
        scheduler._run_category_rotation(_fake_eng(cfg, p, calls, kill_at="short"), cfg, "14:30")
    assert calls == ORDER[:3]
    stored = Portfolio.load_or_fresh(cfg).rotation_report or {}   # what the next process reads
    rots = stored.get("rotations") or []
    assert len(rots) == 1, rots
    assert rots[0]["where"] == "14:30" and rots[0]["finished"] is None
    assert set(rots[0]["passes"]) == {"nifty200_momentum", "intraday"}
    assert cfg.mode == "swing"                       # owner's mode restored
    assert scheduler.rotation_in_progress() is None  # marker cleared


def test_one_rotation_stays_one_record_however_often_it_saves(tmp_path):
    cfg = Config(state_dir=str(tmp_path), mode="swing")
    cfg.apply_mode_preset()
    p = Portfolio.fresh(cfg)
    calls: list = []
    scheduler._run_category_rotation(_fake_eng(cfg, p, calls), cfg, "09:20")
    rots = p.rotation_report["rotations"]
    assert len(rots) == 1 and rots[0]["finished"] and set(rots[0]["passes"]) == set(ORDER)


def test_a_boot_at_1450_runs_only_the_categories_the_killed_rotation_missed(book, monkeypatch):
    p, cfg = book
    cut = _rot("14:30", ["nifty200_momentum", "intraday"], rid="cut1")
    p.rotation_report = {"rotations": [cut]}
    calls: list = []
    eng = _fake_eng(cfg, p, calls)
    monkeypatch.setattr(state, "engine", lambda: eng)
    monkeypatch.setattr(scheduler, "_degraded_halt", lambda where: False)
    monkeypatch.setattr(scheduler, "apply_corporate_actions_job", lambda where: None)
    monkeypatch.setattr(scheduler, "_push_phone_alerts", lambda where: None)
    monkeypatch.setattr(scheduler, "_check_pending_orders", lambda where: None)
    _clock(monkeypatch, 14, 50)
    assert scheduler.catchup_scan_if_missed("startup") is True
    assert calls == ["short", "swing", "positional"]          # never momentum / intraday again
    rots = p.rotation_report["rotations"]
    assert len(rots) == 1 and rots[0]["rid"] == "cut1"        # the SAME record, finished
    assert rots[0]["finished"] and set(rots[0]["passes"]) == set(ORDER)
    assert rots[0]["resumed"][0]["modes"] == ["short", "swing", "positional"]
    summary = [e for e in p.decision_journal if e.get("action") == "ROTATION_SUMMARY"][-1]
    assert "Resumed after a restart (Short, Swing, Positional)" in summary["why"]
    catchup = [e for e in p.decision_journal if e.get("action") == "SCAN_CATCHUP"][-1]
    assert catchup["plan"] == "resume" and catchup["missing"] == ["short", "swing", "positional"]


def test_a_morning_rotation_cut_short_resumes_only_if_it_fits_before_1430(book, monkeypatch):
    p, _ = book
    p.rotation_report = {"rotations": [_rot("09:20", ["nifty200_momentum", "intraday"])]}
    seen = []
    monkeypatch.setattr(scheduler, "_daily_multi_mode_scan", lambda resume=None: seen.append(resume))
    _clock(monkeypatch, 10, 30)
    assert scheduler.catchup_scan_if_missed("startup") is True
    assert seen and seen[0]["rid"] == "r1"
    # three missing passes at 12 min (ASSUMED) from 14:00 run past 14:30
    seen.clear()
    _clock(monkeypatch, 14, 0)
    assert scheduler.catchup_scan_if_missed("startup") is False and seen == []


def test_the_card_labels_a_dead_rotation_cut_short_and_a_live_one_in_progress(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.rotation_report = {"rotations": [_rot("14:30", ["nifty200_momentum", "intraday"], rid="x")]}
    c = wnb.card(p, cfg, DAY, running=None)
    assert c["status"] == "cut_short"
    assert c["status_label"].startswith("cut short after Intraday")
    not_run = {r["mode"]: r["stopped_by"] for r in c["rows"] if not r.get("scanned")}
    assert not_run == {m: "not run (process restarted)" for m in ("short", "swing", "positional")}
    live = wnb.card(p, cfg, DAY, running="x")
    assert live["status"] == "running" and "2 of 5" in live["status_label"]
    assert "not run yet (rotation in progress)" in [r["stopped_by"] for r in live["rows"]]


def test_the_dashboard_prints_cut_short(tmp_path):
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from web import auth as auth_mod
    from web.clock import ist_today_iso
    from web.main import app
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    rot = _rot("14:30", ["nifty200_momentum", "intraday"], rid="dead")
    rot["date"] = ist_today_iso()
    p.rotation_report = {"rotations": [rot]}
    p.save()
    with patch.object(state, "portfolio", lambda: p), patch.object(state, "cfg", lambda: cfg):
        c = TestClient(app)
        assert c.post("/login", data={"email": "owner@sourceborn.in",
                                      "password": auth_mod._SEEDED_PWD}).status_code == 200
        body = c.get("/").text
    assert 'data-why-no-buy-status="cut_short"' in body
    assert "cut short after Intraday" in body and "not run (process restarted)" in body


# =========================================================== B-3 weekly step cap

@pytest.fixture()
def steps(monkeypatch):
    monkeypatch.setattr(scheduler, "_ABANDONED_STEPS", {}, raising=False)
    monkeypatch.setattr(memory_relief, "release", lambda label="": {})
    out: list = []

    def journal(action, why, **extra):
        out.append({"action": action, "why": why, **extra})
    return out, journal


def test_a_hung_step_is_abandoned_journaled_and_the_next_step_still_runs(steps):
    out, journal = steps
    gate = threading.Event()           # the "hung" step waits on this, never a long sleep
    t0 = time.monotonic()
    assert scheduler._run_weekly_step("slow feed", lambda: gate.wait(10), journal,
                                      budget_s=0.5) is False
    assert time.monotonic() - t0 < 3, "the block waited for the hung step"
    tm = out[-1]
    assert tm["action"] == "DATA_REFRESH_STEP_TIMEOUT" and tm["step"] == "slow feed"
    assert tm["elapsed_s"] >= 0.5
    ran = []
    assert scheduler._run_weekly_step("next feed", lambda: ran.append(1), journal,
                                      budget_s=0.5) is True and ran == [1]
    # the hung one is still running: it is not started a second time on top
    again = []
    assert scheduler._run_weekly_step("slow feed", lambda: again.append(1), journal,
                                      budget_s=0.5) is False
    assert again == [] and out[-1].get("still_running") is True
    gate.set()                         # let the abandoned thread finish now


def test_a_crashing_step_still_journals_step_failed(steps):
    out, journal = steps

    def boom():
        raise RuntimeError("feed blew up")
    assert scheduler._run_weekly_step("FII tide (NSDL)", boom, journal, budget_s=2) is False
    assert out[-1]["action"] == "DATA_REFRESH_STEP_FAILED" and "FII tide" in out[-1]["why"]


def test_step_budgets_ten_minutes_with_longer_caps_for_self_bounded_steps(monkeypatch):
    monkeypatch.delenv("SB_WEEKLY_STEP_BUDGET_S", raising=False)
    monkeypatch.delenv("SB_TOPUP_BUDGET_S", raising=False)
    monkeypatch.delenv("SB_FUNDAMENTALS_BUDGET_S", raising=False)
    assert scheduler.weekly_step_budget_s() == 600.0
    assert scheduler._weekly_step_budget_for("SEBI orders") == 600.0
    assert scheduler._weekly_step_budget_for("liquidity universe") == 600.0
    assert scheduler._weekly_step_budget_for("fundamentals refresh") == 600.0 + 300.0
    assert scheduler._weekly_step_budget_for(scheduler.WEEKLY_PULL_LABEL) == 2400.0 + 300.0
    monkeypatch.setenv("SB_WEEKLY_STEP_BUDGET_S", "0")
    assert scheduler._weekly_step_budget_for("SEBI orders") is None   # owner switch: no cap


FEEDS = ("_weekly_fii_refresh", "_weekly_index_refresh", "_sector_index_refresh",
         "_monthly_constituents_snapshot", "_monthly_fundamentals_refresh",
         "_weekly_sebi_refresh")
POST = ("refresh_breadth_store", "run_register_precompute", "run_valuation_precompute")


def _wire_block(monkeypatch, calls, slow=None, pull_sleep=0.0, gate=None):
    monkeypatch.setattr("trading.calendars.is_trading_day", lambda d=None: True)
    monkeypatch.setattr("web.db.is_connected", lambda: True)
    monkeypatch.setattr("web.db.list_history_meta", lambda: [])
    monkeypatch.setattr("trading.data.history_broker", lambda: object())
    monkeypatch.setattr("trading.universes.load_full_universe", lambda *a, **k: ["TCS.NS"])
    monkeypatch.setattr("trading.brokers.upstox_instruments.to_instrument_keys",
                        lambda names: {n: f"KEY|{n}" for n in names})
    monkeypatch.setattr(scheduler, "weekly_block_due", lambda *a, **k: (True, "test"))
    monkeypatch.setattr(scheduler, "_ABANDONED_STEPS", {}, raising=False)
    monkeypatch.setattr(memory_relief, "release", lambda label="": {})
    marked = {}
    monkeypatch.setattr(scheduler, "_mark_weekly_block_done",
                        lambda remaining=0, pull_cut=False: marked.update(
                            remaining=remaining, pull_cut=pull_cut))

    def _topup(*a, **k):
        calls.append("pull")
        time.sleep(pull_sleep)
        return {"ok": 1, "skipped_fresh": 0, "failed": 0, "total": 1, "remaining": 0}
    monkeypatch.setattr("trading.history_store.top_up_history", _topup)

    def _mk(n):
        def _fn(*a, **k):
            calls.append(n)
            if n == slow:
                gate.wait(10)          # hangs until the test releases it
        return _fn
    for name in FEEDS + POST + ("refresh_liquid_universe",):
        monkeypatch.setattr(scheduler, name, _mk(name))
    return marked


def test_the_universe_rebuild_runs_first_and_a_hung_feed_costs_nothing_behind_it(monkeypatch):
    calls: list = []
    gate = threading.Event()
    _wire_block(monkeypatch, calls, slow="_weekly_fii_refresh", gate=gate)
    monkeypatch.setenv("SB_WEEKLY_STEP_BUDGET_S", "0.5")
    p = state.engine().portfolio
    n0 = len(p.decision_journal)
    t0 = time.monotonic()
    try:
        scheduler._weekly_history_topup()
    finally:
        gate.set()                     # release the abandoned FII thread
    assert time.monotonic() - t0 < 4, "the block waited on the hung FII feed"
    assert calls[0] == "refresh_liquid_universe"            # FRONT of the block
    assert calls.index("refresh_liquid_universe") < calls.index("_weekly_fii_refresh")
    for name in FEEDS + POST:
        assert name in calls, name
    assert calls.index("_weekly_sebi_refresh") < calls.index("pull")
    timeouts = [e for e in p.decision_journal[n0:]
                if e.get("action") == "DATA_REFRESH_STEP_TIMEOUT"]
    assert [e["step"] for e in timeouts] == ["FII tide (NSDL)"]


def test_a_pull_left_running_at_its_cap_keeps_the_block_due_tomorrow(monkeypatch):
    calls: list = []
    marked = _wire_block(monkeypatch, calls, pull_sleep=1.0)
    monkeypatch.setattr(scheduler, "_weekly_step_budget_for",
                        lambda label: 0.3 if label == scheduler.WEEKLY_PULL_LABEL else 5)
    scheduler._weekly_history_topup()
    for th, _ in list(scheduler._ABANDONED_STEPS.values()):
        th.join(5)                     # let the abandoned pull finish before the next test
    assert marked == {"remaining": 0, "pull_cut": True}    # unknown remainder: no fake count
    for name in POST:
        assert name in calls, f"{name} lost to a hung pull"
    from datetime import date as _date
    monkeypatch.setattr(db, "load_settings", lambda doc_id=None: {
        scheduler.WEEKLY_BLOCK_KEY: "2026-10-22", scheduler.WEEKLY_BLOCK_LEFT_KEY: 0,
        scheduler.WEEKLY_BLOCK_PULL_CUT_KEY: True})
    due, why = _REAL_WEEKLY_BLOCK_DUE(_date(2026, 10, 22))
    assert due is True and "time cap" in why


# =========================================================== B-4 trigger cool-down

class _FakeSched:
    def __init__(self):
        self.jobs = []

    def add_job(self, fn, trigger, **kw):
        self.jobs.append({"fn": fn, "trigger": trigger, **kw})


def test_the_0920_job_arms_the_cooldown_instead_of_running_the_layer(monkeypatch):
    order = []
    fake = _FakeSched()
    monkeypatch.setattr(scheduler, "_SCHED", fake)
    monkeypatch.setattr("trading.calendars.is_trading_day", lambda *a, **k: True)
    monkeypatch.setattr(scheduler, "_degraded_halt", lambda where: False)
    monkeypatch.setattr(scheduler, "apply_corporate_actions_job", lambda where: None)
    monkeypatch.setattr(scheduler, "_topup_once_today", lambda where: None)
    monkeypatch.setattr(scheduler, "refresh_breadth_store", lambda where: None)
    monkeypatch.setattr(scheduler, "_weekly_index_refresh", lambda: None)
    monkeypatch.setattr(scheduler, "_sector_index_refresh", lambda: None)
    monkeypatch.setattr(scheduler, "_macro_feeds_refresh", lambda: None)
    monkeypatch.setattr(scheduler, "_market_feeds_refresh", lambda: None)
    monkeypatch.setattr(scheduler, "_run_category_rotation",
                        lambda eng, cfg, where: order.append("rotation") or [])
    monkeypatch.setattr(scheduler, "_check_pending_orders", lambda *a: None)
    monkeypatch.setattr(scheduler, "_push_phone_alerts", lambda *a, **k: None)
    monkeypatch.setattr(scheduler, "run_trigger_layer",
                        lambda where="manual": order.append("trigger") or {})
    monkeypatch.setattr(memory_relief, "release", lambda label="": {})
    before = datetime.now(IST)
    scheduler._daily_multi_mode_scan()
    assert order == ["rotation"], "the trigger layer still runs on top of the rotation"
    assert len(fake.jobs) == 1
    job = fake.jobs[0]
    assert job["fn"] is scheduler._trigger_layer_cooldown and job["trigger"] == "date"
    assert job["id"] == "trigger_layer_cooldown" and job["args"] == ["09:20", 0]
    assert job["replace_existing"] is True and job["misfire_grace_time"] == 1800
    run = job["run_date"]
    assert run.tzinfo is not None                      # never a naive (UTC-on-Render) clock
    assert timedelta(minutes=24) < run - before < timedelta(minutes=26)


@pytest.fixture()
def cooldown(monkeypatch):
    fake = _FakeSched()
    ran = []
    monkeypatch.setattr(scheduler, "_SCHED", fake)
    monkeypatch.setattr(memory_relief, "release", lambda label="": {})
    monkeypatch.setattr(scheduler, "_push_phone_alerts", lambda *a, **k: None)
    monkeypatch.setattr(scheduler, "run_trigger_layer", lambda where="manual": ran.append(where) or {})
    monkeypatch.setattr(scheduler, "_trigger_rss_ceiling_mb", lambda: 380.0, raising=False)
    return fake, ran


def test_the_cooldown_runs_the_layer_when_memory_is_low(cooldown, monkeypatch):
    fake, ran = cooldown
    monkeypatch.setattr(memory_relief, "rss_mb", lambda: 300.0)
    assert scheduler._trigger_layer_cooldown("09:20", 0) == "ran"
    assert ran == ["09:20 cool-down"] and fake.jobs == []


def test_the_cooldown_hands_memory_back_before_and_after_the_layer(cooldown, monkeypatch):
    fake, ran = cooldown
    order = []
    monkeypatch.setattr(memory_relief, "rss_mb", lambda: 250.0)
    monkeypatch.setattr(memory_relief, "release", lambda label="": order.append("release") or {})
    monkeypatch.setattr(scheduler, "run_trigger_layer",
                        lambda where="manual": order.append("trigger") or {})
    assert scheduler._trigger_layer_cooldown("09:20", 0) == "ran"
    assert order == ["release", "trigger", "release"]


def test_the_cooldown_retries_twice_then_journals_deferred(cooldown, monkeypatch):
    fake, ran = cooldown
    monkeypatch.setattr(memory_relief, "rss_mb", lambda: 451.0)
    p = state.portfolio()
    n0 = len(p.decision_journal)
    assert scheduler._trigger_layer_cooldown("09:20", 0) == "retry"
    assert fake.jobs[-1]["args"] == ["09:20", 1]
    assert scheduler._trigger_layer_cooldown("09:20", 1) == "retry"
    assert fake.jobs[-1]["args"] == ["09:20", 2]
    assert scheduler._trigger_layer_cooldown("09:20", 2) == "deferred"
    assert len(fake.jobs) == 2 and ran == []
    line = [e for e in p.decision_journal[n0:] if e.get("action") == "TRIGGER_LAYER_DEFERRED"]
    assert len(line) == 1 and line[0]["rss_mb"] == 451.0 and line[0]["attempts"] == 3
    assert "451 MiB" in line[0]["why"] and "380 MiB" in line[0]["why"]


def test_the_cooldown_waits_while_a_rotation_holds_the_lock(cooldown, monkeypatch):
    fake, ran = cooldown
    monkeypatch.setattr(memory_relief, "rss_mb", lambda: 200.0)
    assert scheduler._ROTATION_LOCK.acquire(blocking=False)
    try:
        assert scheduler._trigger_layer_cooldown("09:20", 0) == "retry"
    finally:
        scheduler._ROTATION_LOCK.release()
    assert ran == [] and fake.jobs[-1]["args"] == ["09:20", 1]


def test_the_ceiling_is_the_dens_380_in_production(monkeypatch):
    monkeypatch.delenv("SB_DEN_CHAIN_RSS_CEILING_MB", raising=False)
    assert scheduler._trigger_rss_ceiling_mb() == 380.0


def test_every_assumed_number_names_its_source():
    a = scheduler.ASSUMED
    expect = {"afternoon_catchup_end_ist": (15, 10), "trigger_cooldown_min": 25,
              "trigger_retry_min": 15, "trigger_max_retries": 2,
              "trigger_rss_ceiling_mb": 380.0, "weekly_step_budget_s": 600.0}
    for k, v in expect.items():
        assert a[k]["value"] == v, k
    for k, row in a.items():
        assert len(row.get("source") or "") > 20, k
    assert scheduler.TRIGGER_COOLDOWN_MIN == 25 and scheduler.TRIGGER_MAX_RETRIES == 2


# =========================================================== B-5 refused writes

class _Coll:
    def __init__(self):
        self.fail = True

    def replace_one(self, *a, **k):
        if self.fail:
            raise RuntimeError("OperationFailure: you are over your space quota")


def test_entries_halt_while_mongo_refuses_writes_and_lift_when_a_save_lands(monkeypatch):
    state.portfolio()                                 # boot the book before Mongo is faked
    coll = _Coll()
    monkeypatch.setenv("MONGO_URL", "mongodb://quota.invalid:27017")
    monkeypatch.setattr(db, "_CONNECTED", True)
    monkeypatch.setattr(db, "_DB", SimpleNamespace(portfolio=coll))
    monkeypatch.setattr(db, "LAST_WRITE_ERROR", "OperationFailure: over quota")
    monkeypatch.setattr(db, "LAST_WRITE_ERROR_AT", "2026-10-22T04:00:00")
    assert state.mongo_degraded() is False            # the old predicate saw nothing
    assert scheduler._degraded_halt("test") is True   # bdc82ec: False — it kept buying
    assert db.writes_landing() is False
    today = datetime.utcnow().date().isoformat()
    p = state.portfolio()
    assert any(e.get("action") == "MONGO_WRITE_REFUSED_HALT"
               and str(e.get("ts", "")).startswith(today) for e in p.decision_journal[-100:])
    coll.fail = False                                 # the owner freed space
    assert scheduler._degraded_halt("test") is False
    assert db.LAST_WRITE_ERROR is None and db.writes_landing() is True


def test_writes_landing_is_true_without_mongo_configured(monkeypatch):
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.setattr(db, "LAST_WRITE_ERROR", "stale")
    assert db.writes_landing() is True


# =========================================================== B-6 the card's words

def test_the_deploy_cap_and_share_full_words():
    assert wnb.REASON_WORDS["reserve_breached"] == (
        "deploy cap reached (regime reserve, your own buys and Ocean holdings included)")
    rec = wnb.pass_record("swing", SimpleNamespace(
        ranked=[{"ticker": "A.NS", "score": 70.0}] * 3, picks=[{"ticker": "A.NS"}] * 3,
        opened=[], skipped=[{"ticker": "A.NS", "reason": "category_budget_full x"},
                            {"ticker": "B.NS", "reason": "reserve_breached"}]),
        [], 60.0, None)
    text = wnb.stopped_by(rec)
    assert "share full (of what is left after your own buys and Ocean) 1" in text
    assert "deploy cap reached (regime reserve, your own buys and Ocean holdings included) 1" in text
    assert "regime cash reserve" not in text
    assert wnb.BLOCK_WORDS["CATEGORY_BUDGET_FULL"].endswith("after your own buys and Ocean)")


def test_the_card_names_a_halted_day(tmp_path):
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.decision_journal.append({"ts": f"{DAY}T04:00:00", "action": "MONGO_WRITE_REFUSED_HALT",
                               "why": "[09:20] the database is refusing to save the book"})
    c = wnb.card(p, cfg, DAY, running=None)
    assert any(line.startswith("Entries halted today") for line in c["book"])
