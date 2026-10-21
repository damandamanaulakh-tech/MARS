"""Macro feeds for the valuation chain (owner order 2026-10-21, VALFIX batch 2
item 10: "10-year yield from Upstox only", RBI repo + USD/INR from the RBI
home page, institutional holding from the committed shareholding file).

DISPLAY ONLY. Nothing in the money path (engine, risk, orders, danger,
sizing, targets, exits, scoring) may import this module — the valuation
boundary test pins it.

Three stored series, one row per series per date (Mongo `macro_daily`,
keyed series|date, written by web.scheduler._macro_feeds_refresh):

* "gsec10y_yield" — yield (% a year) of the most-traded 10-year Government
  of India bond on Upstox's PUBLIC daily candles (no token). The bond list
  (ISIN, coupon, maturity) comes from RBI's "Government of India Securities
  outstanding" page; price -> yield by standard bond maths (below).
* "rbi_repo"      — policy repo rate (%) read from www.rbi.org.in. The page
  carries no date for it, so a reading is dated the day it was read (IST).
* "rbi_usdinr"    — RBI/FBIL reference rate, INR per 1 USD, dated by the
  page's own "As at 1.00pm of <date>" line.

Repo history before the first stored reading: data/rbi_repo_history.csv,
built ONLY from RBI MPC resolutions actually fetched
(scripts/fetch_rbi_repo_history.py). A date it cannot prove reads None.

Layering: a trading/ module, so it NEVER imports web.db. The web layer
injects the reader at boot (set_reader) — the data_refresh pattern. A
missing or failing reader means "nothing stored", never an invented value.
"""
from __future__ import annotations

import calendar
import csv
import html as _html
import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import quote

log = logging.getLogger(__name__)

_DATA = Path(__file__).resolve().parents[1] / "data"
REPO_HISTORY_CSV = _DATA / "rbi_repo_history.csv"
SHAREHOLDING_CSV = _DATA / "shareholding_institutional.csv"

#: rbi_gsec10y = RBI's own 10-year benchmark yield from the home page's
#: Government Securities Market box — stored with the RBI leg, printed
#: BESIDE the Upstox yield, never used (the owner chose Upstox; lead's note
#: (a), 2026-10-21).
SERIES = ("gsec10y_yield", "rbi_repo", "rbi_usdinr", "rbi_gsec10y")

RBI_HOME_URL = "https://www.rbi.org.in/"
RBI_GSEC_LIST_URL = "https://rbi.org.in/Scripts/bs_viewcontent.aspx?Id=1956"
UPSTOX_CANDLE_URL = "https://api.upstox.com/v2/historical-candle/{key}/day/{to}/{frm}"

#: ASSUMED: a "10-year paper" = residual maturity 9 to 11 years.
TEN_YEAR_WINDOW = (9.0, 11.0)
#: ASSUMED: "most traded" = highest summed volume over the last 10 sessions.
VOLUME_SESSIONS = 10
#: Contract: a reading older than this many days is STALE.
STALE_DAYS = 10
#: Calendar days of candles asked for on each pull (covers 10 sessions).
CANDLE_LOOKBACK_DAYS = 30

#: Fallback bond list when RBI's page does not answer: the rows with
#: maturity 2034-2038 from RBI's "List of Government of India Securities
#: outstanding as on October 19, 2026" (RBI_GSEC_LIST_URL, read 2026-10-21).
GSEC_REFERENCE_SOURCE = (f"{RBI_GSEC_LIST_URL} — list as on 2026-10-19, "
                         "read 2026-10-21")
