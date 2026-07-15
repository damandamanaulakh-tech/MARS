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
)

app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

app.include_router(auth.router)
app.include_router(dashboard.router)
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


@app.on_event("startup")
def _on_startup() -> None:
    scheduler.start_if_enabled()


@app.get("/healthz")
def healthz() -> dict:
    """For Render's health checks."""
    from . import state
    return {"ok": True, "db": state.db_status()}
