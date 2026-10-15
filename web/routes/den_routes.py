"""🧪 /den — MARS Den: every formula the engine runs, on one page.

OWNER ORDER 2026-10-12 (their words): "Instead of having universe lens,
trigger, weight, selection register, Engine regime can be merged in one
section named as MARS Den, where most of the formulas must be visible and
replica"; "Everest in the Engine regime also must show the parameters in
it"; "setting will only have the APP SETTINGS, NOT THE ALGO, that will move
to MARS Den"; "a special tab in left where I have all formulas, but editable
which will not change the app formula but assist for correction at that
time and have option to add any share over there".

TRUTH RULES FOR THIS PAGE
  * Every number is read at render time from the live Config, the module
    constants it points at (trading/targets.py, trading/sizing.py,
    trading/lens_v2.py, trading/danger.py, trading/risk_v2.py, ...) or
    stored portfolio state. The template never carries a typed number.
  * The ONE hand-written table is the owner's 24-stage selection sequence
    status (SEQUENCE below). It is dated on screen (SEQUENCE_STATUS_ASOF).
  * Section 10, the Sandbox, is an EDITABLE COPY: a draft lands in the
    settings store under doc_id="formula_drafts" and in process memory, and
    the stock readings are computed on a copy.copy() of the Config with the
    draft fields set. The live Config object is never written
    (test-pinned: dataclasses.asdict before == after a sandbox POST).

BOUNDARY: display only. Nothing here opens, closes, sizes or blocks a
trade; engine/risk/orders never import this module.
"""

from __future__ import annotations

import copy
import csv
import inspect
import logging
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth, db, state
from ..templating import templates

log = logging.getLogger(__name__)
router = APIRouter()

_ROOT = Path(__file__).resolve().parents[2]

# Section anchors, in page order. den.html renders a chip row from this and
# the test asserts every id is present on the page.
SECTIONS = [
    ("sequence",      "1 · Sequence"),
    ("engines",       "2 · Engines"),
    ("weights",       "3 · Weights"),
    ("triggers",      "4 · Triggers & Danger"),
    ("timing",        "5 · Timing"),
    ("capacity",      "6 · Sizing & capacity"),
    ("register",      "7 · Register"),
    ("evidence",      "8 · Evidence"),
    ("algo-settings", "9 · Algo settings"),
    ("sandbox",       "10 · Sandbox"),
    ("valuation",     "11 · Valuation context"),
    # Owner order 2026-10-14 ("lets hit the formulas work completion"):
    # stages 07-18 as code — snapshot, route, shares, normalised earnings,
    # quality, scenarios, justified range, verdict. Display only.
    ("chain",         "12 · Valuation chain"),
    # Owner order 2026-10-14 ("put the formulas in the den now … need
    # editable"): every formula of the chain printed, every ASSUMED number
    # editable as a DRAFT that §12 reads on this page only.
    ("formulas",      "13 · Formulas & assumptions"),
]

# --------------------------------------------------------------------- 1.
# The owner's 24-stage selection sequence. STATUS IS HAND-WRITTEN here (the
# one place that is acceptable on this page) and dated. "exists" = the stage
# runs in code today; "partly" = a piece of it runs, named in `where`;
# "not yet" = nothing in the repo does this.
SEQUENCE_STATUS_ASOF = "2026-10-14"
SEQUENCE: list[tuple[str, str, str, str]] = [
    # Owner order 2026-10-14 ("lets hit the formulas work completion"): the
    # valuation chain stages 07-18 run as code in trading/valuation/* and
    # print on Den §12. Display only — no gate reads any of it.
    ("00", "Valuation basis by sector", "exists",
     "trading/valuation/route.py — the 21-row sector-method table, label from "
     "cfg.sector_map then the Nifty-500 industry file, keyword rules for the "
     "coarse labels, unmapped names UNDECLARED (never forced)"),
    ("01", "Evaluation mandate + batch plan", "partly",
     "weekly liquidity batch (scheduler.refresh_liquid_universe) + the owner's "
     "always-include list; no written mandate per batch"),
    ("02", "Capture + validate raw scan inputs", "partly",
     "store-first fetch_daily, stale docs refetched, SCAN_COVERAGE_SHORT "
     "journaled; no per-row validation report"),
    ("03", "Scan captured inputs", "exists",
     "engine.scan — five-category rotation at 09:20 and 14:30"),
    ("04", "Per-stock checkpoint", "not yet", ""),
    ("05", "Rotation reconciliation", "partly",
     "engine._rotation_exits funds only this scan's cleared picks "
     "(audit F-7 / S-4)"),
    ("06", "Durable rotation save", "partly",
     "portfolio.save → JSON + Mongo mirror after each pass; no rotation "
     "ledger of its own"),
    ("07", "Financial snapshot", "partly",
     "trading/valuation/snapshot.py over the committed statements zip (200 "
     "Nifty-200 names) + the monthly Yahoo refresh; the other ~1,000 scan "
     "names read DATA_NEEDED until the batch fetch is approved"),
    ("08", "Point-in-time validation + source lock", "exists",
     "snapshot.py — period end, publication date (45-day lag ASSUMED until "
     "NSE result dates are fetched), the ADR currency trap converted at the "
     "period-end USDINR and labelled, consolidated/standalone DATA_NEEDED, "
     "sha256 lock hash"),
    ("09", "Business model + valuation route", "exists",
     "route.py route_for — method, family, per-share metric, drivers, "
     "defended-by, policy links, PSU / holding overlays"),
    ("10", "Share capital + dilution", "exists",
     "trading/valuation/shares.py — basic / weighted / diluted, corporate "
     "actions parsed (split, bonus, rights, buyback), each period pair "
     "reconciled or named unexplained; ESOP pool DATA_NEEDED"),
    ("11", "Normalised earnings", "exists",
     "trading/valuation/normalise.py — one-offs and their tax effect stripped "
     "from reported PAT, cross-checked against the audited normalised line"),
    ("12", "Earnings quality + balance sheet", "exists",
     "normalise.quality — cash conversion (3 FYs), FCF margin, receivable / "
     "working-capital days, net debt/EBITDA, interest cover, D/E, score with "
     "ASSUMED weights; lenders read DATA_NEEDED on the leverage lines"),
    ("13", "Company/sector/market conditions", "partly",
     "regime + breadth band + sector feed + damage store; no per-company "
     "conditions sheet"),
    ("14", "Thesis + falsifier", "not yet", ""),
    ("15", "Revenue/margin/financing scenarios", "exists",
     "trading/valuation/scenarios.py — the owner's Tata Tech P/E-driver sheet "
     "reproduced cell for cell (164 cells, zero difference), three cases with "
     "ASSUMED deltas; the macro what-if rows the app does not store count 0 "
     "and are listed DATA_NEEDED"),
    ("16", "Scenario EPS", "exists",
     "scenarios.three_cases — downside / base / upside EPS, closing P/E, model "
     "price; BVPS for the book route, EBITDA per share for the EV routes"),
    ("17", "Justified valuation range", "exists",
     "scenarios.justified_range per route family: EPS × P/E band (peer median "
     "+ own 5-year range), justified P/B for lenders, EV/EBITDA → equity bridge, "
     "NAV DATA_NEEDED with a P/B fallback; sensitivity grid"),
    # Owner order 2026-10-12 ("both, indian sector only, discount rate -
    # go"): the context stage sits between the justified range and the
    # selection — it verifies a standalone target against the sector, the
    # discount rate and the fear gauges. The target itself is typed until
    # stages 09-17 produce it.
    ("17b", "Market · sector · rates · sentiment context (target verify)",
     "exists",
     "trading/valuation_context.py + Den §11 — sector leg 1:1 and measured "
     "β side by side (Indian NSE indices), rates through the discount rate, "
     "sentiment as a wider buy discount, move split with a re-run flag; the "
     "standalone target is typed until stages 09-17 produce it"),
    ("18", "Financial-selection assessment", "exists",
     "trading/valuation/assess.py — attractive / watch / unattractive / "
     "insufficient with the reasons; printed on Den §12; NOT a trade "
     "permission (stage 21 and Phase 4 wait on the pre-registered test)"),
    ("19", "IFR state", "exists",
     "trading/ifr.py — frozen thresholds over the 191 reference names, "
     "throttle applied in engine.scan"),
    ("20", "Unified risk governor", "exists",
     "risk.can_open (cash, reserve, category share, sector, heat, ceiling, "
     "minimum) + drawdown governor + kill switch"),
    ("21", "Permitted decision conditions", "not yet", ""),
    ("22", "Paper-engine decision capture", "partly",
     "BUY/SELL journal with config snapshot, stop/target/sizing diagnostics; "
     "no decision-conditions record"),
    ("23", "URR verification", "not yet", ""),
    ("24", "Persist decision + evidence", "not yet", ""),
]

