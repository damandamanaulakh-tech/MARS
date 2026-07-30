"""Dashboard market panels (owner ask 2026-07-30, from 5 reference mockups:
"make some changes in the dashboard, adding few option u can adopt some of
each kind").

Every panel here is backed by data the app ALREADY HAS — the committed index
history, the engine's own scan matrix, the curated event calendars, the
committed walk-forward results, the mode presets, and stored OHLCV. Nothing
is scraped, nothing is invented: a panel with no honest source returns None
and the template hides it or says DATA_NEEDED.

Pure functions, no network, bounded work (512 MB Render box).
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_DATA = Path(__file__).resolve().parents[1] / "data"
_DOCS = Path(__file__).resolve().parents[1] / "docs"
_INDEX_CSV = _DATA / "index_daily_15y.csv"

# (label, close column, is_vix) — the strip every reference dashboard opens with
_INDICES = [
    ("NIFTY 50", "nifty_close", False),
    ("SENSEX", "sensex_close", False),
    ("NIFTY BANK", "bank_nifty_close", False),
    ("INDIA VIX", "india_vix_close", True),
]


def index_strip(bars: int = 60) -> list[dict]:
    """Index cards with sparkline + change, from the committed 15-year index
    CSV. Zero network — so it is honest about being stored data, labelled with
    its own as-of date rather than pretending to be live."""
    try:
        import pandas as pd
        cols = ["trade_date"] + [c for _, c, _ in _INDICES]
        df = pd.read_csv(_INDEX_CSV, usecols=cols, parse_dates=["trade_date"],
                         dayfirst=True).dropna(subset=["trade_date"])
        df = df.sort_values("trade_date").tail(bars + 1)
    except Exception as e:
        log.warning("index_strip unavailable: %s", e)
        return []
    if df.empty:
        return []
    asof = df["trade_date"].iloc[-1]
    out = []
    for label, col, is_vix in _INDICES:
        s = df[col].dropna()
        if len(s) < 2:
            continue
        last, prev = float(s.iloc[-1]), float(s.iloc[-2])
        chg = last - prev
        pct = (chg / prev * 100) if prev else 0.0
        vals = [round(float(v), 2) for v in s.tail(bars)]
        lo, hi = min(vals), max(vals)
        rng = (hi - lo) or 1.0
        # sparkline as a normalised polyline (0..100 x, 0..30 y, y flipped)
        n = len(vals)
        pts = " ".join(
            f"{i / max(n - 1, 1) * 100:.1f},{30 - (v - lo) / rng * 30:.1f}"
            for i, v in enumerate(vals))
        out.append({
            "label": label, "last": last, "chg": chg, "pct": round(pct, 2),
            "up": chg >= 0, "invert": is_vix,       # VIX up = risk up = red
            "points": pts, "high": hi, "low": lo,
            "asof": asof.strftime("%d %b %Y"),
        })
    return out


def breadth_panel() -> Optional[dict]:
    """Market breadth from the engine's OWN scan panel — this is the number
    the A3 breadth gate actually uses to allow or block new buys, so it is
    the most decision-relevant panel on the page."""
    from . import state
    p = state.portfolio()
    rows = ((p.scan_matrix or {}).get("rows") or {})
    if not rows:
        return None
    cfg = state.cfg()
    floor = float((p.scan_matrix or {}).get("score_floor", cfg.min_score_to_enter))
    scores = []
    strong = healthy = neutral = weak = 0
    passing = timing_ok = abs_ok = 0
    for r in rows.values():
        s = float(r.get("score") or 0.0)
        scores.append(s)
        if s >= 70:
            strong += 1
        elif s >= 55:
            healthy += 1
        elif s >= 40:
            neutral += 1
        else:
            weak += 1
        if s >= floor:
            passing += 1
        if r.get("timing"):
            timing_ok += 1
        if r.get("abs_mom"):
            abs_ok += 1
    n = len(scores)
    # sector spread of the scanned panel (real sector map, real scores)
    from collections import defaultdict
    buckets: dict[str, list] = defaultdict(list)
    for t, r in rows.items():
        buckets[cfg.sector_map.get(t, "Unknown")].append(float(r.get("score") or 0.0))
    sectors = sorted(
        ({"name": k, "n": len(v), "avg": round(sum(v) / len(v), 1)}
         for k, v in buckets.items() if k != "Unknown" and len(v) >= 2),
        key=lambda d: -d["avg"])[:8]
    return {
        "scanned": n,
        "strong": strong, "healthy": healthy, "neutral": neutral, "weak": weak,
        "passing": passing,
        "pass_pct": round(passing / n * 100, 1) if n else 0.0,
        "abs_mom_pct": round(abs_ok / n * 100, 1) if n else 0.0,
        "timing_pct": round(timing_ok / n * 100, 1) if n else 0.0,
        "avg_score": round(sum(scores) / n, 1) if n else 0.0,
        "floor": floor,
        "sectors": sectors,
        "gate_on": bool(getattr(cfg, "breadth_gate_enabled", False)),
        "gate_min_pct": round(float(getattr(cfg, "breadth_min_pct", 0.5)) * 100),
        "ts": (p.scan_matrix or {}).get("ts"),
    }


def events_panel(days: int = 45, limit: int = 6) -> list[dict]:
    """Next market-moving dates from the curated calendars (RBI MPC, Budget,
    elections, expiries, festivals). The engine already halts entries before
    system events — this just makes them visible."""
    try:
        from trading.calendars import upcoming_events
        evs = upcoming_events(lookahead_days=days) or []
    except Exception as e:
        log.warning("events_panel unavailable: %s", e)
        return []
    today = date.today()
    out = []
    for e in evs:
        try:
            d = e.event_date
            out.append({
                "type": str(e.event_type).replace("_", " "),
                "desc": e.description,
                "date": d.strftime("%d %b"),
                "days": (d - today).days,
                "system": str(e.impact) == "system_wide",
            })
        except Exception:
            continue
    out.sort(key=lambda r: r["days"])
    return out[:limit]


def evidence_panel() -> Optional[dict]:
    """The committed walk-forward result — the honest proof panel. Reads
    docs/param_search_v3_results.json (191 real tickers, 4 out-of-sample
    windows). These are the app's own preregistered numbers, not marketing."""
    try:
        d = json.loads((_DOCS / "param_search_v3_results.json").read_text())
        wins = d.get("windows") or []
        if not wins:
            return None
        rows, alphas = [], []
        for w in wins:
            val = w.get("validation") or {}
            bench = w.get("bench_universe") or {}
            a = w.get("oos_alpha")
            if a is None:
                continue
            alphas.append(float(a))
            rows.append({
                "window": str(w.get("window", "?")).replace("_", " "),
                "cagr": val.get("cagr"),
                "bench": bench.get("cagr"),
                "dd": val.get("max_dd"),
                "sharpe": val.get("sharpe"),
                "alpha": round(float(a), 2),
            })
        if not rows:
            return None
        wins_n = sum(1 for a in alphas if a > 0)
        return {
            "rows": rows,
            "windows": len(rows),
            "wins": wins_n,
            "avg_alpha": round(sum(alphas) / len(alphas), 2),
            "grid": d.get("grid_size"),
        }
    except Exception as e:
        log.warning("evidence_panel unavailable: %s", e)
        return None


