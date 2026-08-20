"""Scan + refresh routes."""

from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .. import auth, state
from ..templating import templates


log = logging.getLogger(__name__)
router = APIRouter()

_SCAN_LOCK = threading.Lock()
_SCAN_RUNNING = False
_LAST_SCAN_ERROR: str | None = None


def _momentum_preset():
    from trading.modes import get_preset
    return get_preset("nifty200_momentum")


@router.get("/scan", response_class=HTMLResponse)
def scan_view(request: Request, error: str | None = None):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    p = state.portfolio()
    cfg = state.cfg()
    with _SCAN_LOCK:
        running = _SCAN_RUNNING
        last_err = _LAST_SCAN_ERROR

    from collections import Counter
    picks = p.last_scan_picks or []
    piano = _build_piano(p.scan_matrix or {})
    # The owner's funnel arithmetic (their design 2026-08-13: "in real life
    # capital amount is fixed once i use 60-70% then cant use as using
    # here") — how many of these picks today's cash can actually fund, by
    # the same math risk.can_open enforces. Display only; a broken compute
    # hides the card, never the page.
    slots = None
    try:
        from trading.slots import slot_view
        slots = slot_view(p, cfg)
    except Exception:
        log.exception("slot view failed")
    # F8: sector counts of the scanned picks
    sector_counts = Counter(cfg.sector_map.get(pk.get("ticker"), "Unknown")
                            for pk in picks)
    # F8: virtual shadow-book summary + rows (unbought YES picks marked to mkt)
    vbook = list((p.virtual_book or {}).values())
    vbook.sort(key=lambda v: -(v.get("pnl_pct") or 0))
    v_open = [v for v in vbook if v.get("status") == "open"]
    v_closed = [v for v in vbook if v.get("status") != "open"]
    v_summary = {
        "count": len(vbook),
        "open": len(v_open),
        "target_hits": sum(1 for v in v_closed if v.get("status") == "target"),
        "stop_hits": sum(1 for v in v_closed if v.get("status") == "stop"),
        "open_avg": round(sum(v.get("pnl_pct", 0) for v in v_open) / len(v_open), 2) if v_open else 0.0,
    }
    return templates.TemplateResponse(
        request, "scan.html",
        {
            "user": auth.current_user(request),
            "active": "scan",
            # AUDIT FIX B8: ticket placeholders derive from the real momentum
            # preset instead of a hardcoded number that rots (it said "5"
            # while the live target was +12).
            "preset_stop": round(abs(_momentum_preset().stop_loss_pct) * 100, 1),
            "preset_target": round(_momentum_preset().take_profit_pct * 100, 1),
            "picks": picks,
            "slots": slots,
            "custom_universe_n": len(getattr(cfg, "custom_universe", ()) or ()),
            "skipped": p.last_scan_skipped or [],
            "opened": p.last_scan_opened or [],
            "last_scan": p.last_scan,
            "running": running,
            "error": error or last_err,
            "sector_counts": sorted(sector_counts.items(), key=lambda kv: -kv[1]),
            "vbook": vbook[:40],
            "v_summary": v_summary,
            "piano": piano,
            "pending_orders": [o for o in (p.pending_orders or [])
                               if o.get("status") == "open"],
            "be_watch": _load_be_watch(),
        },
    )


# 🎹 Parameter Piano — turn the scan_matrix into grid sections. Every cell is
# a REAL computed check for one ticker; nothing is padded or invented.
_STUB_FACTORS = {"short_interest"}   # analyst_revision went live 2026-07-16
# Derived from trading/candles.py so a new structure can never be mis-styled
# here: gate-capable names colour the confirm lane, bearish names get the ⚠.
from trading import candles as _candles  # noqa: E402

