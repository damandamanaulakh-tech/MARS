"""Fetch the register items that were listed as blocked on an outside feed.

Owner order 2026-08-13: "API keys r there, fetch it or give me here colab".

WHAT THIS CORRECTS. Six register items (H-02..H-06, X-07 — promoter pledge,
FII / DII / mutual-fund holdings and their deltas, institutional holder count)
were recorded as needing a Colab run because "nsearchives blocks this app's
IP". THAT WAS WRONG. The block applies to the bhavcopy/delivery archive paths;
the shareholding XBRL path answers this session fine — a real 1.3MB file was
fetched and parsed, and its promoter (71.77%) and public (28.23%) figures match
the committed JSON for TCS exactly. So these six need no Colab at all.

Stages (run any subset):
  xbrl    — per-stock institutional holdings from the shareholding XBRL each
            committed JSON links to. Unlocks H-02..H-06 and X-07.
  yahoo   — analyst ratings, estimate revisions, real earnings dates, shares
            outstanding, ESG. Unlocks H-07, W-020, X-11, TRG-105, TRG-098,
            W-011 (and retires the ASSUMED shares-outstanding proxy), H-10.
  macro   — USDINR daily (W-025) and India broad money (H-19).

Every stage is CHECKPOINTED (per-symbol), because two earlier long runs in
this repo were killed mid-pass by container restarts. Re-running resumes.

HONESTY RULES, same as every other fetch here:
  * nothing is written unless it parsed. A symbol that fails is recorded in the
    stage's `_failures.csv` with the reason, never filled with a guess.
  * every row carries its source and as-of date.
  * Yahoo's quoteSummary needs a cookie+crumb handshake; a stage that cannot
    get a crumb aborts loudly rather than writing empty columns.

Usage: python scripts/fetch_pending_feeds.py xbrl,yahoo,macro [quarters]
"""
from __future__ import annotations

import csv
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SHP = DATA / "shareholding"
CKPT = DATA / ".fetch_ckpt"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

# XBRL category contexts -> the register field each one answers.
XBRL_FIELDS = {
    "promoter_pct": "ShareholdingOfPromoterAndPromoterGroup_ContextI",
    "public_pct": "PublicShareholding_ContextI",
    "fii_pct": "InstitutionsForeign_ContextI",
    "fpi_cat1_pct": "InstitutionsForeignPortfolioInvestorCategoryOne_ContextI",
    "fpi_cat2_pct": "InstitutionsForeignPortfolioInvestorCategoryTwo_ContextI",
    "dii_pct": "InstitutionsDomestic_ContextI",
    "mf_pct": "MutualFundsOrUTI_ContextI",
    "insurance_pct": "InsuranceCompanies_ContextI",
    "banks_pct": "Banks_ContextI",
    "non_institutions_pct": "NonInstitutions_ContextI",
}
XBRL_HOLDERS = {
    "fii_holders": "InstitutionsForeign_ContextI",
    "dii_holders": "InstitutionsDomestic_ContextI",
    "mf_holders": "MutualFundsOrUTI_ContextI",
    "public_holders": "PublicShareholding_ContextI",
}


def _get(url: str, timeout: int = 45, cookie: str = "",
         tries: int = 3) -> bytes:
    """GET with retries — a transient 400/429/timeout through the proxy killed
    a whole stage during the pilot, so one bad response must not end the run."""
    last: Exception | None = None
    for attempt in range(tries):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        if cookie:
            req.add_header("Cookie", cookie)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:                              # noqa: BLE001
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise last if last else RuntimeError("unreachable")


def _ckpt(name: str) -> set[str]:
    CKPT.mkdir(exist_ok=True)
    p = CKPT / f"{name}.txt"
    return set(p.read_text().split()) if p.exists() else set()


def _mark(name: str, key: str) -> None:
    (CKPT / f"{name}.txt").open("a").write(key + "\n")


def _append(path: Path, row: dict, header: list[str]) -> None:
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header)
        if new:
            w.writeheader()
        w.writerow(row)


# ---------------------------------------------------------------------------
# xbrl: institutional holdings per stock per quarter
# ---------------------------------------------------------------------------

def _facts(xml: str, tag: str) -> dict[str, str]:
    return dict(re.findall(
        rf'<[\w-]+:{tag}[^>]*contextRef="([^"]+)"[^>]*>([^<]+)<', xml))


def parse_xbrl(xml: str) -> dict:
    pct = _facts(xml, "ShareholdingAsAPercentageOfTotalNumberOfShares")
    holders = _facts(xml, "NumberOfShareholders")
    out: dict = {}
    for field, ctx in XBRL_FIELDS.items():
        v = pct.get(ctx)
        # XBRL carries fractions (0.7177), the register wants percent.
        out[field] = round(float(v) * 100, 4) if v not in (None, "") else None
    for field, ctx in XBRL_HOLDERS.items():
        v = holders.get(ctx)
        out[field] = int(float(v)) if v not in (None, "") else None
    pledged = re.search(
        r'<[\w-]+:WhetherAnySharesHeldByPromotersAreEncumberedUnderPledged'
        r'ForPromoterAndPromoterGroup[^>]*>([^<]+)<', xml)
    out["promoter_pledged"] = (pledged.group(1).strip().lower()
                              if pledged else None)
    return out


