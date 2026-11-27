"""OPS-03: a weekly feed that hangs can no longer hold the whole 16:30 block.

The 2026-08-29 isolation protects the block from a feed that CRASHES. It did
nothing about one that simply never answers: `fn()` was called and waited on
with no bound, so one unresponsive host held the block and every step queued
behind it never ran.

Two live readings on 2026-11-27 proved it was still happening, 58 days after
the last completion: scan_universe.last_rebuild = "2026-09-30" (the stamp the
LAST pre-pull step writes) and history_tickers 2233 against a universe file of
2678 — 445 of the owner's own names had never had a price fetched. The block is
brought back every weekday when it does not finish, so it was retrying and
hanging again every evening.
"""
import os
import threading
import time

import pytest

from web import db, scheduler


def journal_sink():
    rows = []
    return rows, (lambda action, why: rows.append((action, why)))


# ------------------------------------------------------------- the cap itself

def test_a_hanging_step_is_abandoned_and_the_block_goes_on(monkeypatch):
    monkeypatch.setenv('SB_WEEKLY_STEP_TIMEOUT_S', '0.3')
    rows, journal = journal_sink()
    release = threading.Event()
    order = []

    def hangs():
        order.append('hangs-started')
        release.wait(10)                       # never answers in time
        order.append('hangs-finished-late')

    def follows():
        order.append('follows-ran')

    t0 = time.monotonic()
    ok = scheduler._run_weekly_step('a feed that hangs', hangs, journal)
    took = time.monotonic() - t0
    scheduler._run_weekly_step('the next feed', follows, journal)
    release.set()

    assert ok is False
    assert took < 5.0, f"the block waited {took:.1f}s on a hung feed"
    assert 'follows-ran' in order, "the step after the hang must still run"
    actions = [a for a, _ in rows]
    assert 'DATA_REFRESH_STEP_TIMEOUT' in actions, actions
    why = [w for a, w in rows if a == 'DATA_REFRESH_STEP_TIMEOUT'][0]
    assert 'a feed that hangs' in why, why
    assert 'LEFT RUNNING' in why and 'already stored is kept' in why


def test_a_step_that_finishes_is_unaffected(monkeypatch):
    monkeypatch.setenv('SB_WEEKLY_STEP_TIMEOUT_S', '30')
    rows, journal = journal_sink()
    ran = []
    assert scheduler._run_weekly_step('quick feed', lambda: ran.append(1), journal) is True
    assert ran == [1] and rows == []


def test_a_crashing_step_still_reports_as_a_crash(monkeypatch):
    """Unchanged behaviour: a crash is not a timeout and must not read as one."""
    monkeypatch.setenv('SB_WEEKLY_STEP_TIMEOUT_S', '30')
    rows, journal = journal_sink()

    def boom():
        raise RuntimeError('feed exploded')

    assert scheduler._run_weekly_step('broken feed', boom, journal) is False
    actions = [a for a, _ in rows]
    assert actions == ['DATA_REFRESH_STEP_FAILED'], actions
    assert 'feed exploded' in rows[0][1]


def test_zero_means_unbounded_the_pre_repair_behaviour(monkeypatch):
    monkeypatch.setenv('SB_WEEKLY_STEP_TIMEOUT_S', '0')
    rows, journal = journal_sink()
    ran = []
    assert scheduler._run_weekly_step('feed', lambda: ran.append(1), journal) is True
    assert ran == [1]
    assert scheduler.weekly_step_timeout_s('feed') == 0.0


def test_a_nonsense_env_value_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv('SB_WEEKLY_STEP_TIMEOUT_S', 'soon')
    assert scheduler.weekly_step_timeout_s('feed') == scheduler.WEEKLY_STEP_TIMEOUT_S


# --------------------------------------------- the cap never fires too early

def test_a_step_with_its_own_budget_gets_that_budget_plus_slack(monkeypatch):
    """The cap exists to catch a hang, never to cut short honest work."""
    monkeypatch.delenv('SB_WEEKLY_STEP_TIMEOUT_S', raising=False)
    monkeypatch.setenv('SB_FUNDAMENTALS_BUDGET_S', '600')
    monkeypatch.setenv('SB_TOPUP_BUDGET_S', '2400')
    assert scheduler.weekly_step_timeout_s('fundamentals refresh') >= 600 + 300
    assert scheduler.weekly_step_timeout_s('OHLCV top-up') >= 2400 + 300
    # and a plain feed keeps the plain default
    assert scheduler.weekly_step_timeout_s('index tail') == scheduler.WEEKLY_STEP_TIMEOUT_S