_CANDLE_HIT = {"breakout", "urr"} | set(_candles._PATTERN_FUNCS)
_BEARISH_NAMES = set(_candles.BEARISH_PATTERNS)
_STRUCT_DEFS = {
    "breakout": "Closes above the last 20 days' high, in the upper half of the bar, on big volume. The engine's main entry trigger.",
    "urr": "Upthrust-Result-Retest: price pokes above resistance, pulls back, then holds on the retest. Confirms the level is real.",
    "engulfing": "A red bar completely swallowed by the next green bar. Buyers took control in one session.",
    "hammer": "Long lower shadow, tiny upper one. Sellers pushed down all day and failed — bottom reversal.",
    "morning_star": "Big red, then a small pause bar, then green closing above the red bar's middle. Three-day bottom turn.",
    "piercing": "Red bar, then green opening lower but closing past its midpoint. Buyers stepped in hard.",
    "tweezer_bottom": "Two bars bottoming at the same low — the floor held twice.",
    "three_soldiers": "Three strong green bars each closing higher. Sustained buying.",
    "inverted_hammer": "Long upper shadow after a fall — buyers tested higher. Needs next-day confirmation.",
    "bullish_harami": "Big red bar, then a small green body inside it. Selling pressure fading.",
    "bearish_engulfing": "⚠ A green bar swallowed by the next red bar. Sellers took control — top warning.",
    "evening_star": "⚠ Big green, pause, then red closing below the green's middle. Three-day top turn.",
    "dark_cloud_cover": "⚠ Green bar, then red opening above it and closing below its midpoint. Buyers trapped.",
    "tweezer_top": "⚠ Two bars topping at the same high — the ceiling held twice.",
    "three_black_crows": "⚠ Three strong red bars each closing lower. Sustained selling.",
    "shooting_star": "⚠ Long upper shadow, tiny lower one. Rally rejected at the top.",
    "doji": "Open and close almost equal — indecision. The trend is pausing, direction unclear.",
    "marubozu": "Body fills the whole bar, no shadows. One side in complete control that day.",
    "three_inside_up": "Harami (small green inside a big red) plus a third green closing above the red bar's open. Confirmed bottom turn.",
    "three_outside_up": "Bullish engulfing plus a third green bar closing higher still. The reversal followed through.",
    "three_line_strike": "Three falling red bars, then one green bar that takes back the whole decline in a day.",
    "abandoned_baby_bull": "Red bar, a doji gapping below it, then a green bar gapping back up. Sellers abandoned overnight — rare and strong.",
    "kicker_bull": "Red bar, then a green bar that opens ABOVE the red bar's open and never trades back into it. Sharpest one-bar turn.",
    "dragonfly_doji": "Doji with a long lower tail after a fall — sellers pushed all day and were fully rejected. Needs next-day confirmation.",
    "morning_doji_star": "Morning star whose pause bar is a true doji — even stronger indecision before the up-turn.",
    "three_inside_down": "⚠ Harami (small red inside a big green) plus a third red closing below the green bar's open. Confirmed top turn.",
    "three_outside_down": "⚠ Bearish engulfing plus a third red bar closing lower still. The breakdown followed through.",
    "abandoned_baby_bear": "⚠ Green bar, a doji gapping above it, then a red bar gapping back down. Buyers abandoned overnight.",
    "kicker_bear": "⚠ Green bar, then a red bar opening BELOW its open and never looking back. Sharpest one-bar top.",
    "gravestone_doji": "⚠ Doji with a long upper tail — the whole rally was sold back by the close.",
    "hanging_man": "⚠ Hammer shape after a RISE — the first crack in an uptrend.",
    "evening_doji_star": "⚠ Evening star whose pause bar is a true doji — stronger top signal.",
    "bearish_harami": "⚠ Big green bar, then a small red body inside it. Buying pressure fading.",
    "spinning_top": "Small body, long shadows both sides — a genuine coin-flip day. Neither side won.",
}


def _factor_cell(f: str, v, t: str) -> tuple[str, str]:
    """One (cls, tip) for a 0-100 factor value — shared by the piano grid
    and the per-parameter page so the two can never disagree."""
    if f in _STUB_FACTORS:
        return "waiting", f"{t} · {f} stub — no live feed"
    if v is None:
        return "waiting", f"{t} · {f} not computed"
    if v >= 60:
        return "hit", f"{t} · {f} {v:.0f}"
    if v >= 40:
        return "weak", f"{t} · {f} {v:.0f}"
    return "blocked", f"{t} · {f} {v:.0f}"


def _candle_cell(lbl: str, t: str) -> tuple[str, str]:
    if lbl in _CANDLE_HIT:
        return "hit", f"{t} · candle {lbl}"
    if lbl in ("disabled", "insufficient_bars", ""):
        return "waiting", f"{t} · candle {lbl or 'n/a'}"
    return "weak", f"{t} · candle {lbl}"   # no_pattern — waiting for confirmation


def _outcome_cell(oc: str, t: str) -> tuple[str, str]:
    if oc == "opened":
        return "hit", f"{t} · OPENED"
    if oc:
        return "blocked", f"{t} · gate: {oc}"
    return "waiting", f"{t} · scored, not selected"