GSEC_REFERENCE: tuple = (
    # isin, nomenclature, coupon %, issue date, maturity date
    ("IN0020050012", "7.40% GS 2035", 7.40, "2005-09-09", "2035-09-09"),
    ("IN0020250091", "6.48% GS 2035", 6.48, "2025-10-06", "2035-10-06"),
    ("IN0020210152", "6.67% GS 2035", 6.67, "2021-09-13", "2035-12-15"),
    ("IN0020260025", "6.94% GS 2036", 6.94, "2026-05-11", "2036-05-11"),
    ("IN0020220029", "7.54% GS 2036", 7.54, "2022-05-23", "2036-05-23"),
    ("IN0020060045", "8.33% GS 2036", 8.33, "2006-06-07", "2036-06-07"),
    ("IN0020220102", "7.41% GS 2036", 7.41, "2022-12-19", "2036-12-19"),
    ("IN0020230077", "7.18% GS 2037", 7.18, "2023-07-24", "2037-07-24"),
)

IST = timezone(timedelta(hours=5, minutes=30))


def _today_ist() -> date:
    return datetime.now(IST).date()


# ---------------------------------------------------------------- injection
_reader: Optional[Callable[..., list]] = None


def set_reader(fn: Optional[Callable[..., list]]) -> None:
    """Inject the stored-row reader (web.db.load_macro_rows)."""
    global _reader
    _reader = fn


def stored_rows(series: Optional[str] = None) -> list[dict]:
    """Stored rows for one series (or all), ascending by date. [] when no
    store — every consumer then treats the series as never read."""
    if _reader is None:
        return []
    try:
        rows = list(_reader(series) or [])
    except TypeError:
        rows = list(_reader() or [])
    except Exception as e:                                     # noqa: BLE001
        log.warning("macro rows unavailable: %s", e)
        return []
    if series:
        rows = [r for r in rows if r.get("series") == series]
    rows = [r for r in rows if r.get("date") and _num(r.get("value")) is not None]
    return sorted(rows, key=lambda r: str(r["date"]))


def _num(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def _reading(row: dict) -> tuple:
    return (float(row["value"]), str(row["date"]), str(row.get("source") or ""))


def latest(series: str) -> Optional[tuple]:
    """(value, date_iso, source) of the newest stored reading, else None."""
    rows = stored_rows(series)
    return _reading(rows[-1]) if rows else None


def latest_row(series: str) -> Optional[dict]:
    """The newest stored row itself (a copy — it carries the bond name,
    price, url), else None."""
    rows = stored_rows(series)
    return dict(rows[-1]) if rows else None


def at(series: str, date_iso: str) -> Optional[tuple]:
    """(value, date_iso, source) in force on `date_iso`, else None.

    gsec10y_yield / rbi_usdinr: the newest stored reading dated on or before
    the date (the reading's own date is returned, so a caller can see how
    far back it reached).
    rbi_repo: the repo rate is a step function, so a date is answered only
    when it can be PROVED — from the committed MPC history (with the next
    decision confirming no change in between), or from two stored daily
    readings either side of the date that agree. Anything else = None.
    """
    if not date_iso:
        return None
    d = str(date_iso)[:10]
    if series == "rbi_repo":
        hit = repo_at(d)
        if hit is not None:
            return hit
        rows = stored_rows(series)
        before = [r for r in rows if r["date"] <= d]
        after = [r for r in rows if r["date"] >= d]
        if before and after and _num(before[-1]["value"]) == _num(after[0]["value"]):
            v, _, src = _reading(before[-1])
            return (v, d, f"{src} (read {before[-1]['date']} and "
                          f"{after[0]['date']}, same rate)")
        return None
    rows = [r for r in stored_rows(series) if r["date"] <= d]
    return _reading(rows[-1]) if rows else None


def is_stale(date_iso: str, today: Optional[date] = None,
             days: int = STALE_DAYS) -> bool:
    """True when a reading dated `date_iso` is more than `days` old."""
    try:
        d = date.fromisoformat(str(date_iso)[:10])
    except ValueError:
        return True
    return ((today or _today_ist()) - d).days > days


# ------------------------------------------------------------- bond maths
# Indian G-sec market convention (FIMMDA): semi-annual coupons, 30/360
# day count for accrued interest, yield compounded semi-annually. Quoted
# prices are CLEAN (no accrued). Same formula as Excel PRICE/YIELD with
# frequency 2 and a 30/360 basis.

def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    m += 1
    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, last))


