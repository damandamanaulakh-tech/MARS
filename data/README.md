# data/ — committed datasets (survive session/container death)

| File | What | Source | Rows |
|---|---|---|---|
| `upstox_nse_equities.csv` | Full NSE cash-equity universe: EQ-series only (excludes BE/BZ trade-to-trade, SM/ST SME). Columns: `ticker` (yfinance `.NS`), `trading_symbol`, `name`, `isin`, `instrument_key` (Upstox), `mis_allowed` (1 = in Upstox NSE_MIS intraday list). | Upstox instrument master (NSE.json.gz + NSE_MIS.json.gz), user-supplied 2026-07-09 | 2,376 (1,483 MIS) |
| `fii_dii_cash.csv` | Daily FII & DII net cash-market flows (₹ crore). Columns: `date`, `fii_cash_net`, `dii_cash_net`. Real data, sample coverage 2024-01 → 2024-09. Live feed 403s from cloud — extend by uploading fresh rows. | User-supplied sample_fii_dii_cash.csv | ~170 |

Usage: `trading.universes.load_universe_from_csv("data/upstox_nse_equities.csv")`
loads a capped (default 200) scan universe from any slice of this master.
This file is a SOURCE catalogue — never scan all 2,376 on Render 512 MB.
