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
from datetime import date, datetime, timedelta, timezone
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

# Owner order 2026-09-09 ("fix it"): the app could not SEE a sector move.
# Nifty IT fell 10% in six sessions and no screen in the app said so — the
# only sector view was an equal-weight index built from the frozen 191-name
# panel. These are the real NSE sector indices, every key VERIFIED live from
# the sandbox on 2026-09-09 through the PUBLIC (tokenless) candle endpoint.
# Keys that Upstox rejects with a 400 are deliberately absent rather than
# guessed: Consumer Durables, Oil & Gas and Healthcare did not resolve, and
# a wrong key would store another index's prices under a sector's name.
SECTOR_INDEX_KEYS: dict[str, str] = {
    "IT": "NSE_INDEX|Nifty IT",
    "Bank": "NSE_INDEX|Nifty Bank",
    "PSU Bank": "NSE_INDEX|Nifty PSU Bank",
    "Fin Service": "NSE_INDEX|Nifty Fin Service",
    "Auto": "NSE_INDEX|Nifty Auto",
    "Pharma": "NSE_INDEX|Nifty Pharma",
    "FMCG": "NSE_INDEX|Nifty FMCG",
    "Metal": "NSE_INDEX|Nifty Metal",
    "Energy": "NSE_INDEX|Nifty Energy",
    "Realty": "NSE_INDEX|Nifty Realty",
    "Infra": "NSE_INDEX|Nifty Infra",
    "Media": "NSE_INDEX|Nifty Media",
    "Midcap 100": "NSE_INDEX|NIFTY MIDCAP 100",
    "Smallcap 100": "NSE_INDEX|NIFTY SMLCAP 100",
    "Next 50": "NSE_INDEX|Nifty Next 50",
    "Nifty 500": "NSE_INDEX|Nifty 500",
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


def fetch_sector_tail(broker, last_iso: str, days_back: int = 30) -> list[dict]:
    """Daily closes for the real NSE sector indices strictly AFTER last_iso.

    Rows are {date, sector, open, high, low, close}. Tokenless (public
    candles). A sector whose fetch dies is simply absent from the result:
    a missing sector reads DATA_NEEDED on the page, never a flat 0% move.

    `last_iso` empty = seed `days_back` calendar days so the first run has
    enough history to show a 5-day change instead of a blank card.
    """
    if broker is None:
        return []
    if last_iso:
        start = (date.fromisoformat(last_iso) + timedelta(days=1)).isoformat()
    else:
        start = (date.today() - timedelta(days=days_back)).isoformat()
    today = date.today().isoformat()
    if start > today:
        return []
    out: list[dict] = []
    for sector, key in SECTOR_INDEX_KEYS.items():
        try:
            df = broker.get_daily_candles(key, start, today)
        except Exception as e:                                 # noqa: BLE001
            log.warning("sector tail fetch failed for %s: %s", sector, e)
            continue
        if df is None or df.empty:
            continue
        for ts, r in df.iterrows():
            d = ts.date().isoformat()
            if last_iso and d <= last_iso:
                continue
            out.append({"date": d, "sector": sector,
                        "open": float(r["Open"]), "high": float(r["High"]),
                        "low": float(r["Low"]), "close": float(r["Close"])})
    return out


def sector_moves(rows: list) -> list[dict]:
    """Latest close per sector with its 1-day and 5-day change, worst first.

    Pure reduction over stored rows — no network, no state. A sector with
    only one stored day reports its close and None for the changes; the
    page prints a dash, never a 0% that would read as "flat".
    """
    by: dict[str, list] = {}
    for r in rows or []:
        s = str(r.get("sector") or "")
        if s and r.get("close") is not None:
            by.setdefault(s, []).append((str(r.get("date") or ""), float(r["close"])))
    out = []
    for s, xs in by.items():
        xs.sort()
        last_d, last_c = xs[-1]
        def chg(n):
            if len(xs) <= n or xs[-1 - n][1] <= 0:
                return None
            return round((last_c / xs[-1 - n][1] - 1) * 100, 2)
        out.append({"sector": s, "close": round(last_c, 2), "asof": last_d,
                    "chg_1d": chg(1), "chg_5d": chg(5), "days": len(xs)})
    out.sort(key=lambda r: (r["chg_1d"] is None, r["chg_1d"] if r["chg_1d"] is not None else 0))
    return out


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


# -------------------------------------------------- Upstox market FII / DII
# Owner order 2026-08-24 ("with upstox key u can wire the FII data"):
# /v2/market/fii + /v2/market/dii (Market Information APIs, 2026-05-11;
# data from 2026-04-01). Segment-level FII derivatives + NSE-Cash DII —
# NOT per stock; no public feed serves daily per-stock FII anywhere.
FII_DATA_TYPES = (
    "NSE_FO|INDEX_FUTURES",
    "NSE_FO|STOCK_FUTURES",
    "NSE_FO|INDEX_OPTIONS",
    "NSE_FO|STOCK_OPTIONS",
)

_pwoi_reader: Optional[Callable[[], list]] = None


def set_pwoi_reader(fn: Callable[[], list]) -> None:
    global _pwoi_reader
    _pwoi_reader = fn


def stored_pwoi_rows() -> list[dict]:
    """Stored Upstox-fed FII PWOI-shaped rows, [] when no store."""
    if _pwoi_reader is None:
        return []
    try:
        return list(_pwoi_reader() or [])
    except Exception as e:                                     # noqa: BLE001
        log.warning("stored PWOI rows unavailable: %s", e)
        return []


_IST = timezone(timedelta(hours=5, minutes=30))


def _row_date(row: dict) -> str:
    """Trading date of one market-info row, ISO, or "" when unprovable.

    FIXED 2026-10-04 (owner: "fix the FII feed reading too"): Upstox sends
    `time_stamp` — Unix MILLISECONDS (verified from the live payload's shape
    on /healthz and the API docs) — and this only looked for string date
    fields, so EVERY real row was dropped and FII-04/05 never moved past the
    committed panel. The stamp is IST midnight of the trading day
    (1777487400000 = 2026-04-29T18:30Z = 2026-04-30 00:00 IST), so it is
    read in IST: a UTC read would date every row one day early."""
    ts = row.get("time_stamp")
    if ts is not None and not isinstance(ts, bool):
        try:
            t = float(ts)
            if t > 1e11:                       # milliseconds (the documented unit)
                t /= 1000.0
            if t > 1e9:                        # plausible epoch seconds (> 2001)
                return datetime.fromtimestamp(t, _IST).date().isoformat()
        except (TypeError, ValueError, OverflowError, OSError):
            pass
    for k in ("date", "trade_date", "timestamp", "as_of_date"):
        v = row.get(k)
        if v:
            return str(v)[:10]
    return ""


def _row_num(row: dict, *keys: str) -> Optional[float]:
    for k in keys:
        v = row.get(k)
        if v is not None:
            try:
                return float(str(v).replace(",", ""))
            except ValueError:
                continue
    return None


def map_market_fii(payload) -> list[dict]:
    """Upstox /market/fii payload -> PWOI-shaped FII rows.

    SHAPE VERIFIED 2026-10-04 (live /healthz `market_info_shape` + the API
    docs): {"NSE_FO|INDEX_FUTURES": [row, ...], "NSE_FO|STOCK_FUTURES":
    [...], "NSE_FO|INDEX_OPTIONS": [...], "NSE_FO|STOCK_OPTIONS": [...]},
    30 daily rows each, newest first; a row carries time_stamp (ms),
    total_long/short_contracts and the four total_call/put_long/short
    counts. Units are contracts, the same as the committed NSE panel
    (FII stock-futures long ~3.5M there, ~4.0M in the docs' example).

    A row enters the store only when it carries a date AND index-futures
    long+short — the two numbers FII-04/05 read. Option legs fill the
    panel's opt_* columns, and total_long/short (NSE's own sum of all six
    long or short legs, checked on the committed 2026-08-07 row) are set
    only when every leg is present — a partial sum would be a wrong
    number, not a missing one."""
    segments: dict[str, list] = {}
    if isinstance(payload, dict):
        for k, v in payload.items():
            if isinstance(v, list):
                segments[str(k).upper()] = v
    elif isinstance(payload, list):
        segments["ALL"] = payload
    out: dict[str, dict] = {}
    for seg, rows in segments.items():
        for row in rows:
            if not isinstance(row, dict):
                continue
            d = _row_date(row)
            if not d:
                continue
            seg_name = str(row.get("data_type") or row.get("segment")
                           or seg).upper()
            long_v = _row_num(row, "long_contracts", "long_oi", "long",
                              "total_long_contracts", "oi_long")
            short_v = _row_num(row, "short_contracts", "short_oi", "short",
                               "total_short_contracts", "oi_short")
            if long_v is None or short_v is None:
                continue
            rec = out.setdefault(d, {"date": d, "client_type": "FII",
                                     "source": "upstox_market_fii"})
            if "INDEX_FUT" in seg_name:
                rec["fut_idx_long"], rec["fut_idx_short"] = long_v, short_v
            elif "STOCK_FUT" in seg_name:
                rec["fut_stk_long"], rec["fut_stk_short"] = long_v, short_v
            elif "INDEX_OPT" in seg_name or "STOCK_OPT" in seg_name:
                pre = "opt_idx" if "INDEX_OPT" in seg_name else "opt_stk"
                legs = {
                    f"{pre}_call_long": _row_num(row, "total_call_long_contracts"),
                    f"{pre}_put_long": _row_num(row, "total_put_long_contracts"),
                    f"{pre}_call_short": _row_num(row, "total_call_short_contracts"),
                    f"{pre}_put_short": _row_num(row, "total_put_short_contracts"),
                }
                if all(v is not None for v in legs.values()):
                    rec.update(legs)
    rows = []
    for d, r in sorted(out.items()):
        # only rows carrying the index-futures legs qualify (FII-04/05's input)
        if "fut_idx_long" not in r or "fut_idx_short" not in r:
            continue
        longs = [r.get(k) for k in _PWOI_LONG_LEGS]
        shorts = [r.get(k) for k in _PWOI_SHORT_LEGS]
        if all(v is not None for v in longs + shorts):
            r["total_long"], r["total_short"] = sum(longs), sum(shorts)
        rows.append(r)
    return rows


# NSE's participant-OI "Total Long/Short" = the sum of these six legs —
# verified on the committed FII row of 2026-08-07 (5,448,595 / 4,766,465).
_PWOI_LONG_LEGS = ("fut_idx_long", "fut_stk_long", "opt_idx_call_long",
                   "opt_idx_put_long", "opt_stk_call_long", "opt_stk_put_long")
_PWOI_SHORT_LEGS = ("fut_idx_short", "fut_stk_short", "opt_idx_call_short",
                    "opt_idx_put_short", "opt_stk_call_short",
                    "opt_stk_put_short")


# Above this a DII amount is rupees, not crore (see map_market_dii).
_RUPEES_NOT_CRORE = 1_000_000.0


def map_market_dii(payload) -> list[dict]:
    """Upstox /market/dii payload -> [{date, dii_buy, dii_sell, dii_net}]
    in RUPEES CRORE, the unit every stored and committed flow row uses.

    Payload shape (API docs, 2026-10-04): {"NSE_EQ|CASH": [row, ...]},
    rows dated by time_stamp (ms). The docs call the amounts INR, but their
    own FII example reads like crore (stock-futures buy 23,109.75), so the
    unit is decided by size, not by the label: a DII day's gross buy is
    thousands of crore, so a figure above 10 lakh can only be rupees (it
    would be 10 lakh crore — more than a year of NSE cash turnover) and is
    divided by 1e7; anything smaller is already crore. All-zero rows are
    refused (a parse miss is not a flat market — the 2026-07-31 lesson)."""
    rows = payload if isinstance(payload, list) else []
    if isinstance(payload, dict):
        for v in payload.values():
            if isinstance(v, list):
                rows = v
                break
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        d = _row_date(row)
        buy = _row_num(row, "buy_amount", "buy_value", "buy", "gross_buy")
        sell = _row_num(row, "sell_amount", "sell_value", "sell",
                        "gross_sell")
        if not d or buy is None or sell is None:
            continue
        if buy == 0 and sell == 0:
            continue
        if max(abs(buy), abs(sell)) > _RUPEES_NOT_CRORE:
            buy, sell = buy / 1e7, sell / 1e7
        out.append({"date": d, "dii_buy": round(buy, 2),
                    "dii_sell": round(sell, 2),
                    "dii_net": round(buy - sell, 2)})
    return sorted(out, key=lambda r: r["date"])


def extend_pwoi_df(df):
    """Append stored Upstox FII rows past the committed PWOI panel's last
    date. Same degrade-to-before contract as extend_index_df — no store or
    any failure returns the committed frame untouched."""
    try:
        import pandas as pd
        stored = stored_pwoi_rows()
        if not stored or df is None or df.empty or "date" not in df.columns:
            return df, 0
        last = str(df["date"].astype(str).max())
        add = [r for r in stored if str(r.get("date", "")) > last]
        if not add:
            return df, 0
        out = pd.concat([df, pd.DataFrame(add)], ignore_index=True)
        return out.sort_values("date"), len(add)
    except Exception as e:                                     # noqa: BLE001
        log.warning("extend_pwoi_df failed: %s", e)
        return df, 0


# ------------------------------------------------------------- fundamentals
def fetch_fundamentals_budgeted(symbols: list[str],
                                time_budget_s: Optional[float] = None,
                                sleep_s: float = 0.2,
                                timeout: float = 20.0) -> dict:
    """Yahoo direct timeseries rows for bare NSE symbols (no .NS), under a
    TIME BUDGET (owner order 2026-10-12: "add a time budget on the
    fundamentals step").

    WHY: this step runs inside the weekly block BEFORE the universe rebuild
    and the OHLCV pull. Its first October run covered ~1,205 names with one
    20 s-timeout GET each and NO total bound — when Yahoo hung, the block sat
    in it for 89 minutes (Fri 10-09) and again on Mon 10-12, and everything
    sequenced after it never ran. The budget is checked BEFORE each symbol,
    so one run overshoots by at most one request (timeout + sleep). Symbols
    never reached come back as `remaining` so the caller resumes from them
    next time — the OHLCV pull's own pattern (PR #103). `time_budget_s`
    None or <= 0 = unbounded (the old behaviour).

    Returns {rows, n_ok, attempted, remaining, elapsed_s, stopped}:
      rows       symbol/field/as_of/value — the committed CSV's own schema
      n_ok       symbols that answered with at least one datapoint
      attempted  symbols a request was sent for (answered or failed), in order
      remaining  symbols never attempted because the budget ran out first
      stopped    "time_budget" when it stopped early, else None
    Symbols are URL-encoded (the M&M / GVT&D lesson)."""
    types = ",".join(FUND_FIELDS_ANNUAL + FUND_FIELDS_QUARTERLY)
    headers = {"User-Agent": "Mozilla/5.0"}
    p2 = int(time.time())
    budget = float(time_budget_s) if time_budget_s and time_budget_s > 0 else None
    t0 = time.monotonic()
    rows: list[dict] = []
    n_ok = 0
    attempted: list[str] = []
    stopped: Optional[str] = None
    for s in symbols:
        if budget is not None and (time.monotonic() - t0) >= budget:
            stopped = "time_budget"
            break
        attempted.append(s)
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
    remaining = list(symbols[len(attempted):]) if stopped else []
    return {"rows": rows, "n_ok": n_ok, "attempted": attempted,
            "remaining": remaining,
            "elapsed_s": round(time.monotonic() - t0, 1), "stopped": stopped}


def fetch_fundamentals(symbols: list[str], sleep_s: float = 0.2,
                       timeout: float = 20.0) -> tuple[list[dict], int]:
    """The UNBOUNDED form, kept for the research kit and older callers:
    (rows, n_ok). The weekly block uses fetch_fundamentals_budgeted."""
    res = fetch_fundamentals_budgeted(symbols, None, sleep_s=sleep_s,
                                      timeout=timeout)
    return res["rows"], res["n_ok"]
