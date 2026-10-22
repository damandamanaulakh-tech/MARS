"""Stored feeds for the composite score's two "fundamental" legs.

OWNER DECISION 2026-10-22 (card D2, "Feed the two legs from stored data").
Intraday / short / swing / positional score on the AM07 composite. Two of its
legs — earnings growth and analyst rating — asked Yahoo's `.info` for every
name, and Yahoo blocks the Render box, so on the live app both legs sat at
neutral 50 for every stock. Positional carries 35% of its weight on those two
legs, so its best possible score was ~77.5 against a pass mark of 68.

This module reads the SAME kind of numbers from data the app already holds:

  earnings growth  the committed statements file
                   data/fundamentals_n200_2026-08-09.csv (200 Nifty-200 names)
                   with the monthly Mongo refresh rows laid on top (the
                   data_refresh reader web/state injects at boot; a stored row
                   wins on the same symbol/field/date — it is the fresher
                   fetch of the same reported figure, register_v4's rule).
  analyst rating   the committed data/analyst_ratings.csv (189 names), whose
                   `mean_rating` column IS Yahoo's financialData
                   .recommendationMean — the exact number the live Yahoo path
                   reads — fetched once on 2026-08-13.

Order in scoring.py: stored first, Yahoo second (unchanged, with its
timeouts), neutral 50 last. Nothing here is guessed: a name the files do not
cover returns None and the next source is asked.

Backtests: both readers return None in backtest mode. These files are
TODAY'S snapshot; a replay as of 2021 must not read 2026 results.

Memory (512 MB box): only the three statement fields the score needs are
kept, and the whole cache is rebuilt at most once per IST day (the monthly
refresh lands monthly, so a day's lag costs nothing).
"""

from __future__ import annotations

import csv
import logging
import math
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[1]
FUND_CSV = _ROOT / "data" / "fundamentals_n200_2026-08-09.csv"
ANALYST_CSV = _ROOT / "data" / "analyst_ratings.csv"

#: Every number this module chooses, with where it comes from.
ASSUMED = {
    # Yahoo's earningsQuarterlyGrowth (the field the live Yahoo path reads)
    # is "latest quarter vs the same quarter a year earlier". A stored quarter
    # counts as "a year earlier" when its period end lies 365 +/- 31 days
    # before the latest one: quarter ends are calendar month-ends, so a real
    # year-ago quarter is 365/366 days back and a neighbouring quarter is
    # ~90 days off — the 31-day band cannot pick the wrong quarter.
    "year_ago_days": 365,
    "year_ago_slack_days": 31,
    # A latest QUARTER older than this is stale (not used). SEBI LODR gives a
    # company 45 days to publish a quarter (60 for the March quarter), so the
    # newest published quarter is at most ~150 days old; 200 allows one
    # missed monthly refresh on top.
    "quarter_max_age_days": 200,
    # A latest FINANCIAL YEAR older than this is stale. The next FY's results
    # are due ~60 days after it ends, so the newest published FY end is at
    # most ~425 days old; 460 allows one missed monthly refresh.
    "annual_max_age_days": 460,
    # Two periods of the same company more than 20x apart are a unit or
    # currency mix-up sooner than an earnings change: the store carries the
    # ADR-listed names (INFY, WIPRO, ...) in US DOLLARS (INFY net income
    # 3.31 bn vs ~Rs 28,000 cr reported), ~90x below rupee figures — a
    # rupee row laid over a dollar row must never read as +8,900% growth.
    "max_credible_ratio": 20.0,
    # The analyst ratings file was fetched once (scripts/fetch_pending_feeds
    # .py, Yahoo quoteSummary) on this date; it carries no date column. The
    # source text on every reading names it so a two-month-old consensus
    # never reads as today's.
    "analyst_file_asof": "2026-08-13",
    # 21 of the 189 rows have no recommendationMean but do carry the broker
    # COUNTS (recommendationTrend, period 0m). For those the mean is taken
    # from the counts on Yahoo's own 1-5 scale (strong buy 1 .. strong sell
    # 5) and then mapped exactly like a Yahoo mean. Labelled "(counts)".
    "analyst_count_weights": {"strong_buy": 1.0, "buy": 2.0, "hold": 3.0,
                              "sell": 4.0, "strong_sell": 5.0},
}

#: The statement fields the earnings-growth reading uses. Net income (not
#: EPS) on purpose: Yahoo's own earningsQuarterlyGrowth is net-income based,
#: and net income is immune to a split or bonus re-basing the share count.
_FIELDS = ("quarterlyNetIncome", "annualNetIncome")

_LOCK = threading.Lock()
_FUND: dict[str, dict[str, dict[str, float]]] = {}
_FUND_DAY: Optional[str] = None
_ANALYST: dict[str, dict] = {}
_ANALYST_LOADED = False


def _ist_today() -> date:
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()


def _base(ticker: str) -> str:
    t = str(ticker or "").strip().upper()
    for suf in (".NS", ".BO"):
        if t.endswith(suf):
            t = t[: -len(suf)]
    return t


def _backtest() -> bool:
    try:
        from .data import is_backtest_mode
        return is_backtest_mode()
    except Exception:                                       # noqa: BLE001
        return False


