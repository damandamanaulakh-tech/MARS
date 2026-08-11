# ============================================================
# AM07 COLAB FETCH v2 — the four remaining data gaps
# Paste this WHOLE file into ONE Colab cell and press Run.
# Outputs -> Google Drive: MyDrive/AM07_DATA2/  (+ a manifest)
# When done: upload the files to the chityy-archive release
# assets, same as you did with the delivery zip.
# ============================================================
# WHAT IT FETCHES              -> WHAT IT UNLOCKS IN THE REGISTER
#  1 shareholding/  (quarterly) -> H-01..06 promoter/pledge/FII/MF holding, X-07
#  2 fo_oi_daily.csv (F&O OI)   -> W-021/22 short interest & PCR per stock, FII-09
#  3 block_bulk_deals.csv       -> H-20 block/bulk-deal signal
#  4 insider_pit.csv (SEBI PIT) -> H-25 insider trading flags
#  5 nifty TRI retry            -> honest total-return benchmark (X-02, SR004)
# Every part prints DATA_NEEDED loudly if its source refuses —
# nothing is guessed, partial output is still saved + named.
# ============================================================

USE_DRIVE  = True
SH_SYMBOLS = 200          # shareholding: how many N200 names
FO_START   = "2024-07-08" # F&O bhavcopy (UDiFF era start); earlier needs the repo kit
DEALS_FROM = "2019-01-01" # block/bulk deals history
PIT_FROM   = "2023-01-01" # insider disclosures history

import os, io, json, time, csv, zipfile, hashlib, datetime as dt
import requests, pandas as pd

ROOT = "/content/AM07_DATA2"
if USE_DRIVE:
    try:
        from google.colab import drive; drive.mount('/content/drive')
        ROOT = "/content/drive/MyDrive/AM07_DATA2"
    except Exception as e:
        print("Drive mount skipped:", e)
for d in ["", "shareholding"]:
    os.makedirs(f"{ROOT}/{d}", exist_ok=True)
print("Saving to:", ROOT)

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                  "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"})

def warm():
    try:
        S.get("https://www.nseindia.com", timeout=20)
        S.get("https://www.nseindia.com/market-data/live-equity-market", timeout=20)
    except Exception: pass
    time.sleep(1.2)

def n200():
    try:
        c = pd.read_csv(io.BytesIO(requests.get(
            "https://niftyindices.com/IndexConstituent/ind_nifty200list.csv",
            timeout=40, headers={"User-Agent": "Mozilla/5.0"}).content))
        return list(c["Symbol"].astype(str))
    except Exception as e:
        print("N200 list failed:", e); return []

TODAY = dt.date.today()

# ---------- 1. SHAREHOLDING PATTERN (quarterly, per stock) ----------
def fetch_shareholding():
    warm(); syms = n200()[:SH_SYMBOLS]; ok = fail = 0
    # NSE has changed this endpoint name over time — try each, keep the
    # first that answers with data, and SAY which one worked.
    candidates = [
        "https://www.nseindia.com/api/corporate-share-holdings-master?index=equities&symbol={s}",
        "https://www.nseindia.com/api/corporates-shareholding-master?index=equities&symbol={s}",
        "https://www.nseindia.com/api/shareholding-pattern?index=equities&symbol={s}",
    ]
    working = None
    for s in syms:
        p = f"{ROOT}/shareholding/{s}.json"
        if os.path.exists(p): ok += 1; continue
        got = False
        for url in ([working] if working else candidates):
            try:
                r = S.get(url.format(s=s), timeout=30)
                if r.status_code == 200 and r.text.strip().startswith(("[", "{")) \
                        and len(r.text) > 50:
                    open(p, "w").write(r.text); ok += 1; got = True
                    if not working:
                        working = url; print("shareholding endpoint OK:", url)
                    break
            except Exception:
                pass
        if not got: fail += 1
        time.sleep(0.8)
    print(f"SHAREHOLDING: {ok} saved, {fail} failed"
          + (" -> DATA_NEEDED (endpoint refused; tell Claude which URL the "
             "NSE shareholding page calls in your browser DevTools)" if not working else ""))

