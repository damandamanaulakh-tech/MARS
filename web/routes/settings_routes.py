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
    ("nifty200_momentum", "Nifty 200 Momentum 30", "NSE methodology: 6m+12m vol-adjusted momentum, quality-blended. Evidence: docs/ folder."),
]


EDITABLE_FIELDS = [
    # (field_name, label, group, input_type, step, hint, is_pct)
    # F13: is_pct=True fields are stored as fractions (0.025) but SHOWN and
    # ENTERED as percentages (2.5) — no more "0.01 means 1%" confusion.
    ("starting_capital",        "Starting Capital (Rs)",  "Capital", "number", 1000, "Total paper capital to simulate", False),
    ("cash_reserve_pct",        "Cash Reserve %",          "Capital", "number", 0.5,  "Always keep this % in cash", True),
    ("position_size_value",     "Position Size (Rs)",      "Capital", "number", 1000, "Fixed Rs per new trade — used when Sizing Mode is 'fixed'", False),
    ("sizing_mode",             "Sizing Mode",             "Capital", "text",   None, "half_kelly = size by edge (owner order 2026-07-30) · fixed = flat Position Size", False),
    ("kelly_fraction",          "Kelly Fraction",          "Capital", "number", 0.05, "0.5 = half-Kelly (75% of max growth, 12.5% halving risk). NEVER above 1.0", False),
    ("kelly_ic",                "Kelly IC (assumed edge)", "Capital", "number", 0.01, "Assumed information coefficient. Higher = bigger bets. 0.05 is realistic", False),
    ("kelly_max_pct",           "Kelly Max % per Position","Capital", "number", 0.5,  "Hard ceiling per name under Kelly sizing", True),
    ("max_position_pct",        "Max % per Position",      "Capital", "number", 0.5,  "Fallback sizing when Position Size (Rs) is 0", True),
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
    ("correction_brake_enabled","Correction Brake On (1/0)","Strategy","number", 1,   "0 = OWNER ORDER 2026-08-09: engine buys even when Nifty is below its 200DMA (correction). 1 = restore the old brake. Panic always blocks", False),
    ("danger_layer_enabled",   "Danger Layer On (1/0)",   "Strategy","number", 1,    "OWNER ORDER 2026-08-11: 10 calibration-VERIFIED tripwires guard held names. Alerts first 7 days, then auto: 1 fire = tighten stop -2.5%, 2+ = exit half. Sell side only", False),
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


# Fields apply_mode_preset() overwrites on every boot — editing them in
# Settings looks like it works and then silently reverts (surfaced 2026-07-31).
PRESET_OWNED_FIELDS = [
    "stop_loss_pct", "take_profit_pct", "trailing_stop_pct", "score_decay_exit",
    "max_hold_days", "min_score_to_enter", "breakout_threshold_pct",
    "volume_confirm_ratio",
]


def _trail_warning() -> dict | None:
    """None when the trailing stop can actually fire; otherwise the numbers."""
    cfg = state.cfg()
    try:
        live, need = cfg.trail_is_reachable()
    except Exception:
        return None
    if live:
        return None
    return {
        "stop": round(cfg.stop_loss_pct * 100, 1),
        "target": round(cfg.take_profit_pct * 100, 1),
        "trail": round(cfg.trailing_stop_pct * 100, 1),
        "need": round(need * 100, 2),
        "window": round((cfg.take_profit_pct - need) * 100, 2),
    }


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
            "trail_warn": _trail_warning(),
            "preset_fields": PRESET_OWNED_FIELDS,
            "modes": MODE_CHOICES,
            "current_mode": getattr(cfg, "mode", "swing"),
            "saved": saved,
            "db_status": state.db_status(),
            "push": _push_state(),
            # OWNER ORDER 2026-08-20: the custom 50-name universe, shown for
            # weekly editing. Empty = rotating full-NSE bucket.
            "custom_universe": list(getattr(cfg, "custom_universe", ()) or ()),
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
    # AUDIT FIX A7: factory reset also clears the per-mode overrides, so the
    # presets govern again exactly as shipped.
    cfg.preset_overrides = {}
    cleared["preset_overrides"] = {}
    # Re-apply mode preset (mode stays — only AM07 / risk params reset)
    cfg.apply_mode_preset()
    # Push the new defaults into Mongo (so persistence reflects the reset)
    db.save_settings({**cleared, "mode": getattr(cfg, "mode", "swing")})
    p = state.portfolio()
    if p.sync_capital(cfg):
        p.save()
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

    # OWNER ORDER 2026-08-20: the custom 50-name universe, editable weekly.
    # Comma/space/newline separated; .NS appended if missing; EVERY name
    # must resolve in the live universe file or the whole edit is REFUSED
    # with the bad names listed — a typo must never silently shrink the
    # scan list ("no fake data" applies to the universe too). Blank = back
    # to the full-NSE rotating bucket.
    if "custom_universe" in form:
        import re as _re
        raw_u = str(form["custom_universe"] or "")
        names = [n.upper() for n in _re.split(r"[,\s]+", raw_u) if n.strip()]
        names = [n if n.endswith(".NS") else n + ".NS" for n in names]
        names = list(dict.fromkeys(names))
        if not names:
            cfg.custom_universe = ()
            overrides["custom_universe"] = []
        elif len(names) > 60:
            # CODEX REVIEW FIX (PR #68): unbounded, a pasted 2,400-name list
            # would have had the scan download 2 years × every name daily —
            # an OOM on the 512 MB box. The design is 50; 60 leaves slack.
            return RedirectResponse(
                f"/settings?saved=universe_too_big&n={len(names)}",
                status_code=303)
        else:
            try:
                from trading.universes import load_full_universe
                known = set(load_full_universe())
            except Exception:
                known = set()
            bad = [n for n in names if known and n not in known]
            if bad:
                return RedirectResponse(
                    "/settings?saved=universe_unknown&bad=" + ",".join(
                        n.replace(".NS", "") for n in bad[:8]),
                    status_code=303)
            cfg.custom_universe = tuple(names)
            overrides["custom_universe"] = names

    # Editable scalar fields
    preset_submitted: dict = {}
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
            if name in PRESET_OWNED_FIELDS:
                preset_submitted[name] = val
        except (TypeError, ValueError):
            continue

    # AUDIT FIX A7 (2026-08-09): a submitted preset-owned value that differs
    # from the mode's PRESET becomes a per-mode override (applied at the end
    # of every apply_mode_preset, persisted below) — "Saved." finally means
    # saved. Submitting the preset's own value clears the override, so a
    # future preset change ordered in code is never shadowed by a stale one.
    try:
        from trading.modes import get_preset
        preset = get_preset(getattr(cfg, "mode", "swing"))
        mode_over = dict((cfg.preset_overrides or {}).get(cfg.mode) or {})
        for k, v in preset_submitted.items():
            base = getattr(preset, k, None)
            same = (abs(v - base) < 1e-9 if isinstance(base, float)
                    and isinstance(v, (int, float)) else v == base)
            if same:
                mode_over.pop(k, None)
            else:
                mode_over[k] = v
        po = dict(cfg.preset_overrides or {})
        if mode_over:
            po[cfg.mode] = mode_over
        else:
            po.pop(cfg.mode, None)
        cfg.preset_overrides = po
        overrides["preset_overrides"] = po
    except Exception:
        pass

    # Guard the sizing knobs: a typo in Sizing Mode must not silently fall back
    # to a size the owner did not choose, and Kelly above 1x is never valid
    # (1.5x earns the same growth as 0.5x with 6x the ruin risk — see
    # docs/ALGO_V1_V2.md section 9).
    # AUDIT FIX A6 (2026-08-09): the fallback used to be "half_kelly" — a
    # fat-finger would silently re-enable edge-based sizing AGAINST the
    # owner's written 2026-08-04 order ("fixed Rs 1.25L each"). A typo now
    # lands on the owner's standing choice, never on mine.
    if str(getattr(cfg, "sizing_mode", "")).lower() not in ("fixed", "kelly", "half_kelly"):
        cfg.sizing_mode = "fixed"
        overrides["sizing_mode"] = "fixed"
    if float(getattr(cfg, "kelly_fraction", 0.5)) > 1.0:
        cfg.kelly_fraction = 1.0
        overrides["kelly_fraction"] = 1.0

    # Re-apply mode preset (overwrites stops/targets per the chosen mode)
    cfg.apply_mode_preset()

    # Mirror to Mongo if connected; falls back to in-memory only when offline
    db.save_settings(overrides)

    # Owner order 2026-07-30: a Starting Capital change really moves the
    # book's cash (delta, journaled CAPITAL_ADJUST) — no more silent no-op.
    p = state.portfolio()
    if p.sync_capital(cfg):
        p.save()

    # Rebuild engine with the new config
    state.reload_config()
    return RedirectResponse("/settings?saved=ok", status_code=303)
