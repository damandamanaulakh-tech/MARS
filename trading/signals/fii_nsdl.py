"""NSDL FPI archive — the runtime tail fetch (Node 3, owner order
2026-08-23: "start fixing ur pending and flaws one by one").

THE FLAW THIS CLOSES: the FII tide the engine scores on had two sources —
the live NSE API (answers only on days NSE tolerates our cloud IP, and only
with TODAY's number) and the committed fii_dii_cash.csv (frozen at commit
time; the 45-day staleness guard rightly silences it as it ages). So the
5-day FII sum the thresholds were calibrated on kept degrading to a 1-day
reading or to neutral. NSDL's FPI archive answers our cloud IP with no
login (proven live 2026-08-09) — this module fetches the recent tail at
runtime; the Friday job stores it in Mongo; flows.py merges all three
sources by date.

The fetch/parse/shift logic is LIFTED from the committed backfill script
(scripts/fetch_fpi_nsdl_archive.py) — the canonical implementation whose
two documented traps stay handled here:
  * REPORT-DATE SHIFT: each NSDL report carries the PREVIOUS trading day's
    activity (correlation vs NSE provisional: 0.075 unshifted, 0.955
    shifted — measured 2026-08-09). Values are re-dated to the prior
    report's date; the window's first row falls back to a previous-weekday
    guess exactly as the script does.
  * FIRST-MATCH-WINS: the page repeats each date in a derivatives table
    and a CUMULATIVE table — the first Equity/Stock-Exchange row is the
    daily one. Without this guard the YTD row (-2.79 lakh Cr) once
    overwrote a +418 Cr day.

requests is not in requirements (the app ships httpx), so this is an httpx
port, not an import of the script. Basis note: NSDL = settlement basis;
NSE API = provisional. Median gap ~800 Cr/day, documented — the merge in
flows.py prefers the NSE number for any date both cover.
"""
from __future__ import annotations

import calendar
import logging
import re
import time
from datetime import date, datetime, timedelta
from typing import Optional

import httpx

log = logging.getLogger(__name__)

URL = "https://www.fpi.nsdl.co.in/web/Reports/Archive.aspx"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_CELL = re.compile(r"<t[dh][^>]*>\s*(.*?)\s*</t[dh]>", re.S)
_DATE = re.compile(r"^\d{2}-[A-Za-z]{3}-\d{4}$")


def _num(s: str) -> float:
    """NSDL prints negatives in parentheses: (6984.73) -> -6984.73."""
    s = s.replace(",", "").strip()
    if s.startswith("(") and s.endswith(")"):
        return -float(s[1:-1])
    return float(s)


def _hidden(html: str, name: str) -> str:
    m = re.search(r'name="%s"[^>]*value="([^"]*)"' % name, html)
    return m.group(1) if m else ""


def _parse(html: str) -> dict:
    """{report_date: (buy, sell, net)} — first Equity/Stock-Exchange row per
    date only (the cumulative-table trap)."""
    cells = [re.sub(r"<[^>]+>", "", c).strip() for c in _CELL.findall(html)]
    cells = [c for c in cells if c]
    out: dict = {}
    cur = None
    section = ""
    i = 0
    while i < len(cells):
        c = cells[i]
        if _DATE.match(c):
            cur = datetime.strptime(c, "%d-%b-%Y").date()
            section = ""
        elif c in ("Equity", "Debt", "Debt-General Limit", "Debt-VRR", "Hybrid"):
            section = c
        elif (c == "Stock Exchange" and section == "Equity"
                and cur is not None and cur not in out):
            try:
                out[cur] = (_num(cells[i + 1]), _num(cells[i + 2]),
                            _num(cells[i + 3]))
            except (ValueError, IndexError):
                pass
        i += 1
    return out


def fetch_month(client: httpx.Client, year: int, month: int,
                retries: int = 3) -> dict:
    """One month of daily report rows: {report_date: (buy, sell, net)}."""
    last_day = min(date(year, month, calendar.monthrange(year, month)[1]),
                   date.today())
    txt = last_day.strftime("%d-%b-%Y")
    for attempt in range(retries):
        try:
            page = client.get(URL, timeout=40.0)
            data = {
                "__EVENTTARGET": "btnSubmit1", "__EVENTARGUMENT": "",
                "__VIEWSTATE": _hidden(page.text, "__VIEWSTATE"),
                "__VIEWSTATEGENERATOR": _hidden(page.text,
                                                "__VIEWSTATEGENERATOR"),
                "__EVENTVALIDATION": _hidden(page.text, "__EVENTVALIDATION"),
                "txtDate": txt, "hdnDate": "", "HdnValexceldata": "",
                "hdnFlag": "",
            }
            time.sleep(1.0)
            resp = client.post(URL, data=data, timeout=60.0)
            out = _parse(resp.text)
            if out:
                return out
        except httpx.HTTPError as exc:
            log.warning("NSDL %d-%02d attempt %d failed: %s", year, month,
                        attempt + 1, exc)
        time.sleep(2.0 * (attempt + 1))
    return {}


def shift_to_trading_dates(rows: list) -> list:
    """[(report_iso, b, s, n)] sorted -> [(trading_iso, b, s, n)].

    Each value belongs to the report date immediately BEFORE its own in the
    series; the first falls back to the previous weekday. Same rule as the
    backfill script — the two must never disagree."""
    rows = sorted(rows)
    if not rows:
        return []
    first_report = datetime.strptime(rows[0][0], "%Y-%m-%d").date()
    prev = first_report - timedelta(days=1)
    while prev.weekday() >= 5:
        prev -= timedelta(days=1)
    shifted = [(prev.isoformat(), *rows[0][1:])]
    for i in range(1, len(rows)):
        shifted.append((rows[i - 1][0], *rows[i][1:]))
    return shifted


def recent_rows(months: int = 2) -> Optional[list]:
    """The last `months` calendar months of FPI equity cash, trading-dated:
    [(iso_date, buy_cr, sell_cr, net_cr)] ascending. None = fetch failed
    entirely (callers must treat that as 'no update', never as zero flow).
    """
    today = date.today()
    ym = []
    y, m = today.year, today.month
    for _ in range(months):
        ym.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    raw: dict = {}
    try:
        with httpx.Client(headers={"User-Agent": UA, "Referer": URL},
                          follow_redirects=True) as client:
            for yy, mm in sorted(ym):
                raw.update(fetch_month(client, yy, mm))
    except Exception as e:                                   # noqa: BLE001
        log.warning("NSDL recent fetch failed: %s", e)
        return None
    if not raw:
        return None
    rows = [(d.isoformat(), b, s, n) for d, (b, s, n) in sorted(raw.items())]
    return shift_to_trading_dates(rows)