def _build_piano(matrix: dict) -> dict | None:
    rows = matrix.get("rows") or {}
    if not rows:
        return None
    floor = float(matrix.get("score_floor", 55.0))
    factor_names: list[str] = []
    for r in rows.values():
        for k in (r.get("c") or {}):
            if k not in factor_names:
                factor_names.append(k)

    sections: list[dict] = []
    n = 0

    def _add(name: str, cells: list[dict], key: str = "") -> None:
        nonlocal n
        for c in cells:
            n += 1
            c["n"] = n
        hits = sum(1 for c in cells if c["cls"] == "hit")
        sections.append({"name": name, "key": key, "cells": cells, "hit": hits,
                         "total": len(cells)})

    tickers = list(rows.keys())
    for f in factor_names:
        cells = [dict(zip(("cls", "tip"),
                          _factor_cell(f, (rows[t].get("c") or {}).get(f), t)),
                      t=t)
                 for t in tickers]
        _add(f.replace("_", " "), cells, key=f)

    _add(f"score ≥ {floor:.0f}", [
        {"t": t, "cls": "hit" if rows[t].get("score", 0) >= floor else "blocked",
         "tip": f"{t} · total {rows[t].get('score', 0)}"} for t in tickers],
        key="score")
    _add("timing", [
        {"t": t, "cls": "hit" if rows[t].get("timing") else "blocked",
         "tip": f"{t} · timing {'pass' if rows[t].get('timing') else 'fail'}"}
        for t in tickers], key="timing")
    _add("absolute momentum (6m > 0)", [
        {"t": t, "cls": "hit" if rows[t].get("abs_mom") else "blocked",
         "tip": f"{t} · 6m return {'positive' if rows[t].get('abs_mom') else 'negative'}"}
        for t in tickers], key="abs_mom")

    candle_cells = []
    for t in tickers:
        cls, tip = _candle_cell(rows[t].get("candle") or "", t)
        candle_cells.append({"t": t, "cls": cls, "tip": tip})
    _add("candle confirm", candle_cells, key="candle")

    # Owner ask 2026-07-26: every candle structure its OWN lane, so a single
    # structure can be tapped and its pass/fail stock list read.
    struct_names = []
    for r in rows.values():
        for k in (r.get("structures") or {}):
            if k not in struct_names:
                struct_names.append(k)
    for name in struct_names:
        cells = []
        for t in tickers:
            fired = bool((rows[t].get("structures") or {}).get(name))
            bearish = name in _BEARISH_NAMES
            cls = ("blocked" if bearish else "hit") if fired else "waiting"
            cells.append({"t": t, "cls": cls,
                          "tip": f"{t} · {name.replace('_',' ')}: "
                                 f"{'YES' if fired else 'no'}"
                                 f"{' (bearish warning)' if bearish and fired else ''}"})
        _add(("⚠ " if name in _BEARISH_NAMES else "") + name.replace("_", " "),
             cells, key=f"struct:{name}")

    outcome_cells = []
    for t in tickers:
        cls, tip = _outcome_cell(rows[t].get("outcome") or "", t)
        outcome_cells.append({"t": t, "cls": cls, "tip": tip})
    _add("outcome", outcome_cells, key="outcome")

    total = sum(s["total"] for s in sections)
    hits = sum(s["hit"] for s in sections)
    return {"ts": matrix.get("ts"), "mode": matrix.get("mode"),
            "universe_size": matrix.get("universe_size"),
            "scanned": len(tickers), "sections": sections,
            "hits": hits, "total": total}


