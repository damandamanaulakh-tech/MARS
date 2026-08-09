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


def _preloaded_history(universe: list, benchmark: str,
                       start_date: str, end_date: str) -> dict:
    """Build the backtest's price panel from data the app ALREADY HAS.

    Owner report (2026-07-27): the backtest button was still crashing the
    512 MB Render box. Cause: run_backtest with no preloaded_history does a
    yfinance bulk download of the whole universe — the in-flight responses
    and float64 frames spike far above the app's steady ~150 MB before the
    float32 downcast runs, and yfinance rate-limits make it fail outright.

    The same OHLCV lives in two places that need NO network: the Mongo
    history store (kept fresh by the Friday top-up) and the repo's committed
    zip (ships with every deploy). Prefer Mongo, fill gaps from the zip,
    benchmark from the committed index CSV. Frames are float32 and sliced to
    the window+lookback, so peak memory stays at the measured ~170 MB.
    Tickers found nowhere are skipped (counted in the log) — never fetched.
    """
    import io
    import zipfile
    from pathlib import Path

    import pandas as pd

    from trading.history_store import records_to_df
    from .. import db

    dl_start = (pd.Timestamp(start_date) - pd.Timedelta(days=420)).date().isoformat()
    dl_end = (pd.Timestamp(end_date) + pd.Timedelta(days=5)).date().isoformat()
    out: dict = {}

    # 1. Mongo store (freshest)
    if db.is_connected():
        for t in universe:
            try:
                df = records_to_df(db.load_history_doc(t))
                if df is not None and not df.empty:
                    out[t] = df.loc[dl_start:dl_end]
            except Exception:
                continue

    # 2. Repo zip fills the gaps (always present in the deploy)
    missing = [t for t in universe if t not in out]
    if missing:
        zpath = Path(__file__).resolve().parents[2] / "data" / \
            "history_upstox_export_2026-07-19.zip"
        try:
            with zipfile.ZipFile(zpath) as z:
                names = set(z.namelist())
                for t in missing:
                    name = f"{t}.csv"
                    if name not in names:
                        continue
                    df = pd.read_csv(io.BytesIO(z.read(name)),
                                     parse_dates=["Date"], index_col="Date")
                    for c in df.columns:
                        df[c] = df[c].astype("float32")
                    out[t] = df.sort_index().loc[dl_start:dl_end]
        except Exception as e:
            log.warning("backtest zip fallback failed: %s", e)

    # 3. Benchmark from the committed index CSV (no network)
    try:
        ipath = Path(__file__).resolve().parents[2] / "data" / "index_daily_15y.csv"
        idx = pd.read_csv(ipath, parse_dates=["trade_date"], dayfirst=True)
        idx = idx.set_index("trade_date").sort_index()
        out[benchmark] = pd.DataFrame({
            "Open": idx["nifty_open"], "High": idx["nifty_high"],
            "Low": idx["nifty_low"], "Close": idx["nifty_close"],
            "Volume": 0.0,
        }).loc[dl_start:dl_end]
    except Exception as e:
        log.warning("backtest benchmark load failed: %s", e)

    # AUDIT FIX B12 (2026-08-09): the preloaded panel never asked for the
    # Nifty-200 second benchmark, and run_backtest only downloads when there
    # is NO preloaded panel — so the N200 line was structurally impossible
    # from the web button. Try Upstox (the index-key map now carries ^CNX200);
    # failure stays a visible DATA_NEEDED on the page, never a crash.
    try:
        from trading.backtest import BENCHMARK2
        from trading.data import fetch_daily_range
        b2 = fetch_daily_range([BENCHMARK2], dl_start, dl_end).get(BENCHMARK2)
        if b2 is not None and not b2.empty:
            out[BENCHMARK2] = b2
        else:
            log.warning("N200 benchmark unavailable (no broker/network) — "
                        "the page will show DATA_NEEDED for benchmark2")
    except Exception as e:
        log.warning("N200 benchmark fetch failed: %s", e)

    skipped = len(universe) - sum(1 for t in universe if t in out)
    log.info("backtest panel: %d/%d tickers from store+zip, %d skipped, "
             "0 downloaded", len(out), len(universe) + 1, skipped)
    return out


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
                preloaded_history=_preloaded_history(
                    cfg.universe, cfg.benchmark, start_date, end_date),
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