# Sandbox knobs: form name + label. `kind` = "pct" means the box is typed as
# a percentage (stored as a fraction on the DRAFT config copy only). The
# prefill for every knob is the live value (_live_draft).
SANDBOX_KNOBS: list[dict] = [
    {"name": "floor",        "label": "Score floor (momentum)",     "kind": "num"},
    {"name": "blend_pct",    "label": "Quality blend %",            "kind": "pct"},
    {"name": "stop_pct",     "label": "Category stop % (floor of the ATR stop)", "kind": "pct"},
    {"name": "atr_stop_k",   "label": "ATR stop k (× ATR14)",       "kind": "num"},
    {"name": "atr_stop_cap_pct", "label": "ATR stop cap %",         "kind": "pct"},
    {"name": "target_k",     "label": "Target k (× ATR14)",         "kind": "num"},
    {"name": "slab",         "label": "Slab minimum (Rs)",          "kind": "num"},
    {"name": "kelly_m",      "label": "Kelly m",                    "kind": "num"},
    {"name": "breadth_low",  "label": "Breadth band low (0-1)",     "kind": "num"},
    {"name": "breadth_high", "label": "Breadth band high (0-1)",    "kind": "num"},
    {"name": "share_momentum",   "label": "Momentum share %",   "kind": "pct"},
    {"name": "share_intraday",   "label": "Intraday share %",   "kind": "pct"},
    {"name": "share_short",      "label": "Short share %",      "kind": "pct"},
    {"name": "share_swing",      "label": "Swing share %",      "kind": "pct"},
    {"name": "share_positional", "label": "Positional share %", "kind": "pct"},
]
DRAFT_DOC_ID = "formula_drafts"
MAX_SANDBOX_SYMBOLS = 25

#: In-process copy of the last stored draft so the sandbox works (and the
#: test can see the draft) when Mongo is offline — exactly like the Settings
#: page's "In-memory only" behaviour. Mongo is the durable copy.
LAST_DRAFT: dict = {}


# --------------------------------------------------------------------- utils

def _safe(fn, *args, default=None):
    """A broken section must never take the whole Den down (the dashboard's
    _panel rule)."""
    try:
        return fn(*args)
    except Exception:                       # pragma: no cover - safety net
        log.exception("den section %s failed", getattr(fn, "__name__", fn))
        return default


def _pct(x, digits: int = 1) -> Optional[float]:
    try:
        return round(float(x) * 100.0, digits)
    except (TypeError, ValueError):
        return None


def _z_divisor() -> Optional[float]:
    """The score-to-z divisor is an inline literal in sizing.edge_estimate
    (z = (score - floor) / 30.0). Read it from there so the sizing ladder and
    the lens card print the live arithmetic, not a copy of it."""
    from trading import sizing
    try:
        src = inspect.getsource(sizing.edge_estimate)
    except Exception:
        return None
    return _literal(src, r"z\s*=\s*\(float\(score\)\s*-\s*float\(floor\)\)"
                         r"\s*/\s*(\d+(?:\.\d+)?)")


def _entry_prices(p) -> dict:
    return {t: pos.entry_price for t, pos in (p.positions or {}).items()}


def _scan_history() -> tuple:
    """What the rotation really pulls per category pass: daily bars, the
    momentum pass 2 years, every other pass `TradingEngine.scan`'s default.
    Read from the engine itself — the presets' bar_interval/history_period
    fields have no reader anywhere (audit 2026-10-15), so printing them told
    the owner intraday scans 5-minute bars when it scans daily ones."""
    from trading.engine import TradingEngine
    other = "6mo"
    mom = "2y"
    try:
        other = str(inspect.signature(TradingEngine.scan)
                    .parameters["history_period"].default)
    except Exception:
        pass
    try:
        m = re.search(r'history_period\s*=\s*"([0-9]+[a-z]+)"',
                      inspect.getsource(TradingEngine.scan_universe))
        if m:
            mom = m.group(1)
    except Exception:
        pass
    return mom, other


_BLIND: Optional[dict] = None


def _danger_blind() -> dict:
    """Which danger tripwires the layer can actually COUNT (audit
    2026-10-15, the upload review's XR-060). `danger.evaluate_stock` counts a
    fire only when the tripwire returns the Python object True; a tripwire
    that returns a NumPy bool can never be counted, whatever the market does.
    Measured here by running each OHLCV tripwire once on a fixed synthetic
    history and reading the TYPE of what it returns — so the line on the
    page disappears by itself the day the one-line fix lands. Cached per
    process (the functions do not change while it runs)."""
    global _BLIND
    if _BLIND is not None:
        return _BLIND
    out = {"blind": [], "countable": [], "unknown": []}
    try:
        import numpy as np
        import pandas as pd
        from trading import danger
        n = 320
        i = np.arange(n, dtype=float)
        c = 100.0 * np.exp(0.0004 * i + 0.02 * np.sin(i / 7.0))
        df = pd.DataFrame({"Open": c * 0.998, "High": c * 1.01, "Low": c * 0.99,
                           "Close": c, "Volume": 2e5 + 5e4 * np.cos(i / 5.0)},
                          index=pd.bdate_range(end="2026-10-14", periods=n))
        x = danger._Ctx(df)
        for tid, fn in sorted(danger._FNS.items()):
            try:
                r = fn(x)
            except Exception:
                r = None
            if isinstance(r, np.bool_):
                out["blind"].append(tid)
            elif isinstance(r, bool):
                out["countable"].append(tid)
            else:
                out["unknown"].append(tid)
    except Exception:
        log.exception("den: danger blind check failed")
    _BLIND = out
    return out


def _literal(src: str, pattern: str) -> Optional[float]:
    """Read one numeric literal out of a function's source — for rules
    written inline (engines/ocean.py evaluate) rather than as constants.
    None when the line is not there any more, so the page prints
    DATA_NEEDED instead of a stale number."""
    m = re.search(pattern, src)
    if not m:
        return None
    try:
        return float(m.group(1))
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ 1. seq

def _sequence() -> dict:
    rows = [{"code": c, "name": n, "status": s, "where": w}
            for c, n, s, w in SEQUENCE]
    counts = Counter(r["status"] for r in rows)
    return {"asof": SEQUENCE_STATUS_ASOF, "rows": rows,
            "n": len(rows), "exists": counts.get("exists", 0),
            "partly": counts.get("partly", 0),
            "not_yet": counts.get("not yet", 0)}


# -------------------------------------------------------------- 2. engines

_MODE_LABELS = {
    "nifty200_momentum": "Momentum (Everest)",
    "intraday": "Intraday",
    "short": "Short",
    "swing": "Swing",
    "positional": "Positional",
}
_MODE_ORDER = ["nifty200_momentum", "intraday", "short", "swing", "positional"]


