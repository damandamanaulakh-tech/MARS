"""Audit S-5 (owner: "go for next", 2026-10-02).

A look-only scan before 09:15 (a boot or a login) used to stamp the day as
done, so when the app then slept through 09:20 the catch-up skipped and no
scan that could BUY ran that day. Done now means a scan ran INSIDE market
hours today; outside hours any scan today is enough.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from web import scheduler, state

ran: list = []


@pytest.fixture()
def catchup(monkeypatch):
    """Catch-up armed, with the real scan and universe refresh replaced so a
    started thread does nothing."""
    ran.clear()
    monkeypatch.setenv("SB_DISABLE_CATCHUP", "0")
    monkeypatch.setattr("trading.calendars.is_trading_day", lambda *a: True)
    monkeypatch.setattr(scheduler, "_daily_multi_mode_scan",
                        lambda: ran.append("scan"))
    monkeypatch.setattr(scheduler, "_universe_refresh_job", lambda: None)
    monkeypatch.setattr("trading.universe_refresh.refreshed_today",
                        lambda: True)
    monkeypatch.setattr(scheduler, "daily_enabled", lambda: True)

    class _Inline:                       # run the "thread" before returning
        def __init__(self, target=None, daemon=None, **kw):
            self._t = target

        def start(self):
            self._t()
    monkeypatch.setattr("threading.Thread", _Inline)
    # REPOINTED 2026-10-22 (entry-fix B-1/B-2): once an in-hours scan ran
    # today the catch-up now also reads the clock (14:30-15:10 runs a missed
    # afternoon rotation) and the stored rotation record (a cut-short one
    # resumes). These pins are about the MORNING rule, so the clock is frozen
    # at 10:00 IST and the book carries no rotation record — on the real
    # clock a CI run crossing 14:30 would have started the afternoon pass.
    import datetime as _d
    from web.clock import IST, ist_now
    monkeypatch.setattr(scheduler, "_ist_now", lambda: _d.datetime.combine(
        ist_now().date(), _d.time(10, 0), tzinfo=IST))
    monkeypatch.setattr(scheduler, "_CURRENT_ROTATION", {})
    p = state.portfolio()
    saved = (p.last_scan, p.last_scan_live, getattr(p, "rotation_report", None))
    p.rotation_report = {}
    yield p, monkeypatch
    p.last_scan, p.last_scan_live, p.rotation_report = saved


def _market(monkeypatch, open_: bool):
    monkeypatch.setattr("trading.calendars.is_market_open",
                        lambda *a, **k: open_)


def test_a_look_only_scan_today_does_not_cancel_the_catchup(catchup):
    p, mp = catchup
    _market(mp, True)
    p.last_scan = datetime.utcnow().isoformat()      # an 08:xx look-only scan
    p.last_scan_live = None
    assert scheduler.catchup_scan_if_missed("test") is True
    assert ran == ["scan"]


def test_an_in_hours_scan_today_does(catchup):
    p, mp = catchup
    _market(mp, True)
    p.last_scan = p.last_scan_live = datetime.utcnow().isoformat()
    assert scheduler.catchup_scan_if_missed("test") is False


def test_outside_hours_any_scan_today_is_enough(catchup):
    p, mp = catchup
    _market(mp, False)
    p.last_scan = datetime.utcnow().isoformat()
    p.last_scan_live = None
    assert scheduler.catchup_scan_if_missed("test") is False


def test_the_live_mark_is_mirrored_to_mongo_both_ways():
    import inspect
    src = inspect.getsource(state)
    assert 'p.last_scan_live = doc.get("last_scan_live")' in src
    assert '"last_scan_live": getattr(self, "last_scan_live", None)' in src
