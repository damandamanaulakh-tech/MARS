"""Risk dashboard — drawdown governor + consecutive-loss + active filters."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth, state as app_state
from ..templating import templates


router = APIRouter()


@router.get("/risk", response_class=HTMLResponse)
def risk_view(request: Request):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    eng = app_state.engine()
    cfg = app_state.cfg()

    # AUDIT FIX B3 (2026-08-09): this page used to re-run the governor
    # with ENTRY-price marks on every page view — mutating live risk state
    # with stale data and showing the result unlabeled. It is read-only now:
    # the governor is updated only by the engine's own scan/refresh passes on
    # live quotes, and the page says which reading it is showing.
    # (An external review on PR#40 caught that the first cut of this fix deleted
    # the equity the template still renders — /risk 500'd. Equity is computed
    # read-only here, on entry marks, and labelled as such by the template.)
    prices = {p2.ticker: p2.entry_price for p2 in eng.portfolio.positions.values()}
    equity = eng.portfolio.equity(prices)
    try:
        dd = eng.drawdown_gov.snapshot()
    except AttributeError:
        dd = None
    try:
        # Owner order 2026-10-21: per-category counts + which categories
        # are halted now, under the live scope.
        # today = the engine's own date (naive UTC, as scan() uses), so a
        # count from an earlier day never reads as a halt now.
        from datetime import datetime as _now_dt
        cl = eng.loss_tracker.snapshot(
            open_positions=len(eng.portfolio.positions),
            open_by_category=eng._open_by_category(),
            scope=eng._loss_halt_scope(cfg),
            today=_now_dt.utcnow().date())
        try:
            from trading import category_budget as _cb
            for row in cl.get("categories") or []:
                row["label"] = _cb.LABELS.get(
                    row["category"], row["category"].replace("_", " ").title())
        except Exception:
            pass
    except AttributeError:
        cl = None

    # v6/v7: IFR status — DAMAGE_CLUSTER + TAIL_DAMAGE_RATIO (Q2-validated).
    # Read from persisted fire dates; full recompute is too expensive per pageview.
    from datetime import datetime as _dt
    try:
        from trading.ifr import ACTIVE_DAYS_AFTER_FIRE
    except ImportError:
        ACTIVE_DAYS_AFTER_FIRE = 22

    def _ifr_signal(last_fire: str | None) -> dict | None:
        if not last_fire:
            return None
        try:
            prev = _dt.fromisoformat(last_fire).date()
            days_since = (_dt.utcnow().date() - prev).days
            return {
                "last_fire": last_fire,
                "days_since": days_since,
                "active": days_since <= ACTIVE_DAYS_AFTER_FIRE,
            }
        except Exception:
            return None

    cluster = _ifr_signal(eng.portfolio.last_ifr_fire_date)
    ratio   = _ifr_signal(eng.portfolio.last_ifr_ratio_fire_date)
    any_active = bool((cluster and cluster["active"]) or (ratio and ratio["active"]))
    ifr = {
        "cluster": cluster,
        "ratio":   ratio,
        "active":  any_active,
        "sizing_multiplier": 0.50 if any_active else 1.0,
        "window_days": ACTIVE_DAYS_AFTER_FIRE,
    } if (cluster or ratio) else None

    # AUDIT F-9 (owner: "F9- write it", 2026-10-02): the same reading the
    # 09:20 scan acts on, recomputed from the book's closed trades (pure, no
    # network), so the owner sees the numbers before they move a rupee. Not
    # engine._measured_kelly: a page view must never write the journal.
    mk = None
    mk_cap = None
    mk_modes: tuple = ()
    try:
        from trading.measured_kelly import live_clip_pct, measure
        if bool(getattr(cfg, "measured_kelly_enabled", False)):
            # 2026-10-05 ("make it momentum only"): the page shows the
            # reading of the FIRST listed category (momentum), the one the
            # scan acts on; an empty list = the old pooled reading.
            mk_modes = tuple(getattr(cfg, "measured_kelly_modes", ()) or ())
            mk = measure(
                eng.portfolio.trades,
                fraction=float(getattr(cfg, "kelly_fraction", 0.29) or 0.29),
                min_trades=int(getattr(cfg, "measured_kelly_min_trades", 0) or 0),
                full_confidence=int(getattr(
                    cfg, "measured_kelly_full_confidence", 300) or 300),
                clip_pct=live_clip_pct(),
                category=(mk_modes[0] if mk_modes else None))
            mk_cap = eng._measured_kelly_cap(mk, cfg, equity)
    except Exception:
        mk = None

    # Owner order 2026-10-04 ("fix it so all 5 categories get cash"): each
    # category's share of the deployable capital, what it has spent at entry
    # cost, and what is left — the numbers risk.can_open refuses on.
    budgets = None
    budget_total = None
    owner_row = None
    try:
        from trading import category_budget as cb
        if cb.enabled(cfg):
            budgets = cb.usage(cfg, eng.portfolio.positions, equity)
            budget_total = cb.share_total(cfg)
            # Owner order 2026-10-21: owner BUY / Buy Desk positions sit in
            # their own bucket outside the five shares (like Ocean).
            owner_row = cb.owner_usage(eng.portfolio.positions)
    except Exception:
        budgets = None

    return templates.TemplateResponse(
        request, "risk.html",
        {
            "user": auth.current_user(request),
            "active": "risk",
            "dd": dd,
            "cl": cl,
            "ifr": ifr,
            "cfg": cfg,
            "equity": equity,
            "mk": mk,
            "mk_cap": mk_cap,
            "mk_on": bool(getattr(cfg, "measured_kelly_enabled", False)),
            "mk_modes": mk_modes,
            "budgets": budgets,
            "budget_total": budget_total,
            "owner_row": owner_row,
        },
    )