# Plain-language definition of every parameter — what it measures, the exact
# pass rule, and WHERE IN THE CODE to change it (the user's explicit ask:
# "so i can change in code if i need"). Unknown/new factor keys fall back to
# a generic entry so the page never breaks when a mode adds a factor.
_PARAM_DEFS: dict[str, dict] = {
    "momentum": {
        "title": "Momentum (6m + 12m, volatility-adjusted)",
        "what": ("Price strength over the last 6 and 12 months, divided by how "
                 "shaky the ride was, then ranked against every scanned stock. "
                 "The core buying signal of the momentum mode."),
        "rule": "6m and 12m return ÷ volatility, percentile-ranked, blended 50/50. 62+ = pass (the live floor).",
        "knob": "trading/strategies/nifty200_momentum.py (lookbacks) · blend vs quality: trading/config.py quality_blend_pct"},
    "quality": {
        "title": "Quality",
        "what": ("Business health — profitability, debt, earnings stability. "
                 "Blended into the momentum score so junk rallies rank lower."),
        "rule": "0–100 quality score, blended 35% with momentum. 62+ = pass (the live floor).",
        "knob": "trading/quality.py · blend weight: trading/config.py quality_blend_pct"},
    "momentum_1m": {
        "title": "1-month momentum",
        "what": "Short-term price push over ~21 trading days.",
        "rule": "0–100; 62+ = pass (the live floor), under 40 = blocked.",
        "knob": "trading/scoring.py score_momentum_1m · weight: trading/modes.py WEIGHTS"},
    "volume_expansion": {
        "title": "Volume expansion",
        "what": "Is trading volume swelling vs its own recent normal? Big moves need fuel.",
        "rule": "0–100 vs 20-day baseline; 62+ = pass (the live floor).",
        "knob": "trading/scoring.py score_volume_expansion (up-days only) · weight: trading/modes.py WEIGHTS"},
    "earnings_growth": {
        "title": "Earnings growth",
        "what": "Is the company's profit actually growing (latest reported numbers)?",
        "rule": "0–100 from reported growth; 62+ = pass (the live floor).",
        "knob": "trading/scoring.py score_earnings_growth · weight: trading/modes.py WEIGHTS"},
    "catalyst": {
        "title": "Catalyst (news)",
        "what": ("Live news sentiment for the stock, plus the FII money-flow "
                 "tide. Neutral 50 when no news feed key is set."),
        "rule": "0–100; 62+ = pass (the live floor).",
        "knob": "trading/signals/news.py + flows.py · weight: trading/modes.py WEIGHTS"},
    "relative_strength": {
        "title": "Relative strength vs Nifty",
        "what": "Is this stock beating the index itself? Leaders outrun the market.",
        "rule": "0–100 vs benchmark; 62+ = pass (the live floor).",
        "knob": "trading/scoring.py score_relative_strength · weight: trading/modes.py WEIGHTS"},
    "vol_adj_upside": {
        "title": "Volatility-adjusted upside",
        "what": "Recent gains per unit of wobble — smooth risers over wild swingers.",
        "rule": "0–100; 62+ = pass (the live floor).",
        "knob": "trading/scoring.py score_vol_adj_upside · weight: trading/modes.py WEIGHTS"},
    "short_interest": {
        "title": "Short interest (STUB — waiting)",
        "what": ("Would measure bets against the stock. NO free live source "
                 "(NSE blocks cloud servers) — so it sits at neutral 50 and is "
                 "clearly marked, never faked."),
        "rule": "Fixed 50 until a data feed exists. Shown grey, counts nowhere.",
        "knob": "trading/scoring.py (stub) — needs a data feed decision to go live"},
    "analyst_revision": {
        "title": "Analyst revisions",
        "what": "Are analysts raising or cutting their estimates for this stock?",
        "rule": "0–100; 62+ = pass (the live floor).",
        "knob": "trading/scoring.py _analyst_revision_score · weight: trading/modes.py WEIGHTS"},
    "breakout_quality": {
        "title": "Breakout quality",
        "what": "How clean is the price breakout — new high on real volume vs a messy poke.",
        "rule": "0–100; 62+ = pass (the live floor).",
        "knob": "trading/scoring.py score_breakout_quality · weight: trading/modes.py WEIGHTS"},
    "sector_alignment": {
        "title": "Sector alignment",
        "what": ("Is the stock's whole sector in favour? Includes the policy "
                 "boost (govt push) and hot-sector rotation bonus."),
        "rule": "0–100; 62+ = pass (the live floor).",
        "knob": "trading/scoring.py score_sector_alignment · weight: trading/modes.py WEIGHTS"},
    "score": {
        "title": "Total score vs floor",
        "what": ("All parameter scores weighted together into one 0–100 number. "
                 "Below the floor the stock cannot be bought, full stop."),
        "rule": "Weighted sum ≥ the mode's floor (shown live on this page).",
        "knob": "trading/config.py min_score_to_enter · per-mode: trading/modes.py PRESETS"},
    "timing": {
        "title": "Entry timing",
        "what": ("The mode's own 'is TODAY the day' check — e.g. price near "
                 "breakout, not overextended. A great stock on a bad day waits."),
        "rule": "Pass/fail per mode.",
        "knob": "trading/modes.py PRESETS (breakout_threshold_pct etc.)"},
    "abs_mom": {
        "title": "Absolute momentum (own 6m > 0)",
        "what": ("The stock's OWN 6-month return must be positive — never buy "
                 "a name in its own downtrend, however good it ranks. Earned "
                 "4/4 test windows in search v3."),
        "rule": "6-month return > 0, else blocked.",
        "knob": "trading/engine.py _abs_mom_ok"},
    "candle": {
        "title": "Candle confirmation",
        "what": ("Daily candlestick pattern must confirm the entry: breakout, "
                 "hammer, engulfing, morning star, piercing, tweezer bottom or "
                 "three soldiers. No pattern = wait (not a rejection)."),
        "rule": "One of the confirm patterns on the daily chart.",
        "knob": "trading/config.py candle_confirm_enabled · patterns: trading/candles.py"},
    "outcome": {
        "title": "Final outcome",
        "what": ("The end of the funnel for each stock this scan: OPENED, or "
                 "the exact gate that blocked it (cash, regime, sector cap…), "
                 "or scored-but-not-selected."),
        "rule": "Decision record — not a check.",
        "knob": "gates live across trading/engine.py scan()"},
}

