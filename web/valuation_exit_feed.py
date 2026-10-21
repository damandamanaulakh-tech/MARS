"""The web half of item 19 (owner order 2026-10-21, "Wire A3" + "Exit as
tested"): the stored valuation RECORDS the engine reads at a buy and on
every exit pass, and the index / sector levels its monthly re-mark reads.

trading/valuation_exit.py holds the rule (pure). This module only reads
STORED state — no network — and is injected into it at boot (web/state):

  records_for(tickers)  in-process LAST, else settings doc
                        "valuation_records" (Mongo), else {}
  level(kind, name, when)
      sensex / nifty   the committed index file + the stored tail
                       (valuation_view._index_field, cached 600 s)
      sector           the stored NSE sector closes
                       (valuation_view._sector_table, cached 600 s)
      `when` = ISO date: the close on or before it, None while the series
      has not reached that day; None = the latest close.

The records are written by web/scheduler.run_valuation_precompute (16:50
IST, the weekly block, a boot catch-up and the owner's Den button), which
reduces each Den §12 chain row (valuation_chain_view.row_for, DEFAULT
assumptions — the tested rule; a Den draft never reaches the money path)
to the RECORD shape below.
"""
from __future__ import annotations

import logging
import threading
import time as _time
from datetime import date, timedelta
from typing import Any, Optional

import pandas as pd

from trading import valuation_context as vc
from trading.calendars import is_trading_day

from . import db
from . import valuation_view as vv

log = logging.getLogger(__name__)

DOC_ID = "valuation_records"
#: In-process copy of the stored doc: {rows{ticker: rec}, built, asof, n,
#: n_judged, skipped, ...}. Mongo is the durable copy.
LAST: dict = {}
_LOCK = threading.Lock()
#: An empty store read is asked again after this many seconds, not on
#: every 30-minute pass and every buy.
MISS_RETRY_S = 300.0
_MISS = {"at": None}


# ---------------------------------------------------------------- records

def load_doc() -> dict:
    try:
        return dict(db.load_settings(doc_id=DOC_ID) or {})
    except Exception as e:                               # noqa: BLE001
        log.warning("valuation records: store read failed: %s", e)
        return {}


def current_doc() -> dict:
    """The records doc: the in-process copy, else the stored one."""
    with _LOCK:
        if LAST.get("rows"):
            return dict(LAST)
        miss = _MISS["at"]
        if miss is not None and _time.monotonic() - miss < MISS_RETRY_S:
            return {}
    doc = load_doc()
    with _LOCK:
        if doc.get("rows"):
            LAST.clear()
            LAST.update(doc)
            _MISS["at"] = None
        else:
            _MISS["at"] = _time.monotonic()
    return doc


def records_for(tickers) -> dict:
    """{ticker: record} for the names asked (the engine's record reader)."""
    rows = current_doc().get("rows") or {}
    return {t: dict(rows[t]) for t in (tickers or []) if t in rows}


def store(doc: dict) -> bool:
    """Keep `doc` in process and in Mongo. True only when the stored doc
    reads back with this build's stamp — db.save_settings logs a refused
    write and returns, so "no exception" is not proof it landed. The
    in-process copy serves this process either way."""
    with _LOCK:
        LAST.clear()
        LAST.update(doc)
        _MISS["at"] = None
    try:
        db.save_settings(dict(doc), doc_id=DOC_ID)
        back = db.load_settings(doc_id=DOC_ID) or {}
    except Exception:                                    # noqa: BLE001
        log.exception("valuation records: store write failed (in-process copy kept)")
        return False
    ok = bool(doc.get("built")) and back.get("built") == doc.get("built")
    if not ok:
        log.warning("valuation records: not stored in the database "
                    "(in-process copy kept)")
    return ok


def clear_cache() -> None:
    """Forget the in-process copy (tests)."""
    with _LOCK:
        LAST.clear()
        _MISS["at"] = None


def built_date() -> Optional[date]:
    built = current_doc().get("built")
    try:
        return date.fromisoformat(str(built)[:10]) if built else None
    except ValueError:
        return None


def previous_trading_day(today: date) -> date:
    d = today - timedelta(days=1)
    for _ in range(15):
        if is_trading_day(d):
            return d
        d -= timedelta(days=1)
    return d


def records_stale(built: Optional[date], today: date) -> bool:
    """Older than one trading day: built before the last trading day
    before today (or never built)."""
    return built is None or built < previous_trading_day(today)


def summary() -> dict:
    """For the Den: when the records were built and how many are judged."""
    doc = current_doc()
    rows = doc.get("rows") or {}
    return {"built": str(doc.get("built") or "")[:16], "asof": doc.get("asof") or "",
            "n": len(rows), "n_judged": int(doc.get("n_judged") or 0),
            "where": doc.get("where") or "", "elapsed_s": doc.get("elapsed_s"),
            "stopped": doc.get("stopped") or "",
            "skipped": dict(doc.get("skipped") or {})}