def days_30_360(d1: date, d2: date) -> int:
    """European 30/360 day count from d1 to d2."""
    a, b = min(d1.day, 30), min(d2.day, 30)
    return 360 * (d2.year - d1.year) + 30 * (d2.month - d1.month) + (b - a)


def _coupon_dates(settle: date, maturity: date) -> tuple[date, date, int]:
    """(previous coupon date, next coupon date, coupons left) at `settle`."""
    n = 0
    nxt = maturity
    while True:
        prev = _add_months(maturity, -6 * (n + 1))
        if prev <= settle:
            return prev, nxt, n + 1
        nxt = prev
        n += 1


def bond_price(yield_pct: float, coupon_pct: float, maturity_iso: str,
               settle_iso: str) -> float:
    """Clean price per 100 face at `yield_pct` (% a year, semi-annual)."""
    settle = date.fromisoformat(str(settle_iso)[:10])
    maturity = date.fromisoformat(str(maturity_iso)[:10])
    if settle >= maturity:
        raise ValueError("settlement on or after maturity")
    prev, _nxt, n = _coupon_dates(settle, maturity)
    e = 180.0
    a = float(days_30_360(prev, settle))
    dsc = e - a
    c = coupon_pct / 2.0
    y = yield_pct / 100.0 / 2.0
    if n == 1:
        return (100.0 + c) / (1.0 + dsc / e * y) - c * a / e
    t0 = dsc / e
    pv = 100.0 / (1.0 + y) ** (n - 1 + t0)
    pv += sum(c / (1.0 + y) ** (k - 1 + t0) for k in range(1, n + 1))
    return pv - c * a / e


def bond_yield(price: float, coupon_pct: float, maturity_iso: str,
               settle_iso: str) -> float:
    """Yield (% a year, semi-annual) that prices the bond at `price` (clean,
    per 100 face). Solved by bisection: price falls as yield rises."""
    p = float(price)
    if not p > 0:
        raise ValueError("price must be positive")
    lo, hi = -50.0, 100.0                  # % a year
    f_lo = bond_price(lo, coupon_pct, maturity_iso, settle_iso) - p
    for _ in range(200):
        mid = (lo + hi) / 2.0
        f_mid = bond_price(mid, coupon_pct, maturity_iso, settle_iso) - p
        if abs(f_mid) < 1e-11:
            return mid
        if (f_mid > 0) == (f_lo > 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def settle_date(trade_iso: str) -> str:
    """NSE G-sec settlement: T+1, the next weekday (exchange holidays are
    not modelled; one extra day moves a 10-year yield by ~0.0001 pp)."""
    d = date.fromisoformat(str(trade_iso)[:10]) + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.isoformat()


# ------------------------------------------------------------ 10-year paper
_GSEC_ROW = re.compile(
    r"(IN\d{4}[A-Z0-9]{6})\s+((\d+(?:\.\d+)?)%\s*GS\s*(\d{4})[A-Z ]*?)\s+"
    r"(\d{1,2}-[A-Za-z]{3}-\d{4})\s+(\d{1,2}-[A-Za-z]{3}-\d{4})")


def _dmy(s: str) -> str:
    return datetime.strptime(s, "%d-%b-%Y").date().isoformat()


def parse_gsec_list(page_html: str) -> list[dict]:
    """Rows of RBI's "Government of India Securities outstanding" page:
    {isin, name, coupon_pct, issue, maturity}. Floating-rate and
    inflation-indexed bonds (no plain "x% GS yyyy" name) are skipped."""
    text = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", page_html or "")))
    out, seen = [], set()
    for m in _GSEC_ROW.finditer(text):
        isin = m.group(1)
        if isin in seen:
            continue
        seen.add(isin)
        try:
            out.append({"isin": isin, "name": m.group(2).strip(),
                        "coupon_pct": float(m.group(3)),
                        "issue": _dmy(m.group(5)), "maturity": _dmy(m.group(6))})
        except ValueError:
            continue
    return out


def parse_gsec_asof(page_html: str) -> str:
    """ISO date of the page's "outstanding as on <date>" line, '' if none."""
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page_html or ""))
    m = re.search(r"outstanding as on ([A-Z][a-z]+ \d{1,2}, \d{4})", text)
    if not m:
        return ""
    try:
        return datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()
    except ValueError:
        return ""


