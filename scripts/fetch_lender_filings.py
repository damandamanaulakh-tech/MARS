"""Build data/lender_networth_filings.csv — each lender's FILED net worth.

Owner choice 2026-10-21 (VALFIX batch 2, VAL-02): a lender's book is the
FILED net worth when the feed's equity is more than 10% off it. This script
reads the exchange filing for every lender in the statement set (route
methods bank_pb_roe and nbfc_pb, from trading/valuation/route.py over the
200 statement names) at two year-ends (31-Mar-2026 and 31-Mar-2025):

  1. NSE results list:  https://www.nseindia.com/api/integrated-filing-results
                        ?index=equities&symbol=<SYM>&period_ended=all
                        &type=Integrated%20Filing-%20Financials
  2. the XBRL it links (nsearchives), CONSOLIDATED where filed, else
     standalone; the latest submission for that quarter-end wins.

Net worth = share capital + reserves at the year-end balance-sheet date
(owners' equity; minority interest is NOT in it):
  * banks (Banking taxonomy): PaidUpValueOfEquityShareCapital
                              + ReservesAndSurplus
  * Ind-AS companies:         EquityShareCapital + OtherEquity
                              (or EquityAttributableToOwnersOfParent)
Facts are read only from a context whose instant IS the year-end and that
carries no dimension. Values in the XBRL are rupees; stored in ₹ crore.

A year-end with no filing, or a filing whose balance sheet is not in the
XBRL, is written with net_worth_cr blank and the reason in `note` — never
guessed. Polite: one request at a time, ~1 s apart.

Usage: python scripts/fetch_lender_filings.py [--symbols HDFCBANK SBIN ...]
(--symbols re-reads those names and keeps every other row on file.)
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
sys.path.insert(0, str(ROOT))
OUT = ROOT / "data" / "lender_networth_filings.csv"
API = ("https://www.nseindia.com/api/integrated-filing-results?index=equities"
       "&symbol={sym}&period_ended=all&type=Integrated%20Filing-%20Financials")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126 Safari/537.36",
      "Accept": "application/json, text/plain, */*"}
IST = timezone(timedelta(hours=5, minutes=30))
FY_ENDS = ("2026-03-31", "2025-03-31")
LENDER_METHODS = ("bank_pb_roe", "nbfc_pb")
CR = 1e7

COLS = ["symbol", "method", "fy_end", "basis", "capital_cr", "reserves_cr",
        "net_worth_cr", "tags", "audited", "submission", "filed", "xbrl_url",
        "list_url", "fetched", "note"]

# A single "total" tag, tried when capital + reserves are not both there.
TOTALS = ("EquityAttributableToOwnersOfParent",)


def lenders() -> list[tuple[str, str]]:
    """(symbol, method) for every lender among the statement names."""
    from trading.config import Config
    from trading.valuation import pipeline as P, route as R
    names = {}
    with (ROOT / "data" / "upstox_nse_equities.csv").open() as f:
        for r in csv.DictReader(f):
            names[r["ticker"]] = r.get("name", "")
    sm = Config().sector_map
    out = []
    for s in P.statement_symbols():
        rt = R.route_for(f"{s}.NS", sm, names.get(f"{s}.NS", ""))
        m = rt.get("sector_method") or rt.get("method")
        if m in LENDER_METHODS:
            out.append((s, m))
    return out


def _qe_iso(s: str) -> str:
    try:
        return datetime.strptime(s.strip(), "%d-%b-%Y").date().isoformat()
    except (ValueError, AttributeError):
        return ""


def _ts(s: str) -> str:
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y"):
        try:
            return datetime.strptime(str(s).strip(), fmt).isoformat()
        except (ValueError, AttributeError):
            continue
    return ""


def pick_filing(rows: list, fy_end: str) -> dict | None:
    """Consolidated where filed, else standalone; the latest submission."""
    hits = [r for r in rows if _qe_iso(r.get("qe_Date", "")) == fy_end and r.get("xbrl")
            and str(r.get("xbrl")).endswith(".xml")]
    if not hits:
        return None
    cons = [r for r in hits if str(r.get("consolidated", "")).lower().startswith("cons")]
    pool = cons or hits
    return max(pool, key=lambda r: (_ts(r.get("creation_Date") or "")
                                    or _ts(r.get("broadcast_Date") or "")))


def _contexts(x: str, kind: str = "instant") -> dict:
    """context id -> date for contexts with no dimension: the instant
    (kind "instant") or the end date of a duration (kind "end")."""
    out = {}
    tag = "instant" if kind == "instant" else "endDate"
    for cid, body in re.findall(r'<xbrli:context id="([^"]+)">(.*?)</xbrli:context>', x, re.S):
        if "xbrldi:" in body or "explicitMember" in body:
            continue
        m = re.search(rf"<xbrli:{tag}>([^<]+)</xbrli:{tag}>", body)
        if m:
            out[cid] = m.group(1).strip()[:10]
    return out


def _fact(x: str, tag: str, ctx_ids: set) -> float | None:
    for m in re.finditer(rf'<[A-Za-z-]+:{tag} contextRef="([^"]+)"[^>]*>([^<]*)<', x):
        if m.group(1) in ctx_ids:
            try:
                return float(m.group(2))
            except ValueError:
                return None
    return None


#: Equity share capital, first match wins: the Ind-AS balance-sheet line,
#: then the labelled paid-up EQUITY capital (banks — their balance-sheet
#: "Capital" line can carry more: ICICIBANK FY26 ₹4,114 cr vs paid-up
#: equity ₹1,432 cr), then the generic capital lines.
CAPITAL_TAGS = (("EquityShareCapital", "instant"),
                ("PaidUpValueOfEquityShareCapital", "end"),
                ("Capital", "instant"), ("ShareCapital", "instant"))
RESERVE_TAGS = ("OtherEquity", "ReservesAndSurplus")


def parse_net_worth(x: str, fy_end: str) -> dict:
    """{capital_cr, reserves_cr, net_worth_cr, tags, note} at the year-end."""
    inst = {c for c, d in _contexts(x, "instant").items() if d == fy_end}
    ends = {c for c, d in _contexts(x, "end").items() if d == fy_end}
    if not inst:
        return {"note": f"no balance-sheet context dated {fy_end} in the XBRL"}
    cap, cap_t = None, ""
    for t, kind in CAPITAL_TAGS:
        v = _fact(x, t, inst if kind == "instant" else ends)
        if v is not None and v > 0:
            cap, cap_t = v, t
            break
    res, res_t = None, ""
    for t in RESERVE_TAGS:
        v = _fact(x, t, inst)
        if v is not None:
            res, res_t = v, t
            break
    if cap is not None and res is not None:
        out = {"capital_cr": round(cap / CR, 2), "reserves_cr": round(res / CR, 2),
               "net_worth_cr": round((cap + res) / CR, 2), "tags": f"{cap_t}+{res_t}"}
        bs = _fact(x, "Capital", inst)
        if bs is not None and cap_t != "Capital" and abs(bs - cap) / cap > 0.005:
            out["note"] = (f"balance-sheet 'Capital' line ₹{bs / CR:,.2f} cr differs from "
                           f"the paid-up equity capital used")
        return out
    for t in TOTALS:
        v = _fact(x, t, inst)
        if v is not None and v > 0:
            return {"net_worth_cr": round(v / CR, 2), "tags": t}
    return {"note": "capital/reserves not found at the year-end in the XBRL"}


def _nature(x: str) -> str:
    m = re.search(r':NatureOfReportStandaloneConsolidated contextRef="[^"]+"[^>]*>([^<]*)<', x)
    return m.group(1).strip() if m else ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    todo = lenders()
    if a.symbols:
        want = {s.upper() for s in a.symbols}
        todo = [t for t in todo if t[0] in want]
    fetched = datetime.now(IST).date().isoformat()
    out = []
    with httpx.Client(headers=UA, timeout=40.0, follow_redirects=True) as c:
        for sym, method in todo:
            url = API.format(sym=sym.replace("&", "%26"))
            try:
                r = c.get(url)
                r.raise_for_status()
                rows = r.json().get("data") or []
            except Exception as e:                             # noqa: BLE001
                rows = []
                err = f"results list failed: {str(e)[:80]}"
            else:
                err = ""
            time.sleep(1.0)
            for fy in FY_ENDS:
                base = {"symbol": sym, "method": method, "fy_end": fy,
                        "list_url": url, "fetched": fetched}
                pick = pick_filing(rows, fy) if rows else None
                if pick is None:
                    out.append({**base, "note": err or f"no XBRL results filing for {fy}"})
                    print(f"{sym:12s} {fy}: {out[-1]['note']}")
                    continue
                xu = pick["xbrl"]
                try:
                    xr = c.get(xu, headers={**UA, "Accept": "application/xml"})
                    xr.raise_for_status()
                    x = xr.text
                except Exception as e:                         # noqa: BLE001
                    out.append({**base, "xbrl_url": xu, "note": f"XBRL fetch failed: {str(e)[:80]}"})
                    print(f"{sym:12s} {fy}: XBRL failed")
                    time.sleep(1.0)
                    continue
                time.sleep(1.0)
                p = parse_net_worth(x, fy)
                filed = (pick.get("broadcast_Date") or pick.get("revised_Date")
                         or pick.get("creation_Date") or "")
                row = {**base, "basis": _nature(x) or pick.get("consolidated", ""),
                       "audited": pick.get("audited", ""),
                       "submission": pick.get("type_Sub", ""),
                       "filed": _ts(filed.title() if filed.isupper() else filed)[:10],
                       "xbrl_url": xu, **p}
                out.append(row)
                print(f"{sym:12s} {fy}: {row.get('basis'):12s} net worth "
                      f"{row.get('net_worth_cr')} cr {row.get('tags', '')} {row.get('note', '')}")
    if a.symbols and Path(a.out).exists():
        # a partial run keeps every other symbol's rows already on file
        done = {r["symbol"] for r in out}
        with open(a.out, newline="", encoding="utf-8") as f:
            keep = [r for r in csv.DictReader(f) if r.get("symbol") not in done]
        out = sorted(keep + out, key=lambda r: (r["symbol"], r["fy_end"] != FY_ENDS[0]))
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in out:
            w.writerow({k: r.get(k, "") for k in COLS})
    ok = sum(1 for r in out if r.get("net_worth_cr"))
    print(f"wrote {len(out)} row(s), {ok} with a net worth, to {a.out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