def test_a_bigger_own_budget_moves_the_cap_with_it(monkeypatch):
    monkeypatch.delenv('SB_WEEKLY_STEP_TIMEOUT_S', raising=False)
    monkeypatch.setenv('SB_TOPUP_BUDGET_S', '7200')
    assert scheduler.weekly_step_timeout_s('OHLCV top-up') >= 7500


def test_the_default_is_generous_enough_for_a_real_feed():
    assert scheduler.WEEKLY_STEP_TIMEOUT_S >= 300


def test_production_does_not_set_the_override():
    """A cap that render.yaml silences is not a cap."""
    from pathlib import Path
    text = Path('render.yaml').read_text()
    assert 'SB_WEEKLY_STEP_TIMEOUT_S' not in text


# ------------------------------------------------- the pull is inside the cap

def test_the_price_pull_now_runs_through_the_same_cap():
    """It was the ONE step not even inside _run_weekly_step."""
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path(scheduler.__file__).read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == '_weekly_history_topup')
    labels = [a.args[0].value for a in ast.walk(fn)
              if isinstance(a, ast.Call)
              and getattr(a.func, 'id', '') == '_run_weekly_step'
              and a.args and isinstance(a.args[0], ast.Constant)]
    assert any('OHLCV' in str(s) for s in labels), labels
    # and nothing calls top_up_history outside the extracted helper
    pull = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == '_do_weekly_pull')
    assert any(getattr(c.func, 'id', '') == 'top_up_history'
               for c in ast.walk(pull) if isinstance(c, ast.Call))


def test_the_cap_can_never_fire_before_a_step_s_own_budget(monkeypatch):
    """Even a tiny override must not cut the pull short mid-work."""
    monkeypatch.setenv('SB_WEEKLY_STEP_TIMEOUT_S', '0.3')
    monkeypatch.setenv('SB_TOPUP_BUDGET_S', '2400')
    assert scheduler.weekly_step_timeout_s('OHLCV top-up') >= 2400
    monkeypatch.setenv('SB_FUNDAMENTALS_BUDGET_S', '600')
    assert scheduler.weekly_step_timeout_s('fundamentals refresh') >= 600
    # a plain feed DOES honour the override — that is what makes it testable
    assert scheduler.weekly_step_timeout_s('index tail') == 0.3


def test_an_abandoned_pull_keeps_the_block_due_tomorrow(monkeypatch):
    """Otherwise the store is recorded as finished and the block waits a week.

    Driven at the branch rather than through the cap: the pull's cap is its
    own budget plus slack by design (the test above), so it cannot be tripped
    quickly. What matters here is what the block does when the pull step comes
    back unsuccessful without a measured remainder.
    """
    stamped = {}
    monkeypatch.setattr(scheduler, '_mark_weekly_block_done',
                        lambda remaining=0: stamped.update(remaining=remaining))
    monkeypatch.setattr(scheduler, 'weekly_block_due', lambda today=None: (True, 'test'))
    monkeypatch.setattr('trading.calendars.is_trading_day', lambda d=None: True)
    monkeypatch.setattr(db, 'is_connected', lambda: True)
    monkeypatch.setattr('trading.data.history_broker', lambda: object())

    real_step = scheduler._run_weekly_step
    seen = []

    def step(label, fn, journal):
        seen.append(label)
        if 'OHLCV' in label:
            return False                       # abandoned on the cap
        return real_step(label, lambda: None, journal)
    monkeypatch.setattr(scheduler, '_run_weekly_step', step)
    scheduler._weekly_history_topup()
    assert any('OHLCV' in s for s in seen), seen
    assert stamped.get('remaining', 0) > 0, \
        "an abandoned pull must leave the block due tomorrow, not finished"


def test_a_pull_that_reports_its_own_remainder_is_believed(monkeypatch):
    """The placeholder must not overwrite a real measured remainder."""
    stamped = {}
    monkeypatch.setattr(scheduler, '_mark_weekly_block_done',
                        lambda remaining=0: stamped.update(remaining=remaining))
    monkeypatch.setattr(scheduler, 'weekly_block_due', lambda today=None: (True, 'test'))
    monkeypatch.setattr('trading.calendars.is_trading_day', lambda d=None: True)
    monkeypatch.setattr(db, 'is_connected', lambda: True)
    monkeypatch.setattr('trading.data.history_broker', lambda: object())
    monkeypatch.setattr(scheduler, '_do_weekly_pull', lambda *a, **k: 77)
    real_step = scheduler._run_weekly_step
    monkeypatch.setattr(scheduler, '_run_weekly_step',
                        lambda label, fn, journal: real_step(label, fn, journal)
                        if 'OHLCV' in label else real_step(label, lambda: None, journal))
    scheduler._weekly_history_topup()
    assert stamped.get('remaining') == 77, stamped
