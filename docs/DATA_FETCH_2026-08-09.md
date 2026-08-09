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

## Money-path impact
None. No engine, sizing, or exit change. Data, tools and records only.
Nothing here is wired into scanning until the owner orders it.
