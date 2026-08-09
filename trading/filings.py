"""Filings feed v1 (owner-approved 2026-07-26) — company announcements
for held + watchlist names, from BSE's public announcement API (free).

The actual-family's first data pipe: order wins, results, OFS notices —
the layer that led price in all five tunnel specimens. v1 scope: fetch
the exchange's announcement stream once daily, keep the items whose
company name matches a held/watched stock, show them on the desk and
journal the refresh. Fail-visible: any error journals FILINGS_FAILED
and yesterday's list stays. No fabrication: no feed -> empty list.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

log = logging.getLogger(__name__)

_API = ("https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
        "?pageno={page}&strCat=-1&strPrevDate={frm}&strScrip=&strSearch=P"
        "&strToDate={to}&strType=C&subcategory=-1")
_HEADERS = {"Accept": "application/json",
            "Referer": "https://www.bseindia.com/",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}

_STOP = {"LTD", "LTD.", "LIMITED", "LT", "THE", "&", "AND", "OF", "INDIA"}


def _key(name: str, words: int = 2) -> str:
    toks = [t for t in (name or "").upper().replace(".", " ").split()
            if t not in _STOP]
    return " ".join(toks[:words])


def fetch_announcements(day_from: date, day_to: date, max_pages: int = 8) -> list:
    """All BSE announcements in the window, raw."""
    import httpx
    out = []
    with httpx.Client(timeout=25.0, headers=_HEADERS) as client:
        for page in range(1, max_pages + 1):
            url = _API.format(page=page, frm=day_from.strftime("%Y%m%d"),
                              to=day_to.strftime("%Y%m%d"))
            r = client.get(url)
            # AUDIT FIX C9 (2026-08-09): a 403/blocked response used to fall
            # through to an empty Table and journal "0 scanned" as SUCCESS —
            # a cloud-blocked feed was indistinguishable from a quiet news
            # day. Non-200 now raises → the scheduler journals+pushes
            # FILINGS_FAILED. An empty FIRST page on a 200 is also suspicious
            # (BSE serves empty JSON to blocked cloud IPs) and raises too.
            r.raise_for_status()
            rows = (r.json() or {}).get("Table") or []
            if not rows:
                if page == 1:
                    raise RuntimeError(
                        "BSE returned an empty first page — feed blocked for "
                        "this IP or format changed; not a quiet news day")
                break
            out.extend(rows)
    return out


def match_filings(announcements: list, watch: dict) -> list:
    """watch: {name_key: ticker}. Returns desk-ready matched items."""
    matched = []
    for a in announcements:
        k = _key(a.get("SLONGNAME", ""))
        tkr = watch.get(k)
        if not tkr:
            continue
        att = a.get("ATTACHMENTNAME") or ""
        matched.append({
            "ticker": tkr,
            "headline": (a.get("NEWSSUB") or "")[:180],
            "when": (a.get("NEWS_DT") or "")[:16].replace("T", " "),
            "pdf": (f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{att}"
                    if att else ""),
        })
    return matched


def build_watch_map(universe_csv_rows: list, tickers: set) -> dict:
    return {_key(r["name"]): r["ticker"]
            for r in universe_csv_rows if r["ticker"] in tickers}


def refresh_filings(portfolio, watch_map: dict, days_back: int = 2) -> dict:
    """Fetch, match, prepend to portfolio.filings (cap 100). Returns summary."""
    today = date.today()
    anns = fetch_announcements(today - timedelta(days=days_back), today)
    matched = match_filings(anns, watch_map)
    seen = {(f.get("ticker"), f.get("headline")) for f in portfolio.filings}
    fresh = [m for m in matched if (m["ticker"], m["headline"]) not in seen]
    portfolio.filings = (fresh + portfolio.filings)[:100]
    return {"scanned": len(anns), "matched": len(matched), "new": len(fresh)}
