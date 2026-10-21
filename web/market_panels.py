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


def safe_panel(fn, *args):
    """A broken panel must never take a page down with it.

    One wrapper for every page that renders a panel — the dashboard and,
    since the owner's 2026-10-12 order moved the sector strip and the scan
    coverage card there, /scan as well. A panel that raises hides itself;
    the page still renders.
    """
    try:
        return fn(*args)
    except Exception:                       # pragma: no cover - safety net
        log.warning("panel %s failed", getattr(fn, "__name__", fn))
        return None


# Owner order 2026-10-12 (item 43): international indices on the index strip.
# NO feed is reachable from the box — Yahoo is blocked on Render and stooq
# returned nothing from the sandbox on 2026-10-12 — so the strip carries this
# chip and never a number. Replace it with a real card only when a feed has
# actually answered from the live box.
INTERNATIONAL_NOTE = "International: DATA_NEEDED — no reachable free feed yet"

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
    delayed_syms: set = set()
    connected = False
    try:
        from trading.quote_router import fetch_quotes
        from . import state
        eng = state.engine()
        broker = getattr(eng, "broker", None)
        symbols = [sym for _l, _c, _v, sym in _INDICES]
        # "connected" = a token the router calls healthy (present AND not
        # past its 03:30 IST expiry) — the same verdict the red bar uses, so
        # a card and the bar can never disagree about the login.
        connected = bool(state.quote_source_status().get("healthy"))
        if connected and broker is not None:
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
        # No login, an expired token, OR a connected token Upstox refused a
        # tick for (rate limit, outage): the PUBLIC ~1-minute-delayed
        # intraday candle carries the number, labelled delayed on the card
        # (owner complaint 2026-08-26 "you r like far away"). 2026-09-24:
        # the connected path used to have NO fallback — a refused tick left
        # the card blank with a "Reconnect Upstox" link, and the owner
        # reconnected in a loop while the token was fine the whole time.
        missing = [s for s in symbols if s not in live]
        if missing:
            delayed = {k: v for k, v in fetch_quotes(missing).items() if v}
            from trading.quote_router import LAST_SOURCES
            delayed_syms = {k for k in delayed
                            if LAST_SOURCES.get(k) == "upstox_1min_delayed"}
            live.update(delayed)
    except Exception as e:                     # never take the page down
        log.warning("index_strip live fetch failed: %s", e)

    hist = {}
    asof = None
    try:
        import pandas as pd
        cols = ["trade_date"] + [c for _l, c, _v, _s in _INDICES]
        df = pd.read_csv(_INDEX_CSV, usecols=cols, parse_dates=["trade_date"],
                         dayfirst=True).dropna(subset=["trade_date"])
        # Node 3: sparkline background extends past the committed CSV with
        # the weekly stored tail (headline number stays LIVE-or-dash).
        from trading import data_refresh as _dr
        df, _ = _dr.extend_index_df(df)
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
            # ~1-minute-delayed public candle, not a tick — card says so
            "delayed": sym in delayed_syms,
            # the login is fine; the card must never send the owner back to
            # the Upstox login when the token is valid
            "connected": connected,
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
        # Re-cut 2026-09-09: the gate blocks a measured dead BAND, not
        # everything under one line. Both edges go to the page so the copy
        # cannot drift from the rule.
        "gate_band_low": round(float(getattr(cfg, "breadth_block_low", 0.40)) * 100),
        "gate_band_high": round(float(getattr(cfg, "breadth_block_high", 0.65)) * 100),
        # The stored reading the gate prefers while fresh — since 2026-10-16
        # the study panel the band was measured on (`population`), with the
        # whole store as context. None when never computed — the template
        # then shows only the bucket numbers.
        "store": (p.breadth_store or None),
        # The SAME test engine._breadth_gate_blocks applies (population
        # present + is_fresh on the engine's own UTC day), so the card can
        # never present a reading as "the number the gate reads" on a day
        # the gate has set it aside for the scan's own reading.
        "store_used": _store_used(p.breadth_store, cfg),
        "store_max_age": int(getattr(cfg, "breadth_store_max_age_days", 10)),
        "ts": (p.scan_matrix or {}).get("ts"),
    }