def _category_rows(cfg) -> list[dict]:
    from trading import candles, category_budget
    from trading import targets as T
    from trading.modes import PRESETS
    from trading.scoring import COMPOSITE_MODES
    po = getattr(cfg, "preset_overrides", None) or {}
    mk_modes = tuple(getattr(cfg, "measured_kelly_modes", ()) or ())
    hist_mom, hist_other = _scan_history()
    rows = []
    for m in _MODE_ORDER:
        pr = PRESETS.get(m)
        if pr is None:
            continue
        over = dict(po.get(m) or {})
        c2 = copy.copy(cfg)          # shallow copy: the live cfg is untouched
        c2.mode = m
        k = T.ATR_TARGET_K.get(m)
        band = T.TARGET_BAND.get(m, (0.0, 1.0))
        rows.append({
            "key": m, "name": _MODE_LABELS.get(m, m),
            "active": m == getattr(cfg, "mode", ""),
            "floor": float(over.get("min_score_to_enter", pr.min_score_to_enter)),
            "preset_floor": float(pr.min_score_to_enter),
            "overrides": sorted(over.keys()),
            "scorer": ("6m+12m momentum blended with quality"
                       if m not in COMPOSITE_MODES else
                       "AM07 composite on this category's own weights"),
            "bar": "daily",
            "history": hist_mom if m == "nifty200_momentum" else hist_other,
            "stop_pct": _pct(over.get("stop_loss_pct", pr.stop_loss_pct)),
            "atr_stop": (m in T.ATR_STOP_MODES
                         and bool(getattr(cfg, "atr_stop_enabled", True))),
            "target_k": k,
            "target_at_median_pct": (round(k * T.MEDIAN_ATR_PCT_OF_PRICE, 1)
                                     if k else None),
            "band_pct": [_pct(band[0]), _pct(band[1])],
            "fixed_target_pct": _pct(over.get("take_profit_pct", pr.take_profit_pct)),
            "trail_pct": _pct(over.get("trailing_stop_pct", pr.trailing_stop_pct)),
            "decay": float(over.get("score_decay_exit", pr.score_decay_exit)),
            "hold": int(over.get("max_hold_days", pr.max_hold_days)),
            "square_off": pr.square_off_time,
            "candle_gate": bool(candles.candle_gate_applies(c2)),
            "share_pct": _pct(category_budget.share(cfg, m)),
            "kelly_gated": bool(not mk_modes or m in mk_modes),
            "breakout_threshold_pct": _pct(pr.breakout_threshold_pct),
            "volume_confirm_ratio": pr.volume_confirm_ratio,
            "timing_off": bool(pr.breakout_threshold_pct >= 1.0
                               and pr.volume_confirm_ratio <= 0.0),
        })
    return rows


def _sizing_block(cfg) -> dict:
    """The slab formula from trading/sizing.py, plus an ILLUSTRATION ladder
    computed with the same pure functions at a stated 20% annual vol. The
    sigma is an illustration input, not market data, and the card says so."""
    from trading import sizing
    from trading.modes import PRESETS
    floor = float(PRESETS["nifty200_momentum"].min_score_to_enter)
    po = (getattr(cfg, "preset_overrides", None) or {}).get("nifty200_momentum") or {}
    floor = float(po.get("min_score_to_enter", floor))
    ic = float(getattr(cfg, "kelly_ic", 0.05))
    m = float(getattr(cfg, "kelly_fraction", 0.29))
    cap = float(getattr(cfg, "kelly_max_pct", 0.05))
    slab = float(getattr(cfg, "position_slab_value", 0.0) or 0.0)
    equity = float(getattr(cfg, "starting_capital", 0.0))
    z_div = _z_divisor()
    sigma = 0.20
    ladder = []
    for score in ((floor + 1, 65.0, 68.0, 72.0, 80.0, 90.0) if z_div else ()):
        z = (score - floor) / z_div
        mu = ic * sigma * z
        f = sizing.kelly_fraction(mu, sigma, fraction=m, cap=cap)
        raw = f * equity
        notional = 0.0 if raw <= 0 else max(slab, raw)
        ladder.append({"score": round(score, 1), "z": round(z, 3),
                       "kelly_pct": round(f * 100, 3), "raw": round(raw),
                       "notional": round(notional),
                       "slab_applied": bool(0 < raw < slab),
                       "capped": bool(f >= cap - 1e-12 and mu > 0)})
    return {
        "mode": str(getattr(cfg, "sizing_mode", "")),
        "z_div": z_div,
        "slab": slab, "m": m, "ic": ic, "cap_pct": _pct(cap),
        "cap_rupees": cap * equity, "equity": equity, "floor": floor,
        "min_position_value": float(getattr(cfg, "min_position_value", 0.0)),
        "position_size_value": float(getattr(cfg, "position_size_value", 0.0)),
        "sigma_pct": _pct(sigma, 0), "ladder": ladder,
        "max_positions": int(getattr(cfg, "max_positions", 0)),
    }


def _measured_kelly_block(cfg, p) -> dict:
    from trading import measured_kelly as MK
    modes = tuple(getattr(cfg, "measured_kelly_modes", ()) or ())
    cat = modes[0] if len(modes) == 1 else None
    reading = None
    try:
        reading = MK.measure(
            list(p.trades or []),
            fraction=float(getattr(cfg, "kelly_fraction", 0.29)),
            min_trades=int(getattr(cfg, "measured_kelly_min_trades", 0)),
            full_confidence=int(getattr(cfg, "measured_kelly_full_confidence",
                                        MK.FULL_CONFIDENCE)),
            clip_pct=MK.live_clip_pct(), category=cat)
    except Exception:
        log.exception("den: measured kelly reading failed")
    return {
        "enabled": bool(getattr(cfg, "measured_kelly_enabled", False)),
        "modes": list(modes) or ["(all categories pooled)"],
        "prior_trades": MK.PRIOR_TRADES, "prior_win_rate": MK.PRIOR_WIN_RATE,
        "clip_pct": MK.live_clip_pct(),
        "full_confidence": int(getattr(cfg, "measured_kelly_full_confidence",
                                       MK.FULL_CONFIDENCE)),
        "reading": reading.to_dict() if reading is not None else None,
    }


def _ocean_block(cfg) -> dict:
    from trading.engines import ocean
    src_eval = inspect.getsource(ocean.evaluate)
    src_surv = inspect.getsource(ocean.survival_check)
    try:
        cvr_sig = inspect.signature(ocean.climax_volume_ratio).parameters
        cvr_lookback = cvr_sig["lookback"].default
        cvr_baseline = cvr_sig["baseline"].default
    except Exception:
        cvr_lookback = cvr_baseline = None
    try:
        rsi_len = inspect.signature(ocean.weekly_rsi).parameters["length"].default
    except Exception:
        rsi_len = None
    return {
        "enabled": bool(getattr(cfg, "ocean_enabled", False)),
        "ticket": float(getattr(cfg, "ocean_position_value", 0.0)),
        "max_positions": int(getattr(cfg, "ocean_max_positions", 0)),
        "everest_ticket": float(getattr(cfg, "position_size_value", 0.0)),
        # Selection literals, read out of evaluate()/survival_check() source
        # — None prints DATA_NEEDED if the line moves.
        "below_200dma_pct": _literal(src_eval, r"pct_below_200\s*>\s*(-?\d+(?:\.\d+)?)"),
        "weekly_rsi_max": _literal(src_eval, r"wrsi\s*>=\s*(\d+(?:\.\d+)?)"),
        "climax_ratio_min": _literal(src_eval, r"cvr\s*<\s*(\d+(?:\.\d+)?)"),
        "de_max": _literal(src_surv, r"de\s*>=\s*(\d+(?:\.\d+)?)"),
        "cvr_lookback": cvr_lookback, "cvr_baseline": cvr_baseline,
        "rsi_length": rsi_len,
        "rails": [
            {"id": "R1", "name": "VIX knife",
             "rule": (f"block while India VIX is above its {ocean.VIX_Q_WINDOW}-day "
                      f"{int(round(ocean.VIX_Q * 100))}th percentile AND rising, "
                      "unless the VIX-crush rule says the fear break already happened"),
             "source": "engines/ocean.py crash_guard_market · v4 study (+2.3pp)"},
            {"id": "R2", "name": "Damage cluster, own q90",
             "rule": ("block while the panel's canonical tail_down3_5d reading "
                      "sits above its OWN rolling 90th percentile (damage_store"
                      "['q90_cluster']); a missing or stale reading also blocks"),
             "source": "engines/ocean.py crash_guard_market · trading/internals.py · owner 2026-10-04"},
            {"id": "R3", "name": "Per-stock reclaim",
             "rule": ("no fresh 20-day low on the entry bar AND the close above "
                      "the prior bar's high — buy the turn, never the fall"),
             "source": "engines/ocean.py crash_guard_stock"},
            {"id": "R4", "name": "Danger veto",
             "rule": ("a danger tripwire the layer can count firing on the name's own "
                      "price history blocks it (the two panel-fed tripwires do not run "
                      "here; see §4 for which ones the layer can count today)"),
             "source": "engines/ocean.py crash_guard_stock · trading/danger.py"},
        ],
        "exempt": ["breadth band (a dip engine exists for fallen markets)",
                   "candle confirmation (the reclaim rail is the confirmation)",
                   "score floor + absolute-momentum gate (Ocean's own rules replace them)",
                   "score-decay exit and the trail-tighten (audit F-2)"],
    }


