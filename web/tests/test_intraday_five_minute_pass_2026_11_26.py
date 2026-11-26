"""RUN-20261125-01 — the 5-minute Intraday pass runs whatever the app's mode.

Owner order 2026-11-26 (question 12, default approved): "scan every 5
minutes all session, whatever the mode". Before it the Intraday segment was
scored twice a day, inside the 09:20 and 14:30 rotations; the 5-minute tick
existed but returned at its first line unless the WHOLE app was switched to
Intraday mode and a Render toggle was set.

These tests pin the scheduling: when the pass runs, when it stands down, and
that the owner's mode is always handed back. The trading behaviour of the
pass itself is pinned in trading/tests/test_intraday_pipeline_2026_11_26.py.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from web import scheduler, state


@pytest.fixture
def tick(monkeypatch):
    """The live objects, with the engine's scan replaced by a recorder."""
    cfg, eng = state.cfg(), state.engine()
    original = cfg.mode
    cfg.mode = "nifty200_momentum"
    cfg.apply_mode_preset()
    cfg.intraday_always_scan = True
    calls = []
    result = SimpleNamespace(ranked=[1, 2, 3], picks=[1], opened=[])

    def scan(*a, **k):
        calls.append({"mode": cfg.mode, "floor": cfg.min_score_to_enter,
                      "side_pass": k.get("side_pass", False)})
        return result
    monkeypatch.setattr(eng, "scan", scan)
    monkeypatch.setattr(eng, "refresh", lambda *a, **k: calls.append({"refresh": True}))
    monkeypatch.setattr(scheduler, "_degraded_halt", lambda where: False)
    monkeypatch.setattr(scheduler, "_check_pending_orders", lambda *a: None)
    pushed = []
    monkeypatch.setattr(scheduler, "_push_phone_alerts", lambda where: pushed.append(where))
    monkeypatch.delenv("INTRADAY_SCHEDULER", raising=False)
    try:
        yield SimpleNamespace(cfg=cfg, eng=eng, calls=calls, result=result, pushed=pushed)
    finally:
        cfg.mode = original
        cfg.apply_mode_preset()
        cfg.intraday_always_scan = True


def test_the_pass_runs_in_any_mode_and_hands_the_owners_mode_back(tick):
    momentum_floor = tick.cfg.min_score_to_enter
    scheduler._intraday_tick()
    assert tick.calls == [{"mode": "intraday", "floor": 72.0, "side_pass": True}]
    assert tick.cfg.mode == "nifty200_momentum"
    assert tick.cfg.min_score_to_enter == momentum_floor != 72.0
    assert tick.pushed == []                      # nothing bought, nothing to push


def test_a_buy_is_pushed_to_the_phone(tick):
    tick.result.opened = ["UPP.NS"]
    assert scheduler._intraday_side_pass() == "scored 3, cleared 1, opened 1"
    assert tick.pushed == ["intraday 5-min"]


def test_a_crashed_pass_still_restores_the_mode_and_never_raises(tick, monkeypatch):
    def boom(*a, **k):
        assert tick.cfg.mode == "intraday"
        raise RuntimeError("provider fell over")
    monkeypatch.setattr(tick.eng, "scan", boom)
    assert scheduler._intraday_side_pass() == "failed: RuntimeError"
    assert tick.cfg.mode == "nifty200_momentum"
    scheduler._intraday_tick()                    # the job itself never raises
    assert tick.cfg.mode == "nifty200_momentum"


def test_the_owners_switch_turns_it_off(tick):
    tick.cfg.intraday_always_scan = False
    scheduler._intraday_tick()
    assert tick.calls == []


def test_it_stands_down_outside_the_session(tick, monkeypatch):
    monkeypatch.setattr("trading.calendars.is_market_open", lambda *a, **k: False)
    assert scheduler._intraday_side_pass() == "skipped: market closed"
    assert tick.calls == []