def _store_used(stored, cfg) -> bool:
    from datetime import datetime as _dt

    from trading.breadth import is_fresh
    stored = stored or {}
    try:
        return bool(stored.get("population")) and is_fresh(
            stored, _dt.utcnow().date(),
            getattr(cfg, "breadth_store_max_age_days", 10))
    except Exception:
        return False


def sector_panel(limit: int = 16) -> dict:
    """Real NSE sector index moves, worst first (owner order 2026-09-09).

    Reads ONLY the stored rows the daily job writes — no network on a page
    render. Empty store = {} so the card hides rather than inventing a flat
    market. Display only: the calibrated sector tripwires keep their own
    equal-weight construction.
    """
    try:
        from trading.data_refresh import sector_moves

        from . import db
        rows = sector_moves(db.load_sector_rows())
    except Exception as e:
        log.warning("sector_panel unavailable: %s", e)
        return {}
    if not rows:
        return {}
    asof = max((r["asof"] for r in rows), default="")
    return {"rows": rows[:limit], "asof": asof, "n": len(rows),
            "worst": rows[0], "best": rows[-1]}


def coverage_panel() -> dict:
    """Sector-by-sector: how much of the tradeable universe the app actually
    READ today. Owner ask 2026-09-10, their words: "what i felt system is not
    reading all 2400 stocks every time, that is the flaw ... we can do sector
    wise and on dashboard after doing each sector's each share it show show
    data".

    The honest answer to "is it reading everything" is a count, not a
    reassurance. Three numbers per sector:
      universe  — names on the current scan list
      scanned   — names that actually got a score in today's scan matrix
      passing   — names that cleared the live entry floor

    WHY THE GAP IS USUALLY REAL AND NOT A BUG: the scan list is rebuilt
    weekly as the top N by median rupee turnover from the names the Mongo
    OHLCV store holds, and that store is still filling (573 of 2,648 at the
    2026-08-29 check). A name with no stored history cannot be scored. The
    panel says so with a number instead of hiding it.

    Reads stored state only — no network on a page render. Returns {} when
    no scan has run, so the card hides rather than showing a row of zeros.
    """
    try:
        from . import state
        cfg, p = state.cfg(), state.portfolio()
        matrix = getattr(p, "scan_matrix", None) or {}
        rows = matrix.get("rows") or {}
        if not rows:
            return {}
        universe = list(getattr(cfg, "custom_universe", ()) or ())
        if not universe:
            # Rotating-bucket mode: the day's bucket IS the universe, so the
            # only honest denominator is what was scanned.
            universe = list(rows.keys())
        floor = float(getattr(cfg, "min_score_to_enter", 62.0))
        sector_of = getattr(cfg, "sector_map", {}) or {}

        buckets: dict = {}
        for t in universe:
            sec = sector_of.get(t, "Unknown")
            b = buckets.setdefault(sec, {"sector": sec, "universe": 0,
                                         "scanned": 0, "passing": 0})
            b["universe"] += 1
            row = rows.get(t)
            if not row:
                continue
            b["scanned"] += 1
            try:
                if float(row.get("score")) >= floor:
                    b["passing"] += 1
            except (TypeError, ValueError):
                pass
        out = sorted(buckets.values(), key=lambda r: (-r["universe"], r["sector"]))
        for r in out:
            r["pct"] = round(r["scanned"] / r["universe"] * 100, 1) if r["universe"] else 0.0
        tot_u = sum(r["universe"] for r in out)
        tot_s = sum(r["scanned"] for r in out)
        return {
            "rows": out,
            "universe": tot_u,
            "scanned": tot_s,
            "passing": sum(r["passing"] for r in out),
            "pct": round(tot_s / tot_u * 100, 1) if tot_u else 0.0,
            "target": int(getattr(cfg, "universe_target_size", 0) or 0),
            "floor": floor,
            "ts": matrix.get("ts", ""),
            # Named so the page can say WHY the list is short of target
            # instead of leaving the owner to guess.
            "short_by": max(0, int(getattr(cfg, "universe_target_size", 0) or 0)
                            - tot_u),
        }
    except Exception as e:
        log.warning("coverage_panel unavailable: %s", e)
        return {}


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
            # AUDIT FIX D3 (2026-08-09), carried WITH the data since the
            # panel left the dashboard (owner order 2026-10-12): these
            # numbers were computed 2026-07-21 under the floor-55 / −12/+30
            # rules of that date — evidence of the recipe's era, not a
            # forecast of the live settings. Whoever renders the rows must
            # print this beside them.
            "era_note": ("computed 2026-07-21 (floor-55 era) — the live rules "
                         "(floor 62, −5/+20) have moved since"),
        }
    except Exception as e:
        log.warning("evidence_panel unavailable: %s", e)
        return None


