"""Owner order 2026-10-02 ("go for next"): audit items S-6 and S-7.

S-6  The owner BUY button, the Buy Desk, limit-order fills and the Settings
     saves all swap or read the one shared Config mode, and none of them took
     the lock the 09:20 rotation holds — and the rotation itself let go of it
     between passes while cfg.mode still held the previous category.
S-7  Resting limit orders were only checked after the 09:20 and 14:30 scans,
     and the 14:30 check was skipped whenever the book was over 60% deployed.
"""
from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from web import auth as auth_mod, scheduler, state
from web.main import app


def _owner() -> TestClient:
    c = TestClient(app)
    c.post("/login", data={"email": "owner@sourceborn.in",
                           "password": auth_mod._SEEDED_PWD})
    return c


class _Holder:
    """Hold the mode lock in another thread — a scan pass in progress."""

    def __init__(self):
        from trading.modes import MODE_LOCK
        self.lock = MODE_LOCK
        self.go = threading.Event()
        self.held = threading.Event()

    def _run(self):
        with self.lock:
            self.held.set()
            self.go.wait(10)

    def __enter__(self):
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()
        assert self.held.wait(5)
        return self

    def __exit__(self, *exc):
        self.go.set()
        self.t.join(5)


# --------------------------------------------------------------------- S-6

def test_owner_buy_says_busy_while_a_scan_pass_holds_the_lock(monkeypatch):
    import trading.modes as modes
    import trading.quote_router as qr
    monkeypatch.setattr(modes, "MODE_LOCK_WAIT_S", 0.2)
    monkeypatch.setattr(qr, "fetch_quotes",
                        lambda tickers, upstox_client=None:
                        {t: 500.0 for t in tickers})
    p = state.portfolio()
    p.positions.pop("CIPLA.NS", None)
    c = _owner()
    with _Holder():
        r = c.post("/scan/buy", data={"ticker": "cipla"},
                   follow_redirects=False)
    assert r.status_code == 303 and "scan_busy" in r.headers["location"]
    assert "CIPLA.NS" not in p.positions, "nothing may be bought while busy"


def test_the_busy_banner_renders():
    r = _owner().get("/scan?error=scan_busy")
    assert r.status_code == 200 and "scan_busy_marker" in r.text


def test_settings_save_changes_nothing_while_a_scan_pass_runs():
    cfg = state.cfg()
    before = cfg.mode
    other = "swing" if before != "swing" else "positional"
    c = _owner()
    with _Holder():
        r = c.post("/settings", data={"mode": other}, follow_redirects=False)
    assert r.status_code == 303 and "saved=busy" in r.headers["location"]
    assert state.cfg().mode == before
    page = c.get("/settings?saved=busy")
    assert "settings_busy_marker" in page.text


def test_settings_refuse_while_the_rotation_is_between_passes():
    cfg = state.cfg()
    before = cfg.mode
    other = "swing" if before != "swing" else "positional"
    c = _owner()
    assert scheduler._ROTATION_LOCK.acquire(blocking=False)
    try:
        r = c.post("/settings/mode", data={"mode": other},
                   follow_redirects=False)
    finally:
        scheduler._ROTATION_LOCK.release()
    assert "saved=busy" in r.headers["location"]
    assert state.cfg().mode == before


def test_a_limit_fill_waits_for_the_running_pass():
    """_fill used to swap the mode with no lock at all."""
    from trading import orders
    eng = state.engine()
    calls = []
    real = orders._fill_locked

    def spy(*a, **k):
        calls.append(time.monotonic())
    orders._fill_locked = spy
    try:
        h = _Holder().__enter__()
        t0 = time.monotonic()
        threading.Timer(0.3, h.go.set).start()
        orders._fill(eng, {"ticker": "NOTHELD.NS", "id": "x", "qty": 1,
                           "limit_price": 1.0, "stop_pct": None,
                           "target_pct": None}, 1.0, None)
        h.t.join(5)
    finally:
        orders._fill_locked = real
    assert calls and calls[0] - t0 >= 0.25, "the fill did not wait for the lock"


def test_the_rotation_holds_the_lock_between_passes(monkeypatch):
    """Between two passes cfg.mode is still the previous category; another
    thread must not get in there."""
    import gc
    from trading.modes import MODE_LOCK
    eng = state.engine()
    seen = []

    def probe():
        def _try():
            if MODE_LOCK.acquire(blocking=False):
                MODE_LOCK.release()          # never leak it on the old code
                seen.append("free")
            else:
                seen.append("held")
        t = threading.Thread(target=_try)
        t.start()
        t.join()

    monkeypatch.setattr(gc, "collect", probe)
    monkeypatch.setattr(eng, "scan", lambda *a, **k: type(
        "R", (), {"opened": [], "skipped": []})())
    monkeypatch.setattr(scheduler, "_degraded_halt", lambda where: False)
    monkeypatch.setattr(scheduler, "_topup_once_today", lambda *a, **k: None)
    monkeypatch.setattr(scheduler, "_weekly_index_refresh", lambda: None)
    monkeypatch.setattr(scheduler, "_sector_index_refresh", lambda: None)
    monkeypatch.setattr(scheduler, "_check_pending_orders", lambda where: None)
    monkeypatch.setattr(scheduler, "run_trigger_layer", lambda *a, **k: None)
    monkeypatch.setattr(scheduler, "_push_phone_alerts", lambda *a, **k: None)
    monkeypatch.setattr("trading.calendars.is_trading_day", lambda *a, **k: True)
    before = state.cfg().mode
    scheduler._daily_multi_mode_scan()
    assert seen and set(seen) == {"held"}, seen
    assert state.cfg().mode == before
    assert MODE_LOCK.acquire(blocking=False)
    MODE_LOCK.release()


# --------------------------------------------------------------------- S-7

def test_the_thirty_minute_pass_checks_resting_orders(monkeypatch):
    checked = []
    monkeypatch.setattr(scheduler, "_check_pending_orders",
                        lambda where: checked.append(where))
    monkeypatch.setattr("trading.calendars.is_market_open",
                        lambda *a, **k: True)
    monkeypatch.setattr(state.engine(), "refresh", lambda: {})
    scheduler._session_refresh()
    assert checked == ["session"]


def test_a_skipped_top_up_still_checks_resting_orders(monkeypatch):
    checked = []
    monkeypatch.setattr(scheduler, "_check_pending_orders",
                        lambda where: checked.append(where))
    monkeypatch.setattr(scheduler, "_degraded_halt", lambda where: False)
    monkeypatch.setattr("trading.calendars.is_trading_day", lambda *a, **k: True)
    # The 60%-deployed skip is gone (owner order 2026-10-04: the 14:30 pass
    # scans all five categories, each on its own cash share). The one skip
    # left is a rotation still running — and it still checks the orders.
    monkeypatch.setattr(state.engine(), "scan",
                        lambda *a, **k: pytest.fail("top-up must have skipped"))
    assert scheduler._ROTATION_LOCK.acquire(blocking=False)
    try:
        scheduler._deployment_topup_scan()
    finally:
        scheduler._ROTATION_LOCK.release()
    assert checked == ["top-up-skip"]