def _risk_block(cfg) -> dict:
    from trading import danger, risk_v2
    stop = abs(float(getattr(cfg, "stop_loss_pct", 0.0)))
    leash = max(abs(float(danger.TIGHTEN_PCT)), stop)
    blind = _danger_blind()
    return {
        "danger_blind": blind["blind"], "danger_countable": blind["countable"],
        "danger_panel_fed": list(danger.PANEL_FED),
        "kill_switch_pct": _pct(getattr(cfg, "daily_loss_halt_pct", 0.0)),
        "ladder": [{"threshold_pct": _pct(t), "level": n, "mult": mult,
                    "action": a} for t, n, mult, a in risk_v2.DRAWDOWN_LEVELS],
        "reserve": [{"regime": k, "pct": _pct(v, 0)}
                    for k, v in risk_v2.DYNAMIC_CASH_RESERVE.items()],
        "cash_reserve_pct": _pct(getattr(cfg, "cash_reserve_pct", 0.0)),
        "max_positions": int(getattr(cfg, "max_positions", 0)),
        "max_sector_positions": int(getattr(cfg, "max_sector_positions", 0)),
        "max_sector_exposure_pct": _pct(getattr(cfg, "max_sector_exposure_pct", 0.0), 0),
        "max_heat_pct": _pct(getattr(cfg, "max_portfolio_heat_pct", 0.0), 0),
        "liquidity_cr": risk_v2.LiquidityRules.min_rupee_volume_5d_cr,
        "liquidity_mcap_cr": risk_v2.LiquidityRules.min_market_cap_cr,
        "liquidity_spread_pct": _pct(risk_v2.LiquidityRules.max_spread_pct, 2),
        "loss_halt_fraction_pct": _pct(risk_v2.LOSS_HALT_BOOK_FRACTION, 0),
        "correction_brake": bool(getattr(cfg, "correction_brake_enabled", False)),
        "vix_reentry": bool(getattr(cfg, "correction_vix_reentry", False)),
        "rotation": bool(getattr(cfg, "rotation_auto_enabled", False)),
        "rotation_min_progress_pct": _pct(getattr(cfg, "rotation_min_progress", 0.0), 0),
        "rotation_cash_trigger_pct": _pct(getattr(cfg, "rotation_cash_trigger_pct", 0.0), 0),
        "danger_enabled": bool(getattr(cfg, "danger_layer_enabled", False)),
        "danger_alert_days": danger.ALERT_DAYS,
        "danger_tighten_pct": _pct(danger.TIGHTEN_PCT),
        "danger_leash_pct": _pct(-leash),
        "market_hours_only": bool(getattr(cfg, "market_hours_only", False)),
        "refresh_minutes": int(getattr(cfg, "intraday_refresh_minutes", 0)),
        "blocklist": list(getattr(cfg, "ticker_blocklist", ()) or ()),
        "max_stale_days": int(getattr(cfg, "max_stale_days", 0)),
        "disable_exits": bool(getattr(cfg, "disable_exits", False)),
        "slippage_bps": float(getattr(cfg, "slippage_bps", 0.0)),
    }


def _regime_block(p) -> dict:
    """Reuses the engine page's cached reading; NEVER fetches here (the
    engine page refreshes it, at most every 15 min)."""
    from . import regime_routes
    reading = getattr(regime_routes, "_LAST_REGIME", None)
    at = getattr(regime_routes, "_LAST_REGIME_AT", None)
    if reading is not None:
        return {"source": "engine page cache", "at": at,
                "regime": getattr(reading, "regime", None),
                "everest_active": getattr(reading, "everest_active", None),
                "ocean_active": getattr(reading, "ocean_active", None),
                "nifty_close": getattr(reading, "nifty_close", None),
                "nifty_200dma": getattr(reading, "nifty_200dma", None),
                "pct_below_52w_high": getattr(reading, "pct_below_52w_high", None),
                "vix": getattr(reading, "vix", None),
                "reasoning": getattr(reading, "reasoning", "")}
    return {"source": "stored portfolio.last_regime", "at": None,
            "regime": getattr(p, "last_regime", None),
            "everest_active": None, "ocean_active": None,
            "nifty_close": None, "nifty_200dma": None,
            "pct_below_52w_high": None, "vix": None, "reasoning": ""}


def _momentum_formula() -> dict:
    """The constants of the momentum and quality formulas, read out of the
    functions themselves (they are inline literals, not module constants).
    A None prints DATA_NEEDED on the page if a line moves — a stale number
    typed into the template would keep printing after the code changed."""
    from trading import quality as Q
    from trading.strategies.nifty200_momentum import momentum_score
    src = inspect.getsource(momentum_score)

    def _default(fn, name):
        try:
            return inspect.signature(fn).parameters[name].default
        except Exception:
            return None
    min_bars = _literal(src, r"len\(close\.dropna\(\)\)\s*<\s*(\d+)")
    return {
        "center": _literal(src, r"score\s*=\s*(\d+(?:\.\d+)?)\s*\+\s*raw_avg"),
        "slope": _literal(src, r"raw_avg\s*\*\s*(\d+(?:\.\d+)?)"),
        "min_bars": int(min_bars) if min_bars is not None else None,
        "low_vol_lookback": _default(Q.low_vol_score, "lookback"),
        "liquidity_lookback": _default(Q.liquidity_score, "lookback"),
        "doc": " ".join((momentum_score.__doc__ or "").split()),
    }


def _market_hours() -> Optional[str]:
    from trading import calendars
    try:
        (oh, om), (ch, cm) = calendars.MARKET_OPEN_IST, calendars.MARKET_CLOSE_IST
        return f"{oh:02d}:{om:02d}–{ch:02d}:{cm:02d} IST"
    except Exception:
        return None


