"""Watchlist capture — the day's scan watchlist, frozen with date, time and
every stock's major parameters (owner item 28, 2026-10-12: "from now the
first scan after the new replacement of full 1200 share — watchlist will be
captured there with date n time and stock details (major parameters)").

What a capture IS: a bounded, read-only copy of what the scan already
computed — the momentum pass's own record (`portfolio.momentum_shortlist`)
joined with the scan matrix's extras (candle, structures, the three-formula
verdicts) and the day's skip reasons. Nothing here fetches, scores or
decides; a crash here costs the report, never the scan.

Sources, in order of trust:
  * `momentum_shortlist` — written ONLY by the momentum pass (audit S-11):
    score, abs-mom, timing, the entry gate's own liquidity verdict, outcome,
    the composite components. The capture's mode and floor are ITS mode and
    floor.
  * `scan_matrix` — overwritten by EVERY pass; after the rotation it holds
    the LAST category (positional). Only mode-independent extras are taken
    from it (candle label, structures, multi-formula verdicts) and, when the
    matrix IS the momentum pass (same mode), the below-floor names that fill
    toward the top 100 (the shortlist itself keeps only rows at or above the
    floor). A different pass's score is never mixed in; when the matrix is a
    different pass the capture holds the at/above-floor rows only and its
    `note` says why.
  * `last_scan_skipped` / `last_scan_opened` — written with the matrix, so
    read only when the matrix is the captured pass; otherwise the
    shortlist's own `outcome` is the skip reason and "opened" = held under
    this category with today's entry day.
  With no shortlist at all (the momentum pass did not run), the matrix is
  the only source and the capture SAYS so (`source`).

  Fullest capture: call capture() right after the momentum pass, while the
  matrix is still that pass. At the end of the rotation it still works,
  with the at/above-floor rows only.

Bounds: every row at or above the floor, plus the top 100 by score,
whichever is larger, capped at MAX_ROWS (300). Mongo keeps the newest 120
captures (db.WATCHLIST_SNAPSHOT_KEEP), disk the newest 60 CSVs.

Clock: timestamps are IST. The engine's `now` is naive UTC
(`datetime.utcnow()` everywhere), so a NAIVE `now` is read as UTC and
converted; an aware one is converted; None = the real clock.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

MAX_ROWS = 300      # hard cap per capture
TOP_N = 100         # top-by-score rows kept even when they sit under the floor
HISTORY_DEPTH = 120  # captures read back to carry `first_seen` forward

try:                                                    # stdlib since 3.9
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except Exception:                                       # pragma: no cover
    from datetime import timedelta
    IST = timezone(timedelta(hours=5, minutes=30))


# ----------------------------------------------------------------- clock

def to_ist(now: Optional[datetime] = None) -> datetime:
    """IST-aware datetime. Naive = UTC (the engine's convention)."""
    if now is None:
        return datetime.now(IST)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc).astimezone(IST)
    return now.astimezone(IST)


def ts_key_for(now_ist: datetime) -> str:
    """Document key + file stem: minute resolution, sorts chronologically,
    URL-safe (no colons, no plus). e.g. 2026-10-12T0935."""
    return now_ist.strftime("%Y-%m-%dT%H%M")


# ----------------------------------------------------------------- helpers

def _multi_labels(multi) -> dict:
    """{'am07': 'PASS', 'ash08': 'fail', 'ashstocks': 'DATA_NEEDED'} from the
    multi_formula.evaluate() dict stored on a matrix row. Labels only — the
    verdict strings the /scan Formulas column already prints."""
    out: dict = {}
    rows = (multi or {}).get("rows") if isinstance(multi, dict) else None
    if not isinstance(rows, dict):
        return out
    for name, r in rows.items():
        if isinstance(r, dict) and r.get("label") is not None:
            out[str(name)] = str(r.get("label"))
    return out


def _structures_fired(structures) -> dict:
    """Keep only the candle structures that fired (True) — the full dict is
    34 keys per row, mostly False, and would triple the row size."""
    if not isinstance(structures, dict):
        return {}
    return {k: True for k, v in structures.items() if v}


