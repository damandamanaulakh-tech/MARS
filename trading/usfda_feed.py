"""USFDA events for the pharma names on the book (owner ask 2026-08-25:
"did u wired the US FDA data") — DISPLAY ONLY, real sources only.

History matters here: the app once carried a USFDA decision calendar with
INVENTED dates driving a real entry blackout — the 2026-08-09 audit
emptied it (fabricated data firing a money-path rule is worse than no
data). This module is the honest replacement: PAST regulatory events from
official sources, shown per stock and journaled, never gating a trade.

Sources:
  * openFDA drug enforcement (recalls) — PUBLIC, keyless, verified live
    from this sandbox (Torrent: 85 classified recalls). Firm names are
    matched by token against the NSE company name and the matched firm is
    always displayed — a wrong match must be visible, never silent.
  * FDA Data Dashboard inspections (Form-483 territory) — real endpoint,
    verified reachable, but requires a free registered key
    (FDA_DATADASHBOARD_USER + FDA_DATADASHBOARD_KEY env, owner action at
    datadashboard.fda.gov). Without the key the inspections half reads
    DATA_NEEDED, never a guess.

Boundary: engine / risk / orders / danger must never import this module
(test-enforced). Events journal and render; they do not block buys or
move stops — the news module's "no block just show" rule applies.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import httpx

log = logging.getLogger(__name__)

OPENFDA_URL = "https://api.fda.gov/drug/enforcement.json"
DASHBOARD_URL = "https://api-datadashboard.fda.gov/v1/inspections_citations"

# words that carry no identity — never used as the query token
_STOP = {"LTD", "LIMITED", "AND", "THE", "OF", "INDIA", "INDUSTRIES",
         "COMPANY", "CO", "CORP", "INC", "PVT", "PRIVATE", "L", "INS"}


def query_token(company_name: str) -> str:
    """First identity-bearing token of the NSE company name — the word we
    search FDA firm names for ('TORRENT PHARMACEUTICALS L' -> 'TORRENT')."""
    for w in (company_name or "").upper().replace(".", " ").split():
        if w not in _STOP and len(w) >= 3:
            return w
    return ""


def firm_matches(firm: str, token: str) -> bool:
    """The FDA firm name must carry the token as its own word (prefix
    match allowed: 'Torrent Pharma Inc.' matches TORRENT)."""
    if not token:
        return False
    words = (firm or "").upper().replace(".", " ").replace(",", " ").split()
    return any(w.startswith(token) for w in words)


def fetch_recalls(name_map: dict[str, str], limit: int = 20,
                  sleep_s: float = 0.3, timeout: float = 25.0) -> list[dict]:
    """openFDA enforcement rows for {ticker: company_name}. Returns
    [{ticker, firm, event_id, date, classification, reason, status}].
    A query miss or non-matching firm yields nothing for that name —
    absence of a row means 'no recall found', and the weekly journal
    line carries the scanned-name count so absence is dated evidence."""
    out: list[dict] = []
    for ticker, cname in sorted(name_map.items()):
        token = query_token(cname)
        if not token:
            continue
        try:
            r = httpx.get(OPENFDA_URL, params={
                "search": f'recalling_firm:"{token}"',
                "limit": limit, "sort": "report_date:desc"}, timeout=timeout)
            if r.status_code == 404:      # openFDA: no matches at all
                time.sleep(sleep_s)
                continue
            r.raise_for_status()
            for res in r.json().get("results", []):
                firm = res.get("recalling_firm", "")
                if not firm_matches(firm, token):
                    continue
                rid = str(res.get("recall_number")
                          or res.get("event_id") or "")
                if not rid:
                    continue
                out.append({
                    "ticker": ticker, "firm": firm, "event_id": rid,
                    "date": str(res.get("report_date") or "")[:8],
                    "classification": res.get("classification", ""),
                    "reason": (res.get("reason_for_recall") or "")[:300],
                    "status": res.get("status", ""),
                    "source": "openfda_enforcement",
                })
        except (httpx.HTTPError, ValueError) as e:
            log.warning("openFDA recalls failed for %s: %s", ticker, e)
        time.sleep(sleep_s)
    return out


def _dashboard_auth() -> Optional[dict]:
    user = os.environ.get("FDA_DATADASHBOARD_USER", "").strip()
    key = os.environ.get("FDA_DATADASHBOARD_KEY", "").strip()
    if not user or not key:
        return None
    return {"Content-Type": "application/json",
            "Authorization-User": user, "Authorization-Key": key}


def fetch_inspections(name_map: dict[str, str], rows: int = 500,
                      timeout: float = 30.0) -> tuple[list[dict], str]:
    """FDA Data Dashboard inspections for India, matched locally to the
    given names. Returns (rows, note). Without the free registered key
    the note says DATA_NEEDED and exactly what unlocks it."""
    auth = _dashboard_auth()
    if auth is None:
        return [], ("DATA_NEEDED — FDA Data Dashboard needs a free "
                    "registered key: set FDA_DATADASHBOARD_USER + "
                    "FDA_DATADASHBOARD_KEY in Render "
                    "(datadashboard.fda.gov, OII Unified Logon)")
    try:
        r = httpx.post(DASHBOARD_URL, json={
            "start": 1, "rows": rows, "sort": "InspectionEndDate",
            "sortorder": "DESC",
            "columns": ["FEINumber", "LegalName", "InspectionID",
                        "InspectionEndDate", "CountryName"],
            "filters": {"CountryName": ["India"]}},
            headers=auth, timeout=timeout)
        r.raise_for_status()
        payload = r.json()
        recs = payload.get("result") or payload.get("results") or []
        out = []
        tokens = {t: query_token(c) for t, c in name_map.items()}
        for rec in recs:
            firm = str(rec.get("LegalName") or "")
            for ticker, token in tokens.items():
                if firm_matches(firm, token):
                    out.append({
                        "ticker": ticker, "firm": firm,
                        "event_id": str(rec.get("InspectionID") or ""),
                        "date": str(rec.get("InspectionEndDate") or "")[:10],
                        "classification": "INSPECTION",
                        "reason": "FDA inspection (see dashboard for "
                                  "classification/citations)",
                        "status": "", "source": "fda_dashboard",
                    })
        return out, f"{len(recs)} India inspections scanned"
    except (httpx.HTTPError, ValueError) as e:
        log.warning("FDA dashboard inspections failed: %s", e)
        return [], f"fetch failed: {e}"