# ----------------------------------------------------------------- levels

def _index(field: str) -> Optional[pd.Series]:
    try:
        return vv._index_field(field)
    except Exception as e:                               # noqa: BLE001
        log.warning("valuation exit: index series %s unreadable: %s", field, e)
        return None


def _sector(name: str, when: Optional[str]) -> Optional[pd.Series]:
    since = None
    if when:
        try:
            since = (date.fromisoformat(str(when)[:10]) - timedelta(days=15)).isoformat()
            since = min(since, vv._default_sector_since())
        except ValueError:
            since = None
    try:
        return (vv._sector_table(since) or {}).get(name)
    except Exception as e:                               # noqa: BLE001
        log.warning("valuation exit: sector series %s unreadable: %s", name, e)
        return None


def level(kind: str, name: Optional[str] = None,
          when: Optional[str] = None) -> Optional[float]:
    """The engine's level reader. Never raises."""
    try:
        if kind == "sensex":
            s = _index("sensex_close")
        elif kind == "nifty":
            s = _index("nifty_close")
        elif kind == "sector" and name:
            s = _sector(name, when)
        else:
            return None
        if s is None:
            return None
        s = s.dropna()
        if not len(s):
            return None
        if not when:
            return float(s.iloc[-1])
        ts = pd.Timestamp(str(when)[:10])
        if s.index[-1] < ts:
            return None                      # that day's close is not stored yet
        part = s[s.index <= ts]
        return float(part.iloc[-1]) if len(part) else None
    except Exception as e:                               # noqa: BLE001
        log.warning("valuation exit: level %s %s %s failed: %s", kind, name, when, e)
        return None


# ------------------------------------------------------------ the records

def _n(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def record_from_row(ticker: str, rec: dict, closes: Optional[pd.Series],
                    sensex: Optional[pd.Series], cfg, where: str = "") -> dict:
    """One Den §12 chain row reduced to the RECORD the engine reads."""
    rng = rec.get("range") or {}
    vd = rec.get("verdict") or {}
    rt = rec.get("route") or {}
    b: dict = {"beta": None, "n": 0}
    if closes is not None and sensex is not None:
        try:
            b = vc.beta(closes, sensex)
        except Exception:                                # noqa: BLE001
            b = {"beta": None, "n": 0}
    idx_name, _label = vv.bench_for(ticker, cfg)
    src = rec.get("price_source") or ""
    return {
        "ticker": ticker, "asof": str(rec.get("price_date") or "")[:10],
        "verdict": vd.get("verdict") or "insufficient",
        "range_status": rng.get("status") or "",
        "base_target": _n(rng.get("base")), "low": _n(rng.get("low")),
        "high": _n(rng.get("high")),
        "family": rng.get("family") or rt.get("family") or "",
        "method": rt.get("method") or "",
        "sector_index": idx_name or vc.MARKET_INDEX_LABEL,
        "beta": _n(b.get("beta")), "beta_n": int(b.get("n") or 0),
        "version": rec.get("version") or "", "drafted": bool(rec.get("drafted")),
        "price": _n(rec.get("price")),
        "note": (f"{where or 'precompute'}: default assumptions"
                 + (f"; price from {src}" if src else "")
                 + ("" if b.get("beta") is not None
                    else f"; beta needs 120 sessions ({int(b.get('n') or 0)}) — 1 used")),
    }


def precompute_scope(p, cap: int) -> tuple[list, list]:
    """(names, cut): held names first, then the momentum shortlist rows at
    or above its floor by score, then the last scan picks, then the Den §11
    targets — unique, capped at `cap`."""
    out: list = []

    def _add(t):
        if t and t not in out:
            out.append(str(t))
    held = getattr(p, "positions", None) or {}
    for t in (held.keys() if isinstance(held, dict) else []):
        _add(t)
    sl = getattr(p, "momentum_shortlist", None) or {}
    rows = sl.get("rows") or {}
    floor = _n(sl.get("score_floor"))
    scored = []
    for t, r in rows.items():
        s = _n((r or {}).get("score"))
        if s is not None and (floor is None or s >= floor):
            scored.append((s, t))
    for _s, t in sorted(scored, key=lambda x: (-x[0], x[1])):
        _add(t)
    for pk in (getattr(p, "last_scan_picks", None) or []):
        _add((pk or {}).get("ticker"))
    try:
        for t in sorted((vv.load_doc().get("targets") or {}).keys()):
            _add(t)
    except Exception:                                    # noqa: BLE001
        pass
    return out[:cap], out[cap:]
