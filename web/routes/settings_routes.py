"""Editable strategy settings."""

from __future__ import annotations

import logging
from dataclasses import fields

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth, db, state
from ..templating import templates


router = APIRouter()
log = logging.getLogger(__name__)


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
    ("sizing_mode",             "Sizing Mode",             "Capital", "text",   None, "slab = LIVE (owner order 2026-09-10): score-driven size on a Rs 1.5 lakh floor · fixed = flat Position Size · half_kelly = Kelly with no slab", False),
    ("kelly_fraction",          "Kelly Fraction",          "Capital", "number", 0.05, "0.5 = half-Kelly (75% of max growth, 12.5% halving risk). NEVER above 1.0", False),
    ("kelly_ic",                "Kelly IC (assumed edge)", "Capital", "number", 0.01, "Assumed information coefficient. Higher = bigger bets. 0.05 is realistic", False),
    ("kelly_max_pct",           "Kelly Max % per Position","Capital", "number", 0.5,  "Hard ceiling per name under Kelly sizing", True),
    ("max_position_pct",        "Max % per Position",      "Capital", "number", 0.5,  "Fallback sizing when Position Size (Rs) is 0", True),
    ("max_positions",           "Max Open Positions",      "Capital", "number", 1,    "Total positions at once", False),
    ("max_sector_positions",    "Max Positions per Sector","Capital", "number", 1,    "Diversification cap", False),
    ("category_budgets_enabled","Category Cash Shares On (1/0)","Capital","number", 1,  "OWNER ORDER 2026-10-04 ('fix it so all 5 categories get cash'). 1 = each category spends only its own share of the deployable capital, so momentum (scanned first at 09:20) cannot use up the cash before intraday/short/swing/positional are scanned. A share left unused stays as cash. 0 = one shared pot (momentum first). Usage on the Risk page", False),
    ("budget_share_momentum",   "Momentum Cash Share %",   "Capital", "number", 1,    "Share of deployable capital momentum may spend (owner pick: 40)", True),
    ("budget_share_intraday",   "Intraday Cash Share %",   "Capital", "number", 1,    "Owner pick: 15. Returns to cash every day at the 15:25 square-off", True),
    ("budget_share_short",      "Short Cash Share %",      "Capital", "number", 1,    "Owner pick: 15", True),
    ("budget_share_swing",      "Swing Cash Share %",      "Capital", "number", 1,    "Owner pick: 15", True),
    ("budget_share_positional", "Positional Cash Share %", "Capital", "number", 1,    "Owner pick: 15. The five shares should add up to 100", True),
    ("stop_loss_pct",           "Stop Loss %",             "Risk",    "number", 0.5,  "The category's stop (negative % e.g. -5). For momentum it is the FLOOR: the ATR stop below can only widen it. Applies to new trades; held positions keep the stop they were opened with", True),
    ("atr_stop_enabled",        "ATR Stop On (1/0)",       "Risk",    "number", 1,    "OWNER ORDER 2026-10-05 ('Go for A'). 1 = a new MOMENTUM buy's stop is the wider of the category stop (-5%) and 2 x its own 14-day ATR, capped at -15%. MEASURED (research/stop_cooldown_study): beat the fixed -5% in train (+2.82 vs +0.88) and in the holdout (+21.14 vs +19.33), 30% fewer stop-outs. Cost: trade size is unchanged, so a losing trade on a jumpy name can lose more than 5% (6.8% on average, 15% at most). 0 = fixed stop. Other categories always use their fixed stop", False),
    ("take_profit_pct",         "Take Profit %",           "Risk",    "number", 0.5,  "Full exit target %", True),
    ("trailing_stop_pct",       "Trailing Stop %",         "Risk",    "number", 0.5,  "From peak price (negative %)", True),
    ("score_decay_exit",        "Score Decay Exit",        "Risk",    "number", 1,    "Exit if score drops below this", False),
    ("max_hold_days",           "Max Hold (days)",         "Risk",    "number", 1,    "Safety cap on holding period", False),
    ("daily_loss_halt_pct",     "Daily Loss Kill Switch %","Risk",    "number", 0.5,  "Halts new entries on the day (negative %)", True),
    ("min_score_to_enter",      "Min Score to Enter",      "Strategy","number", 1,    "AM 07 score threshold", False),
    ("ocean_enabled",           "Ocean Engine On (1/0)",   "Strategy","number", 1,    "1 = allow mean-reversion buys in a correction, 0 = parked (default). Ocean picks skip the score floor and momentum gate", False),
    ("breadth_gate_enabled",    "Breadth Gate On (1/0)",   "Strategy","number", 1,    "1 = block new momentum buys while breadth sits in the measured dead band (default on). RE-CUT 2026-09-09: it is a BAND, not a floor — a washed-out tape below the low edge is buyable", False),
    ("breadth_block_low",       "Breadth Band Low",        "Strategy","number", 0.01, "Low edge of the no-buy band, share of stocks above their own 200DMA (measured 0.40). BELOW this the tape is washed out and entries are allowed", False),
    ("breadth_block_high",      "Breadth Band High",       "Strategy","number", 0.01, "High edge of the no-buy band (measured 0.65). Above this breadth is healthy and entries are allowed", False),
    ("correction_brake_enabled","Correction Brake On (1/0)","Strategy","number", 1,   "0 = OWNER ORDER 2026-08-09: engine buys even when Nifty is below its 200DMA (correction). 1 = restore the old brake. Panic always blocks", False),
    ("rotation_auto_enabled",  "Auto Rotation On (1/0)",  "Strategy","number", 1,    "OWNER ORDER 2026-09-04: a position >=90% to its target is SOLD at the scan to fund a fresh pick when cash is short. Only as many as needed; owner picks never rotated; no live quote = skipped", False),
    ("danger_layer_enabled",   "Danger Layer On (1/0)",   "Strategy","number", 1,    "OWNER ORDER 2026-08-11: 10 calibration-VERIFIED tripwires guard held names. Alerts first 7 days, then auto: 1 fire = tighten stop -2.5%, 2+ = exit half. Sell side only", False),
    ("measured_kelly_enabled", "Measured Kelly Check On (1/0)", "Strategy","number", 1, "AUDIT F-9, owner order 2026-10-02 ('write it'). 1 = the scan reads Kelly from your OWN closed strategy exits (never your sells, square-offs, kill switch or ladder): no measured edge = NO new scan entries; measured edge = each new trade capped at that size (never below the Rs 1.5L slab, never raised). Your BUY button and Buy Desk are never blocked. 0 = off. Reading on the Risk page.", False),
    ("multi_formula_gate",     "Three-Formula Filter On (1/0)", "Strategy","number", 1, "OWNER ORDER 2026-09-11. 0 = show the ASH08 (68) and AshStocks (70) verdicts on every stock but let AM07 decide (default). 1 = a pick must clear those floors too. MEASURED before you switch it on: ASH08's floor removes only 3% of our candidates (its quality leg is data coverage, so 68 means '6m return above +0.385%'), and AshStocks' live 20-day rule scored -0.77 train / -0.45 test. See research/ash_formula_study/", False),
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


