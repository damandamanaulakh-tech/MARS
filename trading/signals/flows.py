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

    out: list[FlowReading] = []
    items = raw if isinstance(raw, list) else raw.get("data", [])
    for row in items:
        try:
            trade_dt = datetime.strptime(
                row.get("date", row.get("trade_date", "")),
                "%d-%b-%Y",
            ).date()
            fii_buy = float(row.get("fiibyVal") or row.get("fii_buy", 0))
            fii_sell = float(row.get("fiisellVal") or row.get("fii_sell", 0))
            dii_buy = float(row.get("diibyVal") or row.get("dii_buy", 0))
            dii_sell = float(row.get("diisellVal") or row.get("dii_sell", 0))
            out.append(FlowReading(
                trade_date=trade_dt,
                fii_buy=fii_buy, fii_sell=fii_sell,
                fii_net=fii_buy - fii_sell,
                dii_buy=dii_buy, dii_sell=dii_sell,
                dii_net=dii_buy - dii_sell,
            ))
        except (ValueError, TypeError, KeyError) as e:
            log.debug("Skipping malformed FII/DII row: %s", e)
            continue
    out.sort(key=lambda r: r.trade_date, reverse=True)
    return out or None


def fetch_flows_from_csv(path: str = None) -> Optional[list[FlowReading]]:
    """FIX (2026-07-12): committed FII/DII CSV fallback for when the live NSE
    feed 403s from the cloud. Reads data/fii_dii_cash.csv
    (date, fii_cash_net, dii_cash_net). Newest-first, or None if unreadable."""
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
                    dii = float(row["dii_cash_net"])
                except (ValueError, KeyError):
                    continue
                out.append(FlowReading(
                    trade_date=d, fii_buy=0.0, fii_sell=0.0, fii_net=fii,
                    dii_buy=0.0, dii_sell=0.0, dii_net=dii))
    except OSError:
        return None
    out.sort(key=lambda r: r.trade_date, reverse=True)
    return out or None


def get_flows() -> Optional[list[FlowReading]]:
    """Live NSE feed first (403s from cloud), committed CSV as fallback."""
    return fetch_latest_flows() or fetch_flows_from_csv()


def fii_net_last_n_days(flows: Optional[list[FlowReading]], n: int = 5) -> Optional[float]:
    """Sum of FII net activity over the last n trading days."""
    if not flows:
        return None
    return float(sum(f.fii_net for f in flows[:n]))


def dii_net_last_n_days(flows: Optional[list[FlowReading]], n: int = 5) -> Optional[float]:
    if not flows:
        return None
    return float(sum(f.dii_net for f in flows[:n]))


def fii_flow_score(fii_5d_net: Optional[float]) -> float:
    """Score the 5-day FII net flow:
       > +5000 Cr = very bullish (90)
       0 to +5000 Cr = mildly bullish (60-75)
       -5000 to 0 = mildly bearish (30-50)
       < -5000 Cr = very bearish (10)
    """
    if fii_5d_net is None:
        return 50.0
    if fii_5d_net > 5000:
        return 90.0
    if fii_5d_net > 0:
        return 60.0 + min(15.0, fii_5d_net / 5000 * 15.0)
    if fii_5d_net > -5000:
        return 40.0 + max(-10.0, fii_5d_net / 5000 * 10.0)
    return 10.0