_GENERIC_DEF = {
    "title": "",
    "what": "A 0–100 scored check computed at scan time for every stock.",
    "rule": "62+ = pass (the live floor), 40–59 = weak, under 40 = blocked.",
    "knob": "trading/scoring.py · weight: trading/modes.py WEIGHTS",
}


def _structure_page(request: Request, name: str, rows: dict, matrix: dict):
    """One candle structure: what it means + which stocks show it today."""
    bearish = name in _BEARISH_NAMES
    d = {"title": ("⚠ " if bearish else "") + name.replace("_", " ").title(),
         "what": _STRUCT_DEFS.get(name, "A candlestick structure read from the daily bars."),
         "rule": ("BEARISH — never triggers a buy; shown as a warning on the desk."
                  if bearish else
                  "BULLISH — can confirm an entry when the score and gates also pass."),
         "knob": "trading/candles.py · enable per-mode: trading/config.py candle_patterns"}
    groups = {"hit": [], "weak": [], "blocked": [], "waiting": []}
    for t, r in rows.items():
        fired = bool((r.get("structures") or {}).get(name))
        bucket = ("blocked" if bearish else "hit") if fired else "waiting"
        groups[bucket].append({"t": t, "label": "YES" if fired else "no",
                               "score": r.get("score", 0),
                               "outcome": r.get("outcome") or ""})
    for g in groups.values():
        g.sort(key=lambda c: -float(c["score"] or 0))
    return templates.TemplateResponse(
        request, "parameter.html",
        {"user": auth.current_user(request), "active": "scan",
         "key": name, "d": d, "groups": groups,
         "mode": matrix.get("mode"), "ts": matrix.get("ts"),
         "scanned": len(rows), "floor": float(matrix.get("score_floor", 55.0))})


@router.get("/scan/parameter/{key}")
def parameter_page(request: Request, key: str):
    """🎹 One piano note, in full: plain-language definition of the
    parameter + every stock it passed/weakened/blocked in the last scan,
    with values. The user's tap-a-parameter view.

    Auth added 2026-07-31: this page was reachable WITHOUT logging in and
    exposed the whole scan — every ticker, score and gate outcome."""
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    p = state.portfolio()
    matrix = p.scan_matrix or {}
    rows = matrix.get("rows") or {}
    floor = float(matrix.get("score_floor", 55.0))

    if key.startswith("struct:"):
        return _structure_page(request, key.split(":", 1)[1], rows, matrix)

    known_factors = set()
    for r in rows.values():
        known_factors.update((r.get("c") or {}).keys())
    specials = {"score", "timing", "abs_mom", "candle", "outcome"}
    if key not in specials and key not in known_factors and key not in _PARAM_DEFS:
        return RedirectResponse("/scan", status_code=303)

    d = dict(_PARAM_DEFS.get(key) or dict(_GENERIC_DEF,
                                          title=key.replace("_", " ").title()))
    if key == "score":
        d["rule"] = f"Weighted total must be ≥ {floor:.0f} (this mode's floor)."
    # live weight for factor keys, straight from the mode's recipe
    try:
        from trading.modes import WEIGHTS
        w = (WEIGHTS.get(matrix.get("mode")) or {}).get(key)
        if w is not None:
            d["rule"] += f" Weight in this mode: {w * 100:.0f}%."
    except Exception:
        pass

    groups: dict[str, list] = {"hit": [], "weak": [], "blocked": [], "waiting": []}
    for t, r in rows.items():
        if key in specials:
            if key == "score":
                cls = "hit" if r.get("score", 0) >= floor else "blocked"
                label = f"{r.get('score', 0):.0f}"
            elif key == "timing":
                cls = "hit" if r.get("timing") else "blocked"
                label = "pass" if r.get("timing") else "fail"
            elif key == "abs_mom":
                cls = "hit" if r.get("abs_mom") else "blocked"
                label = "6m +" if r.get("abs_mom") else "6m −"
            elif key == "candle":
                cls, tip = _candle_cell(r.get("candle") or "", t)
                label = (r.get("candle") or "n/a")
            else:  # outcome
                cls, tip = _outcome_cell(r.get("outcome") or "", t)
                label = r.get("outcome") or "not selected"
        else:
            v = (r.get("c") or {}).get(key)
            cls, _tip = _factor_cell(key, v, t)
            label = "stub" if key in _STUB_FACTORS else \
                ("—" if v is None else f"{v:.0f}")
        groups[cls].append({"t": t, "label": label,
                            "score": r.get("score", 0),
                            "outcome": r.get("outcome") or ""})
    for g in groups.values():
        g.sort(key=lambda c: -float(c["score"] or 0))

    return templates.TemplateResponse(
        request, "parameter.html",
        {"user": auth.current_user(request), "active": "scan",
         "key": key, "d": d, "groups": groups,
         "mode": matrix.get("mode"), "ts": matrix.get("ts"),
         "scanned": len(rows), "floor": floor})


