"""FII / DII daily-flow signals.

NSE publishes FII/DII activity (Foreign Institutional Investors and
Domestic Institutional Investors) daily after market close. This is the
single biggest predictor of next-day NSE direction historically.

Free sources:
  - Moneycontrol RSS / scraping (most reliable)
  - NSE direct: https://www.nseindia.com/api/fiidiiTradeReact
    (note: NSE blocks non-browser user-agents; needs a header workaround)

Phase 4: this module wires the NSE direct endpoint with a browser-style
User-Agent header and caches results in MongoDB for the regime classifier.

If NSE rate-limits or blocks us, we degrade silently — caller gets None.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

import httpx

log = logging.getLogger(__name__)


NSE_URL = "https://www.nseindia.com/api/fiidiiTradeReact"
NSE_HOME = "https://www.nseindia.com/"

# Browser-style headers (NSE blocks anything that looks like a bot)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/reports/fii-dii",
}


@dataclass
class FlowReading:
    """One day of FII/DII activity (₹ crore)."""
    trade_date: date
    fii_buy: float
    fii_sell: float
    fii_net: float
    dii_buy: float
    dii_sell: float
    dii_net: float

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["trade_date"] = self.trade_date.isoformat()
        return d


def fetch_latest_flows() -> Optional[list[FlowReading]]:
    """Pulls the last few trading days of FII/DII data from NSE.

    Returns a list ordered newest-first, or None if NSE blocked us.
    """
    try:
        # NSE requires a session cookie first; visit homepage then API
        with httpx.Client(
            headers=_HEADERS, timeout=10.0, follow_redirects=True,
        ) as client:
            client.get(NSE_HOME)
            r = client.get(NSE_URL)
            r.raise_for_status()
            raw = r.json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as e:
        log.warning("NSE FII/DII fetch failed: %s", e)
        return None

    # FIXED 2026-07-31. The parser looked for keys fiibyVal/fiisellVal/
    # diibyVal/diisellVal. The endpoint ACTUALLY returns one row PER CATEGORY:
    #   {"category": "FII/FPI"|"DII", "date": "03-Aug-2026",
    #    "buyValue": "12621.89", "sellValue": "11699.63", "netValue": "922.26"}
    # So every key lookup missed, every value defaulted to 0, and the function
    # returned all-zero readings DATED TODAY. Those passed every freshness
    # check and scored as a mildly bearish tide (40) every single day — a
    # fabricated signal from a feed that was working the whole time.
    items = raw if isinstance(raw, list) else raw.get("data", [])
    by_date: dict = {}
    for row in items:
        if not isinstance(row, dict):
            continue
        try:
            trade_dt = datetime.strptime(str(row.get("date", "")).strip(),
                                         "%d-%b-%Y").date()
        except (ValueError, TypeError):
            log.debug("Skipping FII/DII row with unparseable date: %r",
                      row.get("date"))
            continue
        cat = str(row.get("category", "")).upper()
        try:
            buy = float(str(row.get("buyValue", "")).replace(",", ""))
            sell = float(str(row.get("sellValue", "")).replace(",", ""))
        except (TypeError, ValueError):
            log.debug("Skipping FII/DII row with unparseable values: %r", row)
            continue
        if buy == 0.0 and sell == 0.0:
            # Not data — a parse miss or an empty pre-publication row. Emitting
            # it as a zero net flow would invent a bearish signal.
            continue
        slot = by_date.setdefault(trade_dt, {})
        if "FII" in cat or "FPI" in cat:
            slot["fii"] = (buy, sell)
        elif "DII" in cat:
            slot["dii"] = (buy, sell)

    out: list[FlowReading] = []
    for d, slot in by_date.items():
        fb, fs = slot.get("fii", (0.0, 0.0))
        db_, ds = slot.get("dii", (0.0, 0.0))
        if not slot.get("fii") and not slot.get("dii"):
            continue
        out.append(FlowReading(
            trade_date=d,
            fii_buy=fb, fii_sell=fs, fii_net=fb - fs,
            dii_buy=db_, dii_sell=ds, dii_net=db_ - ds,
        ))
    out.sort(key=lambda r: r.trade_date, reverse=True)
    return out or None


def fetch_flows_from_csv(path: str = None) -> Optional[list[FlowReading]]:
    """FIX (2026-07-12): committed FII/DII CSV fallback for when the live NSE
    feed 403s from the cloud. Reads data/fii_dii_cash.csv
    (date, fii_cash_net, dii_cash_net). Newest-first, or None if unreadable.

    Rows from 2024-12 on derive from the SEBI FII disclosure archive
    (scripts/build_fii_cash_from_disclosures.py) which has NO DII data —
    their dii_cash_net is EMPTY, read as 0.0. Nothing that touches money
    consumes DII (only fii_flow_score feeds scoring/regime)."""
    import csv
    import os
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "..", "..", "data",
                            "fii_dii_cash.csv")
    out: list[FlowReading] = []
    try:
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                try:
                    d = datetime.strptime(row["date"].strip(), "%Y-%m-%d").date()
                    fii = float(row["fii_cash_net"])
                    dii = float(row["dii_cash_net"] or 0.0)
                except (ValueError, KeyError):
                    continue
                # AUDIT FIX B9: an all-zero row is a data hole, not a flat
                # market — the live parser already rejects these; the CSV
                # fallback must too, or a padded row re-fabricates the
                # "zero scored bearish" signal via the back door.
                if fii == 0.0 and dii == 0.0:
                    continue
                out.append(FlowReading(
                    trade_date=d, fii_buy=0.0, fii_sell=0.0, fii_net=fii,
                    dii_buy=0.0, dii_sell=0.0, dii_net=dii))
    except OSError:
        return None
    out.sort(key=lambda r: r.trade_date, reverse=True)
    return out or None


# A flow reading older than this is NOT a current signal. The committed CSV
# ends 2025-03-29 (SEBI publishes months late and no live feed exists), so
# without this guard the engine scored today's market on flows well over a
# year old and reported it as a live factor.
MAX_FLOW_AGE_DAYS = 45


def flows_age_days(flows: Optional[list[FlowReading]]) -> Optional[int]:
    """How old the newest flow reading is, in days. None when no data."""
    if not flows:
        return None
    return (date.today() - max(f.trade_date for f in flows)).days


# Node 3 (2026-08-23): the Mongo FII store, filled weekly from the NSDL
# archive by the Friday job. Injected by web/state at boot (the broker-attach
# pattern) so this trading-side module never imports web.db.
_STORE_READER = None


def set_store_reader(fn) -> None:
    """fn(since_iso) -> [{date, fii_buy, fii_sell, fii_net}, ...] ascending."""
    global _STORE_READER
    _STORE_READER = fn


def fetch_flows_from_store() -> Optional[list[FlowReading]]:
    """FII rows from the Mongo store (NSDL settlement basis, trading-dated).
    None when no reader is attached or the store is empty — an absent store
    must never read as zero flow."""
    if _STORE_READER is None:
        return None
    try:
        rows = _STORE_READER("") or []
    except Exception as e:                                   # noqa: BLE001
        log.warning("FII store read failed: %s", e)
        return None
    out: list[FlowReading] = []
    for r in rows:
        try:
            d = datetime.strptime(str(r.get("date", "")).strip()[:10],
                                  "%Y-%m-%d").date()
            buy = float(r.get("fii_buy") or 0.0)
            sell = float(r.get("fii_sell") or 0.0)
            net = float(r.get("fii_net") or 0.0)
        except (ValueError, TypeError):
            continue
        if buy == 0.0 and sell == 0.0 and net == 0.0:
            continue                       # a hole is not a flat market
        out.append(FlowReading(trade_date=d, fii_buy=buy, fii_sell=sell,
                               fii_net=net, dii_buy=0.0, dii_sell=0.0,
                               dii_net=0.0))
    out.sort(key=lambda r: r.trade_date, reverse=True)
    return out or None


def get_flows(max_age_days: int = MAX_FLOW_AGE_DAYS) -> Optional[list[FlowReading]]:
    """All three sources MERGED by date (Node 3, 2026-08-23):

      committed CSV (history base)  <  Mongo store (NSDL weekly tail)
                                    <  live NSE API (today, provisional)

    Later sources overwrite earlier ones per date — the live provisional
    number wins any day it covers, matching the pre-merge precedence. Before
    this merge the sources were an either/or: on days NSE answered, the
    engine saw ONE day and the 5-day sum the thresholds are calibrated on
    collapsed to a 1-day reading; on other days the committed CSV aged
    toward the 45-day guard and the factor went quiet. The store keeps the
    window populated between commits. Basis note: NSDL = settlement, NSE =
    provisional (~800 Cr/day median gap, documented in fii_nsdl.py).

    STALENESS GUARD unchanged (2026-07-31): newest reading older than
    `max_age_days` returns None — callers treat None as neutral 50. Pass
    max_age_days=0 to disable (research/backtests)."""
    by_date: dict = {}
    for source in (fetch_flows_from_csv(), fetch_flows_from_store(),
                   fetch_latest_flows()):
        for f in (source or []):
            by_date[f.trade_date] = f
    flows = sorted(by_date.values(), key=lambda r: r.trade_date,
                   reverse=True) or None
    if not flows or not max_age_days:
        return flows
    age = flows_age_days(flows)
    if age is not None and age > max_age_days:
        log.warning("FII/DII data is %d days old (> %d) — treating as NO SIGNAL",
                    age, max_age_days)
        return None
    return flows


def _last_n_trading_days(newest: date, n: int, published=()) -> set:
    """The n NSE trading days ending on `newest`. A date that HAS a flow row
    counts as a trading day whatever the holiday list says — a row the
    exchange published is a trading day by definition (the list could be
    wrong; the data cannot have traded on a closed day)."""
    try:
        from ..calendars import is_trading_day
    except Exception:                                        # noqa: BLE001
        def is_trading_day(d):                               # weekdays only
            return d.weekday() < 5
    published = set(published)
    days = {newest}
    d = newest
    guard = 0
    while len(days) < max(1, n) and guard < 60:
        d = d - timedelta(days=1)
        guard += 1
        if d in published or is_trading_day(d):
            days.add(d)
    return days


def fii_net_window(flows: Optional[list[FlowReading]], n: int = 5
                   ) -> tuple[Optional[float], int]:
    """(sum of FII net over the last n TRADING DAYS by date, how many of
    those days actually have a row).

    XR-055 (2026-10-22 entry-path batch): the sum used to take the first n
    ROWS of the newest-first series. With gaps in the merged CSV / NSDL
    store / live feed, five rows can span weeks — a "5-day" sum built from
    days outside the window. Only rows dated inside the n trading days that
    end on the newest row count now; the day count tells the caller how many
    days the sum really covers (fii_flow_score(days=) scales by it)."""
    if not flows:
        return None, 0
    newest = max(f.trade_date for f in flows)
    window = _last_n_trading_days(newest, n, {f.trade_date for f in flows})
    rows = [f for f in flows if f.trade_date in window]
    return float(sum(f.fii_net for f in rows)), len(rows)


def fii_net_last_n_days(flows: Optional[list[FlowReading]], n: int = 5) -> Optional[float]:
    """Sum of FII net activity over the last n trading days (by date —
    see fii_net_window)."""
    total, _ = fii_net_window(flows, n)
    return total


def dii_net_last_n_days(flows: Optional[list[FlowReading]], n: int = 5) -> Optional[float]:
    if not flows:
        return None
    newest = max(f.trade_date for f in flows)
    window = _last_n_trading_days(newest, n, {f.trade_date for f in flows})
    return float(sum(f.dii_net for f in flows if f.trade_date in window))


def fii_flow_score(fii_5d_net: Optional[float], days: int = 5) -> float:
    """Score the FII net flow, calibrated on a 5-day sum:
       >= +5000 Cr = very bullish (90)
       <= -5000 Cr = very bearish (10)
       in between  = a straight line through 0 Cr = neutral (50)

    AUDIT F-15 (owner: "go for next", 2026-10-02): this used to be four
    steps that jumped at the joins — -1 Cr scored 40, 0 scored 50, +1 Cr
    scored 60, and -4,999 Cr scored 30 while -5,000 Cr scored 10. A one-crore
    difference could swing the composite's catalyst tilt by 4-8 points. The
    two documented end-points (+-5,000 Cr -> 90 / 10) and neutral zero are
    kept; only the jumps are gone. Feeds the catalyst tilt of the composite
    (swing / short / positional / intraday scores); momentum entries never
    read it.

    `days` = how many days the sum actually covers (2026-07-31). The live NSE
    endpoint returns ONE day, the archive can give five; scaling to a 5-day
    equivalent keeps the thresholds meaningful either way instead of reading a
    single strong day as a weak week."""
    if fii_5d_net is None:
        return 50.0
    if days and days > 0 and days != 5:
        fii_5d_net = fii_5d_net * (5.0 / days)
    # Zero flow is NEUTRAL (audit fix B9, 2026-08-09) — the line passes
    # through 50 at zero by construction.
    return float(max(10.0, min(90.0, 50.0 + float(fii_5d_net) / 5000.0 * 40.0)))


def flow_status(max_age_days: int = MAX_FLOW_AGE_DAYS) -> dict:
    """What the desk should SAY about FII/DII (owner 2026-07-26: "FII 5-day
    net — why its not wired yet?"). It IS wired; the source is dead. The
    Engine Regime page prints this instead of the literal words 'not wired',
    so a data gap never reads as a build gap."""
    raw = fetch_latest_flows() or fetch_flows_from_csv()
    if not raw:
        return {"state": "DATA_NEEDED", "why": "No FII/DII source reachable.",
                "newest": None, "age_days": None, "fii_5d_net": None}
    age = flows_age_days(raw)
    newest = max(f.trade_date for f in raw).isoformat()
    if age is not None and age > max_age_days:
        return {"state": "STALE",
                "why": (f"Newest FII/DII row is {age} days old ({newest}). "
                        "NSE blocks cloud servers and SEBI publishes months "
                        "late, so the committed CSV ends there. Not used for "
                        "decisions."),
                "newest": newest, "age_days": age, "fii_5d_net": None}
    return {"state": "LIVE", "why": "Fresh FII/DII flow in use.",
            "newest": newest, "age_days": age,
            "fii_5d_net": round(fii_net_last_n_days(raw, 5) or 0.0, 1)}
