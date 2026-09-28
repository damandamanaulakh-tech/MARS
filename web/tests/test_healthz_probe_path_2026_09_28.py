"""/healthz must answer from memory — Mongo statistics live on a timer.

Owner's Render log, 2026-09-28: every deploy since PR #117 booted, passed
the port probe (`127.0.0.1 "HEAD /" 200`), was killed ~5 min later and
ended `==> Timed Out`. The real health check is GET /healthz, and since
#112/#117 that page ran dbStats + collStats on the app db AND dbStats on
every database on the cluster, per request (4.7 s warm / 14.6 s cold
measured live before #117). Render's check gives up sooner, so no instance
ever passed; once the last pre-#117 instance died the edge answered 503
for two days.

Pinned here: the request path never calls the database (a stats function
that HANGS cannot slow the probe), the background refresh publishes real
numbers that the page then shows, a failing refresh keeps the old numbers
and says so, and app startup starts the thread.
"""
from __future__ import annotations

import inspect
import time

from fastapi.testclient import TestClient

from web import health_stats
from web.main import app


def _operative(src: str) -> str:
    """Strip comment lines and the docstring before pinning source — the
    explanatory text quotes the old expressions."""
    body = src.split('"""', 2)[-1] if src.count('"""') >= 2 else src
    return "\n".join(l for l in body.splitlines() if not l.strip().startswith("#"))


def test_healthz_never_waits_on_the_database(monkeypatch):
    """A stats query that hangs for 5 s must not slow the probe: the page
    reads the snapshot, so the request returns in well under a second."""
    from web import db

    def hang(*a, **k):
        time.sleep(5)
        return {"data_mb": 1.0}

    monkeypatch.setattr(db, "storage_stats", hang)
    monkeypatch.setattr(db, "list_history_meta", hang)
    t0 = time.monotonic()
    r = TestClient(app).get("/healthz")
    took = time.monotonic() - t0
    assert r.status_code == 200
    assert took < 1.0, f"/healthz waited on the database: {took:.2f}s"
    body = r.json()
    assert "stats" in body and set(body["stats"]) >= {"asof", "age_s", "pending"}


def test_the_refresh_publishes_numbers_the_page_then_shows(monkeypatch):
    from web import db
    monkeypatch.setattr(db, "storage_stats",
                        lambda: {"data_mb": 68.0, "index_mb": 0.0,
                                 "cluster": {"databases": [], "total_mb": 68.0}})
    monkeypatch.setattr(db, "list_history_meta", lambda: [{"_id": "A"}, {"_id": "B"}])
    health_stats._reset_for_tests()
    snap = health_stats.refresh()
    assert snap["history_tickers"] == 2
    assert snap["mongo"]["data_mb"] == 68.0
    assert snap["error"] is None and snap["asof"]
    body = TestClient(app).get("/healthz").json()
    assert body["history_tickers"] == 2
    assert body["mongo"]["data_mb"] == 68.0
    assert body["stats"]["pending"] is False
    assert body["stats"]["age_s"] is not None and body["stats"]["age_s"] >= 0


def test_a_failing_refresh_keeps_the_old_numbers_and_names_the_error(monkeypatch):
    from web import db
    monkeypatch.setattr(db, "storage_stats", lambda: {"data_mb": 5.0})
    monkeypatch.setattr(db, "list_history_meta", lambda: [{"_id": "A"}])
    health_stats._reset_for_tests()
    health_stats.refresh()

    def boom():
        raise RuntimeError("cluster unreachable")
    monkeypatch.setattr(db, "storage_stats", boom)
    snap = health_stats.refresh()                       # must not raise
    assert snap["history_tickers"] == 1                 # old numbers stand
    assert snap["mongo"] == {"data_mb": 5.0}
    assert "cluster unreachable" in (snap["error"] or "")
    assert TestClient(app).get("/healthz").json()["stats"]["error"]


def test_the_timer_thread_refreshes_on_its_own(monkeypatch):
    from web import db
    calls = []
    monkeypatch.setattr(db, "storage_stats", lambda: calls.append(1) or {"data_mb": 1.0})
    monkeypatch.setattr(db, "list_history_meta", lambda: [])
    health_stats._reset_for_tests()
    # the module-level thread may already run (app startup in another test);
    # a fresh short-interval start is refused then, so drive the loop by
    # waiting on the snapshot either way
    health_stats.start(interval=0.05)
    deadline = time.time() + 5
    while time.time() < deadline and health_stats.snapshot()["pending"]:
        time.sleep(0.05)
    assert not health_stats.snapshot()["pending"], "no refresh landed in 5 s"


def test_app_startup_starts_the_stats_thread(monkeypatch):
    started = []
    monkeypatch.setattr(health_stats, "start", lambda *a, **k: started.append(1) or True)
    with TestClient(app):
        pass
    assert started, "startup must start the health-stats thread"


def test_the_probe_path_contains_no_database_call():
    """Source pin on the OPERATIVE lines of the handler (docstring and
    comments stripped): the Mongo readers are named nowhere in it."""
    from web import main
    code = _operative(inspect.getsource(main.healthz))
    for name in ("storage_stats", "list_history_meta", "db."):
        assert name not in code, f"/healthz must not call {name} on the probe path"
    assert "health_stats.snapshot()" in code


def test_no_other_date_clock_or_probe_regression():
    """render.yaml keeps /healthz as the check path — the cached page is
    what the probe must hit."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    assert "healthCheckPath: /healthz" in (root / "render.yaml").read_text()