def hold_words(days: int, square_off: Optional[str] = None) -> str:
    """A category's max hold in plain words, as the exit check counts it.

    exits.check_exit compares the entry DATE with today's date, so the
    preset's max_hold_days are CALENDAR days (weekends included), not
    trading sessions. Shared by the Settings mode cards and the Trade
    Horizons card so the two can never disagree."""
    d = int(days or 0)
    if d <= 1:
        return (f"same day — squared off at {square_off} IST"
                if square_off else "same day")
    if d < 60:
        return f"up to {d} calendar days (~{max(1, round(d / 7))} weeks)"
    return f"up to {d} calendar days (~{max(1, round(d / 30.4))} months)"


def target_at_median_atr(mode: str) -> Optional[float]:
    """The k x ATR target (in %) a new trade of this category gets when its
    14-day ATR is the measured median (2.50% of price) — the same figure the
    Den's category table prints (k x MEDIAN_ATR_PCT_OF_PRICE), kept inside
    the category's band exactly as targets.target_pct_for does. None for a
    category with no k (its preset percentage is then the target)."""
    try:
        from trading import targets as T
    except Exception:
        return None
    k = T.ATR_TARGET_K.get(mode)
    if not k:
        return None
    lo, hi = T.TARGET_BAND.get(mode, (0.0, 1.0))
    pct = k * T.MEDIAN_ATR_PCT_OF_PRICE
    return round(max(lo * 100, min(hi * 100, pct)), 1)


def horizons_panel() -> list[dict]:
    """The five trade categories with their REAL exit presets, plus which one
    is live right now. Adopted from the reference dashboards' "4 Horizons".

    Truth pass 2026-10-15: the hold blurbs are read from each preset's
    max_hold_days (they had drifted from the 2026-09-10 horizon remap), all
    five categories run in the 09:20 and 14:30 rotation (intraday too), and
    "target" is what a new trade really gets — k x its own ATR, shown at the
    median ATR — not the preset percentage, which is only the fallback when
    the ATR cannot be computed (kept as `fixed_target`)."""
    try:
        from trading.modes import PRESETS
        from trading import targets as T
        from . import state
        active = getattr(state.cfg(), "mode", "")
    except Exception:
        return []
    order = ["nifty200_momentum", "short", "swing", "positional", "intraday"]
    names = {
        "nifty200_momentum": "Momentum", "short": "Short", "swing": "Swing",
        "positional": "Positional", "intraday": "Intraday",
    }
    out = []
    for key in order:
        pr = PRESETS.get(key)
        if pr is None:
            continue
        hold = hold_words(pr.max_hold_days, pr.square_off_time)
        k = T.ATR_TARGET_K.get(key)
        at_median = target_at_median_atr(key)
        if key == "nifty200_momentum":
            blurb = f"6m+12m momentum + quality · holds {hold}"
        else:
            blurb = f"in the daily rotation · holds {hold}"
        if at_median is not None:
            blurb += f" · target k×ATR (k {k:g}), at median ATR"
        out.append({
            "key": key, "name": names.get(key, key), "blurb": blurb,
            "stop": round(pr.stop_loss_pct * 100, 1),
            "target": (at_median if at_median is not None
                       else round(pr.take_profit_pct * 100, 1)),
            "target_basis": ("k×ATR, at median ATR" if at_median is not None
                             else "preset %"),
            "target_k": k,
            "fixed_target": round(pr.take_profit_pct * 100, 1),
            "trail": round(pr.trailing_stop_pct * 100, 1),
            "hold": pr.max_hold_days,
            "hold_text": hold,
            "floor": pr.min_score_to_enter,
            "active": key == active,
        })
    return out