# Lazy per-ticker candles for the detail row (positions + stale picks).
# Tiny TTL cache so 50 open DETAILS clicks don't re-hit Upstox.
_CNDL_CACHE: dict[str, tuple[float, list]] = {}
_CNDL_TTL = 900.0


@router.get("/candles/{ticker}.json")
def candles_json(request: Request, ticker: str):
    if not auth.current_user(request):
        return JSONResponse({"error": "auth"}, status_code=401)
    import time as _time
    now = _time.time()
    hit = _CNDL_CACHE.get(ticker)
    if hit and now - hit[0] < _CNDL_TTL:
        return JSONResponse({"ticker": ticker, "candles": hit[1]})
    candles: list = []
    try:
        from trading.data import fetch_daily
        df = fetch_daily([ticker], period="3mo").get(ticker)
        if df is not None and not df.empty:
            candles = [[round(float(a), 2) for a in row] for row in
                       df[["Open", "High", "Low", "Close"]].tail(20).values]
    except Exception as e:
        log.warning("candles fetch failed for %s: %s", ticker, e)
    if len(_CNDL_CACHE) > 300:
        _CNDL_CACHE.clear()
    _CNDL_CACHE[ticker] = (now, candles)
    return JSONResponse({"ticker": ticker, "candles": candles})


@router.get("/scan.csv")
def scan_csv(request: Request):
    """F8: download the current scan (picks + skipped + shadow book) as CSV."""
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    import csv
    import io
    from fastapi.responses import StreamingResponse
    p = state.portfolio()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["section", "ticker", "score", "last_close", "candle",
                "opened", "status", "entry_px", "last_px", "pnl_pct", "reason"])
    for pk in (p.last_scan_picks or []):
        w.writerow(["PICK", pk.get("ticker"), pk.get("score"),
                    pk.get("last_close"), pk.get("candle"), pk.get("opened"),
                    "", "", "", "", pk.get("reasoning", "")[:60]])
    for s in (p.last_scan_skipped or []):
        w.writerow(["SKIPPED", s.get("ticker"), "", "", "", "", "", "", "",
                    "", s.get("reason", "")])
    for v in (p.virtual_book or {}).values():
        w.writerow(["VIRTUAL", v.get("ticker"), v.get("score"), "", "", "",
                    v.get("status"), v.get("entry_px"), v.get("last_px"),
                    v.get("pnl_pct"), v.get("skip_reason", "")])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=scan.csv"},
    )


@router.post("/scan/run")
def scan_run(request: Request):
    """Kick scan in a background thread so HTTP returns immediately.
    Universe + yfinance fetch take 30-60s, well past Render's 100s gateway
    limit when combined with risk gates — async avoids any 502."""
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)

    global _SCAN_RUNNING, _LAST_SCAN_ERROR
    with _SCAN_LOCK:
        if _SCAN_RUNNING:
            return RedirectResponse("/scan?error=already_running", status_code=303)
        _SCAN_RUNNING = True
        _LAST_SCAN_ERROR = None

    def _run():
        global _SCAN_RUNNING, _LAST_SCAN_ERROR
        try:
            state.engine().scan()
            try:
                from trading.orders import process_open_orders
                process_open_orders(state.engine())
            except Exception:
                log.exception("Pending-order pass after manual scan failed")
        except Exception as e:
            log.exception("Scan failed: %s", e)
            with _SCAN_LOCK:
                _LAST_SCAN_ERROR = f"{type(e).__name__}: {str(e)[:120]}"
        finally:
            with _SCAN_LOCK:
                _SCAN_RUNNING = False

    threading.Thread(target=_run, daemon=True).start()
    return RedirectResponse("/scan?error=started", status_code=303)


_DL_LOCK = threading.Lock()
_DL_STATE = {"running": False, "done": 0, "total": 0, "last": "", "summary": None}


