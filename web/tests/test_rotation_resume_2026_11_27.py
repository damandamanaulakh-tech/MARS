"""Owner order 2026-11-27: the scan resumes itself as soon as it can.

Their words: "earlier when scan is running it goes to offline itself, which
needs to stop / if half scan isnt saved it should run from starting / and keep
doing considering anything as interruption / even in loss of network it should
start as soon internet connected"

WHAT WAS ALREADY THERE, verified before anything was built: segment-level
resume has existed since 2026-10-21 (web/rotation_progress.py). Progress is
saved after every segment and the save must be acknowledged or the rotation
stops; the next run re-runs only the segments that did not finish; a segment
is all-or-nothing, so a dead one's buys never reached the book and it really
does start again from its own beginning; and `already_held` means a re-run
cannot double-buy.

WHAT WAS MISSING: nothing called it on a timer. It fired at boot + 120 s and
on an Upstox login only. So a rotation that died at 09:25, or a network drop
that healed at 10:00, waited until 14:30, a restart or a login. There was a
60-second loop healing the DATABASE and a tested resume for the SCAN, and the
two were never connected.
"""
from datetime import datetime, timedelta

import pytest

from web import scheduler, state


IST_OPEN = datetime(2026, 11, 27, 10, 30)          # a Friday, mid-session


@pytest.fixture
def armed(monkeypatch):
    """A trading day, market open, past 09:20, with a fresh book."""
    monkeypatch.setattr('trading.calendars.is_trading_day', lambda d=None: True)
    monkeypatch.setattr('trading.calendars.is_market_open', lambda now=None: True)
    monkeypatch.setattr('web.clock.ist_now', lambda: IST_OPEN)
    monkeypatch.setattr('web.clock.ist_today_iso', lambda: '2026-11-27')
    monkeypatch.delenv('SB_DISABLE_CATCHUP', raising=False)
    monkeypatch.setattr(scheduler, 'daily_enabled', lambda: True)
    called = []
    monkeypatch.setattr(scheduler, 'catchup_scan_if_missed',
                        lambda reason='catchup', lock_wait=None:
                        called.append((reason, lock_wait)) or True)
    p = state.portfolio()
    p.rotation_report = {}
    return p, called


def half_done(p, where='09:20'):
    """Momentum finished, the rest never did — the owner's exact case."""
    p.rotation_report = {
        'daily': {'date': '2026-11-27', 'where': where, 'phase': 'live',
                  'status': 'running',
                  'passes': {'nifty200_momentum': {'completed': True, 'live': True}}},
    }


# ------------------------------------------------------------ it fires

def test_an_unfinished_rotation_is_resumed_while_the_market_is_open(armed):
    p, called = armed
    half_done(p)
    scheduler._rotation_resume_tick()
    assert called and called[0][0] == 'resume', called
    assert any(r.get('action') == 'ROTATION_RESUME' for r in p.decision_journal)


def test_a_slot_that_fired_but_never_checkpointed_is_still_resumed(armed):
    """The network-outage case: the rotation stops before its FIRST save, so
    there is no record for today at all."""
    p, called = armed
    p.rotation_report = {'daily_owed': {'date': '2026-11-27', 'slot': '09:20'}}
    scheduler._rotation_resume_tick()
    assert called, "a rotation that never started is exactly what is owed"


def test_the_resume_never_waits_on_the_mode_lock(armed):
    p, called = armed
    half_done(p)
    scheduler._rotation_resume_tick()
    assert called[0][1] is not None and called[0][1] > 0, called


# ------------------------------------------------------- it does not fire

def test_a_finished_rotation_is_left_alone(armed):
    p, called = armed
    p.rotation_report = {'daily': {
        'date': '2026-11-27', 'where': '09:20', 'phase': 'live',
        'status': 'complete',
        'passes': {m: {'completed': True, 'live': True}
                   for m in scheduler._DAILY_MODE_ORDER}}}
    scheduler._rotation_resume_tick()
    assert called == []


def test_nothing_happens_when_no_rotation_is_owed(armed):
    p, called = armed
    scheduler._rotation_resume_tick()
    assert called == []


def test_nothing_happens_outside_market_hours(armed, monkeypatch):
    """Otherwise it would re-run score-only scans all evening."""
    p, called = armed
    half_done(p)
    monkeypatch.setattr('trading.calendars.is_market_open', lambda now=None: False)
    scheduler._rotation_resume_tick()
    assert called == []


