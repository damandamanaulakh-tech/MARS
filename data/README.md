# data/ — committed datasets (survive session/container death)

| File | What | Source | Rows |
|---|---|---|---|
| `upstox_nse_equities.csv` | Full NSE cash-equity universe: EQ-series only (excludes BE/BZ trade-to-trade, SM/ST SME). Columns: `ticker` (yfinance `.NS`), `trading_symbol`, `name`, `isin`, `instrument_key` (Upstox), `mis_allowed` (1 = in Upstox NSE_MIS intraday list). | Upstox instrument master (NSE.json.gz + NSE_MIS.json.gz), user-supplied 2026-07-09 | 2,376 (1,483 MIS) |
| `fii_dii_cash.csv` | Daily FII & DII net cash-market flows (₹ crore). Columns: `date`, `fii_cash_net`, `dii_cash_net`. Real data, sample coverage 2024-01 → 2024-09. Live feed 403s from cloud — extend by uploading fresh rows. | User-supplied sample_fii_dii_cash.csv | ~170 |

Usage: `trading.universes.load_universe_from_csv("data/upstox_nse_equities.csv")`
loads a capped (default 200) scan universe from any slice of this master.
This file is a SOURCE catalogue — never scan all 2,376 on Render 512 MB.

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
- `research/chityy_archive/` — Chityy parameter registry (JSON+CSV, 22k
  params), signal correlation/forward-return studies, top combo & pre-fall
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
