"""Editable strategy settings."""

from __future__ import annotations

from dataclasses import fields

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth, db, state
from ..templating import templates


router = APIRouter()


MODE_CHOICES = [
    ("intraday",          "Intraday",          "5-min bars, auto-exit by 3:25 PM. Needs Upstox + always-on host."),
    ("short",             "Short-term",        "1-7 day holds. Tight stops, daily bars."),
    ("swing",             "Swing",             "1-8 week holds (default). Daily bars."),
    ("positional",        "Positional",        "1-6 month holds. Weekly bars, fundamentals."),
    ("nifty200_momentum", "Nifty 200 Momentum 30", "NSE methodology. Top 30 of Nifty 200 by 6m+12m momentum. 5y CAGR ~29.7%."),
]


EDITABLE_FIELDS = [
    # (field_name, label, group, input_type, step, hint, is_pct)
    # F13: is_pct=True fields are stored as fractions (0.025) but SHOWN and
    # ENTERED as percentages (2.5) — no more "0.01 means 1%" confusion.
    ("starting_capital",        "Starting Capital (Rs)",  "Capital", "number", 1000, "Total paper capital to simulate", False),
    ("cash_reserve_pct",        "Cash Reserve %",          "Capital", "number", 0.5,  "Always keep this % in cash", True),
    ("max_position_pct",        "Max % per Position",      "Capital", "number", 0.5,  "Hard cap per single stock", True),
    ("max_positions",           "Max Open Positions",      "Capital", "number", 1,    "Total positions at once", False),
    ("max_sector_positions",    "Max Positions per Sector","Capital", "number", 1,    "Diversification cap", False),
    ("stop_loss_pct",           "Stop Loss %",             "Risk",    "number", 0.5,  "Hard floor (negative % e.g. -12)", True),
    ("take_profit_pct",         "Take Profit %",           "Risk",    "number", 0.5,  "Full exit target %", True),
    ("trailing_stop_pct",       "Trailing Stop %",         "Risk",    "number", 0.5,  "From peak price (negative %)", True),
    ("score_decay_exit",        "Score Decay Exit",        "Risk",    "number", 1,    "Exit if score drops below this", False),
    ("max_hold_days",           "Max Hold (days)",         "Risk",    "number", 1,    "Safety cap on holding period", False),
    ("daily_loss_halt_pct",     "Daily Loss Kill Switch %","Risk",    "number", 0.5,  "Halts new entries on the day (negative %)", True),
    ("min_score_to_enter",      "Min Score to Enter",      "Strategy","number", 1,    "AM 07 score threshold", False),
    ("ocean_enabled",           "Ocean Engine On (1/0)",   "Strategy","number", 1,    "1 = allow mean-reversion buys in a correction, 0 = parked (default). Ocean picks skip the score floor and momentum gate", False),
    ("breadth_gate_enabled",    "Breadth Gate On (1/0)",   "Strategy","number", 1,    "1 = block new momentum buys when half the market is below its 200DMA (default on)", False),
    ("breakout_threshold_pct",  "Breakout Threshold %",    "Strategy","number", 0.5,  "Within N% of 20-day high", True),
    ("volume_confirm_ratio",    "Volume Confirm Ratio",    "Strategy","number", 0.1,  "5-day vol >= this * 20-day vol", False),
    # F15 pyramiding — opt-in add-to-winners
    ("pyramid_enabled",         "Pyramiding On (1/0)",     "Pyramiding (F15)", "number", 1,   "1 = add to winners in tranches, 0 = off", False),
    ("pyramid_trigger_pct",     "Pyramid Step %",          "Pyramiding (F15)", "number", 0.5, "Add a tranche each +N% above entry", True),
    ("pyramid_add_pct",         "Pyramid Add Size %",      "Pyramiding (F15)", "number", 5,   "% of the original size per tranche", True),
    ("max_pyramid_adds",        "Max Pyramid Adds",        "Pyramiding (F15)", "number", 1,   "Cap on number of tranches", False),
]

# Which config fields are stored as fractions but shown as % (F13)
_PCT_FIELDS = {name for name, *_rest, is_pct in EDITABLE_FIELDS if is_pct}


def _grouped() -> dict[str, list[dict]]:
    cfg = state.cfg()
    groups: dict[str, list[dict]] = {}
    for name, label, group, type_, step, hint, is_pct in EDITABLE_FIELDS:
        raw = getattr(cfg, name, None)
        # Show percentages, not fractions
        if is_pct and isinstance(raw, (int, float)):
            display = round(raw * 100, 4)
        else:
            display = raw
        groups.setdefault(group, []).append({
            "name": name,
            "label": label,
            "value": display,
            "type": type_,
            "step": step,
            "hint": hint,
            "is_pct": is_pct,
        })
    return groups


