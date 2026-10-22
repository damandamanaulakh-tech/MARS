"""The app's APScheduler must never START inside a test process.

2026-10-22: a CI run on PR #185 that crossed 09:20 IST had the REAL
`_daily_multi_mode_scan` cron fire mid-suite (the log: `Job
"_daily_multi_mode_scan (trigger: cron[... hour='9', minute='20'] ...)"
raised an exception ... RuntimeError: release unlocked lock`). It took
`_ROTATION_LOCK`, ten later tests read "busy", the watchlist test saw a
second momentum capture, and a test's cleanup released the job's lock.
The suite had been green for months only because it rarely ran across a
cron slot — the real-clock fault class (the 2026-09-28 RBI-halt lesson).

The web conftest sets SB_SCHEDULER_NO_START=1: `start_if_enabled` still
BUILDS the scheduler and registers every job (tests inspect them) but
never calls `.start()`. Production never sets it.
"""
from __future__ import annotations

import inspect
import os
import pathlib

from fastapi.testclient import TestClient

from web import scheduler
from web.main import app

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _operative(src: str) -> str:
    return "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))


def test_the_conftest_sets_the_guard_and_production_never_does():
    assert os.environ.get("SB_SCHEDULER_NO_START") == "1"
    conftest = (ROOT / "web" / "tests" / "conftest.py").read_text()
    assert 'SB_SCHEDULER_NO_START", "1"' in _operative(conftest)
    render = (ROOT / "render.yaml").read_text()
    assert "SB_SCHEDULER_NO_START" not in render


def test_the_app_builds_its_scheduler_but_never_runs_it_under_tests():
    with TestClient(app):                       # runs the startup hook
        pass
    sched = scheduler._SCHED
    assert sched is not None, "the jobs must still be registered"
    assert sched.running is False, "a running scheduler can fire a real cron mid-suite"
    ids = {j.id for j in sched.get_jobs()}
    # the heavy ones that bit on 2026-10-22 are registered, never running
    assert "valuation_precompute" in ids
    assert not scheduler._ROTATION_LOCK.locked()


def test_the_production_path_still_starts_the_scheduler():
    src = _operative(inspect.getsource(scheduler.start_if_enabled))
    guard = src.index('SB_SCHEDULER_NO_START')
    start = src.index("_SCHED.start()")
    assert guard < start, "the guard must sit right before the real start"
    assert "return" in src[guard:start]