def atr_panel(tickers: list[str], limit: int = 4) -> list[dict]:
    """Volatility context for held names (mockups' "ATR Stop & Position
    Size"). Computed from STORED OHLCV: the name's ATR, the category floor,
    and what the LIVE stop rule gives it today — since 2026-10-05 ("Go for
    A") a new momentum buy's stop is the wider of the floor and 2 x ATR,
    capped at 15% (trading/targets.py). Display only: a held position keeps
    the stop it was opened with; this shows the rule, not the position."""
    from . import state
    from .candle_chart import load_ohlcv
    from trading import targets as _T
    try:
        from trading.lens_v2 import atr as _atr
    except Exception:
        return []
    cfg = state.cfg()
    stop_pct = abs(float(cfg.stop_loss_pct))
    enabled = bool(getattr(cfg, "atr_stop_enabled", True))
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
        live_pct, diag = _T.stop_pct_for("nifty200_momentum", last, df,
                                          -stop_pct, enabled=enabled)
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
            # the live rule, momentum only (other categories read "fixed")
            "atr_stop": round(last * (1 + live_pct), 2),
            "atr_stop_pct": round(abs(live_pct) * 100, 2),
            "source": diag.get("source", "fixed"),
            "widened": abs(live_pct) > stop_pct + 1e-9,
            "k": _T.ATR_STOP_K, "cap_pct": round(_T.ATR_STOP_CAP * 100, 1),
        })
    return out


# ---------------------------------------------------------------------------
# Dashboard rebuild — owner order 2026-10-12 (items 30 a-m, 29, 12, 32, 43).
# Pure arithmetic over the book and the config; no network, no I/O.
# ---------------------------------------------------------------------------

def _ist_zone():
    try:
        from .clock import IST
        return IST
    except Exception:                      # clock.py absent -> same zone
        from zoneinfo import ZoneInfo
        return ZoneInfo("Asia/Kolkata")


def _ist_date_of(ts) -> str:
    """IST calendar date of a stored stamp (journal stamps are naive UTC).
    Unparseable -> '' so it matches no day, never today's by accident."""
    from datetime import datetime as _dt, timezone
    if ts is None or ts == "":
        return ""
    dt = ts
    if isinstance(ts, str):
        try:
            dt = _dt.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return ""
    if not isinstance(dt, _dt):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_ist_zone()).date().isoformat()


def reserve_ladder_text() -> str:
    """'0% bull · 0% neutral · 20% correction · 40% panic' read from
    risk_v2's OWN table, so the screen cannot drift from the code (the
    2026-07-27 truthful-copy pin, kept when the capability panel left the
    dashboard)."""
    try:
        from trading.risk_v2 import DYNAMIC_CASH_RESERVE as ladder
    except Exception:
        return ""
    names = {"bull_extension": "bull"}
    return " · ".join(f"{round(float(v) * 100)}% {names.get(k, k)}"
                      for k, v in ladder.items())


def gates_in_force(cfg) -> dict:
    """The entry rules a non-technical owner asks about when the book is
    quiet, read from config/code — never typed by hand on the page."""
    try:
        from trading.risk_v2 import LiquidityRules
        turnover_cr = float(LiquidityRules().min_rupee_volume_5d_cr)
    except Exception:
        turnover_cr = None
    return {
        "floor": float(getattr(cfg, "min_score_to_enter", 0.0) or 0.0),
        # trading/engine.py: correlation_gate_blocks(..., threshold=0.85)
        "correlation": 0.85,
        "turnover_cr": turnover_cr,
        "ladder": reserve_ladder_text(),
        "max_positions": int(getattr(cfg, "max_positions", 0) or 0),
    }