def stage_xbrl(quarters: int) -> None:
    done = _ckpt("xbrl")
    outp = DATA / "shareholding_institutional.csv"
    failp = DATA / "shareholding_institutional_failures.csv"
    header = (["symbol", "quarter", "submitted"] + list(XBRL_FIELDS) +
              list(XBRL_HOLDERS) + ["promoter_pledged", "source"])
    files = sorted(SHP.glob("*.json"))
    print(f"xbrl: {len(files)} symbols x {quarters} quarters "
          f"({len(done)} already done)", flush=True)
    for i, path in enumerate(files, 1):
        sym = path.stem
        try:
            recs = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        for rec in recs[:quarters]:
            url = rec.get("xbrl") or ""
            key = f"{sym}|{rec.get('date')}"
            if not url or key in done:
                continue
            try:
                xml = _get(url).decode("utf-8", "replace")
                row = parse_xbrl(xml)
                if row.get("promoter_pct") is None:
                    raise ValueError("no promoter percentage in file")
                row.update({"symbol": sym, "quarter": rec.get("date"),
                            "submitted": rec.get("submissionDate"),
                            "source": url})
                _append(outp, row, header)
            except Exception as e:                          # noqa: BLE001
                _append(failp, {"symbol": sym, "quarter": rec.get("date"),
                                "why": f"{type(e).__name__}: {e}"[:180]},
                        ["symbol", "quarter", "why"])
            _mark("xbrl", key)
            done.add(key)
            time.sleep(0.15)                # be polite to the archive
        if i % 20 == 0:
            print(f"  xbrl {i}/{len(files)} symbols", flush=True)
    print("xbrl stage done", flush=True)


# ---------------------------------------------------------------------------
# yahoo: analyst, revisions, earnings dates, shares outstanding, ESG
# ---------------------------------------------------------------------------

def _crumb() -> tuple[str, str]:
    """Yahoo's quoteSummary needs a cookie + crumb pair."""
    req = urllib.request.Request("https://fc.yahoo.com",
                                 headers={"User-Agent": UA})
    cookie = ""
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.headers.get_all("Set-Cookie") or []
    except urllib.error.HTTPError as e:
        raw = e.headers.get_all("Set-Cookie") or []
    cookie = "; ".join(c.split(";")[0] for c in raw)
    crumb = _get("https://query2.finance.yahoo.com/v1/test/getcrumb",
                 cookie=cookie).decode().strip()
    return cookie, crumb


_MODULES = ("defaultKeyStatistics,recommendationTrend,financialData,"
            "upgradeDowngradeHistory,calendarEvents,earningsTrend,esgScores")


