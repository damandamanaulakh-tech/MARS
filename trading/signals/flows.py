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


def get_flows(max_age_days: int = MAX_FLOW_AGE_DAYS) -> Optional[list[FlowReading]]:
    """Live NSE feed first (403s from cloud), committed CSV as fallback.

    STALENESS GUARD added 2026-07-31. Returns None when the newest reading is
    older than `max_age_days` — a stale flow tide is not a signal, and feeding
    one into the score dressed as current is the same fault as showing a stale
    price. Callers already treat None as neutral 50, so the factor goes quiet
    instead of lying. Pass max_age_days=0 to disable (research/backtests)."""
    flows = fetch_latest_flows() or fetch_flows_from_csv()
    if not flows or not max_age_days:
        return flows
    age = flows_age_days(flows)
    if age is not None and age > max_age_days:
        log.warning("FII/DII data is %d days old (> %d) — treating as NO SIGNAL",
                    age, max_age_days)
        return None
    return flows


def fii_net_last_n_days(flows: Optional[list[FlowReading]], n: int = 5) -> Optional[float]:
    """Sum of FII net activity over the last n trading days."""
    if not flows:
        return None
    return float(sum(f.fii_net for f in flows[:n]))


def dii_net_last_n_days(flows: Optional[list[FlowReading]], n: int = 5) -> Optional[float]:
    if not flows:
        return None
    return float(sum(f.dii_net for f in flows[:n]))


def fii_flow_score(fii_5d_net: Optional[float], days: int = 5) -> float:
    """Score the FII net flow, calibrated on a 5-day sum:
       > +5000 Cr = very bullish (90)
       0 to +5000 Cr = mildly bullish (60-75)
       -5000 to 0 = mildly bearish (30-50)
       < -5000 Cr = very bearish (10)

    `days` = how many days the sum actually covers (2026-07-31). The live NSE
    endpoint returns ONE day, the archive can give five; scaling to a 5-day
    equivalent keeps the thresholds meaningful either way instead of reading a
    single strong day as a weak week."""
    if fii_5d_net is None:
        return 50.0
    if days and days > 0 and days != 5:
        fii_5d_net = fii_5d_net * (5.0 / days)
    if fii_5d_net == 0:
        # AUDIT FIX B9 (2026-08-09): a genuine 0.0 net flow used to score 40
        # (mildly bearish) — the exact zero-reads-as-bearish pattern the FII
        # parser bug burned the owner with. Zero flow is NEUTRAL.
        return 50.0
    if fii_5d_net > 5000:
        return 90.0
    if fii_5d_net > 0:
        return 60.0 + min(15.0, fii_5d_net / 5000 * 15.0)
    if fii_5d_net > -5000:
        return 40.0 + max(-10.0, fii_5d_net / 5000 * 10.0)
    return 10.0


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