def test_it_stands_down_after_the_last_buy_time(tick, monkeypatch):
    seen = []

    def allowed(mode, now, cfg):
        seen.append(mode)
        return False
    monkeypatch.setattr("trading.intraday_recovery.auto_entry_allowed", allowed)
    assert scheduler._intraday_side_pass() == "skipped: past the last intraday buy time"
    assert seen == ["intraday"] and tick.calls == []


def test_it_never_runs_beside_a_category_rotation(tick):
    assert scheduler._ROTATION_LOCK.acquire(blocking=False)
    try:
        assert scheduler._intraday_side_pass() == "skipped: a category rotation is running"
    finally:
        scheduler._ROTATION_LOCK.release()
    assert tick.calls == []


def test_it_skips_instead_of_waiting_behind_other_book_work(tick):
    from trading.modes import MODE_LOCK
    holding, release = threading.Event(), threading.Event()

    def other_work():
        with MODE_LOCK:
            holding.set()
            release.wait(5)
    worker = threading.Thread(target=other_work)
    worker.start()
    try:
        assert holding.wait(5)
        assert scheduler._intraday_side_pass() == "skipped: other book work is running"
    finally:
        release.set()
        worker.join(5)
    assert tick.calls == [] and tick.cfg.mode == "nifty200_momentum"


def test_it_does_no_work_while_the_book_cannot_be_saved(tick, monkeypatch):
    asked = []
    monkeypatch.setattr(scheduler, "_degraded_halt", lambda where: asked.append(where) or True)
    assert scheduler._intraday_side_pass() == "skipped: portfolio save not confirmed"
    assert asked == ["intraday 5-min"] and tick.calls == []


def test_intraday_mode_without_the_legacy_toggle_gets_the_same_quiet_pass(tick):
    tick.cfg.mode = "intraday"
    tick.cfg.apply_mode_preset()
    scheduler._intraday_tick()
    assert tick.calls == [{"mode": "intraday", "floor": 72.0, "side_pass": True}]
    assert tick.cfg.mode == "intraday"


def test_the_legacy_toggle_keeps_its_old_full_refresh_and_scan(tick, monkeypatch):
    monkeypatch.setenv("INTRADAY_SCHEDULER", "1")
    tick.cfg.mode = "intraday"
    tick.cfg.apply_mode_preset()
    scheduler._intraday_tick()
    assert tick.calls == [{"refresh": True},
                          {"mode": "intraday", "floor": 72.0, "side_pass": False}]
    # the toggle alone, in another mode, is not the legacy track
    tick.calls.clear()
    tick.cfg.mode = "swing"
    tick.cfg.apply_mode_preset()
    scheduler._intraday_tick()
    assert tick.calls == [{"mode": "intraday", "floor": 72.0, "side_pass": True}]
    assert tick.cfg.mode == "swing"


@pytest.mark.parametrize("daily,registered", [("1", True), ("0", False)])
def test_the_job_is_on_the_always_on_daily_track(monkeypatch, daily, registered):
    monkeypatch.setenv("DAILY_SCHEDULER", daily)
    monkeypatch.delenv("INTRADAY_SCHEDULER", raising=False)
    monkeypatch.setenv("SB_DISABLE_CATCHUP", "1")
    monkeypatch.setenv("SB_SCHEDULER_NO_START", "1")
    monkeypatch.setattr(scheduler, "_SCHED", None)
    scheduler.start_if_enabled()
    job = scheduler._SCHED.get_job("intraday_tick")
    assert bool(job) is registered
    if not registered:
        return
    assert job.func is scheduler._intraday_tick and job.max_instances == 1
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert (fields["day_of_week"], fields["hour"], fields["minute"]) == ("mon-fri", "9-15", "*/5")