# ---------- 2. F&O BHAVCOPY -> one OI row per symbol per day ----------
def fetch_fo_oi():
    out = f"{ROOT}/fo_oi_daily.csv"
    seen = set()
    if os.path.exists(out):
        seen = set(pd.read_csv(out, usecols=["date"])["date"].unique())
    rows = []; d = dt.date.fromisoformat(FO_START); got = 0
    while d <= TODAY:
        ds = d.isoformat()
        if d.weekday() >= 5 or ds in seen:
            d += dt.timedelta(days=1); continue
        u = ("https://nsearchives.nseindia.com/content/fo/"
             f"BhavCopy_NSE_FO_0_0_0_{d.strftime('%Y%m%d')}_F_0000.csv.zip")
        try:
            r = S.get(u, timeout=60)
            if r.status_code == 200 and len(r.content) > 10000:
                z = zipfile.ZipFile(io.BytesIO(r.content))
                df = pd.read_csv(z.open(z.namelist()[0]), low_memory=False)
                # UDiFF columns: TckrSymb, FinInstrmTp (STF/IDF/STO/IDO), OptnTp
                fut = df[df.FinInstrmTp.isin(["STF", "IDF"])].groupby("TckrSymb")["OpnIntrst"].sum()
                ce = df[(df.FinInstrmTp.isin(["STO", "IDO"])) & (df.OptnTp == "CE")]\
                    .groupby("TckrSymb")["OpnIntrst"].sum()
                pe = df[(df.FinInstrmTp.isin(["STO", "IDO"])) & (df.OptnTp == "PE")]\
                    .groupby("TckrSymb")["OpnIntrst"].sum()
                for sym in set(fut.index) | set(ce.index) | set(pe.index):
                    rows.append([ds, sym, int(fut.get(sym, 0)),
                                 int(ce.get(sym, 0)), int(pe.get(sym, 0))])
                got += 1
                if got % 25 == 0: print("fo days:", got)
        except Exception:
            pass
        time.sleep(0.4); d += dt.timedelta(days=1)
    if rows:
        hdr = not os.path.exists(out)
        with open(out, "a", newline="") as f:
            w = csv.writer(f)
            if hdr: w.writerow(["date", "symbol", "fut_oi", "ce_oi", "pe_oi"])
            w.writerows(rows)
    print(f"FO OI: {got} new days -> fo_oi_daily.csv"
          + ("" if got else " -> DATA_NEEDED (archives refused)"))

# ---------- 3. BLOCK + BULK DEALS (monthly windows) ----------
def fetch_deals():
    warm(); allrows = {"block_deals": [], "bulk_deals": []}
    d0 = dt.date.fromisoformat(DEALS_FROM)
    while d0 <= TODAY:
        d1 = min(dt.date(d0.year + (d0.month == 12), (d0.month % 12) + 1, 1)
                 - dt.timedelta(days=1), TODAY)
        for kind in allrows:
            try:
                r = S.get("https://www.nseindia.com/api/historicalOR/bulk-block-short-deals"
                          f"?optionType={kind}&from={d0.strftime('%d-%m-%Y')}"
                          f"&to={d1.strftime('%d-%m-%Y')}", timeout=45)
                j = r.json().get("data", [])
                allrows[kind] += j
            except Exception as e:
                print(f"{kind} {d0.strftime('%Y-%m')} failed: {str(e)[:60]}")
            time.sleep(1.2)
        d0 = d1 + dt.timedelta(days=1)
    for kind, rows in allrows.items():
        if rows:
            pd.DataFrame(rows).to_csv(f"{ROOT}/{kind}.csv", index=False)
            print(f"WROTE {kind}.csv: {len(rows)} rows")
        else:
            print(f"{kind}: DATA_NEEDED (API refused every window)")

