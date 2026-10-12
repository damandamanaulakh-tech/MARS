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


def test_the_rotation_captures_after_releasing_the_mode_lock():
    src = _operative(Path(scheduler.__file__).read_text())
    body = src[src.index("def _run_category_rotation"):src.index("def _capture_watchlist")]
    assert "_capture_watchlist(eng, cfg, where)" in body
    assert body.index("MODE_LOCK.release()") < body.index("_capture_watchlist(eng, cfg, where)")
    assert body.index("_capture_watchlist(eng, cfg, where)") < body.index("return summary")


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