def _universe_paste_cap(cfg) -> int:
    """How many names the Settings universe box will accept.

    DERIVED from the app's own universe target so the guard can never refuse
    the list the engine is already scanning. Floors at 100 so a small
    configured target still leaves the owner room to paste a bigger list.
    """
    target = int(getattr(cfg, "universe_target_size", 0) or 0)
    return max(100, target)


def _dead_trail_positions() -> list[dict]:
    """AUDIT F-18 (2026-10-02): held positions whose OWN stop and target
    (k x ATR since 2026-09-10) leave the trail no room to fire, each judged
    on its own mode's trail. Display only."""
    from trading.exits import trail_reach
    out: list[dict] = []
    try:
        eng = state.engine()
        for pos in list(state.portfolio().positions.values()):
            mode = getattr(pos, "mode", "") or "nifty200_momentum"
            r = trail_reach(pos, eng.mode_cfg(mode))
            if not r["live"]:
                out.append(r)
    except Exception:
        log.exception("per-position trail check failed")
    return out


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
            "trail_dead": _dead_trail_positions(),
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


def _mode_busy() -> bool:
    """AUDIT S-6 (2026-10-02): a Settings save during the 09:20 rotation used
    to change the shared Config underneath a running scan pass — and the
    rotation restores the mode it STARTED with when it ends, so a mode switch
    made mid-rotation was silently undone. True while either a scan pass holds
    the mode lock or the rotation loop is between passes."""
    try:
        from .. import scheduler
        if scheduler._ROTATION_LOCK.locked():
            return True
    except Exception:
        pass
    return False


