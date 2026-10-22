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
    mk_thin = False
    mk_min_block = 30
    try:
        from trading import measured_kelly as _mk_mod
        from trading.measured_kelly import live_clip_pct, measure
        # Owner card 2026-10-22 ("Block only on 30+ usable trades"): below
        # this many usable momentum exits a negative reading CAPS momentum
        # scan buys at the slab instead of blocking them. The number lives in
        # trading/measured_kelly.py (MIN_BLOCK_TRADES); 30 is the owner's own
        # figure if the module predates it.
        mk_min_block = int(getattr(_mk_mod, "MIN_BLOCK_TRADES", 30) or 30)
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
            # read with getattr: the field is added by the thin-record change
            # in trading/measured_kelly.py; an older reading has no such field
            # and is never shown as "thin".
            mk_thin = bool(getattr(mk, "thin_record", False))
    except Exception:
        mk = None

    # Owner order 2026-10-04 ("fix it so all 5 categories get cash"): each
    # category's share of the deployable capital, what it has spent at entry
    # cost, and what is left — the numbers risk.can_open refuses on.
    budgets = None
    budget_total = None
    owner_row = None
    share_base = None
    try:
        from trading import category_budget as cb
        if cb.enabled(cfg):
            budgets = cb.usage(cfg, eng.portfolio.positions, equity)
            budget_total = cb.share_total(cfg)
            # Owner order 2026-10-21: owner BUY / Buy Desk positions sit in
            # their own bucket outside the five shares (like Ocean).
            owner_row = cb.owner_usage(eng.portfolio.positions)
            share_base = _share_base_view(cb, cfg, eng.portfolio.positions,
                                          equity, budgets)
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
            "mk_thin": mk_thin,
            "mk_min_block": mk_min_block,
            "mk_slab": _slab_floor(cfg),
            "share_base": share_base,
            "budgets": budgets,
            "budget_total": budget_total,
            "owner_row": owner_row,
        },
    )


def _slab_floor(cfg) -> float:
    """The size a thin-record momentum scan buy is capped at (owner card
    2026-10-22): the slab under slab sizing, the dust floor otherwise — the
    same floor engine._measured_kelly_cap uses."""
    if str(getattr(cfg, "sizing_mode", "")) == "slab":
        return float(getattr(cfg, "position_slab_value", 0) or 0)
    return float(getattr(cfg, "min_position_value", 0) or 0)


def _share_base_view(cb, cfg, positions: dict, equity: float,
                     rows: list | None = None) -> dict:
    """What the five category shares are taken from, for the /risk card.

    Owner card 2026-10-22 ("Shares from what is left after owner + Ocean"):
    share base = deployable - your own buys - Ocean holdings, at entry cost.
    `category_budget.share_base` is that rule (read with getattr so the page
    still renders if the module predates it). The base PRINTED is the one the
    table's limits — the gate's own numbers — imply (limit / share), so the
    line can never promise a base the gate does not use; the "left after your
    own buys and Ocean" wording is used only when that base is the rule's."""
    vals = list(positions.values())
    deployable = float(cb.deployable(cfg, equity))
    owner = float(cb.used(vals, getattr(cb, "OWNER", "owner")))
    ocean = float(cb.used(vals, getattr(cb, "OCEAN", "ocean")))
    rule = max(0.0, deployable - owner - ocean)
    fn = getattr(cb, "share_base", None)
    fn_base = None
    if callable(fn):
        # Positions as the dict the other budget helpers take, then as a list.
        # A list-style function handed the dict would iterate ticker strings
        # and silently deduct nothing, so a reading that matches the rule is
        # preferred over one that does not.
        got = []
        for arg in (positions, vals):
            try:
                got.append(float(fn(cfg, arg, equity)))
            except Exception:                                 # noqa: BLE001
                continue
        if got:
            fn_base = next((g for g in got if abs(g - rule) < 1.0), got[0])
    table_base = None
    for r in rows or []:
        try:
            if r.get("share") and r.get("limit") is not None:
                table_base = float(r["limit"]) / float(r["share"])
                break
        except (TypeError, ValueError, ZeroDivisionError):
            continue
    base = (table_base if table_base is not None
            else fn_base if fn_base is not None else deployable)
    if owner + ocean >= 1.0:
        after = abs(base - rule) < 1.0
    else:                       # nothing to take off: both rules read the same
        after = fn_base is not None
    return {
        "deployable": deployable,
        "owner": owner,
        "ocean": ocean,
        "reserve_pct": float(getattr(cfg, "cash_reserve_pct", 0.0) or 0.0),
        # True = the shares ARE taken from what is left after your own buys
        # and Ocean; False = the older rule (of all deployable capital)
        "after_owner_ocean": bool(after),
        "base": base,
    }
