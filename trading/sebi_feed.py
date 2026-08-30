"""SEBI enforcement orders for the names on the book (owner order
2026-08-25: "wire the SEBI enforcement orders feed too") — DISPLAY ONLY.

Source: sebi.gov.in's own listing backend
(sebiweb/ajax/home/getnewslistinfo.jsp, sid=2/ssid=9 — the live
Enforcement→Orders category; verified from this sandbox: dated rows with
full order URLs, current to the fetch day). The site's static pages are
JS shells; this is the endpoint the site itself renders from.

Matching: an order TITLE must carry a company's identity token as an
exact word ("Order in the matter of Rain Industries" ↔ RAIN.NS). Most
SEBI orders concern individuals and unlisted entities, so matches for a
50-name book are RARE — an empty week means "no order naming your
companies", and the weekly journal line says how many orders were
scanned so absence is dated evidence, not silence. The matched token and
the full title ride every stored row: a wrong match must be visible.

Boundary: engine / risk / orders / danger never import this module
(test-enforced). Orders journal and render — "no block just show".
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime

import httpx

log = logging.getLogger(__name__)

LIST_URL = ("https://www.sebi.gov.in/sebiweb/ajax/home/"
            "getnewslistinfo.jsp")
_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.sebi.gov.in/",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
}
# sid=2 = Enforcement; ssid=9 = the actively-updated Orders category
# (2026-08-25 probe: ssid=10 stops May-2025, ssid=12 stops Mar-2023).
_SID, _SSID = "2", "9"

_ROW_RE = re.compile(
    r"<td>([A-Z][a-z]{2} \d{1,2}, \d{4})</td>\s*<td>"
    r"<a href='([^']+)'[^>]*>([^<]+)</a>", re.S)


def fetch_orders(pages: int = 3, sleep_s: float = 0.4,
                 timeout: float = 25.0) -> list[dict]:
    """Recent enforcement orders, newest first:
    [{order_id, date(iso), title, url}]. Each listing page holds ~25
    rows; a parse-empty page stops the loop (fail-visible upstream —
    the caller journals the scanned count)."""
    out: list[dict] = []
    for page in range(1, pages + 1):
        data = {"nextValue": str(page), "next": "n", "search": "",
                "fromDate": "", "toDate": "", "fromYear": "", "toYear": "",
                "deptId": "-1", "sid": _SID, "ssid": _SSID, "smid": "0",
                "ssidhidden": _SSID, "intmid": "-1", "sText": "",
                "doDirect": "-1"}
        try:
            r = httpx.post(LIST_URL, data=data, headers=_HEADERS,
                           timeout=timeout)
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("SEBI listing page %d failed: %s", page, e)
            break
        rows = _ROW_RE.findall(r.text.split("#@#")[0])
        if not rows:
            break
        for dt, url, title in rows:
            m = re.search(r"_(\d+)\.html", url)
            oid = m.group(1) if m else url.rsplit("/", 1)[-1][:60]
            try:
                iso = datetime.strptime(dt.strip(), "%b %d, %Y") \
                    .date().isoformat()
            except ValueError:
                iso = ""
            out.append({"order_id": oid, "date": iso,
                        "title": title.strip(), "url": url})
        time.sleep(sleep_s)
    return out


def title_matches(title: str, token: str) -> bool:
    """The token must appear as an EXACT word of the order title (stricter
    than a prefix rule — SEBI titles are free prose over every
    sector, and a prefix match would false-hit generic tokens)."""
    if not token:
        return False
    return token in re.split(r"[^A-Z0-9]+", (title or "").upper())


# Company-name tokenising, moved here 2026-08-30 when trading/usfda_feed.py
# was deleted on the owner's order ("forget about FDA as well"). SEBI was the
# only other consumer, so the helper lives with its remaining user rather
# than in a shared module nothing else needs.
_STOP = {"LTD", "LIMITED", "AND", "THE", "OF", "INDIA", "INDUSTRIES",
         "COMPANY", "CO", "CORP", "INC", "PVT", "PRIVATE", "L", "INS"}


def query_token(company_name: str) -> str:
    """First identity-bearing token of the NSE company name — the word SEBI
    order titles are searched for ('TORRENT PHARMACEUTICALS L' -> 'TORRENT')."""
    for w in (company_name or "").upper().replace(".", " ").split():
        if w not in _STOP and len(w) >= 3:
            return w
    return ""


def match_orders(orders: list[dict],
                 name_map: dict[str, str]) -> list[dict]:
    """Attach orders to tickers by title token. Returns stored-row shape:
    [{ticker, order_id, date, title, url, token}] — the matched token is
    kept so a wrong match is visible on the page."""
    tokens = {t: query_token(c) for t, c in name_map.items()}
    out = []
    for o in orders:
        for ticker, token in tokens.items():
            if title_matches(o["title"], token):
                out.append({**o, "ticker": ticker, "token": token,
                            "source": "sebi_orders"})
    return out
