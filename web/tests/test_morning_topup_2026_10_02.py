"""Owner order 2026-10-02 ("go for next"), audit item 8: the daily price
top-up ran AFTER the 09:20 scan passes, so every morning's scan scored on
bars one trading day stale. It now runs at 08:30 and, if that did not
complete, first thing in the 09:20 job, once per day (ledger)."""
from __future__ import annotations

import pytest

from web import scheduler, state


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr("trading.calendars.is_trading_day", lambda *a: True)
    monkeypatch.setattr(scheduler, "_degraded_halt", lambda where: False)
    monkeypatch.setattr(scheduler, "_push_phone_alerts", lambda *a, **k: None,
                        raising=False)
    monkeypatch.setattr(scheduler, "run_trigger_layer", lambda *a, **k: {})
    monkeypatch.setattr(scheduler, "refresh_breadth_store", lambda *a, **k: {})
    monkeypatch.setattr(scheduler, "_check_pending_orders", lambda *a: None)
    monkeypatch.setattr(scheduler, "_weekly_index_refresh", lambda *a, **k: None)
    monkeypatch.setattr(scheduler, "_sector_index_refresh", lambda *a, **k: None)


class _Settings:
    """In-memory stand-in for the data_refresh settings doc."""
    def __init__(self):
        self.doc = {}

    def load(self, doc_id="default"):
        return dict(self.doc) if doc_id == "data_refresh" else {}

    def save(self, d, doc_id="default"):
        if doc_id == "data_refresh":
            self.doc.update(d)


def _wire(monkeypatch, order, topup_ok=True):
    st = _Settings()
    from web import db
    monkeypatch.setattr(db, "is_connected", lambda: True)
    monkeypatch.setattr(db, "load_settings", st.load)
    monkeypatch.setattr(db, "save_settings", st.save)
    monkeypatch.setattr(scheduler, "_daily_universe_topup",
                        lambda: order.append("topup") or topup_ok)

    def fake_scan():
        order.append("scan")
        return type("R", (), {"opened": [], "skipped": []})()
    monkeypatch.setattr(state.engine(), "scan", fake_scan)
    return st


def test_the_0920_job_tops_up_before_it_scans(monkeypatch):
    order: list = []
    cfg = state.cfg()
    original = cfg.mode
    _wire(monkeypatch, order)
    try:
        scheduler._daily_multi_mode_scan()
    finally:
        cfg.mode = original
        cfg.apply_mode_preset()
    assert order[0] == "topup", order
    assert order.count("topup") == 1
    assert "scan" in order


def test_a_completed_morning_topup_is_not_repeated_at_0920(monkeypatch):
    order: list = []
    cfg = state.cfg()
    original = cfg.mode
    _wire(monkeypatch, order)
    scheduler._morning_topup_job()                 # 08:30
    assert order == ["topup"]
    try:
        scheduler._daily_multi_mode_scan()         # 09:20
    finally:
        cfg.mode = original
        cfg.apply_mode_preset()
    assert order.count("topup") == 1               # ledger skipped it


def test_a_failed_topup_is_retried_by_the_next_caller(monkeypatch):
    order: list = []
    st = _wire(monkeypatch, order, topup_ok=False)
    scheduler._morning_topup_job()
    assert scheduler.DAILY_TOPUP_KEY not in st.doc  # nothing recorded
    scheduler._topup_once_today("09:20")
    assert order == ["topup", "topup"]


def test_the_morning_topup_is_scheduled_before_the_scan():
    src = open("web/scheduler.py").read()
    code = "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith("#"))
    i = code.index("_morning_topup_job, \"cron\"")
    block = code[i:i + 200]
    assert "hour=8" in block and "minute=30" in block
