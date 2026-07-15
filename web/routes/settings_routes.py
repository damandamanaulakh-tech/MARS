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
    # (field_name, label, group, input_type, step, hint)
    ("starting_capital",        "Starting Capital (Rs)",  "Capital", "number", 1000, "Total paper capital to simulate"),
    ("cash_reserve_pct",        "Cash Reserve %",          "Capital", "number", 0.01, "Always keep this fraction in cash"),
    ("max_position_pct",        "Max % per Position",      "Capital", "number", 0.01, "Hard cap per single stock"),
    ("max_positions",           "Max Open Positions",      "Capital", "number", 1,    "Total positions at once"),
    ("max_sector_positions",    "Max Positions per Sector","Capital", "number", 1,    "Diversification cap"),
    ("stop_loss_pct",           "Stop Loss %",             "Risk",    "number", 0.005,"Hard floor (negative number)"),
    ("take_profit_pct",         "Take Profit %",           "Risk",    "number", 0.005,"Full exit target"),
    ("trailing_stop_pct",       "Trailing Stop %",         "Risk",    "number", 0.005,"From peak price"),
    ("score_decay_exit",        "Score Decay Exit",        "Risk",    "number", 1,    "Exit if score drops below this"),
    ("max_hold_days",           "Max Hold (days)",         "Risk",    "number", 1,    "Safety cap on holding period"),
    ("daily_loss_halt_pct",     "Daily Loss Kill Switch %","Risk",    "number", 0.005,"Halts new entries on the day"),
    ("min_score_to_enter",      "Min Score to Enter",      "Strategy","number", 1,    "AM 07 score threshold"),
    ("breakout_threshold_pct",  "Breakout Threshold %",    "Strategy","number", 0.005,"Within N% of 20-day high"),
    ("volume_confirm_ratio",    "Volume Confirm Ratio",    "Strategy","number", 0.1,  "5-day vol >= this * 20-day vol"),
]


def _grouped() -> dict[str, list[dict]]:
    cfg = state.cfg()
    groups: dict[str, list[dict]] = {}
    for name, label, group, type_, step, hint in EDITABLE_FIELDS:
        groups.setdefault(group, []).append({
            "name": name,
            "label": label,
            "value": getattr(cfg, name, None),
            "type": type_,
            "step": step,
            "hint": hint,
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
        },
    )


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
            if isinstance(existing, int) and not isinstance(existing, bool):
                val = int(float(raw))
            elif isinstance(existing, float):
                val = float(raw)
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
