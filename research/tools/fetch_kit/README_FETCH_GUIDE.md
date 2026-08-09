# AM07 DATA FETCH KIT — fills the register's remaining data gaps
Uploaded 2026-08-09 · reviewed, 6 defects fixed, committed same day ·
paper-research only · fail-closed intake

## What changed in the review (vs the uploaded original)
1. **The primary fetch was broken as shipped**: Upstox V3 serves at most
   ~10 years of daily candles per request (verified live — 16y+ returns
   UDAPI1148). The original asked for the whole range in one call, so
   every symbol would have failed. Fixed with 10-year chunking.
2. **No token needed for history**: Upstox historical candles are public
   (verified live from a cloud sandbox). A token in `.env` is still used
   if present. Only LIVE quotes need the daily login token.
3. F&O bhavcopy old-vs-UDiFF naming fallback (the original missed
   everything before ~2024-07-08, including the Apr-Jun 2024 election
   window it advertised).
4. The Kite-vs-Upstox cross-check comparison now actually exists
   (verify_intake.py, CROSS_CHECK_REPORT.csv).
5. Corporate actions fetched in 1-year windows (one 16-year call gets
   refused); TRI series fetch implemented (was promised, absent).
6. verify_intake is genuinely fail-closed now (exit 1 on hard-gate
   violations), computes coverage, checks date order, reads .csv.gz.

## SECURITY RULE (unchanged, read first)
Never paste API keys/secrets into any chat, file share, or screenshot.
Keys live ONLY in a local `.env` file. For OHLCV history no key is needed
at all.

## WHERE TO RUN WHAT (verified 2026-08-09)
| Source | From a cloud session | From your home machine |
|---|---|---|
| Upstox historical candles | ✅ works, tokenless | ✅ |
| nseindia.com APIs (FII/DII snapshot, corporate actions) | ✅ worked from this sandbox | ✅ |
| niftyindices.com (constituents, TRI) | ✅ | ✅ |
| nsearchives.nseindia.com (delivery bhavcopy, F&O bhavcopy, PWOI) | ❌ times out (blocked) | ✅ usually |

So: the OHLCV/CA/TRI/constituents part can run anywhere; the
archives part (delivery %, F&O, PWOI) must run on a normal home
connection — that is the piece only the owner can collect.

## RUN ORDER
    pip install requests pandas python-dotenv kiteconnect
    python fetch_upstox_ohlcv.py            # full NSE + indices, 2000->today, resumable
    python fetch_nse_public.py              # CA, TRI, constituents, FII/DII, archives
    python fetch_fundamentals.py            # N200 key statement lines (direct Yahoo API)
    python fetch_kite_ohlcv.py --sample 25  # optional cross-check (needs Kite keys + add-on)
    python clean_intake.py                  # dedup + quarantine source glitches -> _rejected.csv
    python verify_intake.py                 # fail-closed audit -> DATA_INTAKE_REPORT.csv (+ CROSS_CHECK_REPORT.csv)

Measured on the 2026-08-09 full run: the raw fetch carries 1 identical
duplicate row in ~45% of symbols (API chunk-boundary echo — dropped by
clean_intake and also deduped in the fetcher now) and 84 OHLC-invariant
rows in 29 files out of ~5M rows (source glitches — QUARANTINED to
_rejected.csv with full disclosure, never repaired silently).

Then send DATA_INTAKE_REPORT.csv (small) back to the audit. Nothing is
trusted until the hard gates pass: OHLC invariant = 0, duplicate dates =
0, non-monotonic dates = 0; |return|>20% days and low coverage are
reported for human judgement.

## WHAT EACH OUTPUT FILLS
| Output | Register items unblocked |
|---|---|
| ohlcv per symbol (2000->now) | 79 OHLCV triggers, clean backtest base |
| india_vix / index candles | VIX-01/02, WM023/24, index staleness |
| fii_dii (snapshot + manual monthly archives) | FII-01..03, governor OOS — NOTE: history is a manual download, the API only serves the latest day |
| bhavcopy_delivery/*.csv | T12W_0060 delivery trigger, volume/delivery layer |
| corporate_actions.csv | CA ledger; retires the ±clip band-aid |
| index_lists (dated) + TRI | PIT universe going FORWARD (history cannot be backfilled from here — Wayback snapshots remain the only PIT history), honest benchmark |
| fo_bhavcopy + pwoi/*.csv | FII-04..07, T12W_0100..0102, election-window validation |
