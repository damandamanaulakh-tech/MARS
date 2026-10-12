"""Owner 2026-10-11 item 28: the watchlist capture runs at the END of every
category rotation (09:20 and 14:30), outside the mode lock, and leaves a
journal line either way. The capture itself is tested in
test_watchlist_capture_2026_10_12.py; this file pins the HOOK."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from web import scheduler, state


def _operative(src: str) -> str:
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))


def test_the_rotation_captures_right_after_the_momentum_pass():
    """At the END of the rotation scan_matrix / last_scan_skipped /
    last_scan_opened hold the positional pass, so a capture taken there
    mixed two passes. It runs inside the loop, on the momentum pass only."""
    src = _operative(Path(scheduler.__file__).read_text())
    body = src[src.index("def _run_category_rotation"):src.index("def _capture_watchlist")]
    assert body.count("_capture_watchlist(eng, cfg, where)") == 1
    call = body.index("_capture_watchlist(eng, cfg, where)")
    assert body.index("res = eng.scan()") < call < body.index("MODE_LOCK.release()")
    guard = body[body.rindex("if m ==", 0, call):call]
    assert '"nifty200_momentum"' in guard


def test_the_capture_runs_once_per_rotation_on_the_momentum_pass(monkeypatch):
    from web import memory_relief
    cfg = state.cfg()
    calls = []
    monkeypatch.setattr(state.engine(), "scan",
                        lambda: SimpleNamespace(opened=[], skipped=[]))
    monkeypatch.setattr("trading.calendars.is_market_open", lambda *a, **k: False)
    monkeypatch.setattr(memory_relief, "release", lambda label="": {})
    monkeypatch.setattr(scheduler, "_capture_watchlist",
                        lambda eng, c, where: calls.append(c.mode) or {})
    original = cfg.mode
    try:
        scheduler._run_category_rotation(state.engine(), cfg, "09:20")
    finally:
        cfg.mode = original
        cfg.apply_mode_preset()
    assert calls == ["nifty200_momentum"]


def test_a_capture_that_lands_journals_captured(monkeypatch):
    from web import watchlist_capture
    p = state.portfolio()
    saved = list(p.decision_journal)
    monkeypatch.setattr(watchlist_capture, "capture",
                        lambda *a, **k: {"ok": True, "ts": "2026-10-12T09:35:00+05:30",
                                         "n_rows": 7, "mongo": False, "csv": "/tmp/x.csv",
                                         "error": None, "note": ""})
    try:
        res = scheduler._capture_watchlist(SimpleNamespace(portfolio=p), state.cfg(), "test")
        assert res["ok"] is True
        line = p.decision_journal[-1]
        assert line["action"] == "WATCHLIST_CAPTURED" and line["n_rows"] == 7
        assert "mongo=no" in line["why"] and "csv=yes" in line["why"]
    finally:
        p.decision_journal = saved


def test_a_crash_inside_the_capture_never_reaches_the_rotation(monkeypatch):
    from web import watchlist_capture

    def _boom(*a, **k):
        raise RuntimeError("store exploded")

    p = state.portfolio()
    saved = list(p.decision_journal)
    monkeypatch.setattr(watchlist_capture, "capture", _boom)
    try:
        res = scheduler._capture_watchlist(SimpleNamespace(portfolio=p), state.cfg(), "test")
        assert res["ok"] is False and "store exploded" in res["error"]
        line = p.decision_journal[-1]
        assert line["action"] == "WATCHLIST_CAPTURE_FAILED" and "store exploded" in line["why"]
    finally:
        p.decision_journal = saved
