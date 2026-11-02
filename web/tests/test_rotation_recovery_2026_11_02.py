"""Recovery follows category completion, not a manual scan's timestamp."""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock
import threading

import pytest

from trading.config import Config
from trading.paper import Portfolio
from web import scheduler, state, why_no_buy


@pytest.fixture
def rotation(tmp_path, monkeypatch):
    cfg = Config(state_dir=str(tmp_path), mode="intraday")
    p = Portfolio.fresh(cfg)
    saved = []
    def save():
        saved.append(deepcopy(p.rotation_report))
        return True
    monkeypatch.setattr(p, "save", save)
    calls = []
    def scan():
        calls.append(cfg.mode)
        return SimpleNamespace(ranked=[], picks=[], opened=[], skipped=[])
    eng = SimpleNamespace(portfolio=p, scan=scan, refresh=Mock())
    monkeypatch.setattr(state, "portfolio", lambda: p)
    monkeypatch.setattr(state, "cfg", lambda: cfg)
    monkeypatch.setattr(state, "engine", lambda: eng)
    monkeypatch.setattr("trading.calendars.is_market_open", lambda: True)
    monkeypatch.setattr("trading.calendars.is_trading_day", lambda: True)
    monkeypatch.setattr("web.clock.ist_today_iso", lambda: "2026-11-02")
    monkeypatch.setattr(scheduler, "_exit_pass_due", lambda cfg: False)
    monkeypatch.setattr(scheduler, "_capture_watchlist", lambda *a: None)
    monkeypatch.setattr("web.memory_relief.release", lambda *a: None)
    monkeypatch.setattr(scheduler, "_ROTATION_LOCK", threading.Lock())
    return cfg, p, eng, calls, saved


def test_manual_live_scan_does_not_complete_rotation(rotation, monkeypatch):
    _, p, _, _, _ = rotation
    p.last_scan = p.last_scan_live = datetime.utcnow().isoformat()
    monkeypatch.setenv("SB_DISABLE_CATCHUP", "0")
    monkeypatch.setattr(scheduler, "daily_enabled", lambda: True)
    thread = Mock()
    monkeypatch.setattr("threading.Thread", thread)
    try:
        assert scheduler.catchup_scan_if_missed("test") is True
        thread.assert_called_once()
    finally:
        # The mocked thread never starts its finally block.
        lock = getattr(scheduler, "_CATCHUP_LOCK", None)
        if lock and lock.locked():
            lock.release()


def test_interruption_retains_pass_and_restart_resumes_only_remaining(rotation):
    cfg, p, eng, calls, saved = rotation
    normal_scan = eng.scan
    def interrupt():
        if cfg.mode == "intraday":
            raise KeyboardInterrupt("simulated process interruption")
        return normal_scan()
    eng.scan = interrupt
    with pytest.raises(KeyboardInterrupt):
        scheduler._run_category_rotation(eng, cfg, "09:20")
    assert saved and saved[-1]["daily"]["passes"]["nifty200_momentum"]["completed"]
    assert cfg.mode == "intraday"
    p.rotation_report = deepcopy(saved[-1])  # restart from acknowledged snapshot
    eng.scan = normal_scan
    scheduler._run_category_rotation(eng, cfg, "09:20")
    assert calls == list(scheduler._DAILY_MODE_ORDER)
    assert p.rotation_report["daily"]["status"] == "complete"


def test_failed_pass_is_retryable_without_repeating_successes(rotation):
    cfg, p, eng, calls, _ = rotation
    normal_scan = eng.scan
    def fail():
        if cfg.mode == "short":
            raise ValueError("unavailable")
        return normal_scan()
    eng.scan = fail
    scheduler._run_category_rotation(eng, cfg, "09:20")
    assert p.rotation_report["daily"]["status"] == "partial"
    eng.scan = normal_scan
    scheduler._run_category_rotation(eng, cfg, "09:20")
    assert calls.count("nifty200_momentum") == 1
    assert calls.count("short") == 1
    assert p.rotation_report["daily"]["status"] == "complete"


def test_refused_initial_checkpoint_prevents_scan(rotation, monkeypatch):
    cfg, p, eng, calls, _ = rotation
    monkeypatch.setattr(p, "save", lambda: False)
    scheduler._run_category_rotation(eng, cfg, "09:20")
    assert calls == []
    assert p.rotation_report["daily"]["status"] == "save_failed"
    assert "not acknowledged" in why_no_buy.card(p, cfg, "2026-11-02")["note"]