@router.get("/settings", response_class=HTMLResponse)
def settings_view(request: Request, saved: str | None = None):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    cfg = state.cfg()
    return templates.TemplateResponse(
        request, "settings.html",
        {
            "user": auth.current_user(request),
            "active": "settings",
            "groups": _grouped(),
            "modes": MODE_CHOICES,
            "current_mode": getattr(cfg, "mode", "swing"),
            "saved": saved,
            "db_status": state.db_status(),
            "push": _push_state(),
        },
    )


def _push_state() -> dict:
    from .. import push_ntfy
    try:
        return push_ntfy.get_state()
    except Exception:
        return {"available": False, "enabled": False, "topic": ""}


@router.post("/settings/push/{action}")
def settings_push_toggle(request: Request, action: str):
    """Owner-only: enable/disable phone alerts, or send a test push."""
    user = auth.current_user(request)
    if not user or user.get("role") != "owner":
        return RedirectResponse("/settings?saved=denied", status_code=303)
    from .. import push_ntfy
    if action == "enable":
        push_ntfy.enable()
    elif action == "disable":
        push_ntfy.disable()
    elif action == "test":
        push_ntfy.send("AM07 test", "Phone alerts are working. 🔔")
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/mode")
async def settings_mode_quick_switch(request: Request):
    """One-button mode switch from the Dashboard chip row."""
    user = auth.current_user(request)
    if not user or user.get("role") != "owner":
        return RedirectResponse("/?error=owner_only", status_code=303)
    form = await request.form()
    new_mode = form.get("mode")
    if new_mode in {m[0] for m in MODE_CHOICES}:
        cfg = state.cfg()
        cfg.mode = new_mode
        cfg.apply_mode_preset()
        db.save_settings({"mode": new_mode})
        state.reload_config()
    return RedirectResponse("/", status_code=303)


@router.post("/settings/reset")
async def settings_reset_to_defaults(request: Request):
    """Restore all editable AM 07 + risk params to their factory defaults."""
    user = auth.current_user(request)
    if not user or user.get("role") != "owner":
        return RedirectResponse("/settings?saved=denied", status_code=303)
    # Build a fresh Config to read defaults; copy over editable fields.
    from trading.config import Config as _Config
    defaults = _Config()
    cfg = state.cfg()
    cleared: dict = {}
    for name, *_ in EDITABLE_FIELDS:
        if hasattr(defaults, name):
            val = getattr(defaults, name)
            setattr(cfg, name, val)
            cleared[name] = val
    # Re-apply mode preset (mode stays — only AM07 / risk params reset)
    cfg.apply_mode_preset()
    # Push the new defaults into Mongo (so persistence reflects the reset)
    db.save_settings({**cleared, "mode": getattr(cfg, "mode", "swing")})
    state.reload_config()
    return RedirectResponse("/settings?saved=reset", status_code=303)


@router.post("/settings")
async def settings_save(request: Request):
    user = auth.current_user(request)
    if not user or user.get("role") != "owner":
        return RedirectResponse("/settings?saved=denied", status_code=303)
    form = await request.form()
    cfg = state.cfg()
    cfg_fields = {f.name: f.type for f in fields(cfg)}
    overrides: dict = {}

    # Mode selector
    new_mode = form.get("mode")
    if new_mode in {m[0] for m in MODE_CHOICES}:
        cfg.mode = new_mode
        overrides["mode"] = new_mode

    # Editable scalar fields
    for name, *_ in EDITABLE_FIELDS:
        if name not in form or name not in cfg_fields:
            continue
        raw = form[name]
        existing = getattr(cfg, name)
        try:
            if isinstance(existing, bool):
                # 1/0 toggles. MUST come before the int branch (bool subclasses
                # int) and before the else, which would store the STRING "0" —
                # truthy, so entering 0 to switch a flag off switched it on.
                val = str(raw).strip().lower() in {"1", "true", "yes", "on"}
            elif isinstance(existing, int):
                val = int(float(raw))
            elif isinstance(existing, float):
                val = float(raw)
                # F13: user enters a %, we store the fraction
                if name in _PCT_FIELDS:
                    val = val / 100.0
            else:
                val = raw
            setattr(cfg, name, val)
            overrides[name] = val
        except (TypeError, ValueError):
            continue

    # Re-apply mode preset (overwrites stops/targets per the chosen mode)
    cfg.apply_mode_preset()

    # Mirror to Mongo if connected; falls back to in-memory only when offline
    db.save_settings(overrides)

    # Rebuild engine with the new config
    state.reload_config()
    return RedirectResponse("/settings?saved=ok", status_code=303)
