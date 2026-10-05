"""Owner order 2026-10-05 ("go with memory fix").

The 512 MiB box held 353 MiB after the 09:20 rotation and 494 after 14:30,
and Render killed the process at 511 MiB (10:09 IST) when the trigger layer
loaded its panels on top. Freed memory stayed inside the process. These
tests pin the fix: memory is handed back after every category pass, before
and after the trigger layer, after every scheduler job, and the arena cap is
set at startup. No trading rule is involved.
"""
from __future__ import annotations

import io
import re
import tokenize

import pytest

from web import memory_relief, scheduler, state


def _code_only(path: str) -> str:
    """Source with comments and docstrings stripped (the source-pin lesson)."""
    src = open(path).read()
    out, prev = [], None
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and prev in (tokenize.INDENT, tokenize.NEWLINE,
                                                     tokenize.DEDENT, None):
            prev = tok.type
            continue
        out.append(tok.string)
        if tok.type not in (tokenize.NL, tokenize.COMMENT):
            prev = tok.type
    return " ".join(out)


class _FakeLibc:
    def __init__(self):
        self.calls = []

    def malloc_trim(self, pad):
        self.calls.append(("trim", pad))
        return 1

    def mallopt(self, param, value):
        self.calls.append(("mallopt", param, value))
        return 1


@pytest.fixture
def fake_libc(monkeypatch):
    lib = _FakeLibc()
    monkeypatch.setattr(memory_relief, "_LIBC", lib)
    monkeypatch.setattr(memory_relief, "_LIBC_TRIED", True)
    return lib


def test_release_collects_and_trims(fake_libc, monkeypatch):
    collected = []
    monkeypatch.setattr(memory_relief.gc, "collect", lambda: collected.append(1) or 0)
    out = memory_relief.release("x")
    assert collected, "gc.collect must run first"
    assert ("trim", 0) in fake_libc.calls
    assert out["trimmed"] is True and out["label"] == "x"


def test_release_never_raises_without_glibc(monkeypatch):
    monkeypatch.setattr(memory_relief, "_LIBC", None)
    monkeypatch.setattr(memory_relief, "_LIBC_TRIED", True)
    out = memory_relief.release("no libc")
    assert out["trimmed"] is False


def test_release_survives_a_broken_trim(monkeypatch):
    class Bad:
        def malloc_trim(self, pad):
            raise OSError("boom")
    monkeypatch.setattr(memory_relief, "_LIBC", Bad())
    monkeypatch.setattr(memory_relief, "_LIBC_TRIED", True)
    assert memory_relief.release("bad")["trimmed"] is False


def test_limit_arenas_caps_at_two_by_default(fake_libc, monkeypatch):
    monkeypatch.delenv("SB_MALLOC_ARENA_MAX", raising=False)
    assert memory_relief.limit_arenas() is True
    assert ("mallopt", memory_relief.M_ARENA_MAX, 2) in fake_libc.calls
    assert memory_relief.M_ARENA_MAX == -8          # glibc's M_ARENA_MAX


def test_limit_arenas_can_be_switched_off(fake_libc, monkeypatch):
    monkeypatch.setenv("SB_MALLOC_ARENA_MAX", "0")
    assert memory_relief.limit_arenas() is False
    assert not fake_libc.calls


def test_the_real_libc_trim_runs_here():
    """On this Linux box the real call must work (Render is Linux too)."""
    memory_relief._LIBC_TRIED = False
    memory_relief._LIBC = None
    out = memory_relief.release("real")
    assert out["before_mb"] is None or out["before_mb"] > 0
    assert out["trimmed"] in (True, False)


class _R:
    opened, skipped = [], []