def _under_mode_lock(body, *args, busy_url: str):
    """Run a Settings change while holding the shared mode lock. Async
    routes run on the event loop, so this never WAITS — a scan in progress
    returns the owner to a 'busy, nothing changed' notice at once."""
    from trading.modes import MODE_LOCK
    if _mode_busy() or not MODE_LOCK.acquire(blocking=False):
        return RedirectResponse(busy_url, status_code=303)
    try:
        return body(*args)
    finally:
        MODE_LOCK.release()


@router.post("/settings/mode")
async def settings_mode_quick_switch(request: Request):
    """One-button mode switch from the Dashboard chip row."""
    user = auth.current_user(request)
    if not user or user.get("role") != "owner":
        return RedirectResponse("/?error=owner_only", status_code=303)
    form = await request.form()
    return _under_mode_lock(_mode_switch_body, form,
                            busy_url="/settings?saved=busy")


def _mode_switch_body(form):
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
    return _under_mode_lock(_reset_body, busy_url="/settings?saved=busy")


def _reset_body():
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
    return _under_mode_lock(_settings_save_body, form,
                            busy_url="/settings?saved=busy")


def _settings_save_body(form):
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
        elif len(names) > _universe_paste_cap(cfg):
            # The cap exists because an unbounded paste once meant the scan
            # downloaded 2 years x every name daily (PR #68). That premise
            # died on 2026-09-11 when fetch_daily became STORE-FIRST:
            # measured, 1,200 two-year frames cost +15 MB and 2.8 s, 2,648
            # cost +32 MB — the 512 MB box is not the constraint it was.
            #
            # It is now DERIVED from cfg.universe_target_size (1,200) rather
            # than the old hardcoded 60, which the owner's own list would
            # have breached the moment it grew: the 2026-09-16 defence /
            # shipbuilding / pharma order took it to 83, and a 60-cap would
            # have rejected the live list back at the owner as
            # "universe_too_big". A guard must never refuse the state the
            # app is already running.
            return RedirectResponse(
                f"/settings?saved=universe_too_big&n={len(names)}"
                f"&cap={_universe_paste_cap(cfg)}",
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
    # FIX 2026-10-04: "slab" — the LIVE mode since the owner's 2026-09-10
    # order ("no flat size to follow") — was missing from this list, so
    # EVERY Settings save, whatever field it changed, rewrote the live
    # sizing to "fixed" (flat Rs 1.25 lakh, and a score just over the floor
    # bought at full size). The owner's standing choice is slab, so slab is
    # also where a typo lands now.
    if str(getattr(cfg, "sizing_mode", "")).lower() not in (
            "slab", "fixed", "kelly", "half_kelly"):
        cfg.sizing_mode = "slab"
        overrides["sizing_mode"] = "slab"
    # Owner order 2026-10-04: a category's cash share is a fraction of the
    # deployable capital — never negative, never more than all of it.
    for _f in ("budget_share_momentum", "budget_share_intraday",
               "budget_share_short", "budget_share_swing",
               "budget_share_positional"):
        _v = float(getattr(cfg, _f, 0.0) or 0.0)
        _c = min(1.0, max(0.0, _v))
        if _c != _v:
            setattr(cfg, _f, _c)
            overrides[_f] = _c
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
