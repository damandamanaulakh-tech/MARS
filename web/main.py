"""FastAPI app entry point. Run with: `uvicorn web.main:app --reload`."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import health_stats, readiness, scheduler
from .routes import (
    auth, dashboard, positions, scan_routes, settings_routes,
    upstox, backtest_routes, regime_routes,
    strategy_routes, reports_routes, risk_routes, alerts_routes,
    signal_routes, chart_routes, lens_routes, trigger_routes,
    register_routes,
)


WEB_DIR = Path(__file__).parent

# ADOPTED 2026-08-29 (owner order "go ... 1") from the AshStocks review:
# fail closed instead of falling back. On Render a missing MONGO_URL used to
# put the live book in /tmp (erased on restart) and a missing password used
# to seed a guessable one — both silently. This raises BEFORE the app object
# is served, so `uvicorn web.main:app` fails with the reason and Render keeps
# the last good deploy running. No-op outside production; SB_ALLOW_INSECURE=1
# overrides it loudly. See web/readiness.py.
readiness.assert_ready()


app = FastAPI(
    title="Sourceborn AM07",
    description="AM07 trading engine — control panel",
    version="2.0.0-dev",
)

# Session cookies for simple role-based auth (no JWT yet — keep beginner-friendly)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", "dev-only-change-me-in-render-env"),
    same_site="lax",
    # AUDIT E: Secure cookies on Render (it sets RENDER=true and terminates
    # HTTPS for us). Plain HTTP — local dev and the test client — would never
    # receive a Secure cookie, so the flag must follow the deployment, not be
    # hardcoded. SESSION_SECURE=1 forces it on anywhere else.
    https_only=bool(
        os.environ.get("RENDER") or os.environ.get("SESSION_SECURE") == "1"
    ),
    max_age=30 * 24 * 60 * 60,  # keep the owner logged in for 30 days
)

app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(signal_routes.router)
app.include_router(positions.router)
app.include_router(scan_routes.router)
app.include_router(settings_routes.router)
app.include_router(upstox.router)
app.include_router(backtest_routes.router)
app.include_router(regime_routes.router)
app.include_router(strategy_routes.router)
app.include_router(reports_routes.router)
app.include_router(risk_routes.router)
app.include_router(alerts_routes.router)
app.include_router(chart_routes.router)
app.include_router(lens_routes.router)
app.include_router(trigger_routes.router)
app.include_router(register_routes.router)


@app.on_event("startup")
def _on_startup() -> None:
    scheduler.start_if_enabled()
    # Mongo statistics for /healthz run on a timer in their own thread; the
    # health probe answers from memory (see web/health_stats.py).
    health_stats.start()


@app.get("/sw.js")
def service_worker():
    """PWA service worker — must be served at root scope."""
    from fastapi.responses import FileResponse
    return FileResponse(WEB_DIR / "static" / "sw.js",
                        media_type="application/javascript")


# 2026-09-26 — Render's Events log for every deploy since PR #116 showed:
#     INFO: Uvicorn running on http://0.0.0.0:10000
#     INFO: 127.0.0.1:49914 - "HEAD / HTTP/1.1" 405 Method Not Allowed
# and this route was added so that probe answers 200. CORRECTED 2026-09-28
# from a fuller log: that HEAD is Render's PORT-DETECTION probe, it printed
# 405 on the old build too and those deploys succeeded — it was never the
# failure. The deploys were ending `==> Timed Out` because the real health
# check, GET /healthz, ran Mongo statistics slower than Render's timeout
# (see web/health_stats.py). HEAD / stays: liveness only, no body, no auth
# state; GET / still redirects to login. render.yaml's healthCheckPath is
# /healthz — keep the Render dashboard's Health Check Path on /healthz too.
@app.head("/", include_in_schema=False)
def root_head():
    from fastapi.responses import Response
    return Response(status_code=200)


@app.api_route("/healthz", methods=["GET", "HEAD"])
def healthz() -> dict:
    """For Render's health checks. history_tickers = stored OHLCV count so
    bulk-download completion is externally verifiable (a count leaks nothing);
    universe = size + last-refresh date of the NSE master (proof the daily
    auto-refresh ran, visible without login).

    PROBE-PATH RULE (2026-09-28): nothing below touches Mongo. The stored
    count and the storage sizes come from web/health_stats.py's snapshot,
    refreshed on a timer in its own thread — a slow cluster made every
    deploy since PR #117 time out on this very page and took the app off
    the air for two days. `stats` names the snapshot's age so a stale
    number is visible as stale."""
    from . import state
    stats = health_stats.snapshot()
    hist = stats.get("history_tickers")
    universe = None
    try:
        import os
        from datetime import date

        from trading.universe_refresh import default_universe_path
        upath = default_universe_path()
        with open(upath) as fh:
            n = sum(1 for _ in fh) - 1
        universe = {"count": n,
                    "file_date": date.fromtimestamp(os.path.getmtime(upath)).isoformat()}
    except Exception:
        pass
    # Readiness is booleans + the names of any unset variables, never their
    # values — safe on an unauthenticated endpoint, and it means a
    # misconfigured deploy is visible from outside instead of only in logs.
    # OWNER ASK 2026-09-24 ("update scan to 1200 / chk mongo / its hitting
    # 512 +"): both 512s — Render's RAM and Atlas's free-tier storage — were
    # invisible from outside, and so was the size of the list the scan
    # actually reads (a session only ever saw the local default). All three
    # go here. Sizes and counts only; nothing here leaks a value.
    memory = None
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0  # KiB -> MiB
        rss = None
        try:
            with open("/proc/self/statm") as fh:       # Linux (Render)
                rss = (int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
                       / (1024.0 * 1024.0))            # MiB, same unit as peak
        except Exception:
            pass
        memory = {"rss_mb": round(rss, 0) if rss is not None else None,
                  "peak_mb": round(peak, 0)}
    except Exception:
        pass
    scan_universe = None
    try:
        cfg = state.cfg()
        custom = tuple(getattr(cfg, "custom_universe", ()) or ())
        batch = getattr(state.portfolio(), "universe_batch", None) or {}
        scan_universe = {
            "count": len(custom),
            "target": int(getattr(cfg, "universe_target_size", 0) or 0),
            "last_rebuild": (str(batch.get("ts") or "")[:10] or None),
            "rebuild_count": batch.get("count"),
        }
    except Exception:
        pass
    mongo = stats.get("mongo")
    return {"ok": True, "db": state.db_status(), "history_tickers": hist,
            "universe": universe, "scan_universe": scan_universe,
            "memory": memory, "mongo": mongo,
            # audit M-3: structure only (key names / types / list lengths)
            "market_info_shape": stats.get("market_info_shape"),
            "stats": {"asof": stats.get("asof"), "age_s": stats.get("age_s"),
                      "pending": stats.get("pending"),
                      "elapsed_s": stats.get("elapsed_s"),
                      "error": stats.get("error")},
            "readiness": readiness.status()}