def _safe_float(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:          # NaN
        return None
    return f


# ----------------------------------------------------------------- build

def build_snapshot(portfolio, cfg, now: Optional[datetime] = None,
                   history_index: Optional[dict] = None) -> dict:
    """Pure: portfolio + cfg -> the capture document. No I/O.

    `history_index`: {ticker: 'YYYY-MM-DD'} = the earliest capture date each
    ticker has appeared in so far (the caller reads it from the stored
    captures via `history_index_from`). A ticker not in it is first seen
    today.
    """
    now_ist = to_ist(now)
    today = now_ist.strftime("%Y-%m-%d")
    hist = dict(history_index or {})

    shortlist = getattr(portfolio, "momentum_shortlist", None) or {}
    matrix = getattr(portfolio, "scan_matrix", None) or {}
    sl_rows = shortlist.get("rows") or {}
    mx_rows = matrix.get("rows") or {}
    sl_mode = str(shortlist.get("mode") or "")
    mx_mode = str(matrix.get("mode") or "")

    if sl_rows:
        scan_mode, floor = sl_mode, _safe_float(shortlist.get("score_floor"))
        scan_ts = str(shortlist.get("ts") or "")
        same_pass = bool(mx_rows) and mx_mode == sl_mode
        source = ("momentum_shortlist+scan_matrix" if same_pass
                  else "momentum_shortlist" + ("+scan_matrix_extras" if mx_rows else ""))
    elif mx_rows:
        scan_mode, floor = mx_mode, _safe_float(matrix.get("score_floor"))
        scan_ts = str(matrix.get("ts") or "")
        same_pass, source = True, "scan_matrix"
    else:
        return {
            "ts": now_ist.isoformat(), "ts_key": ts_key_for(now_ist),
            "scan_mode": "", "floor": None, "scan_ts": "", "source": "none",
            "n_rows": 0, "rows": [], "head": [], "universe_count": 0,
            "universe_rebuild": "", "note": "no scan record to capture",
        }
    if floor is None:
        floor = _safe_float(getattr(cfg, "min_score_to_enter", None))

    # last_scan_skipped / last_scan_opened are written by engine.scan in the
    # SAME block as scan_matrix, so they belong to the matrix's pass. After the
    # rotation that is the LAST category (positional), not the momentum pass
    # being captured — read them only when the matrix IS the captured pass.
    # Otherwise the shortlist's own `outcome` (the momentum pass's skip
    # reason) is the reason, and "opened" is read off the book: held, bought
    # under this category, entered today.
    skipped_by: dict = {}
    opened: set = set()
    positions = getattr(portfolio, "positions", None) or {}
    if same_pass:
        for s in getattr(portfolio, "last_scan_skipped", None) or []:
            t = s.get("ticker") if isinstance(s, dict) else None
            if t and t not in skipped_by:
                skipped_by[t] = str(s.get("reason") or "")[:80]
        opened = set(getattr(portfolio, "last_scan_opened", None) or [])
    else:
        for t, pos in positions.items():
            if (getattr(pos, "mode", "") or "") != scan_mode:
                continue
            ed = getattr(pos, "entry_date", None)
            try:
                # entry_date is naive UTC; inside market hours the UTC and
                # IST calendar days are the same day
                day = ed.date() if hasattr(ed, "date") and callable(ed.date) else ed
                if day is not None and str(day)[:10] == today:
                    opened.add(t)
            except Exception:
                continue
    held = set(positions.keys())
    sector_map = getattr(cfg, "sector_map", None) or {}

    # Candidate pool: the shortlist's rows (its scores are the momentum
    # pass's); plus the matrix's rows only when the matrix IS that pass.
    pool: dict = {}
    for t, r in sl_rows.items():
        sc = _safe_float((r or {}).get("score"))
        if sc is None:
            continue
        pool[t] = {"score": sc, "sl": r, "mx": mx_rows.get(t) if mx_rows else None}
    if same_pass:
        for t, r in mx_rows.items():
            if t in pool:
                continue
            sc = _safe_float((r or {}).get("score"))
            if sc is None:
                continue
            pool[t] = {"score": sc, "sl": None, "mx": r}
    elif mx_rows:
        # extras only (candle/structures/multi are computed from the price
        # history, the same for every pass) — never that pass's score
        for t in pool:
            pool[t]["mx"] = mx_rows.get(t)

    ranked = sorted(pool.items(), key=lambda kv: (-kv[1]["score"], kv[0]))
    keep = []
    for i, (t, d) in enumerate(ranked):
        above = floor is not None and d["score"] >= floor
        if above or i < TOP_N:
            keep.append((i + 1, t, d))
        if len(keep) >= MAX_ROWS:
            break

    rows = []
    for rank, t, d in keep:
        sl, mx = d["sl"] or {}, d["mx"] or {}
        comps = sl.get("c") if sl else (mx.get("c") if mx else None)
        liq = sl.get("liq") if sl else None     # None = not judged by that pass
        first = hist.get(t) or today
        if first > today:                       # a corrupt future stamp
            first = today
        rows.append({
            "ticker": t,
            "score": round(d["score"], 1),
            "rank": rank,
            "abs_mom": (sl.get("abs_mom") if sl else mx.get("abs_mom")),
            "timing": (sl.get("timing") if sl else mx.get("timing")),
            "candle": str(mx.get("candle") or "") if mx else "",
            "liquidity": liq,
            "liquidity_why": str(sl.get("liq_why") or "")[:60] if sl else "",
            "outcome": str((sl.get("outcome") if sl else mx.get("outcome")) or ""),
            "sector": sector_map.get(t, "Unknown"),
            "components": {k: _safe_float(v) for k, v in (comps or {}).items()},
            "multi": _multi_labels(mx.get("multi")) if mx else {},
            "structures": _structures_fired(mx.get("structures")) if mx else {},
            "reason_if_skipped": (skipped_by.get(t, "") if same_pass
                                  else str(sl.get("outcome") or "")[:80]),
            "held": t in held,
            "opened": t in opened,
            "first_seen": first,
        })

    # The momentum shortlist stores ONLY rows at/above its floor
    # (engine._store_momentum_shortlist); its below-floor scores live only in
    # the matrix while the matrix is still that pass. Say so rather than let a
    # short capture read as "only N names scored".
    note = ""
    if not same_pass:
        note = ("below-floor names are not in this capture: the momentum pass "
                "keeps only rows at or above its floor"
                + (f", and the scan matrix now holds the {mx_mode or 'other'} pass"
                   if mx_rows else ""))
    return {
        "ts": now_ist.isoformat(),
        "ts_key": ts_key_for(now_ist),
        "scan_mode": scan_mode,
        "floor": floor,
        "scan_ts": scan_ts,          # the scan's own stamp (naive UTC, as stored)
        "source": source,
        "note": note,
        "n_rows": len(rows),
        "n_above_floor": sum(1 for r in rows if floor is not None and r["score"] >= floor),
        "n_pool": len(pool),
        "rows": rows,
        "head": [r["ticker"] for r in rows[:8]],
        "universe_count": len(getattr(cfg, "custom_universe", None) or ()),
        "universe_rebuild": str((getattr(portfolio, "universe_batch", None) or {}).get("ts") or ""),
    }


def history_index_from(snapshots) -> dict:
    """{ticker: earliest 'YYYY-MM-DD'} over stored captures. Each stored row
    already carries its `first_seen`, so the min over the captures read is
    the earliest date the ticker ever appeared in (within HISTORY_DEPTH)."""
    idx: dict = {}
    for s in snapshots or []:
        day = str(s.get("ts") or "")[:10]
        for r in s.get("rows") or []:
            t = r.get("ticker")
            if not t:
                continue
            d = str(r.get("first_seen") or day)[:10] or day
            if not d:
                continue
            if t not in idx or d < idx[t]:
                idx[t] = d
    return idx


# ----------------------------------------------------------------- capture

def capture(portfolio, cfg, now: Optional[datetime] = None,
            db_module=None, state_dir: Optional[str] = None) -> dict:
    """Build today's capture, save it to Mongo (bounded collection) and
    write its CSV. NEVER raises into the caller (the rotation end).

    Returns {ok, ts, ts_key, n_rows, mongo, csv, error, note}:
      ok    - at least one store landed (Mongo or CSV)
      mongo - the Mongo write landed; False = "CSV only" (Mongo down, the
              write refused, or db_module None)
      csv   - path of the written file, or None
      error - the first failure's text, or None
    """
    out = {"ok": False, "ts": "", "ts_key": "", "n_rows": 0, "mongo": False,
           "csv": None, "error": None, "note": ""}
    try:
        if db_module is None:
            from . import db as db_module  # type: ignore[no-redef]
    except Exception as e:                      # pragma: no cover
        out["error"] = f"db import: {e}"
        db_module = None

    # first_seen needs the stored history; a failed read = today for all
    hist: dict = {}
    if db_module is not None:
        try:
            prev = db_module.list_watchlist_snapshots(
                limit=HISTORY_DEPTH, fields=("ts", "rows.ticker", "rows.first_seen"))
            hist = history_index_from(prev)
        except Exception as e:
            log.warning("watchlist history read failed: %s", e)
            out["note"] = f"history unreadable ({type(e).__name__}); first_seen = today"

    try:
        snap = build_snapshot(portfolio, cfg, now, hist)
    except Exception as e:
        log.exception("watchlist build failed")
        out["error"] = f"build: {type(e).__name__}: {e}"
        return out
    out.update(ts=snap["ts"], ts_key=snap["ts_key"], n_rows=snap["n_rows"])
    if snap.get("note") and snap["n_rows"]:
        # e.g. "below-floor names are not in this capture ..." — the
        # scheduler journals `note`, so a short capture explains itself
        out["note"] = (out["note"] + "; " if out["note"] else "") + snap["note"]
    if snap["n_rows"] == 0:
        out["note"] = (out["note"] + "; " if out["note"] else "") + \
            "nothing to capture (no scan record)"
        return out

    if db_module is not None:
        try:
            out["mongo"] = bool(db_module.save_watchlist_snapshot(snap))
        except Exception as e:
            log.warning("watchlist Mongo save failed: %s", e)
            out["error"] = f"mongo: {type(e).__name__}: {e}"
    if not out["mongo"]:
        out["note"] = (out["note"] + "; " if out["note"] else "") + "CSV only"

    try:
        from trading.reports import write_watchlist_csv
        sd = state_dir or getattr(cfg, "state_dir", None) or "/tmp/sourceborn_paper"
        out["csv"] = write_watchlist_csv(snap, sd)
    except Exception as e:
        log.warning("watchlist CSV write failed: %s", e)
        out["error"] = out["error"] or f"csv: {type(e).__name__}: {e}"

    out["ok"] = bool(out["mongo"] or out["csv"])
    return out