def _engines(cfg, p) -> dict:
    from trading import targets as T
    mf = _momentum_formula()
    return {
        "categories": _category_rows(cfg),
        "quality_blend_pct": _pct(getattr(cfg, "quality_blend_pct", 0.0), 0),
        "momentum": mf,
        "momentum_doc": mf["doc"],
        "market_hours": _market_hours(),
        "abs_mom_rule": ("own 6-month return > 0 (engine.scan _abs_mom_ok: "
                         "last close above the close 126 bars earlier) — "
                         "momentum pass only"),
        "breadth": {
            "enabled": bool(getattr(cfg, "breadth_gate_enabled", False)),
            "low": float(getattr(cfg, "breadth_block_low", 0.0)),
            "high": float(getattr(cfg, "breadth_block_high", 0.0)),
            "min_sample": int(getattr(cfg, "breadth_min_sample", 0)),
            "store_max_age_days": int(getattr(cfg, "breadth_store_max_age_days", 0)),
            "today": dict(getattr(p, "breadth_today", None) or {}),
        },
        "stop": {
            "k": T.ATR_STOP_K, "cap_pct": _pct(T.ATR_STOP_CAP),
            "modes": list(T.ATR_STOP_MODES), "source": T.ATR_STOP_SOURCE,
            "enabled": bool(getattr(cfg, "atr_stop_enabled", True)),
            "window": T.ATR_WINDOW,
        },
        "target": {
            "k": dict(T.ATR_TARGET_K), "median_atr_pct": T.MEDIAN_ATR_PCT_OF_PRICE,
            "band": {m: [_pct(lo), _pct(hi)] for m, (lo, hi) in T.TARGET_BAND.items()},
            "window": T.ATR_WINDOW,
        },
        "candle_skip_modes": list(getattr(cfg, "candle_confirm_skip_modes", ()) or ()),
        "candle_enabled": bool(getattr(cfg, "candle_confirm_enabled", False)),
        "sizing": _sizing_block(cfg),
        "measured_kelly": _measured_kelly_block(cfg, p),
        "shares_enabled": bool(getattr(cfg, "category_budgets_enabled", False)),
        "ocean": _ocean_block(cfg),
        "risk": _risk_block(cfg),
        "regime": _regime_block(p),
        "live_mode": getattr(cfg, "mode", ""),
    }


# -------------------------------------------------------------- 3. weights

def _weights(cfg) -> dict:
    from trading.scoring import COMPOSITE_MODES, weights_for
    from .strategy_routes import COMPONENT_DESCRIPTIONS
    tables = []
    for m in COMPOSITE_MODES:
        w = weights_for(m)
        rows = sorted(({"component": c, "weight_pct": round(v * 100, 1),
                        "description": COMPONENT_DESCRIPTIONS.get(c, ("?", "?"))[0],
                        "status": COMPONENT_DESCRIPTIONS.get(c, ("?", "?"))[1]}
                       for c, v in w.items()), key=lambda r: -r["weight_pct"])
        tables.append({"mode": m, "label": _MODE_LABELS.get(m, m),
                       "active": m == getattr(cfg, "mode", ""),
                       "rows": rows, "total_pct": round(sum(w.values()) * 100, 1)})
    return {"tables": tables,
            "momentum_blend_pct": _pct(getattr(cfg, "quality_blend_pct", 0.0), 0),
            "live_mode": getattr(cfg, "mode", "")}


# ------------------------------------------------------ 4. triggers/danger

def _triggers(p) -> dict:
    from trading import danger
    from trading.selection_sequence import STAGES, STAGE_META
    from trading.triggers import (CALIBRATION, CHAIN_FED, COMPUTABLE,
                                  EXTERNAL, EXTERNAL_PENDING, EXTERNAL_WIRED,
                                  GATE_ROWS, NEWS_FED, SCORE_NOTE, TRIGGERS)
    tripwires = sorted(
        ({"id": k, **v} for k, v in danger.VERIFIED.items()),
        key=lambda r: -float(r.get("lift") or 0.0))
    # The two evidence bars, named by the tripwires' own `labels` field so the
    # footnote can never disagree with the table above it.
    ohlcv_labels = sorted({str(t.get("labels")) for t in tripwires
                           if not t.get("needs_panels")})
    panel_labels = sorted({str(t.get("labels")) for t in tripwires
                           if t.get("needs_panels")})
    fam = Counter(t.family for t in TRIGGERS)
    fam_ready = Counter(t.family for t in TRIGGERS if t.ohlcv_ready or t.ext_fn)
    families = [{"name": f, "n": n, "computing": fam_ready.get(f, 0)}
                for f, n in sorted(fam.items(), key=lambda kv: kv[0])]
    cal = Counter(str(v.get("proof_tag")) for v in CALIBRATION.values())
    tw = getattr(p, "trigger_watch", None) or {}
    auto = False
    try:
        auto = danger.auto_mode_active(getattr(p, "danger_alerts_start", "") or "",
                                       date.today().isoformat())
    except Exception:
        pass
    return {
        "tripwires": tripwires, "n_tripwires": len(tripwires),
        "blind": _danger_blind()["blind"],
        "countable": _danger_blind()["countable"],
        "alert_days": danger.ALERT_DAYS, "tighten_pct": _pct(danger.TIGHTEN_PCT),
        "auto_mode": auto,
        "alerts_start": getattr(p, "danger_alerts_start", None),
        "panel_fed": list(danger.PANEL_FED),
        "ohlcv_labels": ohlcv_labels, "panel_labels": panel_labels,
        "n_triggers": len(TRIGGERS), "n_computable": len(COMPUTABLE),
        "n_external": len(EXTERNAL), "n_external_wired": len(EXTERNAL_WIRED),
        "n_external_pending": len(EXTERNAL_PENDING),
        "n_news_fed": len(NEWS_FED), "n_chain_fed": len(CHAIN_FED),
        "families": families,
        "calibration": sorted(cal.items(), key=lambda kv: -kv[1]),
        "n_calibrated": sum(cal.values()),
        "gates": [{"id": g[0], "name": g[1], "logic": g[2]} for g in GATE_ROWS],
        "score_note": SCORE_NOTE,
        "ladder": [{"stage": s, **STAGE_META[s]} for s in STAGES],
        "watch_rows": len(tw.get("rows") or {}),
        "watchlist": len(tw.get("watchlist") or []),
        "watch_ts": tw.get("ts") or tw.get("asof"),
    }


# --------------------------------------------------------------- 5. timing

def _timing(cfg, p, categories: list[dict]) -> dict:
    from trading import candles
    from .scan_routes import _build_piano
    piano = None
    try:
        full = _build_piano(dict(getattr(p, "scan_matrix", None) or {}))
        if full:
            piano = {k: full.get(k) for k in ("ts", "mode", "scanned",
                                               "universe_size", "hits", "total")}
            piano["sections"] = [{"name": s["name"], "hit": s["hit"],
                                  "total": s["total"]} for s in full["sections"]]
    except Exception:
        log.exception("den: piano summary failed")
    gate_capable = sorted(candles._PATTERN_FUNCS)
    configured = list(getattr(cfg, "candle_patterns", ()) or ())
    return {
        "bullish": list(candles.BULLISH_PATTERNS),
        "bearish": list(candles.BEARISH_PATTERNS),
        "neutral": list(candles.NEUTRAL_PATTERNS),
        "n_structures": (len(candles.BULLISH_PATTERNS) + len(candles.BEARISH_PATTERNS)
                         + len(candles.NEUTRAL_PATTERNS)),
        "gate_capable": gate_capable, "configured": configured,
        "lookback": int(getattr(cfg, "candle_breakout_lookback", 0)),
        "vol_mult": float(getattr(cfg, "candle_volume_mult", 0.0)),
        "enabled": bool(getattr(cfg, "candle_confirm_enabled", False)),
        "per_category": [{"name": c["name"], "key": c["key"],
                          "gate": c["candle_gate"]} for c in categories],
        "piano": piano,
    }


# ------------------------------------------------------------- 6. capacity