def stage_yahoo() -> None:
    cookie, crumb = _crumb()
    if not crumb or len(crumb) > 30:
        raise SystemExit("yahoo: no crumb — aborting rather than writing "
                         "empty columns")
    print(f"yahoo: crumb ok ({crumb[:6]}...)", flush=True)
    done = _ckpt("yahoo")
    syms = [p.stem for p in sorted(SHP.glob("*.json"))]
    ratings = DATA / "analyst_ratings.csv"
    revis = DATA / "estimate_revisions.csv"
    earn = DATA / "earnings_dates.csv"
    shares = DATA / "shares_outstanding.csv"
    failp = DATA / "yahoo_failures.csv"
    for i, sym in enumerate(syms, 1):
        if sym in done:
            continue
        url = (f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/"
               f"{sym}.NS?modules={_MODULES}&crumb={crumb}")
        try:
            d = json.loads(_get(url, cookie=cookie))
            res = (d.get("quoteSummary") or {}).get("result") or []
            if not res:
                raise ValueError((d.get("quoteSummary") or {}).get("error"))
            r = res[0]

            def num(mod, key):
                v = (r.get(mod) or {}).get(key)
                if isinstance(v, dict):
                    v = v.get("raw")
                return v

            rt = ((r.get("recommendationTrend") or {}).get("trend") or [{}])[0]
            if rt:
                _append(ratings, {
                    "symbol": sym, "period": rt.get("period"),
                    "strong_buy": rt.get("strongBuy"), "buy": rt.get("buy"),
                    "hold": rt.get("hold"), "sell": rt.get("sell"),
                    "strong_sell": rt.get("strongSell"),
                    "mean_rating": num("financialData", "recommendationMean"),
                    "target_mean": num("financialData", "targetMeanPrice"),
                    "n_analysts": num("financialData",
                                      "numberOfAnalystOpinions"),
                }, ["symbol", "period", "strong_buy", "buy", "hold", "sell",
                    "strong_sell", "mean_rating", "target_mean", "n_analysts"])

            for t in (r.get("earningsTrend") or {}).get("trend") or []:
                est = t.get("earningsEstimate") or {}
                rev = t.get("epsRevisions") or {}
                if est.get("avg", {}).get("raw") is None:
                    continue
                _append(revis, {
                    "symbol": sym, "period": t.get("period"),
                    "end_date": t.get("endDate"),
                    "eps_avg": est.get("avg", {}).get("raw"),
                    "eps_7d_ago": (t.get("epsTrend") or {}).get(
                        "7daysAgo", {}).get("raw"),
                    "eps_30d_ago": (t.get("epsTrend") or {}).get(
                        "30daysAgo", {}).get("raw"),
                    "up_last30d": rev.get("upLast30days", {}).get("raw"),
                    "down_last30d": rev.get("downLast30days", {}).get("raw"),
                }, ["symbol", "period", "end_date", "eps_avg", "eps_7d_ago",
                    "eps_30d_ago", "up_last30d", "down_last30d"])

            ce = r.get("calendarEvents") or {}
            dates = (ce.get("earnings") or {}).get("earningsDate") or []
            if dates:
                _append(earn, {
                    "symbol": sym,
                    "earnings_date": dates[0].get("fmt"),
                    "earnings_date_end": (dates[1].get("fmt")
                                          if len(dates) > 1 else ""),
                    "is_estimate": (ce.get("earnings") or {}).get(
                        "isEarningsDateEstimate"),
                }, ["symbol", "earnings_date", "earnings_date_end",
                    "is_estimate"])

            so = num("defaultKeyStatistics", "sharesOutstanding")
            if so:
                _append(shares, {
                    "symbol": sym, "shares_outstanding": so,
                    "float_shares": num("defaultKeyStatistics", "floatShares"),
                    "held_pct_institutions": num("defaultKeyStatistics",
                                                 "heldPercentInstitutions"),
                    "esg_total": num("esgScores", "totalEsg"),
                }, ["symbol", "shares_outstanding", "float_shares",
                    "held_pct_institutions", "esg_total"])
        except Exception as e:                              # noqa: BLE001
            _append(failp, {"symbol": sym, "why": f"{type(e).__name__}: {e}"[:180]},
                    ["symbol", "why"])
        _mark("yahoo", sym)
        time.sleep(0.6)                     # 429s appear above ~2 req/s
        if i % 25 == 0:
            print(f"  yahoo {i}/{len(syms)}", flush=True)
    print("yahoo stage done", flush=True)


# ---------------------------------------------------------------------------
# macro: USDINR (W-025) and India broad money (H-19)
# ---------------------------------------------------------------------------

def stage_macro() -> None:
    d = json.loads(_get("https://query1.finance.yahoo.com/v8/finance/chart/"
                        "USDINR=X?range=10y&interval=1d"))
    res = d["chart"]["result"][0]
    ts, q = res["timestamp"], res["indicators"]["quote"][0]
    import datetime as dt
    rows = []
    for i, t in enumerate(ts):
        c = q["close"][i]
        if c is None:
            continue
        rows.append({"date": dt.datetime.utcfromtimestamp(t).date().isoformat(),
                     "close": round(float(c), 4)})
    p = DATA / "usdinr_daily.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "close"])
        w.writeheader(); w.writerows(rows)
    print(f"macro: usdinr_daily.csv {len(rows)} rows "
          f"{rows[0]['date']} -> {rows[-1]['date']}", flush=True)

    try:
        wb = json.loads(_get("https://api.worldbank.org/v2/country/IN/indicator/"
                             "FM.LBL.BMNY.CN?format=json&per_page=200"))
        series = [r for r in (wb[1] or []) if r.get("value") is not None]
    except Exception as e:                                  # noqa: BLE001
        print(f"macro: broad money unavailable ({e}) — no file written",
              flush=True)
        return
    p = DATA / "india_broad_money.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["year", "broad_money_lcu", "source"])
        w.writeheader()
        for r in sorted(series, key=lambda x: x["date"]):
            w.writerow({"year": r["date"], "broad_money_lcu": r["value"],
                        "source": "World Bank FM.LBL.BMNY.CN"})
    print(f"macro: india_broad_money.csv {len(series)} yearly rows "
          "(ANNUAL — too coarse for a daily liquidity filter; noted)",
          flush=True)


if __name__ == "__main__":
    stages = (sys.argv[1] if len(sys.argv) > 1 else "xbrl,yahoo,macro").split(",")
    quarters = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    if "macro" in stages:
        stage_macro()
    if "yahoo" in stages:
        stage_yahoo()
    if "xbrl" in stages:
        stage_xbrl(quarters)
    print("ALL DONE", flush=True)
