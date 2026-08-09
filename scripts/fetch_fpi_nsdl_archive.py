"""Fetch daily FPI equity cash activity from NSDL's public archive.

Source: https://www.fpi.nsdl.co.in/web/Reports/Archive.aspx — the official
depository archive ("Daily Trends in FPI Investments"). One POST with a
date returns every trading day of that month up to the date, each with
Equity / Stock Exchange gross purchases, gross sales and net (Rs Crore).
Discovered + verified 2026-08-09: this endpoint answers cloud IPs that
nseindia.com blocks, and it needs no login (the DII archive next to it is
OTP-walled).

PROVENANCE NOTE, do not blur it: NSDL numbers are settlement-based FPI
activity. NSE's provisional "FII" cash numbers (fiidiiTradeReact, what the
live engine reads) differ slightly day to day. Measured on 29 overlap
days (2026-06-30 -> 2026-08-06): correlation 0.955, median |diff| ~800 Cr.

Usage:
    python scripts/fetch_fpi_nsdl_archive.py 2025-04 2026-08 out.csv
"""
from __future__ import annotations

import calendar
import csv
import re
import sys
import time
from datetime import date, datetime, timedelta

import requests

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


def fetch_month(session: requests.Session, year: int, month: int,
                retries: int = 4) -> dict[date, tuple[float, float, float]]:
    """One month of daily Equity/Stock-Exchange rows: {date: (buy, sell, net)}."""
    last_day = min(date(year, month, calendar.monthrange(year, month)[1]),
                   date.today())
    txt = last_day.strftime("%d-%b-%Y")
    for attempt in range(retries):
        try:
            page = session.get(URL, timeout=40)
            data = {
                "__EVENTTARGET": "btnSubmit1", "__EVENTARGUMENT": "",
                "__VIEWSTATE": _hidden(page.text, "__VIEWSTATE"),
                "__VIEWSTATEGENERATOR": _hidden(page.text, "__VIEWSTATEGENERATOR"),
                "__EVENTVALIDATION": _hidden(page.text, "__EVENTVALIDATION"),
                "txtDate": txt, "hdnDate": "", "HdnValexceldata": "", "hdnFlag": "",
            }
            time.sleep(1.5)
            resp = session.post(URL, data=data, timeout=60)
            out = _parse(resp.text)
            # a month the archive genuinely has must yield at least one row
            if out:
                return out
        except requests.RequestException as exc:
            print(f"  {year}-{month:02d} attempt {attempt + 1} failed: {exc}")
        time.sleep(2 * (attempt + 1))
    print(f"  {year}-{month:02d}: DATA_NEEDED (no rows after {retries} tries)")
    return {}


def _parse(html: str) -> dict[date, tuple[float, float, float]]:
    cells = [re.sub(r"<[^>]+>", "", c).strip() for c in _CELL.findall(html)]
    cells = [c for c in cells if c]
    out: dict[date, tuple[float, float, float]] = {}
    cur: date | None = None
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
            # first match only: the page repeats the date in a derivatives
            # table and a cumulative table further down - the FIRST
            # Equity/Stock Exchange row is the daily one (verified
            # 2026-08-09: without this guard the cumulative row overwrote
            # 07-Aug-2026 with the YTD figure, -2.79 lakh Cr).
            try:
                buy, sell, net = _num(cells[i + 1]), _num(cells[i + 2]), _num(cells[i + 3])
            except (ValueError, IndexError):
                i += 1
                continue
            out[cur] = (buy, sell, net)
        i += 1
    return out


def main() -> None:
    start, end, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
    y0, m0 = int(start[:4]), int(start[5:7])
    y1, m1 = int(end[:4]), int(end[5:7])
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Referer": URL})
    rows: list[tuple[str, float, float, float]] = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        month_rows = fetch_month(session, y, m)
        print(f"{y}-{m:02d}: {len(month_rows)} trading days")
        for d in sorted(month_rows):
            buy, sell, net = month_rows[d]
            rows.append((d.isoformat(), buy, sell, net))
        m += 1
        if m == 13:
            y, m = y + 1, 1
        time.sleep(2)
    # REPORT-DATE -> TRADING-DATE SHIFT. The page footer says each report
    # "constitutes FPIs trading/position of the previous trading day", and
    # reports are published on trading days - so the value dated R belongs
    # to the report date immediately before R in the series. Verified
    # empirically 2026-08-09: correlation with NSE provisional FII cash on
    # 29 overlap days is 0.075 unshifted, 0.955 shifted. The first report's
    # value is assigned to the trading day before it (weekend/holiday-aware
    # only to the extent of a plain previous-weekday guess - flagged, since
    # there is no earlier report to anchor on).
    rows.sort()
    shifted = []
    if rows:
        first_report = datetime.strptime(rows[0][0], "%Y-%m-%d").date()
        prev = first_report - timedelta(days=1)
        while prev.weekday() >= 5:
            prev -= timedelta(days=1)
        shifted.append((prev.isoformat(), *rows[0][1:]))
        for i in range(1, len(rows)):
            shifted.append((rows[i - 1][0], *rows[i][1:]))
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "fpi_equity_buy_cr", "fpi_equity_sell_cr", "fpi_equity_net_cr"])
        w.writerows(shifted)
    print(f"WROTE {out_path}: {len(shifted)} rows (trading-date basis)")


if __name__ == "__main__":
    main()