@router.post("/backtest/download-history")
def download_history_route(request: Request, years: int = 12):
    """Owner-only: bulk-pull `years` of daily OHLCV via the logged-in Upstox
    token and store to Mongo. The 15-year-proof unlock. Runs in background."""
    user = auth.current_user(request)
    if not user or user.get("role") != "owner":
        return RedirectResponse("/backtest?error=owner_only", status_code=303)
    with _DL_LOCK:
        if _DL_STATE["running"]:
            return RedirectResponse("/backtest?error=download_running", status_code=303)
        _DL_STATE.update(running=True, done=0, total=0, last="", summary=None)

    def _run():
        from datetime import date
        from .. import db as _db
        from trading.history_store import download_history, df_to_records
        from trading.data import _instrument_key
        from trading.universes import NIFTY_200_TICKERS
        try:
            eng = state.engine()
            broker = eng.broker
            if broker is None:
                with _DL_LOCK:
                    _DL_STATE.update(running=False,
                                     summary={"error": "Upstox not connected"})
                return
            keys = {}
            for t in NIFTY_200_TICKERS:
                k = _instrument_key(t)
                if k:
                    keys[t] = k

            def _save(ticker, df):
                _db.save_history_doc(df_to_records(ticker, df))

            def _prog(done, total, tkr):
                with _DL_LOCK:
                    _DL_STATE.update(done=done, total=total, last=tkr)

            years_i = max(1, min(int(years), 20))
            summ = download_history(broker, keys, years_i, date.today(),
                                    save_one=_save, progress=_prog)
            with _DL_LOCK:
                _DL_STATE.update(running=False, summary=summ)
        except Exception as e:
            log.exception("history download failed: %s", e)
            with _DL_LOCK:
                _DL_STATE.update(running=False, summary={"error": str(e)[:120]})

    threading.Thread(target=_run, daemon=True).start()
    return RedirectResponse("/backtest?error=download_started", status_code=303)


def _load_be_watch(limit: int = 15):
    """N1 shadow list: top BE/T2T movers by real 6m return (never traded
    by the engine; visibility only)."""
    import csv as _csv
    import os as _os
    path = _os.path.join(_os.path.dirname(__file__), "..", "..", "data",
                         "upstox_nse_be_watch.csv")
    try:
        with open(path, newline="") as fh:
            rows = [r for r in _csv.DictReader(fh) if r.get("r126_pct")]
        rows.sort(key=lambda r: -float(r["r126_pct"]))
        return rows[:limit]
    except (OSError, ValueError):
        return []


def _parse_pos_float(raw: str) -> float | None:
    """Lenient positive-number parse for ticket fields ('8', '-8', '+20',
    '₹450.5' -> 8 / 8 / 20 / 450.5). None for blank; ValueError if junk."""
    s = (raw or "").strip().replace("₹", "").replace("%", "").replace(",", "")
    if not s:
        return None
    v = abs(float(s))
    if v <= 0:
        raise ValueError("must be > 0")
    return v