def _capacity(cfg) -> dict:
    from trading import lens_v2

    def _default(fn, name):
        try:
            return inspect.signature(fn).parameters[name].default
        except Exception:
            return None

    def _int_literal(fn, pattern):
        try:
            v = _literal(inspect.getsource(fn), pattern)
        except Exception:
            v = None
        return int(v) if v is not None else None
    return {
        "ic": lens_v2.DEFAULT_IC, "lens_kelly_fraction": lens_v2.KELLY_FRACTION,
        "impact_y": lens_v2.IMPACT_Y, "trading_days": lens_v2.TRADING_DAYS,
        "max_impact_bps": _default(lens_v2.capacity_notional, "max_impact_bps"),
        "adv_days": _default(lens_v2.adv_rupees, "days"),
        "skew_days": _default(lens_v2.return_skew, "days"),
        # Inline literals in lens_v2, read from the functions themselves.
        "sigma_days": _int_literal(lens_v2.impact_bps, r"\.tail\((\d+)\)"),
        "min_bars": _int_literal(lens_v2._closes, r"len\(c\)\s*>=\s*(\d+)"),
        "trap_flags": _int_literal(lens_v2.is_trap, r"len\(flags\)\s*>=\s*(\d+)"),
        "z_div": _z_divisor(),
        "live_m": float(getattr(cfg, "kelly_fraction", 0.0)),
        "live_ic": float(getattr(cfg, "kelly_ic", 0.0)),
        "live_cap_pct": _pct(getattr(cfg, "kelly_max_pct", 0.0)),
    }


# ------------------------------------------------------------- 7. register

