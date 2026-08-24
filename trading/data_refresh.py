"""Node 3 (owner order 2026-08-24: "node 3") — the auto-pull for the data
files that used to freeze at their commit date.

Three feeds, all proven cloud-reachable, all stored in Mongo (Render's disk
is wiped on every restart — a runtime CSV write would be a lie that lasts
until the next deploy):

* INDEX DAILY  — weekly tail of the four dashboard indices (Nifty 50,
  Sensex, India VIX, Bank Nifty) via Upstox PUBLIC candles (the F1 fact:
  no token needed). Consumers merge the stored tail onto the committed
  data/index_daily_15y.csv, which stays the deterministic base. This is
  not only cosmetic: T12W_0095 (a live danger tripwire) reads the Nifty
  series through external_panels — before this module its index state was
  frozen at the CSV's last committed row.
* CONSTITUENTS — monthly re-snapshot of the Nifty 50/200/500 lists from
  niftyindices.com (same GET the 2026-08-09 fetch used). Builds the
  forward point-in-time record data/index_constituents/ started.
* FUNDAMENTALS — monthly refresh of the Yahoo direct timeseries (the raw
  endpoint; the yfinance wrapper returns empty where the endpoint has
  data — measured 2026-08-09) for the custom universe + held names.
  register_v4.load_fundamentals overlays the stored rows on the
  committed CSV.

Layering: this is a trading/ module, so it NEVER imports web.db. The web
layer injects the Mongo readers at boot (set_index_reader /
set_fundamentals_reader) — the same broker-attach pattern flows.py uses.
A missing or failing reader returns [] and every consumer behaves exactly
as it did before this module existed (the committed file alone).
"""
from __future__ import annotations

import csv
import io
import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

_DATA = Path(__file__).resolve().parents[1] / "data"
_INDEX_CSV = _DATA / "index_daily_15y.csv"

# CSV column prefix per index symbol (the committed file's own naming).
INDEX_FIELDS = {
    "^NSEI": "nifty",
    "^BSESN": "sensex",
    "^INDIAVIX": "india_vix",
    "^NSEBANK": "bank_nifty",
}

CONSTITUENT_LISTS = ("ind_nifty50list", "ind_nifty200list",
                     "ind_nifty500list")
_CONSTITUENT_URL = "https://niftyindices.com/IndexConstituent/{name}.csv"

# Yahoo fundamentals-timeseries fields — identical to the committed
# 2026-08-09 fetch (research/tools/fetch_kit/fetch_fundamentals.py) so the
# stored rows and the committed CSV speak the same schema.
FUND_FIELDS_ANNUAL = [
    "annualTotalRevenue", "annualGrossProfit", "annualNetIncome",
    "annualOperatingIncome", "annualTotalAssets", "annualStockholdersEquity",
    "annualTotalDebt", "annualOperatingCashFlow", "annualFreeCashFlow",
    "annualBasicEPS",
]
FUND_FIELDS_QUARTERLY = [
    "quarterlyTotalRevenue", "quarterlyNetIncome", "quarterlyBasicEPS",
    "quarterlyOperatingIncome",
]
_FUND_PERIOD1 = 1420070400          # 2015-01-01, same as the committed fetch


# ---------------------------------------------------------------- injection
_index_reader: Optional[Callable[[], list]] = None
_fund_reader: Optional[Callable[[], list]] = None


def set_index_reader(fn: Callable[[], list]) -> None:
    global _index_reader
    _index_reader = fn


def set_fundamentals_reader(fn: Callable[[], list]) -> None:
    global _fund_reader
    _fund_reader = fn


def stored_index_rows() -> list[dict]:
    """Stored index-daily rows (ascending by date), [] when no store."""
    if _index_reader is None:
        return []
    try:
        return list(_index_reader() or [])
    except Exception as e:                                     # noqa: BLE001
        log.warning("stored index rows unavailable: %s", e)
        return []


def stored_fundamentals_rows() -> list[dict]:
    """Stored fundamentals rows (symbol/field/as_of/value), [] when none."""
    if _fund_reader is None:
        return []
    try:
        return list(_fund_reader() or [])
    except Exception as e:                                     # noqa: BLE001
        log.warning("stored fundamentals unavailable: %s", e)
        return []


# ------------------------------------------------------------ index helpers
def committed_index_last() -> str:
    """ISO date of the committed index CSV's last row ('' if unreadable)."""
    try:
        import pandas as pd
        s = pd.read_csv(_INDEX_CSV, usecols=["trade_date"])["trade_date"]
        dt = pd.to_datetime(s, dayfirst=True, format="mixed").max()
        return dt.date().isoformat()
    except Exception as e:                                     # noqa: BLE001
        log.warning("committed index CSV unreadable: %s", e)
        return ""