@router.post("/scan/buy")
def owner_buy(request: Request, ticker: str = Form(...),
              qty: str = Form(""), order_type: str = Form("market"),
              limit_price: str = Form(""), stop_pct: str = Form(""),
              target_pct: str = Form("")):
    """Owner-only order ticket. Market: open NOW at the live Upstox price
    through the engine's own entry path. Limit: rest a day-validity order
    filled when the price touches. qty blank = engine auto-size; stop/
    target blank = mode preset (-12/+30 momentum). The owner's call
    overrides the regime entry-halt — journaled as exactly that. Paper
    book only, as always."""
    user = auth.current_user(request)
    if not user or user.get("role") != "owner":
        return RedirectResponse("/scan?error=owner_only", status_code=303)
    from datetime import datetime

    from trading.quote_router import fetch_quotes

    tkr = ticker.strip().upper()
    if not (tkr.endswith(".NS") or tkr.endswith(".BO")):
        tkr += ".NS"
    eng = state.engine()
    cfg = state.cfg()
    p = state.portfolio()
    if tkr in (getattr(cfg, "ticker_blocklist", ()) or ()):
        return RedirectResponse("/scan?error=blocklisted", status_code=303)
    if tkr in p.positions:
        return RedirectResponse("/scan?error=already_held", status_code=303)

    try:
        want_qty = _parse_pos_float(qty)
        stop = _parse_pos_float(stop_pct)
        target = _parse_pos_float(target_pct)
        limit = _parse_pos_float(limit_price)
    except ValueError:
        return RedirectResponse("/scan?error=bad_ticket", status_code=303)
    want_qty = int(want_qty) if want_qty else None

    if order_type == "limit":
        if not limit:
            return RedirectResponse("/scan?error=bad_ticket", status_code=303)
        from trading.orders import place_limit
        place_limit(p, ticker=tkr, limit_price=limit, qty=want_qty,
                    stop_pct=stop, target_pct=target)
        return RedirectResponse("/scan?error=order_placed", status_code=303)

    prices = fetch_quotes([tkr], upstox_client=eng.broker)
    price = prices.get(tkr)
    if not price or price <= 0:
        return RedirectResponse("/scan?error=no_quote", status_code=303)

    # The position must carry MOMENTUM identity (tag, -12/+30 snapshot)
    # even if the dashboard currently sits in another mode.
    orig_mode = getattr(cfg, "mode", "nifty200_momentum")
    try:
        cfg.mode = "nifty200_momentum"
        cfg.apply_mode_preset()
        marks = {pos.ticker: pos.entry_price for pos in p.positions.values()}
        equity = p.equity(marks)
        # AUDIT FIX A11 (2026-08-09): size through the ONE shared entry point
        # so manual buys can never silently diverge from engine sizing. With
        # no score/history passed it resolves to the owner's fixed rupee
        # amount under every sizing mode — a manual buy is the owner's own
        # number by design, and now by a single code path.
        from trading.sizing import position_notional_for
        notional, _diag = position_notional_for(cfg, equity)
        auto_qty = int(notional // float(price))
        use_qty = want_qty or auto_qty
        if use_qty < 1 or use_qty * float(price) > p.cash:
            return RedirectResponse("/scan?error=insufficient_cash", status_code=303)
        pick = next((dict(pk) for pk in (p.last_scan_picks or [])
                     if pk.get("ticker") == tkr), None) or \
            {"ticker": tkr, "score": 0.0, "components": {}, "timing_pass": True}
        pick["reasoning"] = ("OWNER BUY CALL — user pushed this YES pick into "
                            "the book (regime halt overridden by owner). "
                            + str(pick.get("reasoning", "")))[:220]
        eng._open(pick, use_qty, float(price), datetime.utcnow())
        # AUDIT FIX A4 (2026-08-09): an owner's deliberate pick is never
        # auto-sold by score decay (check_exit skips SCORE_DECAY for this
        # tag). Stop/target/trail/max-hold still protect it.
        p.positions[tkr].tags.append("owner_pick")
        from trading.orders import apply_custom_levels
        note = apply_custom_levels(p.positions[tkr], stop, target)
        if note:
            p.positions[tkr].reasoning += f" Custom levels: {note}."
        p.save()
    finally:
        cfg.mode = orig_mode
        cfg.apply_mode_preset()
    return RedirectResponse("/positions", status_code=303)


@router.post("/scan/order/cancel")
def owner_order_cancel(request: Request, order_id: str = Form(...)):
    user = auth.current_user(request)
    if not user or user.get("role") != "owner":
        return RedirectResponse("/scan?error=owner_only", status_code=303)
    from trading.orders import cancel
    cancel(state.portfolio(), order_id)
    return RedirectResponse("/scan?error=order_cancelled", status_code=303)


@router.get("/backtest/history-export.zip")
def history_export(request: Request):
    """Owner-only: stream the entire stored OHLCV history (Mongo `ohlcv`)
    as a zip of per-ticker CSVs — the research-side handoff for search v3.
    ~191 tickers x 12y ≈ 25 MB, built transiently (fits the 512 MB box)."""
    user = auth.current_user(request)
    if not user or user.get("role") != "owner":
        return RedirectResponse("/backtest?error=owner_only", status_code=303)
    import io
    import zipfile
    from fastapi.responses import StreamingResponse
    from .. import db as _db
    buf = io.BytesIO()
    n = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for m in _db.list_history_meta():
            doc = _db.load_history_doc(m.get("_id"))
            if not doc or not doc.get("candles"):
                continue
            lines = ["Date,Open,High,Low,Close,Volume"]
            lines += [",".join(str(x) for x in row) for row in doc["candles"]]
            z.writestr(f"{str(doc['_id']).replace('/', '_')}.csv",
                       "\n".join(lines))
            n += 1
        z.writestr("MANIFEST.txt",
                   f"tickers={n}\nsource=Mongo ohlcv (Upstox bulk download)\n")
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="application/zip",
        headers={"Content-Disposition":
                 "attachment; filename=sourceborn_history_export.zip"})


@router.get("/backtest/download-status")
def download_status(request: Request):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    with _DL_LOCK:
        st = dict(_DL_STATE)
    from .. import db as _db
    meta = _db.list_history_meta()
    st["stored_tickers"] = len(meta)
    st["stored_sample"] = [{"ticker": m.get("_id"), "n": m.get("n"),
                            "start": m.get("start"), "end": m.get("end")}
                           for m in meta[:8]]
    return JSONResponse(st)


@router.get("/proof")
def proof_ledger(request: Request):
    """C3: read-only proof-gap ledger — the app's honest 'what's unproven' status."""
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    from trading.proof import proof_gap_ledger
    return JSONResponse({"proof_gap_ledger": proof_gap_ledger()})


@router.post("/refresh")
def refresh(request: Request):
    if not auth.current_user(request):
        return RedirectResponse("/login", status_code=303)
    eng = state.engine()
    try:
        eng.refresh()
    except Exception as e:
        return RedirectResponse(f"/?error={type(e).__name__}", status_code=303)
    return RedirectResponse("/", status_code=303)