def _register(p) -> dict:
    import trading.register_v4 as rv
    from trading import danger
    status_csv = _ROOT / "docs" / "REGISTER_STATUS_2026-08-10.csv"
    cats: Counter = Counter()
    trades = 0
    try:
        with open(status_csv, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                c = str(row.get("category") or "")
                cats[c.split("+")[0]] += 1
                if c == "TRADES":
                    trades += 1
    except OSError:
        log.warning("den: register status CSV missing")
    stored = getattr(p, "register_market", None) or {}
    rows = stored.get("rows") or {}
    # LIVE state of the market-view items = the nightly precompute's own
    # per-row states. NOT len(_STOCK_DATA_NEEDED): that dict is the FALLBACK
    # reason each item prints only when its feed has no row for a symbol
    # (register_v4 applies it with setdefault after the wiring), so its length
    # is not a count of anything missing today.
    states = Counter(str((r or {}).get("state", "NA")) for r in rows.values())
    min_names = None
    try:
        from .. import scheduler
        v = _literal(inspect.getsource(scheduler.run_register_precompute),
                     r"MIN_NAMES\s*=\s*(\d+)")
        min_names = int(v) if v is not None else None
    except Exception:
        log.exception("den: register precompute guard unreadable")
    return {
        "n_registry": len(rv.REGISTRY), "have": len(rv.AM07_HAVE),
        "stock_fallbacks": len(rv._STOCK_DATA_NEEDED),
        "market_fallbacks": len(rv._MARKET_DATA_NEEDED),
        "trades": trades, "danger": len(danger.VERIFIED),
        "money_path": trades + len(danger.VERIFIED),
        "status_counts": sorted(cats.items(), key=lambda kv: -kv[1]),
        "status_asof": status_csv.stem.rsplit("_", 1)[-1],
        "status_file": status_csv.name,
        "assumed": len(rv.ASSUMED_CONSTANTS),
        "precompute_asof": stored.get("asof"),
        "precompute_sample": stored.get("sample"),
        "precompute_rows": len(rows),
        "precompute_states": sorted(states.items(), key=lambda kv: -kv[1]),
        "precompute_data_needed": states.get("DATA_NEEDED", 0),
        "precompute_min_names": min_names,
    }


# ------------------------------------------------------------- 8. evidence

def _evidence(p) -> dict:
    from .. import market_panels as mp
    return {
        "evidence": _safe(mp.evidence_panel),
        "horizons": _safe(mp.horizons_panel, default=[]),
        "atr_rows": _safe(mp.atr_panel, sorted((p.positions or {}).keys()), default=[]),
    }


# -------------------------------------------------------------- 10. sandbox

def _live_draft(cfg) -> dict:
    """The sandbox prefill = the live numbers, in the DRAFT's own units."""
    from trading import targets as T
    from trading.modes import PRESETS
    po = (getattr(cfg, "preset_overrides", None) or {}).get("nifty200_momentum") or {}
    pr = PRESETS["nifty200_momentum"]
    return {
        "floor": float(po.get("min_score_to_enter", pr.min_score_to_enter)),
        "blend_pct": _pct(getattr(cfg, "quality_blend_pct", 0.0), 2),
        "stop_pct": abs(_pct(po.get("stop_loss_pct", pr.stop_loss_pct), 2) or 0.0),
        "atr_stop_k": float(T.ATR_STOP_K),
        "atr_stop_cap_pct": _pct(T.ATR_STOP_CAP, 2),
        "target_k": float(T.ATR_TARGET_K["nifty200_momentum"]),
        "slab": float(getattr(cfg, "position_slab_value", 0.0) or 0.0),
        "kelly_m": float(getattr(cfg, "kelly_fraction", 0.0)),
        "breadth_low": float(getattr(cfg, "breadth_block_low", 0.0)),
        "breadth_high": float(getattr(cfg, "breadth_block_high", 0.0)),
        "share_momentum": _pct(getattr(cfg, "budget_share_momentum", 0.0), 2),
        "share_intraday": _pct(getattr(cfg, "budget_share_intraday", 0.0), 2),
        "share_short": _pct(getattr(cfg, "budget_share_short", 0.0), 2),
        "share_swing": _pct(getattr(cfg, "budget_share_swing", 0.0), 2),
        "share_positional": _pct(getattr(cfg, "budget_share_positional", 0.0), 2),
        "symbols": "",
    }


def load_draft() -> dict:
    """The draft: this process's copy first (the one the owner just typed —
    db.save_settings swallows a failed write, so Mongo can hold an OLDER
    draft), then the Mongo doc "formula_drafts" (survives a restart).
    Empty dict = no draft yet."""
    if LAST_DRAFT:
        return dict(LAST_DRAFT)
    try:
        return dict(db.load_settings(DRAFT_DOC_ID) or {})
    except Exception:
        return {}


#: An NSE/BSE symbol as the store keys it (GVT&D and M&M carry an ampersand,
#: BAJAJ-AUTO a hyphen). Anything else is dropped before it reaches a lookup.
_SYMBOL_RE = re.compile(r"^[A-Z0-9&\-]{1,20}(\.NS|\.BO)?$")


def _parse_symbols(raw: str) -> list[str]:
    names = [n.strip().upper() for n in re.split(r"[,\s]+", str(raw or "")) if n.strip()]
    names = [n for n in names if _SYMBOL_RE.match(n)]
    names = [n if n.endswith((".NS", ".BO")) else n + ".NS" for n in names]
    return list(dict.fromkeys(names))[:MAX_SANDBOX_SYMBOLS]


def _draft_from_form(form, live: dict) -> dict:
    """Numbers typed into the sandbox, falling back to the live value for a
    blank or unreadable box — never to a guess."""
    out: dict = {}
    for k in SANDBOX_KNOBS:
        raw = form.get(k["name"])
        try:
            out[k["name"]] = float(str(raw).strip()) if raw not in (None, "") else live[k["name"]]
        except (TypeError, ValueError):
            out[k["name"]] = live[k["name"]]
    out["symbols"] = " ".join(_parse_symbols(form.get("symbols", "")))
    out["ts"] = datetime.now().isoformat(timespec="seconds")
    return out


def _draft_cfg(cfg, draft: dict):
    """A COPY of the live Config with the draft's fields set. copy.copy is a
    shallow copy: scalar attributes set here never reach the live object
    (the lists it shares are not touched)."""
    c2 = copy.copy(cfg)
    c2.mode = "nifty200_momentum"
    c2.sizing_mode = "slab"
    c2.min_score_to_enter = float(draft["floor"])
    c2.quality_blend_pct = float(draft["blend_pct"]) / 100.0
    c2.position_slab_value = float(draft["slab"])
    c2.kelly_fraction = float(draft["kelly_m"])
    c2.stop_loss_pct = -abs(float(draft["stop_pct"])) / 100.0
    c2.breadth_block_low = float(draft["breadth_low"])
    c2.breadth_block_high = float(draft["breadth_high"])
    c2.budget_share_momentum = float(draft["share_momentum"]) / 100.0
    c2.budget_share_intraday = float(draft["share_intraday"]) / 100.0
    c2.budget_share_short = float(draft["share_short"]) / 100.0
    c2.budget_share_swing = float(draft["share_swing"]) / 100.0
    c2.budget_share_positional = float(draft["share_positional"]) / 100.0
    return c2


def _sandbox_readings(cfg, p, draft: dict) -> list[dict]:
    """What the DRAFT would say for each symbol, from STORED history only
    (web.candle_chart.load_ohlcv — zero network) through the PURE scoring,
    stop, target and sizing functions. No stored history = DATA_NEEDED."""
    from trading import targets as T
    from trading.quality import blend_scores, quality_score
    from trading.sizing import position_notional_for
    from trading.strategies.nifty200_momentum import momentum_score
    from ..candle_chart import load_ohlcv

    c2 = _draft_cfg(cfg, draft)
    live_blend = float(getattr(cfg, "quality_blend_pct", 0.0))
    # The live MOMENTUM floor (preset + owner override), the same number the
    # prefill shows — cfg.min_score_to_enter is whichever category the
    # config is switched to at this moment.
    live_floor = float(_live_draft(cfg)["floor"])
    equity = float(p.equity(_entry_prices(p)))
    lo, hi = T.TARGET_BAND.get("nifty200_momentum", (0.0, 1.0))
    out = []
    for t in _parse_symbols(draft.get("symbols", "")):
        row: dict[str, Any] = {"ticker": t, "status": "DATA_NEEDED", "why": ""}
        df = load_ohlcv(t, tail=420)
        if df is None or df.empty or "Close" not in df.columns:
            row["why"] = "no stored history for this name"
            out.append(row)
            continue
        close = df["Close"].dropna()
        ms = momentum_score(close)
        if ms is None:
            row["why"] = f"{len(close)} stored bars — momentum needs 252"
            out.append(row)
            continue
        vol = df["Volume"] if "Volume" in df.columns else None
        q = quality_score(close, vol)
        d_blend = blend_scores(ms, q, float(draft["blend_pct"]) / 100.0)
        l_blend = blend_scores(ms, q, live_blend)
        last = float(close.iloc[-1])
        asof = close.index[-1]
        stop_pct, sdiag = T.stop_pct_for(
            "nifty200_momentum", last, df, -abs(float(draft["stop_pct"])) / 100.0,
            enabled=True, k=float(draft["atr_stop_k"]),
            cap=float(draft["atr_stop_cap_pct"]) / 100.0)
        a = T.atr(df)
        if a is None:
            tgt_pct, tdiag = None, {"source": "no_atr"}
        else:
            raw = float(draft["target_k"]) * a / last
            tgt_pct = max(lo, min(hi, raw))
            tdiag = {"source": "atr", "raw_pct": round(raw * 100, 2),
                     "clamped": bool(abs(tgt_pct - raw) > 1e-12)}
        notional, ndiag = position_notional_for(c2, equity, d_blend, close)
        row.update({
            "status": "OK",
            "asof": asof.strftime("%d %b %Y") if hasattr(asof, "strftime") else str(asof),
            "bars": int(len(close)), "last": round(last, 2),
            "momentum": round(ms, 1), "quality": round(q, 1),
            "draft_blend": d_blend, "live_blend": l_blend,
            "draft_pass": bool(d_blend >= float(draft["floor"])),
            "live_pass": bool(l_blend >= live_floor),
            "stop_pct": round(abs(stop_pct) * 100, 2), "stop_source": sdiag.get("source"),
            "stop_price": round(last * (1 + stop_pct), 2),
            "atr": round(a, 2) if a else None,
            "atr_pct": round(a / last * 100, 2) if a else None,
            "target_pct": round(tgt_pct * 100, 2) if tgt_pct is not None else None,
            "target_price": round(last * (1 + tgt_pct), 2) if tgt_pct is not None else None,
            "target_source": tdiag.get("source"), "target_clamped": tdiag.get("clamped"),
            "notional": round(notional), "size_reason": ndiag.get("reason", ""),
            "kelly_pct": ndiag.get("kelly_pct"), "sigma_pct": ndiag.get("sigma_pct"),
        })
        out.append(row)
    return out


def _sandbox_book(cfg, p, draft: dict) -> dict:
    """The two book-level knobs, read against stored state: the breadth band
    verdict on the stored breadth reading (the engine's own rule: block while
    low <= breadth < high with a big enough sample), and each category's cash
    share in rupees on the book's equity at entry prices. Live beside draft;
    nothing is fetched and nothing is written."""
    from trading import category_budget as cb
    from trading.breadth import is_fresh
    c2 = _draft_cfg(cfg, draft)
    today = date.today()
    reading, source = None, ""
    stored = getattr(p, "breadth_store", None) or {}
    try:
        if is_fresh(stored, today, getattr(cfg, "breadth_store_max_age_days", 10)):
            reading = {"pct": float(stored["pct"]), "sample": int(stored["sample"])}
            source = f"full store, closes to {stored.get('asof')}"
    except Exception:
        reading = None
    if reading is None:
        cached = getattr(p, "breadth_today", None) or {}
        if cached.get("pct") is not None:
            reading = {"pct": float(cached["pct"]),
                       "sample": int(cached.get("sample") or 0)}
            source = (f"scan reading of {cached.get('asof')} "
                      f"({cached.get('mode') or 'scan'} pass)")

    def _blocks(c) -> Optional[bool]:
        if reading is None or not getattr(c, "breadth_gate_enabled", False):
            return None if reading is None else False
        if reading["sample"] < int(getattr(c, "breadth_min_sample", 30)):
            return False
        return bool(float(c.breadth_block_low) <= reading["pct"]
                    < float(c.breadth_block_high))
    equity = float(p.equity(_entry_prices(p)))
    positions = p.positions or {}
    live_rows = {r["category"]: r for r in cb.usage(cfg, positions, equity)}
    shares = []
    for r in cb.usage(c2, positions, equity):
        lv = live_rows.get(r["category"], {})
        shares.append({"label": r["label"], "used": r["used"],
                       "live_share_pct": _pct(lv.get("share")),
                       "live_limit": lv.get("limit"),
                       "draft_share_pct": _pct(r["share"]),
                       "draft_limit": r["limit"], "draft_left": r["left"]})
    return {"breadth": reading, "breadth_source": source,
            "breadth_pct": _pct(reading["pct"]) if reading else None,
            "live_blocks": _blocks(cfg), "draft_blocks": _blocks(c2),
            "shares": shares, "shares_on": bool(cb.enabled(cfg)),
            "draft_share_total_pct": _pct(cb.share_total(c2))}


def _sandbox(cfg, p, saved_flag: Optional[str]) -> dict:
    live = _live_draft(cfg)
    draft = load_draft()
    values = dict(live)
    for k in SANDBOX_KNOBS:
        if k["name"] in draft:
            values[k["name"]] = draft[k["name"]]
    values["symbols"] = " ".join(_parse_symbols(draft.get("symbols", "")))
    readings = _safe(_sandbox_readings, cfg, p, values, default=[]) if values["symbols"] else []
    diffs = [k["name"] for k in SANDBOX_KNOBS
             if abs(float(values[k["name"]]) - float(live[k["name"]])) > 1e-9]
    # 2026-10-15: the key is "vals", NOT "values" — in a Jinja template
    # `sbx.values` resolves to the dict's built-in .values METHOD, so every
    # draft box rendered empty and, once a breadth reading existed (after
    # any scan), `sbx.values.breadth_low` raised and the WHOLE Den returned
    # a 500 on the live box.
    return {"knobs": SANDBOX_KNOBS, "vals": values, "live": live,
            "draft_ts": draft.get("ts"), "has_draft": bool(draft),
            "changed": diffs, "readings": readings, "saved": saved_flag,
            "max_symbols": MAX_SANDBOX_SYMBOLS,
            "equity": float(p.equity(_entry_prices(p))),
            "measured_kelly_on": bool(getattr(cfg, "measured_kelly_enabled", False)),
            "book": _safe(_sandbox_book, cfg, p, values, default=None),
            "doc_id": DRAFT_DOC_ID}


# ---------------------------------------------------------------- routes

@router.get("/den", response_class=HTMLResponse)
def den_page(request: Request, draft: Optional[str] = None,
             val: Optional[str] = None, val_err: Optional[str] = None,
             chain: Optional[str] = None, terms: Optional[str] = None,
             fa: Optional[str] = None, fa_err: Optional[str] = None):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    from . import settings_routes
    from .. import valuation_chain_view, valuation_view
    cfg = state.cfg()
    p = state.portfolio()
    eng = _safe(_engines, cfg, p, default={}) or {}
    typed = (chain or "").strip()[:24]
    chain_ctx = _safe(valuation_chain_view.section, cfg, p, typed,
                      bool(terms), default=None)
    if chain_ctx is None:
        # the chain could not be read at all — a template-safe shell
        chain_ctx = {"rows": [], "n": 0, "max_rows": valuation_chain_view.MAX_ROWS,
                     "typed": typed, "typed_full": "", "show_terms": False,
                     "error": "the valuation chain could not be read — see the log",
                     "n_statements": 0, "assumed": {}, "today": "",
                     "row_ttl_min": int(valuation_chain_view.ROW_TTL_S // 60),
                     "budget_s": 0.0, "elapsed_s": 0.0, "computed": 0,
                     "cached": 0, "skipped_budget": 0, "skipped_busy": 0,
                     "busy": ""}
    ctx = {
        "user": user, "active": "den",
        "sections": SECTIONS,
        "seq": _safe(_sequence, default={}),
        "eng": eng,
        "wts": _safe(_weights, cfg, default={}),
        "trg": _safe(_triggers, p, default={}),
        "tim": _safe(_timing, cfg, p, eng.get("categories") or [], default={}),
        "cap": _safe(_capacity, cfg, default={}),
        "reg": _safe(_register, p, default={}),
        "evd": _safe(_evidence, p, default={}),
        "sbx": _safe(_sandbox, cfg, p, draft, default={}),
        "val": _safe(valuation_view.section, cfg, p, val, (val_err or "")[:120],
                     default=valuation_view.empty_section(
                         "invalid", "the context could not be read — see the log")),
        "chain": chain_ctx,
        "fml": _safe(valuation_chain_view.formulas_section, fa, (fa_err or "")[:200],
                     default=valuation_chain_view.empty_formulas_section(
                         "invalid", "the formulas could not be read — see the log")),
        "is_owner": user.get("role") == "owner",
        "db_status": state.db_status(),
    }
    # Section 9: the algo half of Settings, same form/action/field names.
    ctx.update(_safe(settings_routes.algo_context, default={}) or {})
    return templates.TemplateResponse(request, "den.html", ctx)


@router.post("/den/sandbox")
async def den_sandbox_save(request: Request):
    """Owner-only. Stores the DRAFT (Mongo settings doc "formula_drafts" +
    in-process copy) and returns to the sandbox. The live Config is never
    written — a draft is a draft."""
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user.get("role") != "owner":
        return RedirectResponse("/den?draft=denied#sandbox", status_code=303)
    form = await request.form()
    draft = _draft_from_form(form, _live_draft(state.cfg()))
    LAST_DRAFT.clear()
    LAST_DRAFT.update(draft)
    try:
        db.save_settings(dict(draft), doc_id=DRAFT_DOC_ID)
    except Exception:
        log.exception("den: draft save failed (in-memory copy kept)")
    return RedirectResponse("/den?draft=saved#sandbox", status_code=303)


@router.post("/den/valuation")
async def den_valuation_save(request: Request):
    """Owner-only (order 2026-10-12). Stores ONE standalone target with its
    date and basis, plus the assumptions, in settings doc
    "valuation_targets"; the GET reads the context for every stored
    target. If the typed valuation date lies before the stored sector
    series, the sector backfill runs now for the days THAT date needs
    (tokenless public candles, once per process per day per depth). The
    live Config is never written.

    2026-10-15: this route is async, so the blocking calls (the settings
    write and the backfill — a store read and up to 16 network calls) run
    in the threadpool; on the event loop they froze every request on the
    server while they ran."""
    from starlette.concurrency import run_in_threadpool

    from .. import valuation_view as vv
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user.get("role") != "owner":
        return RedirectResponse("/den?val=denied#valuation", status_code=303)
    form = await request.form()
    entry, assumptions, err = vv.parse_form(form)
    if entry is None:
        return RedirectResponse(f"/den?val=invalid&val_err={quote(err)}#valuation",
                                status_code=303)
    ok, err = await run_in_threadpool(vv.save_target, entry, assumptions)
    if not ok:
        return RedirectResponse(f"/den?val=invalid&val_err={quote(err)}#valuation",
                                status_code=303)
    try:
        await run_in_threadpool(vv.ensure_sector_depth, entry["asof"])
    except Exception:
        log.exception("den: sector backfill after a valuation save failed")
    return RedirectResponse("/den?val=saved#valuation", status_code=303)


@router.post("/den/valuation/assumptions")
async def den_assumptions_save(request: Request):
    """Owner-only (order 2026-10-14). Stores the assumption DRAFT (settings
    doc "valuation_assumptions" + in-process copy). §12 reads it on this
    page; the live Config and the engine never do. A single bad field
    refuses the whole form — nothing is stored."""
    from .. import valuation_chain_view as cv
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user.get("role") != "owner":
        return RedirectResponse("/den?fa=denied#formulas", status_code=303)
    form = await request.form()
    values, errors = cv.parse_assumptions_form(form)
    if errors:
        return RedirectResponse(f"/den?fa=invalid&fa_err={quote('; '.join(errors)[:400])}#formulas",
                                status_code=303)
    cv.save_assumptions(values)
    return RedirectResponse("/den?fa=saved#formulas", status_code=303)


@router.post("/den/valuation/assumptions/clear")
async def den_assumptions_clear(request: Request):
    """Owner-only: forget the assumption draft — every number back to its
    module default. Nothing in the live Config changes."""
    from .. import valuation_chain_view as cv
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user.get("role") != "owner":
        return RedirectResponse("/den?fa=denied#formulas", status_code=303)
    cv.clear_assumptions()
    return RedirectResponse("/den?fa=cleared#formulas", status_code=303)


@router.post("/den/valuation/clear")
async def den_valuation_clear(request: Request):
    """Owner-only: forget one stored target. Nothing else changes. The
    settings write runs in the threadpool (async route, blocking store)."""
    from starlette.concurrency import run_in_threadpool

    from .. import valuation_view as vv
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user.get("role") != "owner":
        return RedirectResponse("/den?val=denied#valuation", status_code=303)
    form = await request.form()
    ticker = vv.normalise_symbol(str(form.get("ticker") or ""))
    if ticker:
        await run_in_threadpool(vv.clear_target, ticker)
    return RedirectResponse("/den?val=cleared#valuation", status_code=303)


@router.post("/den/sandbox/clear")
async def den_sandbox_clear(request: Request):
    """Owner-only: forget the draft (the sandbox refills from the live
    numbers). Nothing in the live Config changes."""
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user.get("role") != "owner":
        return RedirectResponse("/den?draft=denied#sandbox", status_code=303)
    LAST_DRAFT.clear()
    try:
        keys = [k["name"] for k in SANDBOX_KNOBS] + ["symbols", "ts"]
        db.unset_settings_keys(keys, doc_id=DRAFT_DOC_ID)
    except Exception:
        log.exception("den: draft clear failed")
    return RedirectResponse("/den?draft=cleared#sandbox", status_code=303)
