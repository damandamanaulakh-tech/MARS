"""Mongo statistics for /healthz, computed OFF the probe path.

2026-09-28 — THE DEPLOY TIMEOUTS. Render's health check hits /healthz. From
PR #112 that page ran dbStats + collStats on the app's database on every
request, and from PR #117 it also ran dbStats on EVERY database on the
cluster (to name the storage hog). Measured on the live box before #117:
4.7 s warm, 14.6 s cold. Render's check gives up sooner than that, so every
deploy since #117 booted, passed the port probe (`HEAD /` 200) and then
ended with `==> Timed Out`; once the last pre-#117 instance died on
2026-09-26 night, no instance ever passed the check again and the edge
answered 503 for two days.

Rule from that: the probe path may read MEMORY only. Everything that
touches Mongo runs here, in one daemon thread, on a timer; /healthz copies
the latest snapshot and answers in milliseconds. A slow or dead database
makes the numbers stale (and says so via `asof`/`age_s`) — it can never
make the health check fail, and a health check that fails for a slow stats
query is a lie about the app's health.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("web.health_stats")

#: seconds between refreshes; SB_HEALTH_STATS_INTERVAL_S overrides (tests).
INTERVAL_S = float(os.environ.get("SB_HEALTH_STATS_INTERVAL_S", "60"))

_LOCK = threading.Lock()
_SNAPSHOT: dict = {
    "history_tickers": None,   # stored OHLCV count (db.list_history_meta)
    "mongo": None,             # db.storage_stats() — sizes only
    "asof": None,              # ISO UTC of the last successful compute
    "elapsed_s": None,         # how long that compute took
    "error": None,             # last compute error text, cleared on success
}
_THREAD: Optional[threading.Thread] = None


def compute() -> dict:
    """One pass of the Mongo reads. Never raises; a failure is recorded in
    `error` and the previous numbers are kept by refresh()."""
    from . import db
    t0 = time.monotonic()
    out: dict = {}
    try:
        out["history_tickers"] = len(db.list_history_meta())
        out["mongo"] = db.storage_stats()
        out["error"] = None
    except Exception as e:                                    # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {str(e)[:160]}"
    out["elapsed_s"] = round(time.monotonic() - t0, 3)
    return out


def refresh() -> dict:
    """compute() and publish. On an error the old numbers stand and the
    error is shown beside them — a broken stats query must never turn a
    live app into 'no data'."""
    got = compute()
    with _LOCK:
        if got.get("error") is None:
            _SNAPSHOT["history_tickers"] = got.get("history_tickers")
            _SNAPSHOT["mongo"] = got.get("mongo")
            _SNAPSHOT["asof"] = datetime.now(timezone.utc).isoformat()
        _SNAPSHOT["error"] = got.get("error")
        _SNAPSHOT["elapsed_s"] = got.get("elapsed_s")
        return dict(_SNAPSHOT)


def snapshot() -> dict:
    """A copy of the latest numbers plus `age_s` (None until the first
    compute lands) and `pending` — memory only, no database call."""
    with _LOCK:
        snap = dict(_SNAPSHOT)
    age = None
    if snap.get("asof"):
        try:
            age = round((datetime.now(timezone.utc)
                         - datetime.fromisoformat(snap["asof"])).total_seconds(), 1)
        except Exception:                                     # noqa: BLE001
            age = None
    snap["age_s"] = age
    snap["pending"] = snap.get("asof") is None
    return snap


def _loop(interval: float) -> None:
    while True:
        try:
            refresh()
        except Exception:                                     # noqa: BLE001
            log.exception("health stats refresh failed")
        time.sleep(max(0.01, interval))


def start(interval: Optional[float] = None) -> bool:
    """Start the refresh thread once (idempotent). Returns True when a
    thread was started by this call. The first compute runs IN the thread,
    so app startup — and the health probe — never wait on Mongo."""
    global _THREAD
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return False
        _THREAD = threading.Thread(
            target=_loop, args=(INTERVAL_S if interval is None else float(interval),),
            name="health-stats", daemon=True)
        _THREAD.start()
        return True


def _reset_for_tests() -> None:
    """Forget the snapshot (the thread, if any, keeps running)."""
    with _LOCK:
        for k in ("history_tickers", "mongo", "asof", "elapsed_s", "error"):
            _SNAPSHOT[k] = None
