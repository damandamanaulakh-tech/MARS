"""Backtest Lab — replays the engine on 2-3 years of historical NSE data.

GET  /backtest        Page: form to pick range, last-result summary
POST /backtest/run    Kick off a synchronous backtest (~30-90s for 30 stocks)
GET  /backtest/result Cached last result as JSON (for client polling)
"""

from __future__ import annotations

import logging
import threading
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .. import auth
from .. import state as app_state
from ..templating import templates


log = logging.getLogger(__name__)
router = APIRouter()


_LAST_RESULT_LOCK = threading.Lock()
_LAST_RESULT: Optional[dict] = None
_RUNNING: bool = False


@router.get("/backtest", response_class=HTMLResponse)
def backtest_page(request: Request, error: str | None = None):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    with _LAST_RESULT_LOCK:
        result = _LAST_RESULT
        running = _RUNNING
    today = date.today()
    default_start = (today - timedelta(days=2 * 365)).isoformat()
    default_end = today.isoformat()
    # Surface the archive of past backtests so user can compare runs
    archive = []
    try:
        from .. import db
        archive = db.list_backtests(limit=50) or []
    except Exception:
        pass
    return templates.TemplateResponse(
        request, "backtest.html",
        {
            "user": auth.current_user(request),
            "active": "backtest",
            "result": result,
            "running": running,
            "default_start": default_start,
            "default_end": default_end,
            "archive": archive,
            "error": error,
        },
    )


@router.post("/backtest/run")
def backtest_run(
    request: Request,
    start_date: str = Form(...),
    end_date: str = Form(...),
):
    """Kick off backtest in a background thread so HTTP returns immediately
    (no more 502 timeouts on large universe / long range). Poll /backtest/result
    or just refresh /backtest — `running` flag tells you when done."""
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    global _LAST_RESULT, _RUNNING
    with _LAST_RESULT_LOCK:
        if _RUNNING:
            return RedirectResponse("/backtest?error=already_running", status_code=303)
        _RUNNING = True

    cfg = app_state.cfg()

    def _run_in_thread():
        global _LAST_RESULT, _RUNNING
        try:
            from trading.backtest import run_backtest
            result = run_backtest(
                cfg=cfg,
                start_date=start_date,
                end_date=end_date,
                universe=cfg.universe,
                benchmark=cfg.benchmark,
                history_period="max",
            )
            with _LAST_RESULT_LOCK:
                _LAST_RESULT = result.to_dict()
                _LAST_RESULT["markdown"] = result.summary_markdown()
            try:
                from datetime import datetime
                from .. import db
                db.save_backtest({
                    "ts": datetime.utcnow().isoformat(),
                    "start_date": start_date,
                    "end_date": end_date,
                    "mode": getattr(cfg, "mode", "swing"),
                    "universe_size": len(cfg.universe),
                    "summary": {
                        "total_return_pct": _LAST_RESULT.get("total_return_pct"),
                        "cagr_pct": _LAST_RESULT.get("cagr_pct"),
                        "win_rate_pct": _LAST_RESULT.get("win_rate_pct"),
                        "num_trades": _LAST_RESULT.get("num_trades"),
                        "sharpe_ratio": _LAST_RESULT.get("sharpe_ratio"),
                        "max_drawdown_pct": _LAST_RESULT.get("max_drawdown_pct"),
                        "alpha_pct": _LAST_RESULT.get("alpha_pct"),
                    },
                })
            except Exception:
                log.exception("Backtest archive failed")
        except Exception as e:
            log.exception("Backtest failed: %s", e)
            with _LAST_RESULT_LOCK:
                _LAST_RESULT = {"error": str(e)[:200]}
        finally:
            with _LAST_RESULT_LOCK:
                _RUNNING = False

    threading.Thread(target=_run_in_thread, daemon=True).start()
    return RedirectResponse("/backtest?error=started", status_code=303)


@router.get("/backtest/result")
def backtest_result_json(request: Request):
    if not auth.current_user(request):
        return JSONResponse({"error": "auth_required"}, status_code=401)
    with _LAST_RESULT_LOCK:
        return JSONResponse({"result": _LAST_RESULT, "running": _RUNNING})