# ---------- 4. INSIDER TRADING (SEBI PIT disclosures) ----------
def fetch_insider():
    warm(); rows = []
    d0 = dt.date.fromisoformat(PIT_FROM)
    while d0 <= TODAY:
        d1 = min(d0 + dt.timedelta(days=89), TODAY)
        try:
            r = S.get("https://www.nseindia.com/api/corporates-pit?index=equities"
                      f"&from_date={d0.strftime('%d-%m-%Y')}&to_date={d1.strftime('%d-%m-%Y')}",
                      timeout=45)
            j = r.json().get("data", [])
            rows += j; print(f"PIT {d0} -> {d1}: {len(j)}")
        except Exception as e:
            print(f"PIT {d0} failed: {str(e)[:60]}")
        time.sleep(1.5); d0 = d1 + dt.timedelta(days=1)
    if rows:
        pd.DataFrame(rows).to_csv(f"{ROOT}/insider_pit.csv", index=False)
        print("WROTE insider_pit.csv:", len(rows))
    else:
        print("insider: DATA_NEEDED (API refused)")

# ---------- 5. NIFTY TRI RETRY (hardened session) ----------
def fetch_tri():
    T = requests.Session()
    T.headers.update(S.headers)
    try:
        T.get("https://www.niftyindices.com/reports/historical-data", timeout=30)
    except Exception: pass
    time.sleep(2)
    for name, out in [("Nifty 200", "nifty200_tri.csv"), ("Nifty 50", "nifty50_tri.csv")]:
        rows = []
        for y in range(2010, TODAY.year + 1, 4):
            d0, d1 = f"01-Jan-{y}", f"31-Dec-{min(y+3, TODAY.year)}"
            cinfo = ("{'name':'%s','startDate':'%s','endDate':'%s','indexName':'%s'}"
                     % (name, d0, d1, name))
            try:
                r = T.post("https://www.niftyindices.com/Backpage.aspx/getTotalReturnIndexString",
                           json={"cinfo": cinfo}, timeout=60,
                           headers={"Content-Type": "application/json; charset=UTF-8",
                                    "X-Requested-With": "XMLHttpRequest",
                                    "Referer": "https://www.niftyindices.com/reports/historical-data",
                                    "Origin": "https://www.niftyindices.com"})
                data = json.loads(r.json()["d"])
                rows += [(x["Date"], x.get("TotalReturnsIndex")
                          or x.get("TOTAL_RETURN_INDEX")) for x in data]
                print(name, y, len(data)); time.sleep(2)
            except Exception as e:
                print(name, y, "FAILED:", str(e)[:70])
        if rows:
            pd.DataFrame(rows, columns=["date", "tri"]).dropna()\
              .to_csv(f"{ROOT}/{out}", index=False)
            print("WROTE", out, len(rows))
        else:
            print(name, "-> DATA_NEEDED (WAF blocked Colab too)")

# ---------- MANIFEST ----------
def manifest():
    rep = [["file", "bytes", "sha256_16"]]
    for base, _, files in os.walk(ROOT):
        for fn in files:
            p = os.path.join(base, fn)
            try:
                h = hashlib.sha256(open(p, "rb").read()).hexdigest()[:16]
                rep.append([p.replace(ROOT + "/", ""), os.path.getsize(p), h])
            except Exception: pass
    with open(f"{ROOT}/MANIFEST_v2.csv", "w", newline="") as f:
        csv.writer(f).writerows(rep)
    print(f"MANIFEST: {len(rep)-1} files")

fetch_shareholding()
fetch_fo_oi()
fetch_deals()
fetch_insider()
fetch_tri()
manifest()
print("\nALL DONE ->", ROOT, "\nZip the folder and attach to the chityy-archive release assets.")