def fund_summary(p, cfg, prices: dict, live_count: int) -> dict:
    """Capital · allocated · reserved · used · cash · P&L, per segment and
    in total (owner order 2026-10-12, item 30b).

    Definitions, each the engine's own:
      capital    = cfg.starting_capital (total); a segment's SHARE of the
                   deployable capital (category_budget.limit — the number
                   risk.can_open refuses on) or Ocean's ticket × slots.
      allocated  = entry cost of the open positions (qty × entry price) —
                   the deploy-cap convention, so it agrees with the gate.
      now        = the same positions at the marks this page holds.
      reserved   = the regime cash reserve, cfg.cash_reserve_pct × equity
                   (engine.scan forces the 0/0/20/40 ladder into that field).
      free       = share − allocated, what the category may still spend.
      P&L        = now − allocated; "vs cost" over allocated, "vs capital"
                   over the starting capital (total) or the share (segment).
    `prices` carries live marks where the router had them and the entry
    price elsewhere; `live_count` says how many names were live so the
    block can label itself honestly instead of wearing "live" over an
    entry mark.
    """
    from trading import category_budget as cb
    positions = list(getattr(p, "positions", {}).values())
    n = len(positions)
    capital = float(getattr(cfg, "starting_capital", 0.0) or 0.0)
    equity = float(p.equity(prices))
    reserve_pct = float(getattr(cfg, "cash_reserve_pct", 0.0) or 0.0)
    reserved = reserve_pct * equity
    deployable = cb.deployable(cfg, equity)
    budgets_on = cb.enabled(cfg)
    min_open = max(cb.min_open(cfg), 1.0)

    def _row(cat: str, label: str, limit, share, note: str = "") -> dict:
        mine = [x for x in positions if cb.category_of(x) == cat]
        allocated = sum(float(x.qty) * float(x.entry_price) for x in mine)
        now_val = sum(float(x.qty) * float(prices.get(x.ticker, x.entry_price))
                      for x in mine)
        pnl = now_val - allocated
        return {
            "category": cat, "label": label, "note": note,
            "share": share, "limit": limit,
            "allocated": allocated, "now": now_val, "pnl": pnl,
            "free": (max(0.0, limit - allocated) if limit is not None else None),
            "pnl_vs_cost": ((pnl / allocated * 100) if allocated else None),
            "pnl_vs_share": ((pnl / limit * 100) if limit else None),
            "open": len(mine),
            "full": (limit is not None and (limit - allocated) < min_open),
        }

    rows = []
    for cat in cb.SHARE_FIELDS:
        rows.append(_row(cat, cb.LABELS[cat], cb.limit(cfg, cat, equity),
                         cb.share(cfg, cat)))
    ocean_limit = (float(getattr(cfg, "ocean_position_value", 0.0) or 0.0)
                   * int(getattr(cfg, "ocean_max_positions", 0) or 0))
    rows.append(_row(cb.OCEAN, "Ocean", ocean_limit or None, None,
                     note="ticket × slots, outside the five shares"))
    # Owner order 2026-10-21: owner BUY / limit fills / Buy Desk positions
    # are their own bucket outside the five shares (like Ocean).
    rows.append(_row(cb.OWNER, "Owner", None, None,
                     note="your BUY button / Buy Desk, outside the five shares"))
    known = set(cb.SHARE_FIELDS) | {cb.OCEAN, cb.OWNER}
    for cat in sorted({cb.category_of(x) for x in positions} - known):
        rows.append(_row(cat, cat.replace("_", " ").title(), None, None,
                         note="no budget applies to this category"))

    allocated = sum(r["allocated"] for r in rows)
    now_val = sum(r["now"] for r in rows)
    pnl = now_val - allocated
    if n == 0:
        marks = "no open positions"
    elif live_count >= n:
        marks = "live marks"
    elif live_count == 0:
        marks = "entry marks — connect Upstox for live P&L"
    else:
        marks = f"{live_count} of {n} names live, the rest at entry marks"
    return {
        "capital": capital, "equity": equity, "cash": float(p.cash),
        "allocated": allocated, "now": now_val, "pnl": pnl,
        "pnl_vs_cost": ((pnl / allocated * 100) if allocated else None),
        "pnl_vs_capital": ((pnl / capital * 100) if capital else None),
        "reserve_pct": reserve_pct, "reserved": reserved,
        "deployable": deployable,
        "budgets_on": budgets_on,
        "share_total": (cb.share_total(cfg) if budgets_on else None),
        "rows": rows, "open": n,
        "marks": marks, "live": bool(n and live_count >= n),
        "ladder": reserve_ladder_text(),
    }


