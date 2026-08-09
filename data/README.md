# data/ — committed datasets (survive session/container death)

| File | What | Source | Rows |
|---|---|---|---|
| `upstox_nse_equities.csv` | Full NSE cash-equity universe: EQ-series only (excludes BE/BZ trade-to-trade, SM/ST SME). Columns: `ticker` (yfinance `.NS`), `trading_symbol`, `name`, `isin`, `instrument_key` (Upstox), `mis_allowed` (1 = in Upstox NSE_MIS intraday list). | Upstox instrument master (NSE.json.gz + NSE_MIS.json.gz), auto-refreshed DAILY by the 08:50 job (repo copy = last committed snapshot; 2,428 rows at 2026-08-09) | ~2,400+ |
| `fii_dii_cash.csv` | Daily FII & DII net cash-market flows (₹ crore). Columns: `date`, `fii_cash_net`, `dii_cash_net`. Real data, coverage 2024-01 → 2025-03-29 (dii blank from 2024-12 — SEBI FII disclosure archive has no DII). Live feed 403s from cloud; staleness guard returns neutral past 45 days. | Hand-fed rows + scripts/build_fii_cash_from_disclosures.py | 265 |

Usage: `trading.universes.load_universe_from_csv("data/upstox_nse_equities.csv")`
loads a capped (default 200) scan universe from any slice of this master.
This file is a SOURCE catalogue — never scan the whole list at once on Render 512 MB.

## Chityy archive — GitHub Release `chityy-archive` (published 2026-07-27)

https://github.com/damandamanaulakh-tech/AM07/releases/tag/chityy-archive

Owner-uploaded archive (~1.5 GB total). Two storage areas, one important
difference for Claude sessions:

**A. Release ASSETS (74 files, 1.32 GB) — fetchable from sessions** via
`https://github.com/damandamanaulakh-tech/AM07/releases/download/chityy-archive/<name>`.
Monthly zips of TRANSACTION-LEVEL FII disclosure records (registrar format:
one row per FII trade — fund id, scrip, ISIN, date, rate, quantity, value,
buy/sell type). E.g. `2021_03.zip` -> `Mar_2021.csv`, 161k rows. This is the
raw material for per-stock FII behaviour research (the Chityy FII
sell-cluster family). Month coverage: 2009 (2), 2014 (4), 2017 (1),
2018 (11), 2019 (12), 2020 (12), 2021 (6), 2022 (7), 2023 (12),
2024 (Jan/Feb/Mar/Dec), 2025 (Jan/Feb/Mar). NOT a current feed — newest
month is Mar 2025; `data/fii_dii_cash.csv` staleness (F6) is unchanged.

**B. Release DESCRIPTION links (31 files) — browser-only** (they live on
`github.com/user-attachments/...`, which the session proxy blocks).
Chityy engine packs: volume/delivery/cash batch checks v0_1, UNIQUE_TRIGGER
_MINER v0_3, IFR_Expansion_Check v0_4, IFR_FII_Cash_Stack_Test v0_6 (x2),
PRE RISE_FALL_TRIGGER_LAYER CSV+CODE v0_2, MICRO_SPLIT_CSV_PACK v0_6,
fii_volume_derivatives_review v0_3, volume_derivatives_review v0_3,
FII DII Nifty.zip, FO Gold COmm.zip, BhavCopy_NSE_FO 2026-06-08, aqu.zip,
5 years for Stocks.zip, 15 stock 10 years trades.zip, Back Test Claude.zip,
nse_all_stock_data.csv, and 12 xlsx of NSE stock data (2011-2016 yearly,
NSE 2017/2018/2019/2020-24, January_Equity_2022, April 2023).
For a session to read any of these, the owner must re-attach that file in
the ASSETS box (edit release -> drag into "Attach binaries" -> Update).

### Rescued from the release into the repo (2026-07-27, second pass)
Owner re-attached 5 key zips as ASSETS (AM.07.Parameter.Files, aqu,
Back.Test.Claude, FII.DII.Nifty, FII). Rescued into version control:
- `research/chityy_archive/` — Chityy parameter registry (JSON+CSV;
  1,500 + 1,200 params committed — the "22k" figure from old notes counted
  spreadsheet cells, not parameters), signal correlation/forward-return studies, top combo & pre-fall
  triggers, Q1 FII 20d stats, industry coverage, n200 live report,
  early-warning + data-format docs, and `master_30000_stock_list.csv`
  (the file KEEP_DELETE.md marked absorbed but never landed).
- `data/chityy/master_extended_chityy.csv` — REBUILT clean from
  `master_extended.xlsx` (31 rows x 59 cols); the old copy was corrupt
  (zero line breaks).
- `data/fii_dii_daily_2014_2023.csv` — daily FII/DII net + Nifty close,
  2014-01 -> 2023-07 (2,339 rows), exported from the owner's historical
  study workbook. RESEARCH data — the live-regime feed (fii_dii_cash.csv,
  newest 2024-09) is still stale; F6 stays open for a current source.
NOT rescued (not in any asset): the `sourceborn_chityy` .py code — none of
the 5 asset zips contains code. Likely candidate holding it:
`PRE RISE_FALL_TRIGGER_LAYER_CSV_CODE_v0_2.zip` (description-link,
browser-only). Owner can asset-attach it if the code should be preserved.
`Back.Test.Claude.zip` = 191 tickers 2014->2026-07-17, same panel as
`data/history_upstox_export_2026-07-19.zip` — kept in the release only.

