"""FastAPI app entry point. Run with: `uvicorn web.main:app --reload`."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import scheduler
from .routes import (
    auth, dashboard, positions, scan_routes, settings_routes,
    stubs, upstox, backtest_routes, regime_routes,
    strategy_routes, reports_routes, risk_routes, alerts_routes,
    signal_routes, chart_routes, lens_routes,
)


WEB_DIR = Path(__file__).parent


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
    https_only=False,  # set True when behind Render's HTTPS proxy
    max_age=30 * 24 * 60 * 60,  # keep the owner logged in for 30 days
)

app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(signal_routes.router)
app.include_router(positions.router)
app.include_router(scan_routes.router)
app.include_router(settings_routes.router)
app.include_router(stubs.router)
app.include_router(upstox.router)
app.include_router(backtest_routes.router)
app.include_router(regime_routes.router)
app.include_router(strategy_routes.router)
app.include_router(reports_routes.router)
app.include_router(risk_routes.router)
app.include_router(alerts_routes.router)
app.include_router(chart_routes.router)
app.include_router(lens_routes.router)


@app.on_event("startup")
def _on_startup() -> None:
    scheduler.start_if_enabled()


@app.get("/sw.js")
def service_worker():
    """PWA service worker — must be served at root scope."""
    from fastapi.responses import FileResponse
    return FileResponse(WEB_DIR / "static" / "sw.js",
                        media_type="application/javascript")


@app.get("/healthz")
def healthz() -> dict:
    """For Render's health checks. history_tickers = stored OHLCV count so
    bulk-download completion is externally verifiable (a count leaks nothing);
    universe = size + last-refresh date of the NSE master (proof the daily
    auto-refresh ran, visible without login)."""
    from . import db, state
    try:
        hist = len(db.list_history_meta())
    except Exception:
        hist = None
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
    return {"ok": True, "db": state.db_status(), "history_tickers": hist,
            "universe": universe}