def reference_securities() -> list[dict]:
    return [{"isin": i, "name": n, "coupon_pct": c, "issue": s, "maturity": m}
            for i, n, c, s, m in GSEC_REFERENCE]


def ten_year_candidates(securities: Iterable[dict], asof_iso: str,
                        window: tuple = TEN_YEAR_WINDOW) -> list[dict]:
    """Bonds whose residual maturity at `asof_iso` sits inside the window."""
    asof = date.fromisoformat(str(asof_iso)[:10])
    lo, hi = window
    out = []
    for s in securities:
        try:
            mat = date.fromisoformat(str(s["maturity"])[:10])
        except (KeyError, ValueError, TypeError):
            continue
        yrs = (mat - asof).days / 365.25
        if lo <= yrs <= hi:
            out.append({**s, "residual_years": round(yrs, 2)})
    return out


def pick_most_traded(candles: dict, sessions: int = VOLUME_SESSIONS) -> Optional[str]:
    """ISIN with the highest summed volume over its last `sessions` candles.
    `candles` = {isin: [(date_iso, close, volume), ...] ascending}. Ties go
    to the ISIN that sorts first, so the pick is deterministic."""
    best, best_vol = None, -1.0
    for isin in sorted(candles):
        rows = candles[isin] or []
        vol = sum(float(v or 0) for _d, _c, v in rows[-sessions:])
        if rows and vol > best_vol:
            best, best_vol = isin, vol
    return best if best_vol > 0 else None


def gsec_rows(bond: dict, candles: list, volume_10d: float,
              after_iso: str = "", fetched_iso: str = "") -> list[dict]:
    """Stored rows (one per trading date after `after_iso`) for the chosen
    bond: yield from each day's close, settled T+1."""
    key = f"NSE_EQ|{bond['isin']}"
    out = []
    for d, close, vol in candles:
        if after_iso and d <= after_iso:
            continue
        px = _num(close)
        if px is None or px <= 0:
            continue
        try:
            y = bond_yield(px, bond["coupon_pct"], bond["maturity"], settle_date(d))
        except ValueError:
            continue
        out.append({
            "series": "gsec10y_yield", "date": d, "value": round(y, 4),
            "source": (f"Upstox NSE candles · {bond['name']} ({bond['isin']}) "
                       f"close {px:.2f} on {d}"),
            "bond": bond["name"], "isin": bond["isin"],
            "instrument_key": key, "coupon_pct": bond["coupon_pct"],
            "maturity": bond["maturity"], "price": round(px, 4),
            "volume": float(vol or 0), "volume_10d": float(volume_10d),
            "url": UPSTOX_CANDLE_URL.format(key=quote(key, safe=""), to=d, frm=d),
            "fetched": fetched_iso,
        })
    return out


