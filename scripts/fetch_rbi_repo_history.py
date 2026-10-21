"""Build data/rbi_repo_history.csv from RBI's own MPC resolutions.

Owner order 2026-10-21 (VALFIX batch 2, item 10): the repo rate at a
valuation's base period end must come ONLY from RBI-verifiable pages. This
script reads RBI's "Monetary Policy Statements" listing
(https://www.rbi.org.in/Scripts/Annualpolicy.aspx, one page per financial
year), opens every "Resolution of the Monetary Policy Committee (MPC)"
press release it links, and keeps the sentence that states the decision:

  "... keep the policy repo rate ... unchanged at 5.25 per cent"
  "... reduce the policy repo rate ... by 25 bps to 5.25 per cent"
  "... increase the policy repo rate ... by 25 bps to 5.50 per cent"

Each row: decision_date (the press release date = the last meeting day),
meeting, action, change_bps, repo_before_pct, repo_after_pct, the quoted
sentence, the press-release URL and the fetch date. A release whose
sentence does not parse is LEFT OUT and printed — never guessed.

Usage: python scripts/fetch_rbi_repo_history.py [--years 2027 2026 2025 2024]
(2027 = the current page = financial year 2026-27; 2026 = 2025-26; ...).
Polite: one request at a time, 1 s apart.
"""
from __future__ import annotations

import argparse
import csv
import html
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "rbi_repo_history.csv"
LIST_URL = "https://www.rbi.org.in/Scripts/Annualpolicy.aspx"
PR_URL = "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid={}"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126 Safari/537.36"}
IST = timezone(timedelta(hours=5, minutes=30))

COLS = ["decision_date", "meeting", "action", "change_bps", "repo_before_pct",
        "repo_after_pct", "sentence", "source_url", "fetched"]


def _text(page: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))


def _form_fields(page: str) -> dict:
    out = {}
    for name in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION",
                 "__EVENTTARGET", "__EVENTARGUMENT"):
        m = (re.search(r'name="%s"[^>]*value="([^"]*)"' % re.escape(name), page)
             or re.search(r'value="([^"]*)"[^>]*name="%s"' % re.escape(name), page))
        out[name] = html.unescape(m.group(1)) if m else ""
    btn = re.search(r'<input[^>]*id="btn"[^>]*>', page)
    if btn:
        n = re.search(r'name="([^"]+)"', btn.group(0))
        if n:
            out[n.group(1)] = ""
    return out


def resolution_links(page: str) -> list[tuple[str, str]]:
    """[(meeting text, prid)] for every MPC resolution on a listing page."""
    out = []
    for m in re.finditer(
            r"Resolution of the Monetary Policy Committee \(MPC\)\s*([^<]+?)\s*<BR>"
            r"(.{0,600}?)prid=(\d+)", page, re.S | re.I):
        out.append((re.sub(r"\s+", " ", m.group(1)).strip(), m.group(3)))
    return out


# The decision sentence: "<the MPC> decided/voted ... to <verb> the policy
# repo rate ... unchanged at / at / by N bps to X per cent".
_DECISION = re.compile(
    r"\b(?:decided|voted)\b[^.]{0,160}?\bto:?\s*"
    r"(keep|maintain|reduce|cut|lower|increase|raise|hike)\b"
    r"[^.]{0,20}?the policy repo rate[^.]{0,120}?"
    r"(?:unchanged at|at|by (\d+) (?:basis points|bps) to)\s*"
    r"(\d+(?:\.\d+)?) per cent", re.I)


def parse_resolution(page: str) -> dict | None:
    """Decision fields from one resolution press release, else None."""
    t = _text(page)
    if "Resolution of the Monetary Policy Committee" not in t:
        return None
    m = _DECISION.search(t)
    if not m:
        return None
    start = t.rfind(".", 0, m.start()) + 1
    if m.start() - start > 250:            # no full stop before it (2024 pages)
        start = m.start()
    sentence = t[start:m.end()].strip()
    verb = m.group(1).lower()
    after = float(m.group(3))
    if verb in ("keep", "maintain"):
        if m.group(2):
            return None
        action, bps = "unchanged", 0
    elif not m.group(2):
        return None
    elif verb in ("reduce", "cut", "lower"):
        action, bps = "cut", -int(m.group(2))
    else:
        action, bps = "hike", int(m.group(2))
    d = re.search(r"Date\s*:\s*([A-Z][a-z]{2} \d{1,2}, \d{4})", t)
    if not d:
        return None
    dec = datetime.strptime(d.group(1), "%b %d, %Y").date().isoformat()
    return {"decision_date": dec, "action": action, "change_bps": bps,
            "repo_after_pct": after,
            "repo_before_pct": round(after - bps / 100.0, 4),
            "sentence": sentence[:400]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", nargs="*", default=["2027", "2026", "2025", "2024"],
                    help="RBI listing years (2027 = 2026-27, the current page)")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    fetched = datetime.now(IST).date().isoformat()
    with httpx.Client(headers=UA, timeout=40.0, follow_redirects=True) as c:
        first = c.get(LIST_URL)
        first.raise_for_status()
        pages = {}
        for yr in a.years:
            if yr == a.years[0]:
                pages[yr] = first.text
                continue
            time.sleep(1.0)
            r = c.post(LIST_URL, data={**_form_fields(first.text), "hdnYear": yr})
            r.raise_for_status()
            pages[yr] = r.text
        links = []
        for yr, page in pages.items():
            got = resolution_links(page)
            print(f"listing {yr}: {len(got)} resolution link(s)")
            links += got
        rows, seen = [], set()
        for meeting, prid in links:
            if prid in seen:
                continue
            seen.add(prid)
            time.sleep(1.0)
            url = PR_URL.format(prid)
            r = c.get(url)
            if r.status_code != 200:
                print(f"  SKIP {meeting}: HTTP {r.status_code} {url}")
                continue
            p = parse_resolution(r.text)
            if p is None:
                print(f"  SKIP {meeting}: decision sentence not found ({url})")
                continue
            rows.append({**p, "meeting": meeting, "source_url": url,
                         "fetched": fetched})
            print(f"  {p['decision_date']} {meeting}: {p['action']} "
                  f"{p['change_bps']:+d} bps -> {p['repo_after_pct']}%")
    rows.sort(key=lambda r: r["decision_date"])
    # continuity: each decision's "before" must equal the previous "after"
    for prev, cur in zip(rows, rows[1:]):
        if abs(cur["repo_before_pct"] - prev["repo_after_pct"]) > 1e-9:
            print(f"  WARNING gap: {prev['decision_date']} after "
                  f"{prev['repo_after_pct']} vs {cur['decision_date']} before "
                  f"{cur['repo_before_pct']} (off-cycle change or a missed "
                  "resolution — dates between them read DATA_NEEDED)")
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in COLS})
    print(f"wrote {len(rows)} row(s) to {a.out}")
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