def fetch_index_tail(broker, last_iso: str) -> list[dict]:
    """Candle rows for the four indices strictly AFTER last_iso, shaped to
    the committed CSV's columns plus a 'date' key (ISO). Tokenless — Upstox
    daily candles are public. A dead index leg just leaves its four columns
    absent from that date's row; consumers dropna per column."""
    from .brokers.upstox_instruments import INDEX_KEYS
    if broker is None or not last_iso:
        return []
    start = (date.fromisoformat(last_iso) + timedelta(days=1)).isoformat()
    today = date.today().isoformat()
    if start > today:
        return []
    rows: dict[str, dict] = {}
    for sym, prefix in INDEX_FIELDS.items():
        key = INDEX_KEYS.get(sym)
        if not key:
            continue
        try:
            df = broker.get_daily_candles(key, start, today)
        except Exception as e:                                 # noqa: BLE001
            log.warning("index tail fetch failed for %s: %s", sym, e)
            df = None
        if df is None or df.empty:
            continue
        for ts, r in df.iterrows():
            d = ts.date().isoformat()
            if d <= last_iso:
                continue
            row = rows.setdefault(d, {"date": d})
            row[f"{prefix}_open"] = float(r["Open"])
            row[f"{prefix}_high"] = float(r["High"])
            row[f"{prefix}_low"] = float(r["Low"])
            row[f"{prefix}_close"] = float(r["Close"])
    return [rows[d] for d in sorted(rows)]


def extend_index_df(df, date_col: str = "trade_date"):
    """Append stored index rows past a committed-CSV frame's last date.

    Returns (df, n_added). Any failure returns the frame untouched — the
    world before this module, never something new."""
    try:
        import pandas as pd
        stored = stored_index_rows()
        if not stored or df is None or df.empty:
            return df, 0
        last = pd.to_datetime(df[date_col]).max()
        add = []
        for r in stored:
            try:
                dt = pd.Timestamp(r["date"])
            except (KeyError, ValueError):
                continue
            if dt <= last:
                continue
            row = {date_col: dt}
            row.update({k: v for k, v in r.items()
                        if k in df.columns and k != date_col})
            add.append(row)
        if not add:
            return df, 0
        out = pd.concat([df, pd.DataFrame(add)], ignore_index=True)
        return out.sort_values(date_col), len(add)
    except Exception as e:                                     # noqa: BLE001
        log.warning("extend_index_df failed: %s", e)
        return df, 0


def extend_close_series(s, field: str):
    """Append stored values of one close column past a Series' last date.
    Same degrade-to-before contract as extend_index_df."""
    try:
        import pandas as pd
        stored = stored_index_rows()
        if not stored or s is None or len(s) == 0:
            return s
        last = s.index.max()
        add = {}
        for r in stored:
            v = r.get(field)
            if v is None:
                continue
            try:
                dt = pd.Timestamp(r["date"])
            except (KeyError, ValueError):
                continue
            if dt > last:
                add[dt] = float(v)
        if not add:
            return s
        return pd.concat([s, pd.Series(add)]).sort_index()
    except Exception as e:                                     # noqa: BLE001
        log.warning("extend_close_series failed: %s", e)
        return s


# ------------------------------------------------------------- constituents
def fetch_constituents(timeout: float = 30.0) -> dict[str, list[dict]]:
    """{list_name: rows} from niftyindices.com — the exact GET the committed
    2026-08-09 snapshots came from. A list that fails is simply absent."""
    out: dict[str, list[dict]] = {}
    headers = {"User-Agent": "Mozilla/5.0",
               "Referer": "https://niftyindices.com/"}
    for name in CONSTITUENT_LISTS:
        try:
            r = httpx.get(_CONSTITUENT_URL.format(name=name),
                          headers=headers, timeout=timeout)
            r.raise_for_status()
            rows = list(csv.DictReader(io.StringIO(r.text)))
            # a real list has a Symbol column and dozens of rows; an HTML
            # error page parsed as CSV has neither — refuse it
            if rows and "Symbol" in rows[0] and len(rows) >= 40:
                out[name] = rows
            else:
                log.warning("constituent list %s came back malformed "
                            "(%d rows)", name, len(rows))
        except Exception as e:                                 # noqa: BLE001
            log.warning("constituent fetch failed for %s: %s", name, e)
    return out


# ------------------------------------------------------------- fundamentals
def fetch_fundamentals(symbols: list[str], sleep_s: float = 0.2,
                       timeout: float = 20.0) -> tuple[list[dict], int]:
    """Yahoo direct timeseries rows for bare NSE symbols (no .NS).

    Returns (rows, n_ok). Symbols are URL-encoded (the M&M / GVT&D lesson).
    Same fields and shape as the committed CSV: symbol/field/as_of/value."""
    types = ",".join(FUND_FIELDS_ANNUAL + FUND_FIELDS_QUARTERLY)
    headers = {"User-Agent": "Mozilla/5.0"}
    p2 = int(time.time())
    rows: list[dict] = []
    n_ok = 0
    for s in symbols:
        y = quote(s + ".NS", safe="")
        url = ("https://query2.finance.yahoo.com/ws/fundamentals-timeseries/"
               f"v1/finance/timeseries/{y}?symbol={y}&type={types}"
               f"&period1={_FUND_PERIOD1}&period2={p2}")
        try:
            j = httpx.get(url, headers=headers, timeout=timeout).json()
            n = 0
            for res in (j.get("timeseries", {}) or {}).get("result", []):
                typ = (res.get("meta", {}).get("type") or [""])[0]
                for item in (res.get(typ) or []):
                    if item and item.get("reportedValue") is not None:
                        rows.append({"symbol": s, "field": typ,
                                     "as_of": item["asOfDate"],
                                     "value": float(
                                         item["reportedValue"]["raw"])})
                        n += 1
            n_ok += 1 if n else 0
        except Exception as e:                                 # noqa: BLE001
            log.warning("fundamentals fetch failed for %s: %s", s, e)
        time.sleep(sleep_s)
    return rows, n_ok