def test_refused_pass_checkpoint_stops_next_category(rotation, monkeypatch):
    cfg, p, eng, calls, saved = rotation
    save = p.save
    def refuse_after_scan():
        return False if calls else save()
    monkeypatch.setattr(p, "save", refuse_after_scan)
    scheduler._run_category_rotation(eng, cfg, "09:20")
    assert calls == ["nifty200_momentum"]
    assert p.rotation_report["daily"]["status"] == "save_failed"


def test_preparation_failure_releases_rotation_lock(rotation, monkeypatch):
    _, p, _, _, _ = rotation
    monkeypatch.setattr(scheduler, "_degraded_halt", lambda where: False)
    def fail(where):
        raise RuntimeError("prep failed")
    monkeypatch.setattr(scheduler, "apply_corporate_actions_job", fail)
    scheduler._daily_multi_mode_scan()
    assert not scheduler._ROTATION_LOCK.locked()
    assert any(e["action"] == "SCAN_FAILED" for e in p.decision_journal)


def test_look_only_rotation_does_not_skip_live_categories(rotation, monkeypatch):
    cfg, p, eng, calls, _ = rotation
    monkeypatch.setattr("trading.calendars.is_market_open", lambda: False)
    scheduler._run_category_rotation(eng, cfg, "09:20")
    monkeypatch.setattr("trading.calendars.is_market_open", lambda: True)
    scheduler._run_category_rotation(eng, cfg, "09:20")
    assert calls == list(scheduler._DAILY_MODE_ORDER) * 2
    assert p.rotation_report["daily"]["phase"] == "live"


def test_complete_rotation_does_not_repeat(rotation):
    cfg, p, eng, calls, _ = rotation
    scheduler._run_category_rotation(eng, cfg, "09:20")
    scheduler._run_category_rotation(eng, cfg, "09:20")
    assert calls == list(scheduler._DAILY_MODE_ORDER)


def test_manual_scan_cannot_supply_missing_momentum_reason(rotation):
    cfg, p, _, _, _ = rotation
    p.last_scan_live = "2026-11-02T06:50:27"
    p.last_scan_skipped = [{"ticker": "A.NS", "reason": "already_held"}]
    card = why_no_buy.card(p, cfg, "2026-11-02")
    assert "single-mode" in card["note"]
    assert card["rows"] == []


def test_duplicate_catchup_triggers_launch_one_worker(rotation, monkeypatch):
    monkeypatch.setenv("SB_DISABLE_CATCHUP", "0")
    monkeypatch.setattr(scheduler, "daily_enabled", lambda: True)
    monkeypatch.setattr(scheduler, "_CATCHUP_LOCK", threading.Lock())
    worker = Mock()
    monkeypatch.setattr("threading.Thread", worker)
    assert scheduler.catchup_scan_if_missed("startup") is True
    assert scheduler.catchup_scan_if_missed("login") is False
    worker.assert_called_once()
    # Run the actual worker closure after replacing external preparation.
    monkeypatch.setattr("trading.universe_refresh.refreshed_today", lambda: True)
    monkeypatch.setattr(scheduler, "apply_corporate_actions_job", lambda where: None)
    monkeypatch.setattr(scheduler, "_daily_multi_mode_scan", lambda: None)
    worker.call_args.kwargs["target"]()
    assert not scheduler._CATCHUP_LOCK.locked()


def test_completed_daily_rotation_suppresses_catchup(rotation, monkeypatch):
    cfg, _, eng, _, _ = rotation
    scheduler._run_category_rotation(eng, cfg, "09:20")
    monkeypatch.setenv("SB_DISABLE_CATCHUP", "0")
    monkeypatch.setattr(scheduler, "daily_enabled", lambda: True)
    worker = Mock()
    monkeypatch.setattr("threading.Thread", worker)
    assert scheduler.catchup_scan_if_missed("startup") is False
    worker.assert_not_called()


def test_off_hours_scan_stamp_is_compared_in_ist(rotation, monkeypatch):
    _, p, _, _, _ = rotation
    monkeypatch.setenv("SB_DISABLE_CATCHUP", "0")
    monkeypatch.setattr(scheduler, "daily_enabled", lambda: True)
    monkeypatch.setattr("trading.calendars.is_market_open", lambda: False)
    # 02:00 IST on November 2, despite the UTC timestamp's November 1 date.
    p.last_scan = "2026-11-01T20:30:00"
    worker = Mock()
    monkeypatch.setattr("threading.Thread", worker)
    assert scheduler.catchup_scan_if_missed("startup") is False
    worker.assert_not_called()


def test_old_or_incomplete_completion_record_cannot_skip_work(rotation):
    from web.rotation_progress import complete
    cfg, p, eng, _, _ = rotation
    scheduler._run_category_rotation(eng, cfg, "09:20")
    rot = deepcopy(p.rotation_report["daily"])
    assert not complete(rot, scheduler._DAILY_MODE_ORDER, "2026-11-03")
    rot["passes"].pop("nifty200_momentum")
    assert not complete(rot, scheduler._DAILY_MODE_ORDER, "2026-11-02")


