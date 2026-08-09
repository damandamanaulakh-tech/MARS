#!/usr/bin/env python3
"""Public NSE/NiftyIndices datasets — no broker keys needed.

FIXED 2026-08-09 (session review of the uploaded kit):
- F&O bhavcopy: NSE switched to UDiFF naming ~2024-07-08. The original kit
  only knew the new name, so Jan-2023 -> mid-2024 (including the whole
  Apr-Jun 2024 election window it advertised) silently failed. Old naming
  is now the fallback.
- Corporate actions: one 16-year API call gets refused/truncated. Now
  fetched in 1-year windows and merged to a single CSV.
- TRI (total-return index): promised by the README, never fetched. Now
  fetched from niftyindices (POST endpoint, chunked by year).
- save() no longer trusts "200 + >200 bytes": an HTML error page saved as
  .csv is a lie. Content is sniffed; junk is rejected and logged.
- PWOI default start is 2012 (the archive really goes back that far) —
  that is the FII derivatives-OI panel the register wants.

NOTE ON WHERE TO RUN: nsearchives.nseindia.com and the nseindia.com APIs
block many cloud/datacenter IPs. Run this on a normal home connection.
(Verified 2026-08-09: the sandbox reached nseindia.com APIs + niftyindices
but nsearchives timed out — your machine will typically reach all of them.)
"""
import csv
import datetime as dt
import json
import os
import time

import requests

S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "*/*",
    "Referer": "https://www.nseindia.com/",
})
UDIFF_SINCE = dt.date(2024, 7, 8)   # first UDiFF-named F&O bhavcopy


def warm():
    try:
        S.get("https://www.nseindia.com", timeout=30)
        time.sleep(1)
    except requests.RequestException:
        pass


def save(url: str, path: str) -> bool:
    """Download url -> path. Rejects HTML/error bodies masquerading as data."""
    try:
        r = S.get(url, timeout=60)
    except requests.RequestException:
        return False
    body = r.content
    if r.status_code != 200 or len(body) < 200:
        return False
    sniff = body[:256].lstrip().lower()
    if sniff.startswith((b"<!doctype", b"<html")) and not path.endswith(".html"):
        return False
    with open(path, "wb") as f:
        f.write(body)
    return True


def daterange(d0: dt.date, d1: dt.date):
    d = d0
    while d <= d1:
        if d.weekday() < 5:
            yield d
        d += dt.timedelta(days=1)


def fo_bhavcopy_urls(d: dt.date) -> list:
    """UDiFF first for modern dates, old naming as fallback (and vice versa)."""
    new = (f"https://nsearchives.nseindia.com/content/fo/"
           f"BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip")
    mon = d.strftime("%b").upper()
    old = (f"https://nsearchives.nseindia.com/content/historical/DERIVATIVES/"
           f"{d:%Y}/{mon}/fo{d:%d}{mon}{d:%Y}bhav.csv.zip")
    return [new, old] if d >= UDIFF_SINCE else [old, new]


def fetch_daily_archives(start: dt.date, end: dt.date,
                         pwoi_start: dt.date = dt.date(2012, 1, 1)):
    misses = {"bhavcopy_delivery": 0, "fo_bhavcopy": 0, "pwoi": 0}
    for d in daterange(start, end):
        s1 = d.strftime("%d%m%Y")
        if not save(f"https://nsearchives.nseindia.com/products/content/"
                    f"sec_bhavdata_full_{s1}.csv",
                    f"data/bhavcopy_delivery/{s1}.csv"):
            misses["bhavcopy_delivery"] += 1
        got_fo = False
        for url in fo_bhavcopy_urls(d):
            ext = ".zip" if url.endswith(".zip") else ".csv"
            if save(url, f"data/fo_bhavcopy/{d:%Y%m%d}{ext}"):
                got_fo = True
                break
        if not got_fo:
            misses["fo_bhavcopy"] += 1
        time.sleep(0.6)
    for d in daterange(pwoi_start, end):
        s1 = d.strftime("%d%m%Y")
        if not save(f"https://nsearchives.nseindia.com/content/nsccl/"
                    f"fao_participant_oi_{s1}.csv", f"data/pwoi/{s1}.csv"):
            misses["pwoi"] += 1
        time.sleep(0.4)
    print("archive misses (holidays are expected misses):", misses)