`india_10y_bond_yield.csv` — India 10-year government bond yield, daily,
2016-01-01 -> 2026-06-08 (2,550 rows, newest first, US date format
MM/DD/YYYY, from the owner's AM.07.Parameter.Files pack; committed verbatim
2026-07-27, owner: "BOND u can go"). Macro-research input (rate regime vs
equity momentum) — not wired into any live signal.

### Release update (2026-07-27 evening): 106 assets, code question CLOSED
Owner moved nearly all description-link files into ASSETS (79 -> 106) —
sessions can now fetch the batch-check packs, trigger miner, IFR packs,
NSE yearly xlsx (1991-2019), the big masked FII data-loads (Jan-2022,
Apr-2023) and more directly. PRE RISE_FALL_TRIGGER_LAYER_CSV_CODE_v0_2
contained NO .py files — the "code" is 8 CSV design maps (trigger params
T12W_*, score gates, clusters, pyramid insert map, paper running map,
reverse-backtest design, proof-gap ledger v0_2, data-extraction request),
now committed under `research/chityy_archive/trigger_layer_v0_2/`.
CONCLUSION: the sourceborn_chityy .py code was never saved in any upload;
the design maps are the surviving record. F6 note: still no CURRENT
FII/DII feed data in the release (newest remains Mar 2025 monthly /
Jul 2023 daily) — F6 stays open for a current source.

### Uploads batch 2026-08-09 (10 files, session review — full verdicts in docs/UPLOADS_REVIEW_2026-08-09.md)
- `data/fo_mwpl_2026-06.csv` — NSE Market-Wide Position Limits, June 2026,
  211 F&O symbols (symbol, MWPL contracts). REAL published limits,
  extracted from the owner's AM07_FO_OI_Signals_Report upload.
- `data/fo_oi_top_contracts_2026-06-08.csv` — top-25 stock-futures
  contracts by open interest for bhavcopy date 2026-06-08 (OI, change,
  volume, buildup/unwinding label). One-day slice, reference only.
- `research/chityy_archive/reverse_pattern_v0_4/` — the owner's Chityy
  Reverse Pattern Parameter Engine v0.4, all 14 sheets as CSV (1,200-param
  locked bank, 42 adopted params, 16-pattern reverse library, scoring
  rules, source-hash audit, P0 data gaps). Sample-data sheet verified
  against the committed Upstox panel (TCS/RELIANCE exact match).
- `research/external/ASHSTOCKS_Flaw_Register_2026-08-09.csv` — 15-flaw
  self-audit of the separate ASH-STOCKS build; kept because its F-003/
  F-005 flags settled two files in this same batch.
- REJECTED from the same batch: FPI category positions (synthetic —
  mechanically 30/20/10% of MWPL, confirmed by arithmetic); India-VIX
  history CSV (real but a subset of index_daily_15y.csv); three backtest
  workbooks recorded as narrative, not evidence (no ledgers/panels).

### Data fetch round 2026-08-09 (owner: "fix all 5 and commit the kit / Fetch the data as well")
Fetched THIS SESSION via the fixed kit (research/tools/fetch_kit/) — Upstox
historical candles proved PUBLIC (no token; verified live). Full detail:
docs/DATA_FETCH_2026-08-09.md.
- `data/nifty200_daily.csv` — Nifty 200 daily OHLC 2004-01-01 -> 2026-08-07
  (7,284 rows) — the honest benchmark series, offline at last.
- `data/index_daily_15y.csv` — EXTENDED 2026-06-05 -> 2026-08-07 (44 rows,
  all four indices; 80 overlap values byte-checked, 0 disagreements).
- `data/corporate_actions_2010_2026.csv` — NSE corporate actions 2010 ->
  2026-08, 34,792 rows (566 splits, 613 bonuses), 17 yearly API windows.
- `data/index_constituents/2026-08-09_*.csv` — N50/N200/N500 dated
  snapshots. The forward PIT record STARTS here; add a dated file per
  refresh, never overwrite.
- `data/fii_dii_net_recent.csv` — FII/DII net, last 30 sessions
  (2026-06-29 -> 2026-08-07), niftytrader API, cross-checked vs NSE
  snapshot. The 2025-04 -> 2026-06 history hole remains (monthly NSE
  reports, owner machine).
- `data/fundamentals_n200_2026-08-09.csv` — 11,377 statement datapoints,
  200/200 N200 symbols (annual ~4y + quarters), direct Yahoo timeseries
  (yfinance's wrapper returns empty here — REL FY23 revenue and TCS FY24
  profit verified vs published figures).
- FULL-UNIVERSE OHLCV (2,715 instruments + 5 indices, 2000 -> 2026-08-07,
  ~103MB gz): fetched, cleaned, auditor hard gates PASSED (0 invalid, 0
  dup) — intake report + quarantined rows committed under
  research/tools/fetch_kit/. The archive itself stays OUT of the git tree
  (Render clones the repo); delivered to the owner as a zip — attach to
  the `chityy-archive` release to make it session-fetchable forever.
