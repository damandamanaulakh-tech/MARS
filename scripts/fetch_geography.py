"""TASK G — geographic revenue split for the 200 statement names.

Source of truth: each company's own NSE results XBRL ("Integrated Filing-
Financials", the format NSE has carried since FY25), the annual (12-month)
segment table. NOTHING is invented: a value is kept only when the company
tagged it, every row carries the XBRL URL and the UTC time it was fetched,
anything else is a status (NOT_FILED / FETCH_FAILED) with the reason.

Order tried per symbol (first filing with any annual segment-revenue lines
wins; a filing that says "Single segment" stops the search):
    FY26 consolidated -> FY25 consolidated -> FY26 standalone -> FY25 standalone
("FY26" = the newest 12-month period in the list, so a Dec- or Sep-year
company gets its own latest year.)

Supplementary (separate file, never mixed into the split): the BRSR XBRL
field PercentageOfContributionOfExportsInTheTotalTurnoverOfTheEntity — the
filer's export share of turnover (an India-vs-outside number, entity scope
as the filer's BRSR reporting boundary says).

Usage:
    # 1. symbol list from the app itself (needs the repo's packages):
    cd <repo root> && PYTHONPATH=. python -c "from trading.valuation import \
        pipeline as P; print('\n'.join(P.statement_symbols()))" > symbols.txt
    # 2. fetch + parse (stdlib only, so it runs isolated):
    python -I <this file> --symbols-file symbols.txt [--brsr]
    (--repo <repo root> imports the list directly, but not under -I.)
Re-running reuses <GEO_OUT_DIR>/cache/ (a 200-status download is never fetched twice).
In the repo: data/geography_splits.csv (+ geography_brsr_exports.csv) are a
reviewed run's outputs; KPITTECH's XBRL regions are gross of unallocated
inter-segment revenue, so the chain reads its investor deck instead
(trading/valuation/currency.py MEASURED).
Status values: FOUND / ONLY_INDIA_OUTSIDE / NOT_FILED / FETCH_FAILED, plus
MIXED_BUSINESS_GEO (a table mixing business lines with regions: regions are
shown as % of GROSS segment revenue, no currency shares are summed).
Summary share columns: a bucket is blank when no filed line maps to it
("not separately reported", it may sit inside an OTHER or Europe line).
Raw downloads are cached under <GEO_OUT_DIR>/cache/ (untrusted data, parsed
only, never executed). Pacing: <= ~1.4 requests/second to NSE.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone

# Committed 2026-10-21 (VALFIX batch 3): outputs + raw downloads go to
# GEO_OUT_DIR (default: a "geography_fetch" folder in the system temp dir),
# never into the repo; copy geography_splits.csv and
# geography_brsr_exports.csv to data/ after a reviewed run.
import tempfile  # noqa: E402

HERE = os.environ.get("GEO_OUT_DIR") or os.path.join(tempfile.gettempdir(),
                                                     "geography_fetch")
os.makedirs(HERE, exist_ok=True)
CACHE = os.path.join(HERE, "cache")
os.makedirs(CACHE, exist_ok=True)
COOKIES = os.path.join(CACHE, "nse_cookies.txt")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
PACE_S = 0.7          # ~1.4 requests/second, under the 2/s ceiling
LIST_URL = ("https://www.nseindia.com/api/integrated-filing-results?index=equities"
            "&symbol={sym}&period_ended=all&type=Integrated%20Filing-%20Financials")
BRSR_URL = ("https://www.nseindia.com/api/corporate-bussiness-sustainabilitiy"
            "?index=equities&symbol={sym}")
REFERER = "https://www.nseindia.com/companies-listing/corporate-filings-financial-results"
OLDEST_FY_END = date(2025, 1, 1)   # "FY25" is the oldest year accepted

_last_call = [0.0]


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pace():
    wait = PACE_S - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()


def curl(url: str, out: str, api: bool, referer: str = REFERER,
         max_time: int = 90) -> int:
    """GET url into out; returns HTTP status (0 on transport failure)."""
    _pace()
    cmd = ["curl", "-sS", "--http1.1", "--max-time", str(max_time), "-A", UA,
           "-o", out, "-w", "%{http_code}"]
    if api:
        cmd += ["-c", COOKIES, "-b", COOKIES, "-H", f"Referer: {referer}",
                "-H", "Accept: application/json"]
    r = subprocess.run(cmd + [url], capture_output=True, text=True)
    try:
        return int(r.stdout.strip() or 0)
    except ValueError:
        return 0


def get(url: str, out: str, api: bool, tries: int = 3, **kw) -> tuple[int, str]:
    """Cached GET with retries. Returns (status, fetched_utc)."""
    meta = out + ".meta.json"
    if os.path.exists(out) and os.path.exists(meta):
        m = json.load(open(meta))
        if m.get("status") == 200:
            return 200, m["fetched_utc"]
    st = 0
    for i in range(tries):
        st = curl(url, out, api, **kw)
        if st == 200 and os.path.getsize(out) > 0:
            break
        if api and i == 0:      # re-prime cookies once
            curl("https://www.nseindia.com/", os.path.join(CACHE, "_home.html"), True)
        time.sleep(1.5 * (i + 1))
    ts = utcnow()
    json.dump({"url": url, "status": st, "fetched_utc": ts}, open(meta, "w"))
    return st, ts


# ----------------------------------------------------------------- XBRL parse
XBRLI = "{http://www.xbrl.org/2003/instance}"
XBRLDI = "{http://xbrl.org/2006/xbrldi}"


def _d(s: str | None) -> date | None:
    try:
        return date.fromisoformat((s or "").strip()[:10])
    except ValueError:
        return None


def parse_xbrl(path: str) -> dict:
    """Pull the segment table + a few header facts out of one results XBRL."""
    tree = ET.parse(path)
    root = tree.getroot()
    ctx = {}
    for c in root.iter(XBRLI + "context"):
        cid = c.get("id")
        per = c.find(XBRLI + "period")
        st = en = inst = None
        if per is not None:
            st = _d(getattr(per.find(XBRLI + "startDate"), "text", None))
            en = _d(getattr(per.find(XBRLI + "endDate"), "text", None))
            inst = _d(getattr(per.find(XBRLI + "instant"), "text", None))
        ctx[cid] = {"start": st, "end": en, "instant": inst}
    facts = {}          # local name -> list of (contextRef, text)
    for el in root:
        tag = el.tag
        if not isinstance(tag, str) or "}" not in tag:
            continue
        local = tag.split("}", 1)[1]
        if local in ("context", "unit", "schemaRef"):
            continue
        facts.setdefault(local, []).append((el.get("contextRef"), (el.text or "").strip(),
                                            el.get("unitRef")))

    def first(name):
        v = facts.get(name)
        return v[0][1] if v else None

    def dur(cid):
        c = ctx.get(cid) or {}
        if c.get("start") and c.get("end"):
            return (c["end"] - c["start"]).days, c["end"]
        return None, None

    desc = {cid: t for cid, t, _ in facts.get("DescriptionOfReportableSegment", [])}
    rev = {}
    for cid, t, u in facts.get("SegmentRevenue", []):
        try:
            rev[cid] = float(t)
        except ValueError:
            pass
    # annual = a duration context of >= 350 days
    seg_rows = []
    for cid, name in desc.items():
        if cid not in rev:
            continue
        days, end = dur(cid)
        if days is None or days < 350:
            continue
        seg_rows.append({"ctx": cid, "label": name, "value": rev[cid], "end": end})
    ann_end = max((r["end"] for r in seg_rows), default=None)
    seg_rows = [r for r in seg_rows if r["end"] == ann_end]
    # inter-segment revenue: per segment context if the filer tagged it there,
    # else one annual total (not allocable to a region)
    inter_by_ctx, inter_total, seg_ops_total, seg_gross_total = {}, None, None, None
    for name, store in (("InterSegmentRevenue", "inter"),
                        ("SegmentRevenueFromOperations", "ops"),
                        ("SegmentRevenue", "gross")):
        for cid, t, u in facts.get(name, []):
            days, end = dur(cid)
            if not days or days < 350 or (ann_end and end != ann_end):
                continue
            try:
                val = float(t)
            except ValueError:
                continue
            if cid in desc:
                if store == "inter":
                    inter_by_ctx[cid] = val
                continue
            if store == "inter":
                inter_total = val
            elif store == "ops":
                seg_ops_total = val
            else:
                seg_gross_total = val
    for r in seg_rows:
        r["inter"] = inter_by_ctx.get(r["ctx"])
    # de-duplicate (same label + value tagged twice)
    seen, rows = set(), []
    for r in sorted(seg_rows, key=lambda r: r["ctx"]):
        k = (r["label"].strip().lower(), r["value"])
        if k in seen:
            continue
        seen.add(k)
        rows.append(r)

    # annual P&L revenue (any >=350-day context) for reference
    rev_ops, any_annual_end = None, None
    for cid, t, u in facts.get("RevenueFromOperations", []):
        days, end = dur(cid)
        if days and days >= 350:
            any_annual_end = max(any_annual_end or end, end)
            if ann_end is None or end == ann_end:
                try:
                    rev_ops = float(t)
                except ValueError:
                    pass
    if rev_ops is None:   # banks: total income
        for name in ("Income", "TotalIncome", "RevenueFromOperationsAndOtherIncome"):
            for cid, t, u in facts.get(name, []):
                days, end = dur(cid)
                if days and days >= 350:
                    any_annual_end = max(any_annual_end or end, end)
                    try:
                        rev_ops = rev_ops or float(t)
                    except ValueError:
                        pass

    notes = []
    for name in ("DisclosureOfNotesOnSegmentsExplanatoryTextBlock",
                 "DisclosureOfNotesOnFinancialResultsExplanatoryTextBlock"):
        for cid, t, u in facts.get(name, []):
            s = re.sub(r"<[^>]+>", " ", html.unescape(t))
            notes.append(re.sub(r"\s+", " ", s).strip())
    return {
        "segment_flag": first("IsCompanyReportingMultisegmentOrSingleSegment"),
        "nature": first("NatureOfReportStandaloneConsolidated"),
        "rows": rows,
        "inter_total": inter_total,
        "seg_ops_total": seg_ops_total,
        "seg_gross_total": seg_gross_total,
        "annual_end": (ann_end or any_annual_end),
        "has_annual": bool(ann_end or any_annual_end),
        "revenue_ops": rev_ops,
        "notes": notes,
    }


# ------------------------------------------------------------ classification
def _norm(s: str) -> str:
    s = s.lower().replace("&", " and ")
    s = re.sub(r"^\s*(\(?[a-z0-9]{1,4}[.)]\s+|[ivx]+\)\s*)", "", s)   # "a) ", "(b) ", "ii) "
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^less\s+", "", s)
    return s.replace("intersegment", "inter segment")


NEUTRAL = re.compile(r"^(others?|other (segments?|regions?|countries|geograph\w*)|"
                     r"others? unallocated|unallocated|unallocable|elimination\w*|"
                     r"reconciling items?|inter segment\w*"
                     r"( elimination\w*| revenue| sales)?)$")
MINOR_BUSINESS_PCT = 2.0   # business lines this small (share of gross) are set aside
# Reviewed by hand (2026-10-21) — a region word inside a BUSINESS name, or a
# label set that names plants/businesses, is not a revenue geography.
OVERRIDES = {
    "ETERNAL": ("NOT_FILED", "segments are business lines; 'India' is part of the business "
                "name 'India food ordering and delivery', not a geographic split"),
    "HINDUNILVR": ("NOT_FILED", "segments are business lines; exports sit inside the "
                   "'Others (includes Exports)' business segment and are not separated"),
}
NOTES = {
    "SAIL": "segment flag says 'Geographical' but the lines are Indian steel plants, "
            "not customer geography",
    "CONCOR": "EXIM / DOM are container-logistics business lines (rupee billing), "
              "not customer geography",
    "MCX": "NSE returned no filings for MCX (its shares trade on BSE); not fetched from BSE",
    "GODREJCP": "the label 'Africa (including Strength of Nature)' includes Strength of "
                "Nature, a US hair-care business; its USD part is not separable",
    "WIPRO": "Americas 1/2 include Canada and Latin America; APMEA includes India",
}
RULES = [  # (regex on normalised label, bucket, note) — first match wins
    (r"\boutside india\b|\boverseas\b|\binternational\b|\bexports?\b|\bforeign\b|"
     r"\bglobal\b(?! generics)", "OUTSIDE", ""),
    (r"\bnorth america\b|\bamericas?\b|\busa\b|\bu s a\b|\bunited states\b|\bus\b|"
     r"\blatin america\b|\bsouth america\b|\bcanada\b|\bmexico\b|\bbrazil\b|\blatam\b|"
     r"\bargentina\b|\bchile\b|\bcolombia\b|\bperu\b",
     "USD", ""),
    (r"\bemea\b|\beurope middle east\b", "EUR", "EMEA mapped to EUR (includes UK/Middle East/Africa)"),
    (r"\beurope\b.*\b(uk|united kingdom)\b|\b(uk|united kingdom)\b.*\beurope\b", "EUR",
     "Europe incl. UK mapped to EUR (UK not separable)"),
    (r"\bcontinental europe\b|\beurope\b|\beuropean\b|\beu\b|\bgermany\b|\bfrance\b|"
     r"\bnetherlands\b|\bitaly\b|\bspain\b", "EUR", ""),
    (r"\bunited kingdom\b|\buk\b|\bu k\b|\bbritain\b|\bengland\b", "GBP", ""),
    (r"\bindian?\b|\bdomestic\b|\bwithin india\b", "INR", ""),
    (r"\brest of (the )?world\b|\brow\b|\bmiddle east\b|\basia\b|\bapac\b|"
     r"\bafrica\b|\baustralia\b|\bjapan\b|\bchina\b|\basian\b|\bsaarc\b|"
     r"\bgcc\b|\bjapan\b|\bnepal\b|\bbangladesh\b|\bsri lanka\b|\bsingapore\b|"
     r"\binternational markets\b|\bindonesia\b|\bmalaysia\b|\bthailand\b|\bvietnam\b|"
     r"\bphilippines\b|\bnigeria\b|\bkenya\b|\begypt\b|\buae\b|\bsaudi\b|\bqatar\b|"
     r"\boman\b|\bkuwait\b|\bturkey\b|\brussia\b|\bcis\b|\bisrael\b|\bmena\b|"
     r"\bgulf\b|\bfar east\b|\bsouth asia\b|\boceania\b|\bnew zealand\b|\bkorea\b|"
     r"\btaiwan\b|\bmauritius\b|\bmyanmar\b|\bbhutan\b|\bmaldives\b|\bsea\b|"
     r"\bapmea\b|\bapac\b",
     "OTHER", ""),
]


def classify_label(label: str) -> tuple[str | None, str]:
    n = _norm(label)
    if NEUTRAL.match(n):
        return "NEUTRAL", ""
    for rx, b, note in RULES:
        if re.search(rx, n):
            return b, note
    return None, ""


def classify(rows: list[dict]) -> dict:
    out, geo, biz = [], 0, 0
    gross = sum(r["value"] for r in rows if r["value"] > 0)
    biz_val = 0.0
    for r in rows:
        b, note = classify_label(r["label"])
        if b is None and r["value"] == 0:
            b, note = "NEUTRAL", "zero-value line ignored"
        out.append({**r, "bucket": b, "note": note})
        if b in ("USD", "EUR", "GBP", "INR", "OTHER", "OUTSIDE"):
            geo += 1
        elif b is None:
            biz += 1
            biz_val += max(r["value"], 0)
    minor = ""
    if geo and biz and gross and biz_val / gross * 100.0 <= MINOR_BUSINESS_PCT:
        minor = ("business line(s) " + ", ".join(r["label"] for r in out if r["bucket"] is None)
                 + f" = {biz_val / gross * 100.0:.2f}% of gross segment revenue, set aside "
                 "(shares are of the regional lines)")
        biz = 0
    if not rows:
        kind = "NONE"
    elif geo and not biz:
        kind = "GEO"
    elif geo and biz:
        kind = "MIXED"
    else:
        kind = "BUSINESS"
    return {"rows": out, "kind": kind, "minor": minor}


# --------------------------------------------------------------- per symbol
def qe_date(s: str) -> date | None:
    try:
        return datetime.strptime(s.strip().title(), "%d-%b-%Y").date()
    except ValueError:
        return None


def pick_filings(listing: list[dict]) -> list[dict]:
    """Newest revision per (qe, basis); returns the candidate order."""
    best = {}
    for r in listing:
        q = qe_date(r.get("qe_Date") or "")
        if not q or q < OLDEST_FY_END or not r.get("xbrl"):
            continue
        basis = "consolidated" if (r.get("consolidated") or "").lower().startswith("cons") \
            else "standalone"
        k = (q, basis)
        stamp = r.get("broadcast_Date") or ""
        try:
            bt = datetime.strptime(stamp, "%d-%b-%Y %H:%M:%S")
        except ValueError:
            bt = datetime.min
        if k not in best or bt > best[k][0]:
            best[k] = (bt, {**r, "_q": q, "_basis": basis})
    return [v[1] for v in best.values()]


def fetch_symbol(sym: str, log) -> dict:
    enc = urllib.parse.quote(sym, safe="")
    lst_path = os.path.join(CACHE, f"list_{sym.replace('&', '_')}.json")
    url = LIST_URL.format(sym=enc)
    st, ts = get(url, lst_path, api=True)
    res = {"symbol": sym, "list_url": url, "list_status": st, "list_fetched": ts}
    if st != 200:
        res.update(status="FETCH_FAILED", reason=f"NSE filing list HTTP {st}")
        return res
    try:
        listing = json.load(open(lst_path)).get("data") or []
    except Exception as e:  # noqa: BLE001
        res.update(status="FETCH_FAILED", reason=f"filing list not JSON: {e}")
        return res
    cands = pick_filings(listing)
    if not cands:
        res.update(status="NOT_FILED", reason="no integrated results filing dated 2025 or later")
        return res
    # quarter-ends newest first; annual years are found by reading the XBRL
    # FY26 for a March-year company is the 2026-03-31 filing; try quarter-ends up
    # to that date first (newest first), the later quarters (no 12-month column
    # for a March-year filer) only after them.
    pivot = date(2026, 3, 31)
    allq = sorted({c["_q"] for c in cands}, reverse=True)
    qs = [q for q in allq if q <= pivot] + [q for q in allq if q > pivot]
    tried, chosen, fy_seen = [], None, []
    for basis in ("consolidated", "standalone"):
        years_found = 0
        for q in qs:
            c = next((x for x in cands if x["_q"] == q and x["_basis"] == basis), None)
            if c is None:
                continue
            xurl = c["xbrl"]
            xp = os.path.join(CACHE, "x_" + os.path.basename(urllib.parse.urlparse(xurl).path))
            xst, xts = get(xurl, xp, api=False, max_time=180)
            if xst != 200:
                tried.append({"q": str(q), "basis": basis, "url": xurl, "err": f"HTTP {xst}"})
                continue
            try:
                px = parse_xbrl(xp)
            except Exception as e:  # noqa: BLE001
                tried.append({"q": str(q), "basis": basis, "url": xurl, "err": f"parse {e}"})
                continue
            if not px["has_annual"]:
                tried.append({"q": str(q), "basis": basis, "url": xurl, "err": "no 12-month period"})
                continue
            years_found += 1
            fy_seen.append(str(px["annual_end"]))
            rec = {"q": str(q), "basis": basis, "url": xurl, "fetched": xts, **px}
            tried.append({"q": str(q), "basis": basis, "url": xurl,
                          "flag": px["segment_flag"], "n_rows": len(px["rows"]),
                          "fy_end": str(px["annual_end"])})
            if px["rows"]:
                chosen = rec
                break
            if (px["segment_flag"] or "").lower().startswith("single"):
                chosen = rec          # declared single segment: stop
                break
            if chosen is None:
                chosen = rec          # remember the first annual filing seen
            if years_found >= 2:      # FY26 then FY25 only
                break
        if chosen and chosen["rows"]:
            break
        if chosen and (chosen.get("segment_flag") or "").lower().startswith("single"):
            break
    res["tried"] = tried
    if chosen is None:
        res.update(status="NOT_FILED", reason="no 12-month results XBRL for FY25/FY26 in the list")
        return res
    for row in chosen["rows"]:
        if row.get("inter"):
            row["value_gross"] = row["value"]
            row["value"] = row["value"] - row["inter"]
    cl = classify(chosen["rows"])
    res.update(chosen=chosen, cls=cl)
    flag = chosen.get("segment_flag") or ""
    if cl["kind"] == "GEO":
        buckets = {r["bucket"] for r in cl["rows"]} - {"NEUTRAL", None}
        res["status"] = "ONLY_INDIA_OUTSIDE" if buckets <= {"INR", "OUTSIDE"} else "FOUND"
        res["reason"] = cl.get("minor") or ""
    elif cl["kind"] == "MIXED":
        res["status"] = "MIXED_BUSINESS_GEO"
        res["reason"] = ("segment table mixes business lines with regions; regions shown, "
                         "business lines not assigned a currency")
    elif cl["kind"] == "BUSINESS":
        res["status"] = "NOT_FILED"
        res["reason"] = "segments are business lines, no geographic lines in the results XBRL"
    else:
        res["status"] = "NOT_FILED"
        res["reason"] = f"no segment lines in the results XBRL (flag: {flag or 'blank'})"
    if sym in OVERRIDES:
        res["status"], res["reason"] = OVERRIDES[sym]
        res["reason"] = "[reviewed] " + res["reason"]
    if chosen["basis"] == "consolidated" and chosen.get("annual_end") and \
            res["status"] in ("FOUND", "ONLY_INDIA_OUTSIDE", "MIXED_BUSINESS_GEO"):
        newer = [t for t in tried if t.get("basis") == "consolidated" and t.get("fy_end")
                 and t["fy_end"] > str(chosen["annual_end"])]
        if newer:
            res["reason"] = ((res["reason"] + " | ") if res["reason"] else "") + \
                f"newer FY{newer[0]['fy_end'][:4]} filing has no segment lines " \
                f"(flag: {newer[0].get('flag')}); older year used"
    return res


def fetch_brsr(sym: str) -> dict:
    enc = urllib.parse.quote(sym, safe="")
    p = os.path.join(CACHE, f"brsr_list_{sym.replace('&', '_')}.json")
    url = BRSR_URL.format(sym=enc)
    st, ts = get(url, p, api=True,
                 referer="https://www.nseindia.com/companies-listing/"
                         "corporate-filings-bussiness-sustainabilitiy-reports")
    out = {"symbol": sym, "list_url": url}
    if st != 200:
        return {**out, "status": "FETCH_FAILED", "reason": f"HTTP {st}"}
    try:
        data = json.load(open(p)).get("data") or []
    except Exception as e:  # noqa: BLE001
        return {**out, "status": "FETCH_FAILED", "reason": f"not JSON {e}"}
    data = [d for d in data if d.get("xbrlFile")]
    if not data:
        return {**out, "status": "NOT_FILED", "reason": "no BRSR XBRL listed"}
    data.sort(key=lambda d: (d.get("fyTo") or 0, d.get("submissionDate") or ""), reverse=True)
    d = data[0]
    xurl = d["xbrlFile"]
    xp = os.path.join(CACHE, "b_" + os.path.basename(urllib.parse.urlparse(xurl).path))
    xst, xts = get(xurl, xp, api=False, max_time=180)
    if xst != 200:
        return {**out, "status": "FETCH_FAILED", "reason": f"XBRL HTTP {xst}", "source_url": xurl}
    try:
        root = ET.parse(xp).getroot()
    except Exception as e:  # noqa: BLE001
        return {**out, "status": "FETCH_FAILED", "reason": f"parse {e}", "source_url": xurl}
    vals = {}
    for el in root:
        if isinstance(el.tag, str) and "}" in el.tag:
            vals.setdefault(el.tag.split("}", 1)[1], []).append(
                ((el.text or "").strip(), el.get("contextRef")))

    def v(name):
        x = vals.get(name)
        return x[0][0] if x else None
    raw = v("PercentageOfContributionOfExportsInTheTotalTurnoverOfTheEntity")
    pct, unit_note = None, ""
    if raw not in (None, ""):
        try:
            f = float(raw)
            if f > 1.0:
                pct, unit_note = f, "filed as a percent (value > 1), taken as is"
            else:
                pct, unit_note = f * 100.0, "filed as a fraction (taxonomy 'pure'), x100"
        except ValueError:
            unit_note = f"not numeric: {raw!r}"
    return {**out, "status": "FOUND" if pct is not None else "NOT_FILED",
            "reason": "" if pct is not None else "export % field blank",
            "fy": f"{d.get('fyFrom')}-{d.get('fyTo')}", "raw": raw, "export_pct": pct,
            "unit_note": unit_note,
            "boundary": v("ReportingBoundary") or v("TypeOfReportingBoundary") or "",
            "n_countries": v("NumberOfCountriesWhereMarketServedByTheEntity"),
            "source_url": xurl, "fetched_utc": xts}


# ------------------------------------------------------------------- output
def cr(x: float) -> float:
    return round(x / 1e7, 2)


def write_outputs(results: list[dict]):
    split_cols = ["symbol", "fy_end", "basis", "region_label_as_filed", "amount_cr", "bucket",
                  "share_pct_of_revenue", "source_url", "fetched_utc", "mapping_note"]
    sum_cols = ["symbol", "fy_end", "usd_share_pct", "eur_share_pct", "gbp_share_pct",
                "inr_share_pct", "other_share_pct", "n_regions", "status", "source_url",
                "basis", "segment_flag_as_filed", "segments_as_filed", "geo_total_cr",
                "revenue_from_operations_cr", "intersegment_unallocated_cr",
                "intersegment_pct_of_gross", "fetched_utc", "note"]
    with open(os.path.join(HERE, "geography_splits.csv"), "w", newline="") as fs, \
            open(os.path.join(HERE, "geography_summary.csv"), "w", newline="") as fm:
        ws, wm = csv.writer(fs), csv.writer(fm)
        ws.writerow(split_cols)
        wm.writerow(sum_cols)
        for r in results:
            ch = r.get("chosen") or {}
            cl = r.get("cls") or {"rows": []}
            src = ch.get("url") or r.get("list_url")
            fetched = ch.get("fetched") or r.get("list_fetched")
            fy = str(ch.get("annual_end") or "")
            st = r["status"]
            per_seg_inter = any(x.get("inter") for x in cl["rows"])
            inter_un = None if per_seg_inter else ch.get("inter_total")
            gross_all = sum(x["value"] for x in cl["rows"] if x["value"] > 0) or None
            inter_pct = (inter_un / gross_all * 100.0) if (inter_un and gross_all) else None
            gross_note = ""
            if inter_pct is not None and inter_pct >= 0.5:
                gross_note = (f"lines are GROSS of inter-segment revenue Rs {cr(inter_un)} cr "
                              f"({inter_pct:.1f}% of gross) that the XBRL does not allocate "
                              "to a region: shares are of gross segment revenue")
            elif per_seg_inter:
                gross_note = "inter-segment revenue tagged per segment and removed (external)"
            shares = {k: None for k in ("USD", "EUR", "GBP", "INR", "OTHER")}
            n_regions, geo_total = 0, None
            if st in ("FOUND", "ONLY_INDIA_OUTSIDE", "MIXED_BUSINESS_GEO"):
                geo_rows = [x for x in cl["rows"] if x["bucket"] not in (None,)]
                # NEUTRAL lines ("Others") count as OTHER only in a pure GEO table
                use = []
                for x in geo_rows:
                    b = x["bucket"]
                    if b == "NEUTRAL":
                        n = _norm(x["label"])
                        if st == "MIXED_BUSINESS_GEO" or x["value"] <= 0 or not re.fullmatch(
                                r"others?|other (regions?|countries|geograph\w*)", n):
                            continue
                        b, note = "OTHER", "'Others' in a geographic table -> OTHER"
                    else:
                        note = x["note"]
                    if b == "OUTSIDE":
                        b, note = "OTHER", ("outside-India lump -> OTHER (owner rule: OTHER "
                                            "treated as USD by the pipeline)")
                    use.append((x, b, note))
                pos = [u for u in use if u[0]["value"] > 0]
                geo_total = sum(u[0]["value"] for u in pos)
                gross = sum(x["value"] for x in cl["rows"] if x["value"] > 0)
                mixed_parts = []
                # a bucket gets a number only when a filed line maps to it; blank =
                # "not separately reported" (it may sit inside an OTHER/Europe line)
                has_uk = any(b == "GBP" for _, b, _ in use)
                for i, (x, b, note) in enumerate(use):
                    if b == "EUR" and not has_uk and not note:
                        use[i] = (x, b, "label does not say whether the UK is inside "
                                        "this Europe line; GBP not separable")
                if st != "MIXED_BUSINESS_GEO" and geo_total > 0:
                    for _, b, _ in pos:
                        shares[b] = shares[b] or 0.0
                for x, b, note in use:
                    if st == "MIXED_BUSINESS_GEO":
                        share = (x["value"] / gross * 100.0) if gross else None
                        note = ((note + "; ") if note else "") + \
                            "share of GROSS segment revenue (business lines unassigned)"
                        if share is not None and x["value"] > 0:
                            mixed_parts.append(f"{b} {share:.1f}% ({x['label']})")
                    else:
                        share = (x["value"] / geo_total * 100.0) if geo_total else None
                        if share is not None and x["value"] > 0:
                            shares[b] += share
                    if gross_note:
                        note = ((note + "; ") if note else "") + gross_note
                    ws.writerow([r["symbol"], fy, ch.get("basis"), x["label"], cr(x["value"]),
                                 b, None if share is None else round(share, 2), src, fetched,
                                 note])
                if mixed_parts:
                    unassigned = sum(x["value"] for x in cl["rows"]
                                     if x["bucket"] is None and x["value"] > 0)
                    r["reason"] = (r.get("reason", "") + " | regions as % of gross segment "
                                   "revenue: " + "; ".join(mixed_parts) +
                                   f"; business lines unassigned {unassigned / gross * 100:.1f}%")
                n_regions = len(pos)
                if st == "MIXED_BUSINESS_GEO":
                    for x in cl["rows"]:
                        if x["bucket"] is None:
                            ws.writerow([r["symbol"], fy, ch.get("basis"), x["label"],
                                         cr(x["value"]), "BUSINESS_LINE",
                                         round(x["value"] / gross * 100.0, 2)
                                         if gross and x["value"] > 0 else None, src, fetched,
                                         "business line, currency not assigned; share of "
                                         "GROSS segment revenue"])
            segs = "; ".join(f"{x['label']}={cr(x['value'])}" for x in cl["rows"])[:600]
            note = r.get("reason", "")
            if gross_note and st != "NOT_FILED":
                note = (note + " | " + gross_note).strip(" |")
            if r.get("hint"):
                note = (note + " | notes text: " + r["hint"]).strip(" |")
            wm.writerow([r["symbol"], fy,
                         *[None if shares[k] is None else round(shares[k], 2)
                           for k in ("USD", "EUR", "GBP", "INR", "OTHER")],
                         n_regions, st, src, ch.get("basis"), ch.get("segment_flag"),
                         segs, None if geo_total is None else cr(geo_total),
                         None if ch.get("revenue_ops") is None else cr(ch["revenue_ops"]),
                         None if inter_un is None else cr(inter_un),
                         None if inter_pct is None else round(inter_pct, 2),
                         fetched, note])


GEO_HINT = re.compile(r"[^.]{0,160}\b(outside india|geograph\w*|overseas|exports? "
                      r"(revenue|sales)|north america|europe)\b[^.]{0,200}", re.I)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo")
    ap.add_argument("--symbols-file")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--brsr", action="store_true")
    a = ap.parse_args()
    if a.only:
        syms = a.only
    elif a.symbols_file:
        syms = [s.strip() for s in open(a.symbols_file) if s.strip()]
    else:
        sys.path.insert(0, a.repo)
        from trading.valuation import pipeline as P  # noqa: E402
        syms = list(P.statement_symbols())
        open(os.path.join(HERE, "symbols.txt"), "w").write("\n".join(syms) + "\n")
    # prime cookies
    curl("https://www.nseindia.com/", os.path.join(CACHE, "_home.html"), True)
    results = []
    log = open(os.path.join(HERE, "fetch_log.jsonl"), "w")
    for i, s in enumerate(syms, 1):
        try:
            r = fetch_symbol(s, log)
        except Exception as e:  # noqa: BLE001
            r = {"symbol": s, "status": "FETCH_FAILED", "reason": f"script error {e}"}
        ch = r.get("chosen") or {}
        if r["status"] == "NOT_FILED" and ch.get("notes"):
            m = GEO_HINT.search(" ".join(ch["notes"]))
            if m:
                r["hint"] = m.group(0).strip()[:300]
        if s in NOTES:
            r["reason"] = ((r.get("reason") or "") + " | " + NOTES[s]).strip(" |")
        slim = {k: v for k, v in r.items() if k not in ("chosen", "cls")}
        slim["chosen_url"] = ch.get("url")
        slim["rows"] = [(x["label"], x["value"], x.get("bucket")) for x in
                        (r.get("cls") or {}).get("rows", [])]
        log.write(json.dumps(slim, default=str) + "\n")
        log.flush()
        results.append(r)
        print(f"{i:3d} {s:12s} {r['status']:20s} {str(ch.get('annual_end') or ''):10s} "
              f"{ch.get('basis') or '':12s} {len(slim['rows'])} rows", flush=True)
    write_outputs(results)
    if a.brsr:
        with open(os.path.join(HERE, "geography_brsr_exports.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["symbol", "fy", "export_pct_of_turnover", "raw_as_filed", "unit_note",
                        "reporting_boundary", "n_countries_served", "status", "reason",
                        "source_url", "fetched_utc"])
            for s in syms:
                try:
                    b = fetch_brsr(s)
                except Exception as e:  # noqa: BLE001
                    b = {"symbol": s, "status": "FETCH_FAILED", "reason": f"script error {e}"}
                w.writerow([s, b.get("fy"), None if b.get("export_pct") is None
                            else round(b["export_pct"], 2), b.get("raw"), b.get("unit_note"),
                            b.get("boundary"), b.get("n_countries"), b["status"],
                            b.get("reason", ""), b.get("source_url") or b.get("list_url"),
                            b.get("fetched_utc")])
                f.flush()
                print("BRSR", s, b["status"], b.get("export_pct"), flush=True)


if __name__ == "__main__":
    main()