def fetch_fii_dii_recent():
    """FII/DII net values, LAST 30 DAYS ONLY, via niftytrader's public API.

    Verified 2026-08-09: values match NSE's own snapshot to the paisa, but
    the window is a rolling 30 days — the owner's Colab script wrongly
    called this 'full history'. History beyond 30d still means the NSE
    monthly report archives (manual download).
    """
    import json as _json
    try:
        r = requests.get("https://webapi.niftytrader.in/webapi/Resource/"
                         "fii-dii-activity-data", timeout=60,
                         headers={"User-Agent": "Mozilla/5.0"})
        rows = r.json()["resultData"]["fii_dii_data"]
    except Exception as e:
        print("niftytrader failed:", e)
        return 0
    out = sorted([str(x.get("created_at", ""))[:10],
                  x.get("fii_net_value"), x.get("dii_net_value")]
                 for x in rows)
    with open("data/fii_dii/fii_dii_net_recent.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "fii_net_cr", "dii_net_cr"])
        w.writerows(out)
    print(f"FII/DII recent window: {len(out)} days "
          f"({out[0][0]} -> {out[-1][0]})")
    return len(out)


def fetch_corporate_actions(start_year: int = 2010):
    """Equity corporate actions, one calendar-year window per call."""
    rows, this_year = [], dt.date.today().year
    for y in range(start_year, this_year + 1):
        url = (f"https://www.nseindia.com/api/corporates-corporateActions"
               f"?index=equities&from_date=01-01-{y}&to_date=31-12-{y}")
        try:
            r = S.get(url, timeout=60)
            data = r.json() if r.status_code == 200 else []
        except (requests.RequestException, json.JSONDecodeError):
            data = []
        print(f"corporate actions {y}: {len(data)} rows")
        rows.extend(data)
        time.sleep(1)
    if rows:
        keys = sorted({k for r in rows for k in r})
        with open("data/corporate_actions/corporate_actions.csv", "w",
                  newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
    return len(rows)


def fetch_index_lists():
    for name in ["ind_nifty50list", "ind_nifty200list", "ind_nifty500list"]:
        ok = save(f"https://niftyindices.com/IndexConstituent/{name}.csv",
                  f"data/index_lists/{dt.date.today()}_{name}.csv")
        print(f"index list {name}: {'ok' if ok else 'FAILED'}")


def fetch_tri(index_name: str = "NIFTY 200", start_year: int = 2010):
    """Total-return index series from niftyindices, chunked by year."""
    url = "https://niftyindices.com/Backpage.aspx/getTotalReturnIndexString"
    hdrs = {"Content-Type": "application/json; charset=UTF-8",
            "User-Agent": "Mozilla/5.0", "Referer": "https://niftyindices.com/"}
    rows, this_year = [], dt.date.today().year
    for y in range(start_year, this_year + 1):
        cinfo = json.dumps({
            "name": index_name,
            "startDate": f"01-Jan-{y}",
            "endDate": f"31-Dec-{y}",
            "indexName": index_name.replace(" ", "%20"),
        })
        try:
            r = requests.post(url, headers=hdrs,
                              json={"cinfo": cinfo}, timeout=60)
            data = json.loads(r.json().get("d", "[]"))
        except Exception:
            data = []
        print(f"TRI {index_name} {y}: {len(data)} rows")
        rows.extend(data)
        time.sleep(1)
    if rows:
        keys = sorted({k for r in rows for k in r})
        safe = index_name.replace(" ", "_").lower()
        with open(f"data/index_lists/tri_{safe}.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
    return len(rows)


def run(start=dt.date(2023, 1, 1), end=None):
    end = end or dt.date.today()
    warm()
    for sub in ["fii_dii", "bhavcopy_delivery", "fo_bhavcopy", "pwoi",
                "corporate_actions", "index_lists"]:
        os.makedirs(f"data/{sub}", exist_ok=True)
    save("https://www.nseindia.com/api/fiidiiTradeReact",
         "data/fii_dii/fii_dii_latest.json")
    fetch_fii_dii_recent()
    fetch_corporate_actions()
    fetch_index_lists()
    fetch_tri("NIFTY 200")
    fetch_tri("NIFTY 50")
    fetch_daily_archives(start, end)
    print("Public fetch pass complete. FII/DII history beyond the snapshot: "
          "monthly archives from nseindia.com Reports -> FII/DII activity, "
          "drop into data/fii_dii/.")


if __name__ == "__main__":
    run()