def fetch_gsec_list(timeout: float = 30.0) -> tuple[list[dict], str]:
    """(securities, source) — RBI's outstanding list, else the committed
    reference table (labelled). Never raises."""
    try:
        import httpx
        r = httpx.get(RBI_GSEC_LIST_URL, timeout=timeout,
                      headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
        r.raise_for_status()
        rows = parse_gsec_list(r.text)
        if len(rows) >= 20:
            asof = parse_gsec_asof(r.text)
            return rows, f"{RBI_GSEC_LIST_URL} (as on {asof or 'undated'})"
        log.warning("RBI G-sec list parsed only %d rows", len(rows))
    except Exception as e:                                     # noqa: BLE001
        log.warning("RBI G-sec list unavailable: %s", e)
    return reference_securities(), f"committed table: {GSEC_REFERENCE_SOURCE}"


def fetch_gsec10y(client, last_iso: str = "", securities: Optional[list] = None,
                  today: Optional[date] = None) -> list[dict]:
    """Daily 10-year yield rows strictly AFTER `last_iso`.

    `client` = any object with get_daily_candles(key, from, to) (the
    tokenless Upstox history client). Picks the most-traded 9-11-year bond
    on the latest candles, then turns each of its closes into a yield.
    [] when nothing new or nothing answers."""
    if client is None:
        return []
    today = today or _today_ist()
    src = "given list"
    if securities is None:
        securities, src = fetch_gsec_list()
    cands = ten_year_candidates(securities, today.isoformat())
    if not cands:
        log.warning("no 9-11-year G-sec in the bond list (%s)", src)
        return []
    frm = (today - timedelta(days=CANDLE_LOOKBACK_DAYS)).isoformat()
    to = today.isoformat()
    candles: dict = {}
    for b in cands:
        try:
            df = client.get_daily_candles(f"NSE_EQ|{b['isin']}", frm, to)
        except Exception as e:                                 # noqa: BLE001
            log.warning("G-sec candles failed for %s: %s", b["isin"], e)
            df = None
        if df is None or getattr(df, "empty", True):
            continue
        candles[b["isin"]] = [(ts.date().isoformat(), float(r["Close"]),
                               float(r.get("Volume", 0) or 0))
                              for ts, r in df.iterrows()]
    pick = pick_most_traded(candles)
    if pick is None:
        return []
    bond = next(b for b in cands if b["isin"] == pick)
    vol10 = sum(v for _d, _c, v in candles[pick][-VOLUME_SESSIONS:])
    fetched = datetime.now(IST).isoformat(timespec="seconds")
    rows = gsec_rows(bond, candles[pick], vol10, after_iso=last_iso,
                     fetched_iso=fetched)
    for r in rows:
        r["bond_list"] = src
    return rows


# --------------------------------------------------------------- RBI home
def _cell(text: str, label: str) -> Optional[str]:
    m = re.search(re.escape(label) + r"\s*:\s*([0-9]+(?:\.[0-9]+)?)", text)
    return m.group(1) if m else None


def parse_rbi_home(page_html: str, read_iso: str) -> dict:
    """Repo rate and USD/INR reference rate from RBI's home page.

    -> {repo_pct, repo_date, usdinr, usdinr_date}; a value that is not on
    the page is None. The repo has no date on the page, so it is dated the
    day it was read; the reference rate carries its own date."""
    text = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", page_html or "")))
    repo = _num(_cell(text, "Policy Repo Rate"))
    usd = _num(_cell(text, "INR / 1 USD"))
    usd_date = None
    m = re.search(r"As at [0-9.:]+\s*[ap]m of ([A-Z][a-z]+ \d{1,2}, \d{4})", text)
    if m:
        try:
            usd_date = datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()
        except ValueError:
            usd_date = None
    return {"repo_pct": repo, "repo_date": str(read_iso)[:10] if repo is not None else None,
            "usdinr": usd, "usdinr_date": usd_date if usd is not None else None}


def rbi_home_rows(parsed: dict, fetched_iso: str) -> list[dict]:
    """Stored rows from a parse_rbi_home result (missing values skipped)."""
    rows = []
    if parsed.get("repo_pct") is not None and parsed.get("repo_date"):
        rows.append({"series": "rbi_repo", "date": parsed["repo_date"],
                     "value": parsed["repo_pct"],
                     "source": f"RBI home page (www.rbi.org.in), read {parsed['repo_date']}",
                     "url": RBI_HOME_URL, "fetched": fetched_iso})
    if parsed.get("usdinr") is not None and parsed.get("usdinr_date"):
        rows.append({"series": "rbi_usdinr", "date": parsed["usdinr_date"],
                     "value": parsed["usdinr"],
                     "source": (f"RBI home page reference rate (FBIL), as at "
                                f"1.00pm of {parsed['usdinr_date']}"),
                     "url": RBI_HOME_URL, "fetched": fetched_iso})
    return rows


_GSEC_MARKET_ROW = re.compile(r"(\d+(?:\.\d+)?% GS (\d{4}))\s*:\s*(\d+(?:\.\d+)?)%")
_GSEC_MARKET_ASOF = re.compile(r"#\s*as on\s*([A-Z][a-z]+ \d{1,2}, \d{4})")


def parse_rbi_gsec_market(page_html: str, read_iso: str) -> dict:
    """RBI's 10-year benchmark from the home page's "Government Securities
    Market" box (e.g. "6.94% GS 2036 : 7.3725% #", footnote "# as on
    October 16, 2026" — the live page, fetched 2026-10-21).

    -> {bond, yield_pct, date, papers}: the benchmark is the listed paper
    maturing nearest 10 years after the read date (9-11 years); its date is
    the box's own "#" footnote. Anything missing is None — no footnote, no
    date, no row (nothing is dated by guess)."""
    text = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", page_html or "")))
    out: dict = {"bond": None, "yield_pct": None, "date": None, "papers": []}
    i = text.find("Government Securities Market")
    if i < 0:
        return out
    block = text[i:i + 1500]
    papers = [(name, int(year), float(y)) for name, year, y in _GSEC_MARKET_ROW.findall(block)]
    out["papers"] = [{"bond": n, "maturity_year": yr, "yield_pct": y} for n, yr, y in papers]
    m = _GSEC_MARKET_ASOF.search(block)
    if m:
        try:
            out["date"] = datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()
        except ValueError:
            out["date"] = None
    try:
        ry = int(str(read_iso)[:4])
    except (TypeError, ValueError):
        return out
    cands = [(abs(yr - ry - 10), n, y) for n, yr, y in papers if 9 <= yr - ry <= 11]
    if cands:
        _, out["bond"], out["yield_pct"] = min(cands)
    return out


def rbi_gsec_row(parsed: dict, fetched_iso: str) -> list[dict]:
    """The stored rbi_gsec10y row from a parse_rbi_gsec_market result —
    [] without a yield or a date."""
    if parsed.get("yield_pct") is None or not parsed.get("date") or not parsed.get("bond"):
        return []
    return [{"series": "rbi_gsec10y", "date": parsed["date"], "value": parsed["yield_pct"],
             "bond": parsed["bond"],
             "source": (f"RBI home page, Government Securities Market: {parsed['bond']} : "
                        f"{parsed['yield_pct']}% # as on {parsed['date']} — display only, "
                        "beside the Upstox yield"),
             "url": RBI_HOME_URL, "fetched": fetched_iso}]


def fetch_rbi_home(timeout: float = 30.0) -> list[dict]:
    """Rows for rbi_repo and rbi_usdinr from www.rbi.org.in, plus the
    rbi_gsec10y benchmark row when the page carries the Government
    Securities Market box. Raises on a network failure so the caller
    journals it; a page that answers without the two values returns no
    repo / USD-INR rows."""
    import httpx
    r = httpx.get(RBI_HOME_URL, timeout=timeout,
                  headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
    r.raise_for_status()
    now = datetime.now(IST)
    today, fetched = now.date().isoformat(), now.isoformat(timespec="seconds")
    return (rbi_home_rows(parse_rbi_home(r.text, today), fetched)
            + rbi_gsec_row(parse_rbi_gsec_market(r.text, today), fetched))


# ------------------------------------------------------------ repo history
def repo_history(path: Optional[Path] = None) -> list[dict]:
    """Committed MPC decisions (data/rbi_repo_history.csv), ascending."""
    p = Path(path) if path else REPO_HISTORY_CSV
    if not p.exists():
        return []
    out = []
    with p.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            after = _num(r.get("repo_after_pct"))
            before = _num(r.get("repo_before_pct"))
            d = (r.get("decision_date") or "")[:10]
            if after is None or not d:
                continue
            out.append({**r, "decision_date": d, "repo_after_pct": after,
                        "repo_before_pct": before})
    return sorted(out, key=lambda r: r["decision_date"])


def repo_at(date_iso: str, history: Optional[list] = None) -> Optional[tuple]:
    """(repo %, date_iso, source) in force on a date, from the MPC history.

    Proved only when the next decision's "before" rate equals this
    decision's "after" rate (no off-cycle change in between). A date after
    the last decision is answered only up to the newest stored live
    reading that still shows the same rate. Else None."""
    rows = history if history is not None else repo_history()
    d = str(date_iso)[:10]
    prior = [r for r in rows if r["decision_date"] <= d]
    if not prior:
        return None
    cur = prior[-1]
    later = [r for r in rows if r["decision_date"] > d]
    src = (f"RBI MPC resolution {cur['decision_date']} "
           f"({cur.get('source_url', '')})")
    if later:
        nxt = later[0]
        if nxt.get("repo_before_pct") is None or \
                abs(nxt["repo_before_pct"] - cur["repo_after_pct"]) > 1e-9:
            return None
        return (cur["repo_after_pct"], d, src)
    live = [r for r in stored_rows("rbi_repo") if r["date"] >= d]
    if live and abs(float(live[0]["value"]) - cur["repo_after_pct"]) < 1e-9:
        return (cur["repo_after_pct"], d, src + f"; still {live[0]['value']}% "
                                              f"on RBI home {live[0]['date']}")
    return None


# ------------------------------------------------------ institutional hold
_SHP_CACHE: dict = {}


def _shp_rows() -> list[dict]:
    key = (str(SHAREHOLDING_CSV), SHAREHOLDING_CSV.stat().st_mtime
           if SHAREHOLDING_CSV.exists() else 0)
    if _SHP_CACHE.get("key") == key:
        return _SHP_CACHE["rows"]
    rows = []
    if SHAREHOLDING_CSV.exists():
        with SHAREHOLDING_CSV.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                fii, dii = _num(r.get("fii_pct")), _num(r.get("dii_pct"))
                if fii is None or dii is None:
                    continue
                try:
                    q = datetime.strptime(r["quarter"].strip(), "%d-%b-%Y").date().isoformat()
                except (KeyError, ValueError):
                    continue
                rows.append({"symbol": r["symbol"].strip().upper(), "quarter": q,
                             "inst_pct": round(fii + dii, 4)})
    _SHP_CACHE.update(key=key, rows=rows)
    return rows


def inst_holding(symbol: str, period_end: str) -> Optional[tuple]:
    """(start %, end %, start_date, end_date) of FII + DII holding.

    start = the quarter at or just before `period_end`; end = the latest
    quarter on file. None when the file has no quarter at or before the
    period end for this stock (said by the caller as DATA_NEEDED)."""
    sym = str(symbol or "").upper().replace(".NS", "")
    pe = str(period_end or "")[:10]
    rows = sorted((r for r in _shp_rows() if r["symbol"] == sym),
                  key=lambda r: r["quarter"])
    if not rows or not pe:
        return None
    start = [r for r in rows if r["quarter"] <= pe]
    if not start:
        return None
    s, e = start[-1], rows[-1]
    return (s["inst_pct"], e["inst_pct"], s["quarter"], e["quarter"])