def horizons_panel() -> list[dict]:
    """The four trade horizons with their REAL exit presets, plus which one
    is live right now. Adopted from the reference dashboards' "4 Horizons"."""
    try:
        from trading.modes import PRESETS
        from . import state
        active = getattr(state.cfg(), "mode", "")
    except Exception:
        return []
    order = ["nifty200_momentum", "short", "swing", "positional", "intraday"]
    labels = {
        "nifty200_momentum": ("Momentum", "N200 momentum — the live engine"),
        "short": ("Short", "1–7 day holds"),
        "swing": ("Swing", "1–8 week holds"),
        "positional": ("Positional", "1–6 month holds"),
        "intraday": ("Intraday", "manual only — out of the daily rotation"),
    }
    out = []
    for key in order:
        pr = PRESETS.get(key)
        if pr is None:
            continue
        name, blurb = labels.get(key, (key, ""))
        out.append({
            "key": key, "name": name, "blurb": blurb,
            "stop": round(pr.stop_loss_pct * 100, 1),
            "target": round(pr.take_profit_pct * 100, 1),
            "trail": round(pr.trailing_stop_pct * 100, 1),
            "hold": pr.max_hold_days,
            "floor": pr.min_score_to_enter,
            "active": key == active,
        })
    return out


def atr_panel(tickers: list[str], limit: int = 4) -> list[dict]:
    """ATR-based stop context for held names (mockups' "ATR Stop & Position
    Size"). Computed from STORED OHLCV — shows what a volatility-scaled stop
    would be next to the owner's fixed -5%, so the two are comparable.
    Display only: the live engine still uses the fixed stop."""
    from . import state
    from .candle_chart import load_ohlcv
    try:
        from trading.lens_v2 import atr as _atr
    except Exception:
        return []
    cfg = state.cfg()
    stop_pct = abs(float(cfg.stop_loss_pct))
    out = []
    for t in (tickers or [])[:limit]:
        df = load_ohlcv(t, tail=90)
        if df is None or df.empty:
            continue
        a = _atr(df)
        try:
            last = float(df["Close"].dropna().iloc[-1])
        except Exception:
            continue
        if a <= 0 or last <= 0:
            continue
        out.append({
            "ticker": t,
            "last": round(last, 2),
            "atr": round(a, 2),
            "atr_pct": round(a / last * 100, 2),
            "fixed_stop": round(last * (1 - stop_pct), 2),
            "fixed_stop_pct": round(stop_pct * 100, 1),
            "atr_stop": round(last - 1.5 * a, 2),
            "atr_stop_pct": round(1.5 * a / last * 100, 2),
            "tighter": (1.5 * a / last) < stop_pct,
        })
    return out
