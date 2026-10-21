"""Valuation target as the EXIT target — item 19 (owner order 2026-10-21,
card answers "Wire A3" + "Exit as tested").

Owner's words: "Use the valuation target as the exit target for all NEW
buys. When a judged valuation (attractive/watch) exists, the
market-adjusted valuation target replaces +20% as the exit target at
entry; otherwise k x ATR as today ... Also do it on open positions."

Measured BEFORE wiring (pre-registered, scratchpad item19/PREREG.md; the
record is docs/VALUATION_EXIT_2026-10-21.md). TEST half 2024-01..2026-06:
C0 (k x ATR) +5.46% / max DD -4.11, A3 (this rule) +5.68% / -4.18,
coverage 33% of entries. A3 = A1 + A2 + the re-target of open positions:

  A1  at entry: a JUDGED record (verdict attractive/watch, range OK) whose
      own base target is above fill x 1.05 sets the target to that base.
      Anything else keeps the k x ATR target.
  A2  every closed month the target is re-marked with the market:
        T_m = T_entry x (1 + beta x Sensex move) x (1 + (sector move - Sensex move))
      moves measured from the anchor day (fill day / re-target day) to the
      month-end close; beta = the stock's OLS slope on the Sensex over 252
      sessions (fewer than 120 = 1, labelled). A re-marked target at or
      below the stock's month-end close = exit (VAL_TARGET_DOWN).
  A3  a held position on k x ATR whose record LATER qualifies (measured
      against its ENTRY price) is re-targeted once, then re-marked as A2 —
      only while today's price is inside the envelope a same-day judged
      verdict implies (at most base x 1.10 and the range high), and only on
      an in-session pass, so the target and the exit check read one price.

PURE: no network, no web import, no config mutation, and it never runs
the valuation pipeline. The web side precomputes the records
(web/scheduler.run_valuation_precompute) and injects two readers at boot
(the flows.set_store_reader pattern — trading/ never imports web):

  records_reader(tickers) -> {ticker: record}
  level_reader(kind, name, when=None) -> float | None
      kind "sensex" | "nifty" | "sector"; `when` = ISO date: the close on
      or before it, None while the stored series has not reached that day;
      when=None = the latest close.

A missing reader means no valuation target and no re-mark — k x ATR
stands, nothing raises.

RECORD shape (web precompute): {ticker, asof (ISO date of the closes
used), verdict, range_status, base_target, low, high, family, method,
sector_index (NSE index name or "Nifty 50"), beta, version, drafted, note}.
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Optional, Tuple

from .calendars import is_trading_day

log = logging.getLogger(__name__)

# MEASURED in the study, not knobs (test-pinned).
VALUATION_EXIT_MARGIN = 1.05         # base must exceed fill x this
JUDGED = ("attractive", "watch")     # the verdicts the study called judged
# ASSUMED: the study used the latest month-end valuation before entry, so a
# record up to ~35 days old is inside the tested behaviour. Older = k x ATR.
RECORD_MAX_AGE_DAYS = 35
# The study's A3 re-target read a record valued at THAT SAME close, so a
# judged verdict meant the price was at most base x 1.10 and at most the
# range high (trading/valuation/assess: above either = unattractive). A live
# record is from an earlier close, so the re-target checks today's price
# against the same envelope. Mirrors assess "unattractive_premium_pct"
# (test-pinned; this module may not import the pipeline).
RETARGET_PRICE_CEILING = 1.10
# ASSUMED: the store gets a stock's month-end close with the next
# morning's top-up; the re-mark waits this many calendar days for it before
# it compares with the pass's own price instead (named close_source).
REMARK_CLOSE_WAIT_DAYS = 5
REMARKS_CAP = 24                     # re-mark history kept on a position
MARKET_SECTOR = "Nifty 50"           # the sector leg of an unmapped name
SOURCE_SET = ("valuation", "retarget")   # positions the re-mark follows

_RECORD_READER: Optional[Callable[[list], dict]] = None
_LEVEL_READER: Optional[Callable[..., Optional[float]]] = None


def set_record_reader(fn) -> None:
    """fn(tickers) -> {ticker: record}. None detaches."""
    global _RECORD_READER
    _RECORD_READER = fn


def set_level_reader(fn) -> None:
    """fn(kind, name, when=None) -> float | None. None detaches."""
    global _LEVEL_READER
    _LEVEL_READER = fn


def ready() -> bool:
    """Both readers attached (the live app attaches them at boot)."""
    return _RECORD_READER is not None and _LEVEL_READER is not None


def records_for(tickers) -> dict:
    """{ticker: record} for the names asked; {} on any failure."""
    names = [t for t in (tickers or []) if t]
    if _RECORD_READER is None or not names:
        return {}
    try:
        out = _RECORD_READER(names) or {}
        return dict(out) if isinstance(out, dict) else {}
    except Exception as e:                               # noqa: BLE001
        log.warning("valuation exit: record read failed: %s", e)
        return {}


def level(kind: str, name: Optional[str] = None,
          when: Optional[str] = None) -> Optional[float]:
    """One stored close (see the module docstring); None on any failure."""
    if _LEVEL_READER is None:
        return None
    try:
        v = num(_LEVEL_READER(kind, name, when))
    except Exception as e:                               # noqa: BLE001
        log.warning("valuation exit: level read failed (%s %s): %s", kind, name, e)
        return None
    return v if v is not None and v > 0 else None


def leg_for(sector_index: Optional[str]) -> Tuple[str, Optional[str]]:
    """(reader kind, name) of a record's sector leg."""
    s = (sector_index or "").strip()
    if not s or s == MARKET_SECTOR:
        return "nifty", None
    return "sector", s