def test_render_config_needs_no_new_setting():
    """The pass must work on the live service as it is configured today:
    the daily track on, the legacy toggle off (so the quiet pass is the one
    that runs), and the test-only market-hours override never set."""
    import re
    from pathlib import Path
    text = Path(__file__).resolve().parents[2].joinpath("render.yaml").read_text()
    live = "\n".join(line.split("#")[0] for line in text.splitlines())
    toggle = re.search(r'key:\s*INTRADAY_SCHEDULER\s*\n\s*value:\s*"?(\w+)"?', live)
    assert toggle is None or toggle.group(1) == "0"
    assert not re.search(r'key:\s*DAILY_SCHEDULER\s*\n\s*value:\s*"?0"?', live)
    assert "SB_IGNORE_MARKET_HOURS" not in live


# ------------------------------------------- the Scan page shows the pass

def _scan_page(tmp_path, monkeypatch, status):
    """Render /scan with a stored intraday status; the view may not fetch."""
    from fastapi.testclient import TestClient
    from trading.config import Config
    from trading.engine import TradingEngine
    from trading.paper import Portfolio
    from web import auth
    from web.main import app
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.intraday_data_status = status
    e = TradingEngine(cfg, p)
    monkeypatch.setattr(state, "cfg", lambda: cfg)
    monkeypatch.setattr(state, "portfolio", lambda: p)
    monkeypatch.setattr(state, "engine", lambda: e)
    monkeypatch.setattr(auth, "current_user", lambda r: {"role": "owner", "email": "fixture"})

    def forbidden(*a, **k):
        raise AssertionError("view must not fetch or trigger a scan")
    monkeypatch.setattr("trading.intraday_data.fetch", forbidden)
    monkeypatch.setattr(e, "scan", forbidden)
    page = TestClient(app).get("/scan")
    assert page.status_code == 200, page.text[:400]
    return " ".join(page.text.split())


def _stored_status(**extra):
    from web.tests.test_intraday_data_2026_11_12 import coverage
    status = coverage()
    status.update(extra)
    return status


def test_the_scan_page_states_the_list_the_timing_and_the_last_pass(tmp_path, monkeypatch):
    text = _scan_page(tmp_path, monkeypatch, _stored_status(
        pool=847, list_rule="top 100 of 847 by median rupee turnover, last 20 daily bars",
        timing={"before_fetch_s": 14.2, "fetch_s": 41.0, "scored_after_fetch_s": 3.5, "scored": 96},
        last_pass={"at": "2026-11-26T05:05:00", "asked": 100, "ready": 97, "scored": 96,
                   "cleared_score": 4, "opened": ["TCS.NS"],
                   "not_bought": [{"reason": "score", "stocks": 92},
                                  {"reason": "intraday_position_cap", "stocks": 3}],
                   "timing": None}))
    assert "Stocks read on every pass: top 100 of 847 by median rupee turnover" in text
    assert "advances through the remaining names" not in text
    assert "bars fetched in 41.0 s and scored 3.5 s later (96 stocks scored)" in text
    assert "Last 5-minute pass" in text and "97 of 100 stocks had usable bars" in text
    assert "bought: TCS.NS" in text
    assert "score: 92 · intraday position cap: 3" in text


@pytest.mark.parametrize("extra", [
    {},                                                       # what the live book holds today
    {"list_rule": "whole scan list", "pool": None},
    {"timing": None, "last_pass": None},
    {"last_pass": {"at": "2026-11-26T05:05:00", "asked": None, "ready": None, "scored": 0,
                   "cleared_score": 0, "opened": [], "not_bought": [], "timing": None}},
])
def test_every_stored_shape_of_the_status_still_renders(tmp_path, monkeypatch, extra):
    text = _scan_page(tmp_path, monkeypatch, _stored_status(**extra))
    assert "1 / 2 stocks" in text and "MISSING.NS" in text
    if not extra.get("last_pass"):
        assert "Last 5-minute pass" not in text
        assert "advances through the remaining names" in text
    else:
        assert "bought: none" in text


def test_it_waits_for_the_days_first_completed_bar(tick, monkeypatch):
    monkeypatch.setattr("trading.intraday_recovery.first_bar_complete", lambda now, minutes=5: False)
    assert scheduler._intraday_side_pass() == "skipped: no completed 5-minute bar yet"
    assert tick.calls == []
