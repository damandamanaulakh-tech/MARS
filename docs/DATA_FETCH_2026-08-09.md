# Data fetch round — 2026-08-09 (owner: "fix all 5 and commit the kit / Fetch the data as well, no placeholder")

## The Upstox answer first
The owner asked why I couldn't fetch from the Upstox API. Tested live, the
honest answer is: **I can, and did.** Upstox HISTORICAL candles are public —
no access token needed (verified from this cloud session). Only LIVE quotes
need the daily login token. My earlier caution was wrong and is corrected
here and in the kit.

## What was fetched THIS SESSION (all real, all verified)
| Dataset | Span | Where it lives | Verification |
|---|---|---|---|
| Full-universe daily OHLCV (NSE EQ+BE, 2,713 instruments + 5 indices) | 2000 → 2026-08-07 (per listing date) | too big for the git tree — archive delivered to the owner; regenerate anytime with the kit (~20 min) | TCS 2026-06-03 close 2241.70 == committed panel; intake auditor hard gates |
| Nifty 200 daily (the honest benchmark) | 2004-01-01 → 2026-08-07, 7,284 rows | `data/nifty200_daily.csv` | last close 14,231.10 on 2026-08-07 |
| Index OHLC refresh (Nifty 50, Sensex, VIX, Bank Nifty) | extends `index_daily_15y.csv` from 2026-06-05 → 2026-08-07 | `data/index_daily_15y.csv` | overlap days byte-compared before append |
| Corporate actions ledger | 2010 → 2026-08, 34,792 rows (566 splits, 613 bonuses) | `data/corporate_actions_2010_2026.csv` | fetched in 17 yearly windows from NSE's own API |
| Index constituents (N50/N200/N500), dated | snapshot 2026-08-09 | `data/index_constituents/` | PIT record STARTS today — history cannot be backfilled |
| FII/DII net values, recent window | last 30 sessions (2026-06-29 → 2026-08-07) | `data/fii_dii_net_recent.csv` | 2026-08-07 DII net 235.56 == NSE's own snapshot |
| N200 fundamentals snapshots (~4y annual + quarters) | as of 2026-08-09 | `data/fundamentals_n200_2026-08-09.zip` | coverage log inside; free-source ceiling stated |

## What is NOT fetchable from a cloud session (tested, not assumed)
`nsearchives.nseindia.com` (delivery bhavcopy, F&O bhavcopy, PWOI) times
out from here, and niftyindices' TRI POST endpoint serves back the HTML
shell (WAF). **These need a home connection — that is exactly what the
committed kit (`research/tools/fetch_kit/`) does.** FII/DII history for
the 2025-04 → 2026-06 hole exists only in NSE's monthly report archives
(manual download; the API serves one day, niftytrader serves 30).

## The two scripts reviewed this round
**Uploaded fetch kit (files.zip)** — committed FIXED at
`research/tools/fetch_kit/`. Six defects corrected (the six are documented
in the kit README): the fatal one was that Upstox V3 caps one request at
~10 years of daily candles, so the kit as shipped failed on every symbol;
plus the F&O old-naming gap (which covered the whole Apr–Jun 2024 election
window), the phantom Kite cross-check, unwindowed corporate actions,
missing TRI fetch, and an auditor that never actually failed.

**Colab script (AM07_Colab_Data_Fetch.py)** — reviewed, not committed; its
two good ideas were absorbed into the kit (niftytrader FII/DII window,
fundamentals fetcher). Two claims corrected: niftytrader is a rolling
30-day window, NOT "full history" as the script says; and its 250-symbol
fundamentals cap in "universe order" is an alphabetical-bias sample — the
kit version uses the actual N200 list.

## Owner's Colab run — intake verdicts (same day, later)
The owner ran the Colab script from their own Colab; its network reaches
nsearchives where this cloud IP cannot. Uploaded results, audited piece by
piece:
- **PWOI (participant-wise F&O OI): REAL, NEW, COMMITTED** — 641 files
  2024-01-01 -> 2026-08-07 combined into
  `data/fo_participant_oi_2024_2026.csv` (3,205 rows). All files verified
  (totals balance within NSE's own +/-1-contract arithmetic; election-day
  2024-06-04 FII short positioning matches the public record).