def test_market_close_mid_pass_requires_live_retry(rotation, monkeypatch):
    cfg, p, eng, calls, _ = rotation
    market = {"open": True}
    monkeypatch.setattr("trading.calendars.is_market_open", lambda: market["open"])
    normal = eng.scan
    def close_mid_pass():
        result = normal()
        market["open"] = False
        return result
    eng.scan = close_mid_pass
    scheduler._run_category_rotation(eng, cfg, "09:20")
    assert p.rotation_report["daily"]["status"] == "partial"
    assert not p.rotation_report["daily"]["passes"]["nifty200_momentum"]["live"]


def test_refused_final_checkpoint_is_not_complete(rotation, monkeypatch):
    cfg, p, eng, _, _ = rotation
    save = p.save
    def refuse_final():
        active = p.rotation_report.get("active", {})
        return False if active.get("status") == "complete" else save()
    monkeypatch.setattr(p, "save", refuse_final)
    scheduler._run_category_rotation(eng, cfg, "09:20")
    assert p.rotation_report["daily"]["status"] == "save_failed"
    assert "not acknowledged" in why_no_buy.card(p, cfg, "2026-11-02")["note"]


def test_daily_refused_checkpoint_does_not_process_pending_orders(rotation, monkeypatch):
    _, p, _, calls, _ = rotation
    for name in ("apply_corporate_actions_job", "_topup_once_today", "refresh_breadth_store",
                 "_weekly_index_refresh", "_sector_index_refresh"):
        monkeypatch.setattr(scheduler, name, lambda *a: None)
    monkeypatch.setattr(scheduler, "_degraded_halt", lambda where: False)
    monkeypatch.setattr(p, "save", lambda: False)
    orders = Mock()
    monkeypatch.setattr(scheduler, "_check_pending_orders", orders)
    scheduler._daily_multi_mode_scan()
    assert not calls
    orders.assert_not_called()
    assert not scheduler._ROTATION_LOCK.locked()


def test_catchup_worker_releases_lock_after_preparation_error(rotation, monkeypatch):
    _, p, _, _, _ = rotation
    monkeypatch.setenv("SB_DISABLE_CATCHUP", "0")
    monkeypatch.setattr(scheduler, "daily_enabled", lambda: True)
    monkeypatch.setattr(scheduler, "_CATCHUP_LOCK", threading.Lock())
    worker = Mock()
    monkeypatch.setattr("threading.Thread", worker)
    monkeypatch.setattr("trading.universe_refresh.refreshed_today", lambda: False)
    def fail():
        raise RuntimeError("preparation unavailable")
    monkeypatch.setattr(scheduler, "_universe_refresh_job", fail)
    assert scheduler.catchup_scan_if_missed("startup")
    with pytest.raises(RuntimeError, match="preparation unavailable"):
        worker.call_args.kwargs["target"]()
    assert not scheduler._CATCHUP_LOCK.locked()
    assert any(e["action"] == "SCAN_FAILED" for e in p.decision_journal)


def test_partial_progress_notice_is_rendered(rotation):
    from web.templating import templates
    cfg, p, eng, _, _ = rotation
    p.save = lambda: False
    scheduler._run_category_rotation(eng, cfg, "09:20")
    # Render the actual card fragment to avoid unrelated page dependencies.
    source = templates.env.loader.get_source(templates.env, "dashboard.html")[0]
    fragment = source.split("{% if why_no_buy %}", 1)[1].split("{% if wnb.rows %}", 1)[0]
    rendered = templates.env.from_string(fragment).render(
        why_no_buy=why_no_buy.card(p, cfg, "2026-11-02"))
    assert 'data-rotation-progress="1"' in rendered
    assert "save failed" in rendered
    assert "not acknowledged" in rendered
    assert "0 recorded buys" in rendered
    assert "NO BUYS" not in rendered


def test_momentum_timestamp_survives_later_category_failure(rotation):
    cfg, p, eng, _, _ = rotation
    normal = eng.scan
    def fail_later():
        if cfg.mode == "intraday":
            raise RuntimeError("data unavailable")
        return normal()
    eng.scan = fail_later
    scheduler._run_category_rotation(eng, cfg, "09:20")
    card = why_no_buy.card(p, cfg, "2026-11-02")
    assert card["momentum_completed"]["mode"] == "nifty200_momentum"
    assert card["momentum_completed"]["ts"]
    assert card["rotation"]["status"] == "partial"
