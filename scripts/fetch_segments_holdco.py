"""Build data/segment_ebit.csv and data/holdco_stakes.csv from NSE filings.

Owner choices 2026-10-21 (VALFIX batch 2, VAL-01 / VAL-14b):

* CONGLOMERATE SUM-OF-PARTS — each segment's EBIT ("segment profit before
  tax and finance costs") for the financial year, from the company's own
  results XBRL (NSE integrated filing, CONSOLIDATED where filed):
    list: https://www.nseindia.com/api/integrated-filing-results?index=equities
          &symbol=<SYM>&period_ended=all&type=Integrated%20Filing-%20Financials
  The full-year facts are the ones whose context runs FY start -> FY end;
  the segment name is the DescriptionOfReportableSegment in that context.
  The script checks the segments add up to the filed segment total.

* HOLDING COMPANY — the holding company's share count in each listed
  subsidiary, read BY NAME from the subsidiary's latest shareholding
  pattern XBRL:
    list: https://www.nseindia.com/api/corporate-share-holdings-master
          ?index=equities&symbol=<SUBSIDIARY>
  Only the subsidiaries named below are read (owner's list); a holder name
  that is not in the filing writes NO row and is printed — never guessed.
  TATAINVEST is left for the owner: its many small stakes cannot be proved
  complete from the filings (holders under 1% are not named).

Every row keeps the XBRL URL, the list URL and the fetch date.
Polite: one request at a time, ~1 s apart.

Usage: python scripts/fetch_segments_holdco.py [--only segments|holdco]
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SEG_OUT = ROOT / "data" / "segment_ebit.csv"
HOLD_OUT = ROOT / "data" / "holdco_stakes.csv"
RES_API = ("https://www.nseindia.com/api/integrated-filing-results?index=equities"
           "&symbol={sym}&period_ended=all&type=Integrated%20Filing-%20Financials")
SHP_API = ("https://www.nseindia.com/api/corporate-share-holdings-master"
           "?index=equities&symbol={sym}")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126 Safari/537.36",
      "Accept": "application/json, text/plain, */*"}
IST = timezone(timedelta(hours=5, minutes=30))
CR = 1e7

#: Conglomerates and the financial year read (owner: RELIANCE first).
SEGMENT_SYMBOLS = {"RELIANCE": ("2025-04-01", "2026-03-31")}

#: Holding companies: the holder name exactly as it appears in the
#: subsidiaries' filings, and the listed subsidiaries (owner's list).
HOLDCOS = {
    "BAJAJHLDNG": {"holder": "BAJAJ HOLDINGS AND INVESTMENT LIMITED",
                   "subsidiaries": ["BAJAJ-AUTO", "BAJAJFINSV"]},
}

SEG_COLS = ["symbol", "segment", "ebit_cr", "revenue_cr", "fy_start", "fy_end",
            "basis", "segment_total_cr", "filed", "xbrl_url", "list_url", "fetched"]
HOLD_COLS = ["holdco", "subsidiary", "holder_name", "shares_held", "stake_pct",
             "asof", "filed", "xbrl_url", "list_url", "fetched"]


def _ts(s: str) -> str:
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y"):
        try:
            return datetime.strptime(str(s).strip(), fmt).isoformat()
        except ValueError:
            continue
    return ""


def _contexts(x: str) -> dict:
    """context id -> (start, end, has_dimension)."""
    out = {}
    for cid, body in re.findall(r'<xbrli:context id="([^"]+)">(.*?)</xbrli:context>', x, re.S):
        s = re.search(r"<xbrli:startDate>([^<]+)", body)
        e = re.search(r"<xbrli:endDate>([^<]+)", body)
        out[cid] = (s.group(1)[:10] if s else "", e.group(1)[:10] if e else "",
                    "xbrldi:" in body)
    return out


def parse_segments(x: str, fy_start: str, fy_end: str) -> tuple[list[dict], float | None]:
    """([{segment, ebit_cr, revenue_cr}], filed segment total ₹ cr)."""
    ctx = _contexts(x)
    full = {c for c, (s, e, _d) in ctx.items() if s == fy_start and e == fy_end}
    desc = {}
    for m in re.finditer(r':DescriptionOfReportableSegment\s+contextRef="([^"]+)"[^>]*>([^<]*)<', x):
        if m.group(1) in full:
            desc[m.group(1)] = m.group(2).strip()

    def facts(tag):
        got = {}
        for m in re.finditer(rf':{tag}\s+contextRef="([^"]+)"[^>]*>([^<]*)<', x):
            if m.group(1) in full:
                try:
                    got[m.group(1)] = float(m.group(2))
                except ValueError:
                    pass
        return got
    ebit = facts("SegmentProfitLossBeforeTaxAndFinanceCosts")
    rev = facts("SegmentRevenue")
    total = next((v for c, v in ebit.items() if not ctx[c][2]), None)
    rows = []
    for c, v in ebit.items():
        if c not in desc:
            continue
        # revenue sits in the sibling context of the same segment number
        num = re.search(r"(\d+)D$", c)
        r_val = None
        if num:
            for rc, rv in rev.items():
                if rc in desc and desc[rc] == desc[c] and ctx[rc][2]:
                    r_val = rv
                    break
        rows.append({"segment": desc[c], "ebit_cr": round(v / CR, 2),
                     "revenue_cr": round(r_val / CR, 2) if r_val is not None else ""})
    return rows, (round(total / CR, 2) if total is not None else None)


def _norm(s: str) -> str:
    s = str(s or "").upper().replace("&", " AND ")
    return re.sub(r"[^A-Z0-9]+", " ", s).strip()


def parse_holder(x: str, holder: str) -> dict | None:
    """{shares_held, stake_pct, asof} of a holder named in an SHP XBRL."""
    want = _norm(holder)
    for m in re.finditer(r':NameOfTheShareholder\s+contextRef="([^"]+)"[^>]*>([^<]*)<', x):
        if _norm(m.group(2)) != want:
            continue
        base = re.sub(r"^[A-Z]_", "", m.group(1))

        def fact(tag):
            mm = re.search(rf':{tag}\s+contextRef="(?:[A-Z]_)?{re.escape(base)}"[^>]*>([^<]*)<', x)
            try:
                return float(mm.group(1)) if mm else None
            except ValueError:
                return None
        sh = fact("NumberOfShares") or fact("NumberOfFullyPaidUpEquityShares")
        pc = fact("ShareholdingAsAPercentageOfTotalNumberOfShares")
        if sh is None or sh <= 0:
            return None
        d = re.search(r':DateOfReport\s+contextRef="[^"]+"[^>]*>([^<]*)<', x)
        return {"shares_held": int(sh), "holder_name": m.group(2).strip(),
                "stake_pct": round(pc * 100, 4) if pc is not None else "",
                "asof": d.group(1).strip()[:10] if d else ""}
    return None


def _latest(rows: list, key_date: str, key_ts: str) -> dict | None:
    rows = [r for r in rows if r.get("xbrl") and str(r["xbrl"]).endswith(".xml")]
    if not rows:
        return None
    return max(rows, key=lambda r: (datetime.strptime(r[key_date].strip(), "%d-%b-%Y")
                                    if r.get(key_date) else datetime.min,
                                    _ts(r.get(key_ts) or "")))


def do_segments(c: httpx.Client, fetched: str) -> list[dict]:
    out = []
    for sym, (fs, fe) in SEGMENT_SYMBOLS.items():
        url = RES_API.format(sym=sym)
        rows = (c.get(url).json().get("data") or [])
        time.sleep(1.0)
        fe_nse = datetime.fromisoformat(fe).strftime("%d-%b-%Y").upper()
        hits = [r for r in rows if str(r.get("qe_Date", "")).upper() == fe_nse
                and str(r.get("xbrl", "")).endswith(".xml")]
        cons = [r for r in hits if str(r.get("consolidated", "")).lower().startswith("cons")]
        pick = _latest(cons or hits, "qe_Date", "creation_Date")
        if pick is None:
            print(f"{sym}: no results filing for {fe} — no rows written")
            continue
        x = c.get(pick["xbrl"], headers={**UA, "Accept": "application/xml"}).text
        time.sleep(1.0)
        segs, total = parse_segments(x, fs, fe)
        s = round(sum(r["ebit_cr"] for r in segs), 2)
        if not segs or total is None or abs(s - total) > 1.0:
            print(f"{sym}: segments {s} do not add to the filed total {total} — no rows written")
            continue
        for r in segs:
            out.append({"symbol": sym, **r, "fy_start": fs, "fy_end": fe,
                        "basis": pick.get("consolidated", ""), "segment_total_cr": total,
                        "filed": _ts(pick.get("broadcast_Date") or "")[:10],
                        "xbrl_url": pick["xbrl"], "list_url": url, "fetched": fetched})
            print(f"{sym}: {r['segment']:28s} EBIT ₹{r['ebit_cr']:,.2f} cr")
        print(f"{sym}: {len(segs)} segments add to the filed total ₹{total:,.2f} cr")
    return out


def do_holdco(c: httpx.Client, fetched: str) -> list[dict]:
    out = []
    for hold, spec in HOLDCOS.items():
        for sub in spec["subsidiaries"]:
            url = SHP_API.format(sym=sub)
            rows = c.get(url).json()
            rows = rows if isinstance(rows, list) else (rows.get("data") or [])
            time.sleep(1.0)
            pick = _latest(rows, "date", "broadcastDate")
            if pick is None:
                print(f"{hold} <- {sub}: no shareholding XBRL listed — no row")
                continue
            x = c.get(pick["xbrl"], headers={**UA, "Accept": "application/xml"}).text
            time.sleep(1.0)
            got = parse_holder(x, spec["holder"])
            if got is None:
                print(f"{hold} <- {sub}: '{spec['holder']}' not named in {pick['xbrl']} — no row")
                continue
            out.append({"holdco": hold, "subsidiary": sub, **got,
                        "filed": _ts(pick.get("broadcastDate") or "")[:10],
                        "xbrl_url": pick["xbrl"], "list_url": url, "fetched": fetched})
            print(f"{hold} <- {sub}: {got['shares_held']:,} shares "
                  f"({got['stake_pct']}%) as on {got['asof']}")
    return out


def _write(path: Path, cols: list, rows: list) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})
    print(f"wrote {len(rows)} row(s) to {path}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=("segments", "holdco"))
    a = ap.parse_args(argv)
    fetched = datetime.now(IST).date().isoformat()
    with httpx.Client(headers=UA, timeout=40.0, follow_redirects=True) as c:
        if a.only in (None, "segments"):
            _write(SEG_OUT, SEG_COLS, do_segments(c, fetched))
        if a.only in (None, "holdco"):
            _write(HOLD_OUT, HOLD_COLS, do_holdco(c, fetched))
    return 0


if __name__ == "__main__":
    sys.exit(main())
