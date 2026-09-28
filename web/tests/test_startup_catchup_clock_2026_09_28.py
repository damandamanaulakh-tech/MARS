"""The boot catch-up job must be scheduled on an IST-AWARE clock.

Owner's Render log, 2026-09-28, on EVERY boot:

    Run time of job "start_if_enabled.<locals>.<lambda> (trigger:
    date[2026-09-28 10:20:00 IST] ...)" was missed by 5:28:00

The run_date was a NAIVE datetime.now() + 120 s. Render's clock is UTC;
APScheduler reads a naive run_date in the scheduler's own timezone (IST),
so the job was born 5h28m overdue, past the 300 s misfire grace, and
dropped — the after-restart scan had never once run on Render. Same clock
class as the 2026-09-16 exit drain (naive UTC handed to an IST reader).

Pinned here BEHAVIOURALLY (a real scheduler under a UTC process clock must
actually run the job), not by grepping for a name in the source.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta

import pytest

from web import scheduler as sched


@pytest.fixture()
def utc_process_clock():
    """Render's container clock is UTC; force the same here so the test
    means the same thing wherever it runs."""
    old = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    yield
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()


def test_run_date_is_ist_aware_and_two_minutes_from_the_real_now(utc_process_clock):
    from pytz import timezone
    ist = timezone("Asia/Kolkata")
    rd = sched.startup_catchup_run_date(ist, timedelta(seconds=120))
    assert rd.tzinfo is not None, "naive run_date = read as IST = 5h28m in the past"
    # the SAME instant as now+120s, whatever wall-clock label it carries
    delta = (rd - datetime.now(ist)).total_seconds()
    assert 100 <= delta <= 125, delta


def test_a_real_ist_scheduler_runs_the_job_instead_of_dropping_it(utc_process_clock):
    """End to end through APScheduler with the production settings
    (timezone IST, misfire grace 300 s): the job must FIRE, and no
    EVENT_JOB_MISSED may be emitted."""
    from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_MISSED
    from apscheduler.schedulers.background import BackgroundScheduler
    from pytz import timezone

    ist = timezone("Asia/Kolkata")
    s = BackgroundScheduler(
        timezone=ist,
        job_defaults={"misfire_grace_time": 300, "coalesce": True})
    seen = []
    s.add_listener(lambda ev: seen.append(("missed", ev.job_id)), EVENT_JOB_MISSED)
    s.add_listener(lambda ev: seen.append(("ran", ev.job_id)), EVENT_JOB_EXECUTED)
    s.add_job(lambda: None, "date",
              run_date=sched.startup_catchup_run_date(ist, timedelta(seconds=1)),
              id="startup_catchup")
    s.start()
    try:
        deadline = time.time() + 10
        while time.time() < deadline and not seen:
            time.sleep(0.2)
    finally:
        s.shutdown(wait=False)
    assert seen == [("ran", "startup_catchup")], seen


def test_the_old_naive_clock_is_indeed_dropped(utc_process_clock):
    """The fault, reproduced: the pre-fix expression under the production
    scheduler settings is discarded with a MISSED event. Kept so the
    reason for the helper can never be doubted."""
    from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_MISSED
    from apscheduler.schedulers.background import BackgroundScheduler
    from pytz import timezone

    ist = timezone("Asia/Kolkata")
    s = BackgroundScheduler(
        timezone=ist,
        job_defaults={"misfire_grace_time": 300, "coalesce": True})
    seen = []
    s.add_listener(lambda ev: seen.append("missed"), EVENT_JOB_MISSED)
    s.add_listener(lambda ev: seen.append("ran"), EVENT_JOB_EXECUTED)
    s.add_job(lambda: None, "date",
              run_date=datetime.now() + timedelta(seconds=120),   # the old line
              id="naive")
    s.start()
    try:
        deadline = time.time() + 10
        while time.time() < deadline and not seen:
            time.sleep(0.2)
    finally:
        s.shutdown(wait=False)
    assert seen == ["missed"], seen


def test_production_registers_the_catchup_through_the_helper():
    """Source pin on the OPERATIVE code only (comment lines stripped — the
    explanatory comment above the call quotes the old expression)."""
    import inspect
    src = inspect.getsource(sched.start_if_enabled)
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "run_date=startup_catchup_run_date(ist" in code
    assert "run_date=_dt.now()" not in code