- **Fundamentals (yfinance from Colab): REAL, COMMITTED as superset** —
  `data/fundamentals_yf_statements_2026-08-09.zip`, 200/200 coverage.
  yfinance works from Colab's network (it returns empty from this cloud
  sandbox). RELIANCE quarterly net income identical to our direct-endpoint
  CSV — two independent paths agree.
- **Corporate actions: DUPLICATE, skipped** — 34,736 rows vs our committed
  34,792 (same API, ours slightly fuller); nothing new.
- **fii_dii_cash_2023_2026.csv: NOT history — 2 rows, one day** — the
  niftytrader primary failed in Colab too, so the script fell back to
  NSE's single-day snapshot, and its fallback writes BOTH categories into
  the fii columns (row 2 "fii_net 235.56" is actually DII). Values match
  our committed 2026-08-07 exactly (480.24 FII / 235.56 DII). The
  2025-04 -> 2026-06 FII/DII history hole is STILL OPEN (NSE monthly
  reports, manual).
- **Delivery % (sec_bhavdata_full): DELIVERED AND COMMITTED** — owner
  attached the zip to the `chityy-archive` release (172MB, all 1,778
  files, every hash identical to the run's manifest). Compact panel
  committed as `data/delivery_daily_2019_2026.csv.gz` (1,698 days,
  3,477,095 EQ/BE/BZ rows). Intake facts worth keeping: keying by the
  date inside each file (not the filename) both removed the 80 holiday
  echoes and RECOVERED 6 sessions the filename loop got wrong — Muhurat
  2019/2020/2021, the 2024-01-20 and 2024-05-18 special Saturday
  sessions, and a stray 2019-06-27; NSE served 08-Aug-2022 as xlsx
  behind a .csv name (converted); BE/BZ delivery shows NSE's own '-'
  (T2T = all delivery); 0 content conflicts across duplicate dates. The
  raw zip stays release-only.
- Still missing after this run: TRI series — no nifty200_tri.csv /
  nifty50_tri.csv in the run's own manifest, so every TRI chunk failed
  in Colab (cause not captured; the same WAF that blocks this sandbox is
  the likely one). Owner home machine via the kit remains the path.

## FII/DII history hole CLOSED via NSDL (same day, evening)
The 2025-04 -> 2026-06 FII/DII hole did NOT need Colab or the owner's home
machine after all. NSDL's official FPI archive
(fpi.nsdl.co.in/web/Reports/Archive.aspx) answers this cloud IP with no
login — one POST per month returns every trading day's Equity/Stock
Exchange gross buy/sell/net. `scripts/fetch_fpi_nsdl_archive.py` is the
reproducible fetcher; `data/fii_dii_cash.csv` extended 2025-04-01 ->
2026-08-06 (326 rows, FII settlement basis, DII empty as before — NSDL's
DII archive is OTP-login-walled, and no engine path consumes DII).
Two traps found and fixed DURING verification, both now in the script:
1. NSDL dates are REPORT dates — the page footer says each report holds
   the PREVIOUS trading day's activity. Unshifted, correlation with NSE
   provisional FII cash on 29 overlap days was 0.075 (noise); shifted one
   report back it is 0.955 with 23/29 sign agreement (the 6 misses are
   all sub-1,000-Cr near-zero days).
2. The page repeats each date in a derivatives table and a CUMULATIVE
   table — a last-match parser wrote the YTD figure (-2.79 lakh Cr) over
   07-Aug's real +417.92 Cr daily row. First-match-wins fixed it.
Provenance stays visible: NSDL is settlement-based FPI activity; NSE's
provisional numbers differ by a median ~800 Cr/day on the overlap. The
live engine still reads the NSE API first; this CSV is the fallback and
research history. Remaining known holes: 2024-09-09 -> 2024-11-29 (SEBI
era, unchanged) and DII after 2025-03 (empty, unconsumed).

## Money-path impact
None. No engine, sizing, or exit change. Data, tools and records only.
Nothing here is wired into scanning until the owner orders it.
