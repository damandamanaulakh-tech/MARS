"""Direct tests for the scheduler's daily jobs — the other half of the F6
leftover ("web/scheduler.py … no direct unit tests"). Owner order
2026-08-23: "start fixing ur pending and flaws one by one".

The jobs tested are the ones nothing else exercised directly: the 09:20
multi-mode rotation (mode restore + one-at-a-time lock), the catch-up
gate, and the 15:35 refresh. Engine work is stubbed — these tests pin the
SCHEDULING behaviour, not the trading maths (test_engine_direct covers
that).
"""
from __future__ import annotations

from datetime import datetime

import pytest

from web import scheduler, state


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    # every job checks the calendar; pin "today is a trading day"
    monkeypatch.setattr("trading.calendars.is_trading_day", lambda *a: True)
    monkeypatch.setattr(scheduler, "_degraded_halt", lambda where: False)
    monkeypatch.setattr(scheduler, "_push_phone_alerts", lambda *a, **k: None,
                        raising=False)


def test_daily_rotation_restores_the_owners_mode(monkeypatch):
    """The 09:20 job cycles momentum→short→swing→positional and MUST hand
    back the mode it found — a scan crash mid-rotation must not leave the
    app parked in 'positional' (audit fix C3/A8 territory)."""
    cfg = state.cfg()
    original = cfg.mode
    seen = []

    def fake_scan():
        seen.append(cfg.mode)
        if cfg.mode == "swing":
            raise RuntimeError("mid-rotation crash")
        class R:
            opened, skipped = [], []
        return R()
    monkeypatch.setattr(state.engine(), "scan", fake_scan)
    monkeypatch.setattr(scheduler, "run_trigger_layer", lambda *a, **k: {})
    monkeypatch.setattr(scheduler, "refresh_breadth_store", lambda *a, **k: {})
    monkeypatch.setattr(scheduler, "_daily_universe_topup",
                        lambda *a, **k: None, raising=False)
    monkeypatch.setattr(scheduler, "_check_pending_orders", lambda *a: None)
    try:
        scheduler._daily_multi_mode_scan()
        assert seen == list(scheduler._DAILY_MODE_ORDER)   # crash didn't stop the loop
        assert cfg.mode == original                        # mode handed back
    finally:
        cfg.mode = original
        cfg.apply_mode_preset()


def test_rotation_lock_refuses_a_second_concurrent_run(monkeypatch):
    """Two rotations at once = double entries (audit C3). The second caller
    must skip, not queue."""
    calls = []
    monkeypatch.setattr(state.engine(), "scan",
                        lambda: calls.append(1) or type("R", (), {"opened": [], "skipped": []})())
    monkeypatch.setattr(scheduler, "run_trigger_layer", lambda *a, **k: {})
    monkeypatch.setattr(scheduler, "refresh_breadth_store", lambda *a, **k: {})
    monkeypatch.setattr(scheduler, "_daily_universe_topup",
                        lambda *a, **k: None, raising=False)
    monkeypatch.setattr(scheduler, "_check_pending_orders", lambda *a: None)
    assert scheduler._ROTATION_LOCK.acquire(blocking=False)
    try:
        scheduler._daily_multi_mode_scan()                 # lock held → skip
        assert calls == []
    finally:
        scheduler._ROTATION_LOCK.release()


def test_catchup_skips_when_todays_scan_already_ran(monkeypatch):
    # REPOINTED 2026-10-22 (entry-fix B-1/B-2): the catch-up now also reads
    # the clock and the stored rotation record once today's scan ran — a
    # morning clock and an empty record keep this pin about the morning rule
    # (on the real clock a run crossing 14:30 would start the afternoon pass).
    import datetime as _d
    from web.clock import IST, ist_now
    monkeypatch.setattr(scheduler, "_ist_now", lambda: _d.datetime.combine(
        ist_now().date(), _d.time(10, 0), tzinfo=IST))
    monkeypatch.setattr(scheduler, "_CURRENT_ROTATION", {})
    p = state.portfolio()
    old, old_live = p.last_scan, p.last_scan_live
    old_rot = getattr(p, "rotation_report", None)
    p.last_scan = p.last_scan_live = datetime.utcnow().isoformat()
    p.rotation_report = {}
    try:
        monkeypatch.setenv("SB_DISABLE_CATCHUP", "0")
        assert scheduler.catchup_scan_if_missed("test") is False
    finally:
        p.last_scan, p.last_scan_live = old, old_live
        p.rotation_report = old_rot


def test_catchup_respects_the_disable_env(monkeypatch):
    p = state.portfolio()
    old = p.last_scan
    p.last_scan = "2020-01-01T09:20:00"                    # long overdue
    try:
        monkeypatch.setenv("SB_DISABLE_CATCHUP", "1")
        assert scheduler.catchup_scan_if_missed("test") is False
    finally:
        p.last_scan = old


def test_eod_refresh_calls_the_engine_and_survives_its_crash(monkeypatch):
    called = []

    def fake_refresh():
        called.append(1)
        raise RuntimeError("refresh blew up")
    monkeypatch.setattr(state.engine(), "refresh", fake_refresh)
    scheduler._eod_refresh()                               # must not raise
    assert called == [1]