def equity_month_series(history, now_ist=None) -> dict:
    """The CURRENT month's equity curve, one point per calendar day (the
    last mark of that day, a day with no mark = a gap), with the previous
    month beside it for comparison (owner order 2026-10-12, item 30d).

    `history` is `portfolio.equity_history`: (iso_ts, equity) tuples,
    stamped naive UTC by the engine; days are bucketed in IST. Labels are
    the owner's 1..31 — a 30-day month shows an empty 31st slot rather
    than a different axis every month.
    """
    from datetime import datetime as _dt, timedelta, timezone
    ist = _ist_zone()
    now = now_ist or _dt.now(ist)
    cur = (now.year, now.month)
    prev_dt = now.replace(day=1) - timedelta(days=1)
    prev = (prev_dt.year, prev_dt.month)
    cur_vals: list = [None] * 31
    prev_vals: list = [None] * 31
    # Walk backwards: the first stamp seen for a day is that day's LAST
    # mark; stop once a stamp is older than the previous month.
    for item in reversed(list(history or [])):
        try:
            ts, v = item[0], item[1]
            dt = _dt.fromisoformat(str(ts).replace("Z", "+00:00"))
            val = round(float(v), 2)
        except (TypeError, ValueError, IndexError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(ist)
        key = (dt.year, dt.month)
        if key == cur:
            if cur_vals[dt.day - 1] is None:
                cur_vals[dt.day - 1] = val
        elif key == prev:
            if prev_vals[dt.day - 1] is None:
                prev_vals[dt.day - 1] = val
        elif key < prev:
            break
    cur_seen = [v for v in cur_vals if v is not None]
    return {
        "labels": list(range(1, 32)),
        "current": cur_vals,
        "previous": prev_vals,
        "month": now.strftime("%b %Y"),
        "prev_month": prev_dt.strftime("%b %Y"),
        "n_current": len(cur_seen),
        "n_previous": sum(1 for v in prev_vals if v is not None),
        "first": cur_seen[0] if cur_seen else None,
        "last": cur_seen[-1] if cur_seen else None,
    }


# Journal actions that explain a quiet book (owner order 2026-10-12, item 32).
WHY_NOT_ACTIONS = (
    "CATEGORY_BUDGET_FULL", "BREADTH_BLOCKED", "MEASURED_KELLY_BLOCKED",
    "SCAN_FAILED", "SCAN_COVERAGE_SHORT", "CORRECTION_BRAKE_OFF",
    "ROTATION_NOTHING_TO_FUND", "KILL_SWITCH_SCAN",
)


def why_not_today(journal, today_iso: str) -> list[dict]:
    """One row per category plus one for the whole book: the LATEST block
    the journal recorded today, in the engine's own words. A category with
    nothing recorded reads "no block recorded today" — never a reason the
    journal did not write."""
    from trading import category_budget as cb
    order = list(cb.SHARE_FIELDS) + ["all"]
    labels = dict(cb.LABELS)
    labels["all"] = "All categories"
    latest: dict = {}
    for e in list(journal or [])[-800:]:
        action = str(e.get("action", "") or "")
        if action not in WHY_NOT_ACTIONS:
            continue
        if _ist_date_of(e.get("ts")) != today_iso:
            continue
        cat = str(e.get("category") or e.get("mode") or "").strip() or "all"
        if cat not in labels:
            cat = "all"
        why = e.get("why") or e.get("detail") or e.get("err") or ""
        # SCAN_FAILED's `why` is a generic hint; the real failure is `err`
        # (scheduler._run_category_rotation) — show both, never just the hint.
        if action == "SCAN_FAILED" and e.get("err") and str(e.get("err")) not in str(why):
            why = f"{why} ({e.get('err')})" if why else str(e.get("err"))
        if not why and action == "SCAN_COVERAGE_SHORT" and e.get("asked") is not None:
            why = (f"scored {e.get('covered')} of the {e.get('asked')} names "
                   f"asked for — the rest had no stored history")
        latest[cat] = {"action": action, "why": str(why).strip(),
                       "ts": e.get("ts")}
    out = []
    for c in order:
        row = {"category": c, "label": labels[c],
               "action": None, "why": "", "ts": None}
        row.update(latest.get(c) or {})
        out.append(row)
    return out