def _finite(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# ------------------------------------------------------------ fundamentals
def _load_fundamentals() -> None:
    """(Re)build the slim cache: committed CSV, then the Mongo overlay."""
    global _FUND_DAY
    fresh: dict[str, dict[str, dict[str, float]]] = {}
    try:
        with open(FUND_CSV, newline="") as f:
            for r in csv.DictReader(f):
                if r.get("field") not in _FIELDS:
                    continue
                v = _finite(r.get("value"))
                if v is None:
                    continue
                fresh.setdefault(r["symbol"].upper(), {}).setdefault(
                    r["field"], {})[str(r["as_of"])[:10]] = v
    except OSError as e:
        log.warning("stored_feeds: fundamentals CSV unreadable: %s", e)
    try:
        from . import data_refresh as _dr
        for r in _dr.stored_fundamentals_rows():
            if r.get("field") not in _FIELDS:
                continue
            v = _finite(r.get("value"))
            if v is None:
                continue
            fresh.setdefault(str(r.get("symbol", "")).upper(), {}).setdefault(
                r["field"], {})[str(r.get("as_of", ""))[:10]] = v
    except Exception as e:                                  # noqa: BLE001
        log.warning("stored_feeds: fundamentals overlay failed: %s", e)
    _FUND.clear()
    _FUND.update(fresh)
    _FUND_DAY = _ist_today().isoformat()


def _fundamentals(symbol: str) -> dict[str, dict[str, float]]:
    with _LOCK:
        if _FUND_DAY != _ist_today().isoformat():
            _load_fundamentals()
        return _FUND.get(symbol, {})


def clear_cache() -> None:
    """Forget both caches (tests; a caller that just stored new rows)."""
    global _FUND_DAY, _ANALYST_LOADED
    with _LOCK:
        _FUND.clear()
        _FUND_DAY = None
        _ANALYST.clear()
        _ANALYST_LOADED = False


def _year_ago(series: dict[str, float], latest: str) -> Optional[tuple[str, float]]:
    d_latest = date.fromisoformat(latest)
    want = ASSUMED["year_ago_days"]
    slack = ASSUMED["year_ago_slack_days"]
    best = None
    for d, v in series.items():
        if d == latest:
            continue
        try:
            gap = (d_latest - date.fromisoformat(d)).days
        except ValueError:
            continue
        if abs(gap - want) <= slack and (best is None
                                         or abs(gap - want) < best[0]):
            best = (abs(gap - want), d, v)
    return (best[1], best[2]) if best else None


def _growth(series: dict[str, float], max_age_days: int, basis: str,
            today: date) -> Optional[dict]:
    if not series:
        return None
    latest = max(series)
    try:
        age = (today - date.fromisoformat(latest)).days
    except ValueError:
        return None
    if age > max_age_days or age < 0:
        return None
    prior = _year_ago(series, latest)
    if prior is None:
        return None
    cur, base = series[latest], prior[1]
    if base <= 0:
        # Growth off a loss or a zero is not a number (a loss-to-profit turn
        # reads as "-300%" by the formula). Not guessed: the next source is
        # asked, and with none the leg stays neutral.
        return None
    ratio = cur / base
    lim = ASSUMED["max_credible_ratio"]
    if not (1.0 / lim <= abs(ratio) <= lim):
        return None
    return {"growth_pct": round((ratio - 1.0) * 100.0, 2), "basis": basis,
            "latest": latest, "prior": prior[0]}


def earnings_growth(ticker: str, today: Optional[date] = None) -> Optional[dict]:
    """Year-on-year net-income growth from the STORED statements, or None.

    Latest quarter vs the same quarter a year earlier first (Yahoo's own
    definition of the number the live path reads); when the quarters do not
    give a fresh pair, the latest financial year vs the one before. Returns
    {growth_pct, basis 'quarter'|'year', latest, prior, source} or None."""
    if _backtest():
        return None
    sym = _base(ticker)
    if not sym:
        return None
    today = today or _ist_today()
    f = _fundamentals(sym)
    out = _growth(f.get("quarterlyNetIncome", {}),
                  ASSUMED["quarter_max_age_days"], "quarter", today)
    if out is None:
        out = _growth(f.get("annualNetIncome", {}),
                      ASSUMED["annual_max_age_days"], "year", today)
    if out is None:
        return None
    what = "quarter" if out["basis"] == "quarter" else "year"
    out["source"] = (f"fundamentals store, {what} to {out['latest']} vs "
                     f"{out['prior']}")
    return out


# ----------------------------------------------------------------- analyst
def _load_analysts() -> None:
    global _ANALYST_LOADED
    _ANALYST.clear()
    try:
        with open(ANALYST_CSV, newline="") as f:
            for r in csv.DictReader(f):
                sym = str(r.get("symbol", "")).strip().upper()
                if sym and sym not in _ANALYST:     # first row = newest period
                    _ANALYST[sym] = r
    except OSError as e:
        log.warning("stored_feeds: analyst ratings CSV unreadable: %s", e)
    _ANALYST_LOADED = True


def analyst_reading(ticker: str) -> Optional[dict]:
    """The consensus mean (Yahoo 1-5 scale) for one name from the committed
    ratings file, or None. {mean, n_analysts, basis 'mean'|'counts', source}."""
    if _backtest():
        return None
    sym = _base(ticker)
    with _LOCK:
        if not _ANALYST_LOADED:
            _load_analysts()
        r = _ANALYST.get(sym)
    if not r:
        return None
    n = _finite(r.get("n_analysts"))
    mean = _finite(r.get("mean_rating"))
    basis = "mean"
    if mean is None:
        w = ASSUMED["analyst_count_weights"]
        counts = {k: _finite(r.get(k)) or 0.0 for k in w}
        total = sum(counts.values())
        if total <= 0:
            return None
        mean = sum(counts[k] * w[k] for k in w) / total
        basis = "counts"
        if n is None:
            n = total
    if not (1.0 <= mean <= 5.0) or not n or n <= 0:
        return None
    src = f"ratings file {ASSUMED['analyst_file_asof']}, {int(n)} analysts"
    if basis == "counts":
        src += " (counts)"
    return {"mean": round(mean, 4), "n_analysts": int(n), "basis": basis,
            "source": src}