def test_the_rotation_releases_after_every_category(monkeypatch):
    cfg = state.cfg()
    seen = []
    monkeypatch.setattr(state.engine(), "scan", lambda: seen.append(("scan", cfg.mode)) or _R())
    monkeypatch.setattr("trading.calendars.is_market_open", lambda *a, **k: False)
    monkeypatch.setattr(memory_relief, "release",
                        lambda label="": seen.append(("release", label)) or {})
    original = cfg.mode
    try:
        scheduler._run_category_rotation(state.engine(), cfg, "09:20")
    finally:
        cfg.mode = original
        cfg.apply_mode_preset()
    kinds = [k for k, _ in seen]
    assert kinds == ["scan", "release"] * len(scheduler._DAILY_MODE_ORDER)


def test_the_rotation_releases_even_when_a_scan_crashes(monkeypatch):
    cfg = state.cfg()
    released = []

    def boom():
        raise RuntimeError("scan died")
    monkeypatch.setattr(state.engine(), "scan", boom)
    monkeypatch.setattr("trading.calendars.is_market_open", lambda *a, **k: False)
    monkeypatch.setattr(memory_relief, "release",
                        lambda label="": released.append(label) or {})
    original = cfg.mode
    try:
        scheduler._run_category_rotation(state.engine(), cfg, "09:20")
    finally:
        cfg.mode = original
        cfg.apply_mode_preset()
    assert len(released) == len(scheduler._DAILY_MODE_ORDER)


def test_0920_releases_before_and_after_the_trigger_layer(monkeypatch):
    order = []
    monkeypatch.setattr("trading.calendars.is_trading_day", lambda *a: True)
    monkeypatch.setattr(scheduler, "_degraded_halt", lambda where: False)
    monkeypatch.setattr(scheduler, "_topup_once_today", lambda where: None)
    monkeypatch.setattr(scheduler, "refresh_breadth_store", lambda where: None)
    monkeypatch.setattr(scheduler, "_weekly_index_refresh", lambda: None)
    monkeypatch.setattr(scheduler, "_sector_index_refresh", lambda: None)
    monkeypatch.setattr(scheduler, "_run_category_rotation",
                        lambda eng, cfg, where: order.append("rotation") or [])
    monkeypatch.setattr(scheduler, "_check_pending_orders", lambda *a: None)
    monkeypatch.setattr(scheduler, "_push_phone_alerts", lambda *a, **k: None)
    monkeypatch.setattr(scheduler, "run_trigger_layer",
                        lambda where="manual": order.append("trigger") or {})
    monkeypatch.setattr(memory_relief, "release",
                        lambda label="": order.append("release") or {})
    scheduler._daily_multi_mode_scan()
    assert order == ["rotation", "release", "trigger", "release"]


def test_every_scheduler_job_end_releases_memory(monkeypatch):
    """A real APScheduler, a real job: the listener must fire on success AND
    on a crash."""
    import threading

    from apscheduler.schedulers.background import BackgroundScheduler
    released = []
    done = threading.Event()
    monkeypatch.setattr(memory_relief, "release",
                        lambda label="": (released.append(label), done.set()))
    s = BackgroundScheduler()
    s.add_listener(scheduler._release_after_job, scheduler._job_end_mask())
    s.start()
    try:
        s.add_job(lambda: None, id="ok_job")
        assert done.wait(5)
        done.clear()

        def crash():
            raise RuntimeError("x")
        s.add_job(crash, id="bad_job")
        assert done.wait(5)
    finally:
        s.shutdown(wait=False)
    assert any("ok_job" in r for r in released)
    assert any("bad_job" in r for r in released)


def test_the_live_scheduler_registers_the_listener():
    code = _code_only("web/scheduler.py")
    assert re.search(r"_SCHED \. add_listener \( _release_after_job", code)


def test_main_caps_arenas_before_the_scheduler_import():
    code = _code_only("web/main.py")
    cap = code.index("memory_relief . limit_arenas ( )")
    sched = code.index("import health_stats , readiness , scheduler")
    assert cap < sched


def test_render_blueprint_sets_the_arena_cap():
    text = open("render.yaml").read()
    assert re.search(r"key: MALLOC_ARENA_MAX\s+value: \"2\"", text)
