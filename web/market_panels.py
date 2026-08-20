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

# (label, stored close column, is_vix, live symbol)
_INDICES = [
    ("NIFTY 50", "nifty_close", False, "^NSEI"),
    ("SENSEX", "sensex_close", False, "^BSESN"),
    ("NIFTY BANK", "bank_nifty_close", False, "^NSEBANK"),
    ("INDIA VIX", "india_vix_close", True, "^INDIAVIX"),
]


def index_strip(bars: int = 60) -> list[dict]:
    """LIVE index cards (Upstox), with the stored series only as a background
    sparkline.

    Owner caught this on 2026-07-31: the strip was printing the committed
    CSV's last close — 05 Jun 2026 — as though it were today's market
    (Sensex 74,243 on screen while the real number was 78,196). A small
    "stored to" caption does not undo a big wrong number at the top of a
    live dashboard.

    So the rule now: the headline number is LIVE or it is not shown. When
    the live quote is missing the card renders `live=False` and the template
    prints "—" plus a reconnect prompt. Stale closes are never dressed up as
    current prices.
    """
    live: dict[str, float] = {}
    prev_close: dict[str, float] = {}
    try:
        from trading.quote_router import fetch_quotes
        from . import state
        eng = state.engine()
        broker = getattr(eng, "broker", None)
        symbols = [sym for _l, _c, _v, sym in _INDICES]
        if broker is not None and broker.is_connected():
            from trading.brokers.upstox_instruments import INDEX_KEYS
            for sym in symbols:
                key = INDEX_KEYS.get(sym)
                if not key:
                    continue
                q = broker.get_quote(key)
                if not isinstance(q, dict):
                    continue
                lp = q.get("last_price")
                ohlc = q.get("ohlc") if isinstance(q.get("ohlc"), dict) else {}
                pc = ohlc.get("close")
                if lp:
                    live[sym] = float(lp)
                if pc:
                    prev_close[sym] = float(pc)
        else:
            live = {k: v for k, v in fetch_quotes(symbols).items() if v}
    except Exception as e:                     # never take the page down
        log.warning("index_strip live fetch failed: %s", e)

    hist = {}
    asof = None
    try:
        import pandas as pd
        cols = ["trade_date"] + [c for _l, c, _v, _s in _INDICES]
        df = pd.read_csv(_INDEX_CSV, usecols=cols, parse_dates=["trade_date"],
                         dayfirst=True).dropna(subset=["trade_date"])
        df = df.sort_values("trade_date").tail(bars + 1)
        if not df.empty:
            asof = df["trade_date"].iloc[-1]
            hist = {c: df[c].dropna() for _l, c, _v, _s in _INDICES}
    except Exception as e:
        log.warning("index_strip history unavailable: %s", e)

    out = []
    for label, col, is_vix, sym in _INDICES:
        s = hist.get(col)
        last = live.get(sym)
        is_live = last is not None
        # The day change is shown ONLY against a real previous close from the
        # live feed. Differencing a live price against a two-month-old stored
        # close produces a nonsense "day move" (+4.4% etc.) — the same class of
        # error the owner caught. No true previous close => no change shown.
        prev = prev_close.get(sym)
        chg = (last - prev) if (is_live and prev) else None
        pct = (chg / prev * 100) if (chg is not None and prev) else None

        pts, lo, hi = "", None, None
        if s is not None and len(s) >= 2:
            vals = [round(float(v), 2) for v in s.tail(bars)]
            lo, hi = min(vals), max(vals)
            rng = (hi - lo) or 1.0
            n = len(vals)
            pts = " ".join(
                f"{i / max(n - 1, 1) * 100:.1f},{30 - (v - lo) / rng * 30:.1f}"
                for i, v in enumerate(vals))

        out.append({
            "label": label,
            "live": is_live,
            "last": last,                       # None => template shows "—"
            "chg": chg, "pct": round(pct, 2) if pct is not None else None,
            "up": bool(chg is not None and chg >= 0),
            "invert": is_vix,                   # VIX up = risk up = red
            "points": pts, "high": hi, "low": lo,
            "hist_asof": asof.strftime("%d %b %Y") if asof is not None else None,
        })
    return out


def breadth_panel() -> Optional[dict]:
    """Market breadth from the engine's scan panel, plus (since the F3 fix,
    2026-08-20) the FULL-STORE 200DMA reading the A3 gate now prefers —
    ~2,700 names, ~±1pp noise, refreshed daily before the 09:20 scan. The
    bucket numbers stay on the panel (score bands, gate-pass rates are
    per-scan facts) but the gate's own number is the store reading while
    fresh, and the panel says which is which."""
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
        # F3: the full-store reading the gate prefers while fresh. None when
        # never computed — the template then shows only the bucket numbers.
        "store": (p.breadth_store or None),
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
            closes = df["Close"].dropna()
            last = float(closes.iloc[-1])
            bar_date = closes.index[-1]
        except Exception:
            continue
        if a <= 0 or last <= 0:
            continue
        out.append({
            "ticker": t,
            # This is the last STORED close, not a live tick. It is dated on
            # screen — the 2026-07-31 index-strip lesson: an undated price
            # beside live-looking levels reads as current.
            "asof": bar_date.strftime("%d %b") if hasattr(bar_date, "strftime") else "",
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