# ------------------------------------------------------------------ utils

def num(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def as_date(x: Any) -> Optional[date]:
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    try:
        return date.fromisoformat(str(x)[:10])
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ rule

def target_from_record(rec: Optional[dict], fill_price: Any, today: date,
                       enabled: bool = True) -> Tuple[Optional[float], dict]:
    """(target, diag). target = the record's base target when the record is
    judged, fresh and above fill x VALUATION_EXIT_MARGIN; else None and
    diag["reason"] says why: disabled / no_record / stale:<days> /
    not_judged:<verdict> / below_margin (and a few input faults)."""
    if not enabled:
        return None, {"reason": "disabled"}
    if not rec:
        return None, {"reason": "no_record"}
    asof = as_date(rec.get("asof"))
    if asof is None:
        return None, {"reason": "stale:unknown"}
    age = (today - asof).days
    if age > RECORD_MAX_AGE_DAYS:
        return None, {"reason": f"stale:{age}"}
    verdict = str(rec.get("verdict") or "none")
    if verdict not in JUDGED:
        return None, {"reason": f"not_judged:{verdict}"}
    if rec.get("range_status") != "OK":
        return None, {"reason": f"not_judged:range_{rec.get('range_status')}"}
    base = num(rec.get("base_target"))
    if base is None or base <= 0:
        return None, {"reason": "not_judged:no_base"}
    if rec.get("drafted"):
        # the study used the default assumptions; a Den draft never trades
        return None, {"reason": "drafted"}
    fill = num(fill_price)
    if fill is None or fill <= 0:
        return None, {"reason": "no_fill_price"}
    if not base > fill * VALUATION_EXIT_MARGIN:
        return None, {"reason": "below_margin", "base": round(base, 2),
                      "needs_above": round(fill * VALUATION_EXIT_MARGIN, 2)}
    return round(base, 2), {
        "reason": "judged", "base": round(base, 2), "asof": asof.isoformat(),
        "age_days": age, "verdict": verdict,
        "family": rec.get("family") or "", "method": rec.get("method") or "",
        "upside_pct": round((base / fill - 1.0) * 100.0, 2)}


def retarget_price_ok(rec: Optional[dict], price: Any) -> Tuple[bool, str]:
    """A3 only: would the record still be judged at today's price? The
    study re-targeted on a same-day verdict, so a price above base x 1.10
    or above the range high (unattractive at that price) never got a
    re-target there. No price = not judged now."""
    p = num(price)
    if p is None or p <= 0:
        return False, "no_price"
    base = num((rec or {}).get("base_target"))
    if base is None or base <= 0:
        return False, "no_base"
    if p > base * RETARGET_PRICE_CEILING:
        return False, "price_above_base_x1.10"
    high = num((rec or {}).get("high"))
    if high is not None and p > high:
        return False, "price_above_range_high"
    return True, "ok"


def remarked_target(val: Any, sensex_entry: Any, sensex_now: Any,
                    sector_entry: Any, sector_now: Any,
                    beta: Any = None) -> Optional[float]:
    """T_entry x (1 + beta x Sensex move) x (1 + (sector move - Sensex move)).
    beta None (or unreadable) = 1. None when a level is missing."""
    t0 = num(val)
    se, sn = num(sensex_entry), num(sensex_now)
    ke, kn = num(sector_entry), num(sector_now)
    if None in (t0, se, sn, ke, kn) or min(se, sn, ke, kn) <= 0:
        return None
    b = num(beta)
    if b is None:
        b = 1.0
    sm = sn / se - 1.0
    sc = kn / ke - 1.0
    return t0 * (1.0 + b * sm) * (1.0 + (sc - sm))


def target_down(t_m: Any, close_m: Any) -> bool:
    """The re-marked target is at or below the month-end close = exit."""
    t, c = num(t_m), num(close_m)
    return t is not None and c is not None and t <= c


# ---------------------------------------------------------------- months

def month_closed(today_ist: date, last_remark_month: Optional[str]) -> Optional[str]:
    """The month ("YYYY-MM") to re-mark for, or None. It is always the
    month before `today_ist`; None once that month is recorded."""
    prev = today_ist.replace(day=1) - timedelta(days=1)
    m = f"{prev.year:04d}-{prev.month:02d}"
    if last_remark_month and str(last_remark_month)[:7] >= m:
        return None
    return m


def month_end(month: str) -> Optional[date]:
    """The last NSE trading day of `month` ("YYYY-MM")."""
    try:
        y, mo = int(month[:4]), int(month[5:7])
        nxt = date(y + (mo == 12), 1 if mo == 12 else mo + 1, 1)
    except (TypeError, ValueError, IndexError):
        return None
    d = nxt - timedelta(days=1)
    for _ in range(31):
        if d.month != mo:
            return None
        if is_trading_day(d):
            return d
        d -= timedelta(days=1)
    return None


def remark_entry(month: str, me: date, sensex_move: float, sector_move: float,
                 beta: Optional[float], target: float, close: Optional[float],
                 sector_leg: str) -> Dict[str, Any]:
    """One row of a position's re-mark history (kept REMARKS_CAP rows)."""
    return {"month": month, "month_end": me.isoformat(),
            "sensex_move": round(sensex_move, 4), "sector_move": round(sector_move, 4),
            "beta": None if beta is None else round(float(beta), 3),
            "target": round(float(target), 2),
            "close": None if close is None else round(float(close), 2),
            "sector_leg": sector_leg}
