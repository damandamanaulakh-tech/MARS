"""Owner order 2026-10-04: "fix the 30 min exit pass too / 14:30 scan as
well".

1. The 30-minute exit pass waited with NO timeout behind the 09:20
   rotation's mode lock, and APScheduler (max_instances 1) dropped every run
   that fell due meanwhile — stop checks stopped while the rotation ran.
   Now the rotation runs the exit pass itself between categories, and the
   timed job stands down quickly instead of blocking.
2. The 14:30 job scanned ONE category and skipped at 60% deployed. It now
   runs the same five-category rotation as 09:20.
"""
from __future__ import annotations

import threading
import time

import pytest

from web import scheduler, state


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr("trading.calendars.is_trading_day", lambda *a: True)
    monkeypatch.setattr(scheduler, "_degraded_halt", lambda where: False)
    monkeypatch.setattr(scheduler, "_push_phone_alerts", lambda *a, **k: None,
                        raising=False)
    monkeypatch.setattr(scheduler, "_check_pending_orders", lambda *a: None)


class _R:
    opened, skipped = [], []


def _record(monkeypatch, open_market=True):
    """Stub scan/refresh; return the ordered event log."""
    cfg = state.cfg()
    events = []
    monkeypatch.setattr(state.engine(), "scan",
                        lambda: events.append(("scan", cfg.mode)) or _R())
    monkeypatch.setattr(state.engine(), "refresh",
                        lambda: events.append(("exit", cfg.mode)) or {})
    monkeypatch.setattr("trading.calendars.is_market_open",
                        lambda *a, **k: open_market)
    return events


def test_the_rotation_runs_the_exit_pass_between_categories(monkeypatch):
    events = _record(monkeypatch)
    monkeypatch.setattr(scheduler, "_exit_pass_due", lambda cfg: True)
    cfg = state.cfg()
    original = cfg.mode
    try:
        assert scheduler._ROTATION_LOCK.acquire(blocking=False)
        try:
            scheduler._run_category_rotation(state.engine(), cfg, "09:20")
        finally:
            scheduler._ROTATION_LOCK.release()
    finally:
        cfg.mode = original
        cfg.apply_mode_preset()
    kinds = [k for k, _ in events]
    # an exit pass before every category, the scans in the owner's order
    assert kinds == ["exit", "scan"] * len(scheduler._DAILY_MODE_ORDER)
    assert [m for k, m in events if k == "scan"] == \
        list(scheduler._DAILY_MODE_ORDER)


def test_the_rotation_respects_the_30_minute_interval(monkeypatch):
    events = _record(monkeypatch)
    monkeypatch.setitem(scheduler._LAST_EXIT_PASS, "t", None)
    cfg = state.cfg()
    original = cfg.mode
    try:
        scheduler._run_category_rotation(state.engine(), cfg, "09:20")
    finally:
        cfg.mode = original
        cfg.apply_mode_preset()
    # the first pass is due (none yet); the rest come inside 30 minutes
    assert [k for k, _ in events].count("exit") == 1


def test_no_exit_pass_inside_the_rotation_when_the_market_is_shut(monkeypatch):
    events = _record(monkeypatch, open_market=False)
    monkeypatch.setattr(scheduler, "_exit_pass_due", lambda cfg: True)
    cfg = state.cfg()
    original = cfg.mode
    try:
        scheduler._run_category_rotation(state.engine(), cfg, "catch-up")
    finally:
        cfg.mode = original
        cfg.apply_mode_preset()
    assert not [k for k, _ in events if k == "exit"]


def test_the_timed_exit_pass_never_blocks_behind_a_rotation(monkeypatch):
    """The old job waited forever and later runs were DROPPED. Now it waits
    a bounded time and stands down (the rotation covers the slot)."""
    events = _record(monkeypatch)
    monkeypatch.setattr(scheduler, "SESSION_EXIT_LOCK_WAIT_S", 0.2)
    from trading.modes import MODE_LOCK
    held, release = threading.Event(), threading.Event()

    def hold():
        with MODE_LOCK:
            held.set()
            release.wait(5)
    t = threading.Thread(target=hold, daemon=True)
    t.start()
    held.wait(2)
    try:
        t0 = time.monotonic()
        scheduler._session_refresh()
        took = time.monotonic() - t0
    finally:
        release.set()
        t.join(2)
    assert took < 2.0
    assert not events                           # stood down, did not run


def test_the_timed_exit_pass_still_runs_when_the_lock_is_free(monkeypatch):
    events = _record(monkeypatch)
    monkeypatch.setitem(scheduler._LAST_EXIT_PASS, "t", None)
    scheduler._session_refresh()
    assert events == [("exit", state.cfg().mode)]
    assert scheduler._LAST_EXIT_PASS["t"] is not None


def test_1430_scans_all_five_categories_even_when_well_deployed(monkeypatch):
    events = _record(monkeypatch)
    monkeypatch.setattr(scheduler, "_exit_pass_due", lambda cfg: False)
    cfg = state.cfg()
    original = cfg.mode
    p = state.portfolio()
    # old rule: >= 60% of starting capital deployed => skip. Fake 100%.
    monkeypatch.setattr(type(p), "deployed_value",
                        lambda self, prices=None: cfg.starting_capital,
                        raising=False)
    try:
        scheduler._deployment_topup_scan()
    finally:
        restored = cfg.mode
        cfg.mode = original
        cfg.apply_mode_preset()
    assert [m for k, m in events if k == "scan"] == \
        list(scheduler._DAILY_MODE_ORDER)
    assert restored == original                  # owner's mode handed back


def test_1430_and_0920_share_one_rotation():
    import ast
    import inspect
    for fn in (scheduler._deployment_topup_scan,
               scheduler._daily_multi_mode_scan):
        tree = ast.parse(inspect.getsource(fn))
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "_run_category_rotation" in called, fn.__name__