def test_nothing_happens_on_a_holiday(armed, monkeypatch):
    p, called = armed
    half_done(p)
    monkeypatch.setattr('trading.calendars.is_trading_day', lambda d=None: False)
    scheduler._rotation_resume_tick()
    assert called == []


def test_nothing_happens_before_the_slot_itself(armed, monkeypatch):
    """At 09:16 the real 09:20 slot has not run — resuming would pre-empt it."""
    p, called = armed
    half_done(p)
    monkeypatch.setattr('web.clock.ist_now', lambda: datetime(2026, 11, 27, 9, 16))
    scheduler._rotation_resume_tick()
    assert called == []


def test_yesterdays_unfinished_rotation_is_not_resumed_today(armed):
    p, called = armed
    half_done(p)
    p.rotation_report['daily']['date'] = '2026-11-26'
    scheduler._rotation_resume_tick()
    assert called == []


def test_the_automatic_track_switch_is_honoured(armed, monkeypatch):
    p, called = armed
    half_done(p)
    monkeypatch.setattr(scheduler, 'daily_enabled', lambda: False)
    scheduler._rotation_resume_tick()
    assert called == []


# ------------------------------------------------- it cannot loop for ever

def test_a_cooldown_stops_it_re_entering_every_tick(armed):
    p, called = armed
    half_done(p)
    scheduler._rotation_resume_tick()
    assert len(called) == 1
    scheduler._rotation_resume_tick()            # five minutes later
    assert len(called) == 1, "the cooldown must hold"


def test_the_day_has_a_hard_attempt_cap(armed):
    p, called = armed
    half_done(p)
    for _ in range(8):
        res = p.rotation_report.setdefault('resume', {})
        res['last'] = None                        # ignore the cooldown
        scheduler._rotation_resume_tick()
    assert len(called) == scheduler.RESUME_MAX_ATTEMPTS_PER_DAY, called


def test_the_counters_live_where_they_survive_a_restart(armed):
    """A module variable would reset on exactly the restart this survives."""
    p, called = armed
    half_done(p)
    scheduler._rotation_resume_tick()
    res = p.rotation_report.get('resume') or {}
    assert res.get('date') == '2026-11-27' and res.get('attempts') == 1
    # rotation_report is mirrored to Mongo both ways already
    assert 'rotation_report' in p.snapshot()


def test_a_new_day_clears_the_counters(armed, monkeypatch):
    p, called = armed
    half_done(p)
    scheduler._rotation_resume_tick()
    p.rotation_report['resume']['attempts'] = scheduler.RESUME_MAX_ATTEMPTS_PER_DAY
    monkeypatch.setattr('web.clock.ist_now', lambda: IST_OPEN + timedelta(days=1))
    monkeypatch.setattr('web.clock.ist_today_iso', lambda: '2026-11-28')
    p.rotation_report['daily']['date'] = '2026-11-28'
    scheduler._rotation_resume_tick()
    assert len(called) == 2, "a fresh day gets fresh attempts"


# -------------------------------------------------------------- the wiring

def test_the_job_is_registered_on_the_always_on_track():
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path(scheduler.__file__).read_text())
    ids = [kw.value.value for node in ast.walk(tree)
           if isinstance(node, ast.Call) and getattr(node.func, 'attr', '') == 'add_job'
           for kw in node.keywords
           if kw.arg == 'id' and isinstance(kw.value, ast.Constant)]
    assert 'rotation_resume_tick' in ids, ids


def test_the_scheduled_slots_still_wait_as_long_as_it_takes():
    """09:20 and 14:30 own their slot; only the resume stands down."""
    import inspect
    src = inspect.getsource(scheduler._run_category_rotation)
    assert 'if lock_wait is None:' in src and 'MODE_LOCK.acquire()' in src
    assert 'standing down' in src


def test_healthz_now_shows_whether_the_scan_finished():
    from fastapi.testclient import TestClient
    from web.main import app
    body = TestClient(app).get('/healthz').json()
    assert 'rotation' in body
    rot = body['rotation'] or {}
    for key in ('date', 'status', 'segments_done', 'segments_total',
                'complete_today', 'owed', 'resume_attempts'):
        assert key in rot, (key, rot)


def test_the_09_20_slot_stamps_that_a_rotation_is_owed():
    """Without this a network outage leaves no record and nothing to resume."""
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path(scheduler.__file__).read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == '_daily_multi_mode_scan')
    body = ast.unparse(fn)
    assert 'daily_owed' in body
    # ...and BEFORE the degraded-book halt, which is what an outage trips
    assert body.index('daily_owed') < body.index('_degraded_halt')
