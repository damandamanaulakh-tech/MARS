# 07 — DATA FORMATS REQUIRED FOR VALIDATION

> One CSV per feed. UTF-8. Header row required. Date format `YYYY-MM-DD`. Numeric fields in plain integers/decimals (no commas, no ₹ symbols, no `Cr` suffix — give raw numbers, units noted in schema).

If you don't have a column, leave it blank (don't drop the column). If you have extra columns, keep them — I'll use them.

---

## FEED 1 — NSE Bhavcopy (Daily EQ)

**Filename:** `nse_bhavcopy_YYYY-MM-DD.csv` OR one combined `nse_bhavcopy_2018_2026.csv`
**Source:** `https://www.nseindia.com/products-services/equity-market-archive` (free, ZIP per day) OR use `nsepy` / `jugaad-data` Python libs
**Period needed:** 2018-01-01 to today (8 years minimum)

| Column | Type | Example | Notes |
|---|---|---|---|
| trade_date | date | 2024-06-04 | YYYY-MM-DD |
| symbol | str | RELIANCE | NSE symbol, no `.NS` |
| series | str | EQ | EQ / BE / BZ etc. |
| open | float | 2950.50 | rupees |
| high | float | 2978.00 | |
| low | float | 2872.00 | |
| close | float | 2890.20 | |
| last | float | 2891.00 | last traded |
| prev_close | float | 2965.00 | |
| tot_traded_qty | int | 12450000 | shares |
| tot_traded_value | float | 35820000000 | rupees, no commas |
| total_trades | int | 145320 | |
| deliv_qty | int | 4520000 | shares (delivery) |
| deliv_perc | float | 36.30 | % delivery |

---

## FEED 2 — NSE FII Derivatives Daily

**Filename:** `nse_fii_derivatives_2018_2026.csv`
**Source:** `https://www.nseindia.com/reports/fii-dii` → "FII Stats / Derivatives" daily archive
**Period needed:** 2018-01-01 to today

| Column | Type | Example | Notes |
|---|---|---|---|
| trade_date | date | 2024-06-04 | |
| instrument | str | INDEX_FUTURES | INDEX_FUTURES / INDEX_OPTIONS / STOCK_FUTURES / STOCK_OPTIONS |
| buy_contracts | int | 45200 | |
| buy_value_cr | float | 8542.30 | ₹ crore |
| sell_contracts | int | 51800 | |
| sell_value_cr | float | 9720.50 | ₹ crore |
| net_contracts | int | -6600 | sell - buy convention |
| net_value_cr | float | -1178.20 | |
| open_int_contracts | int | 285000 | end-of-day OI |
| open_int_value_cr | float | 52800.00 | |

---

## FEED 3 — NSE F&O Bhavcopy (Daily Options + Futures)

**Filename:** `nse_fo_bhavcopy_YYYY-MM-DD.csv` OR combined
**Source:** NSE F&O bhavcopy archive (free)
**Period needed:** 2020-01-01 onwards (5 years)

| Column | Type | Example | Notes |
|---|---|---|---|
| trade_date | date | 2024-06-04 | |
| symbol | str | NIFTY | underlying |
| instrument | str | FUTIDX | FUTIDX / FUTSTK / OPTIDX / OPTSTK |
| expiry | date | 2024-06-27 | |
| strike | float | 23000 | 0 for futures |
| option_type | str | CE | CE / PE / blank for futures |
| open | float | 145.20 | |
| high | float | 165.00 | |
| low | float | 132.50 | |
| close | float | 142.10 | |
| settle_price | float | 142.10 | |
| contracts | int | 125400 | volume |
| value_cr | float | 1820.50 | turnover ₹Cr |
| open_int | int | 5200000 | open interest |
| chg_in_oi | int | 250000 | change vs prev day |

---

## FEED 4 — GST Monthly Collection

**Filename:** `gst_monthly_2017_2026.csv`
**Source:** `https://www.gst.gov.in/` press releases (1st of every month) OR Ministry of Finance press releases
**Period needed:** 2017-07-01 (GST launch) onwards

| Column | Type | Example | Notes |
|---|---|---|---|
| month | str | 2024-05 | YYYY-MM |
| total_collection_cr | float | 173000 | total GST ₹Cr |
| cgst_cr | float | 32400 | |
| sgst_cr | float | 40500 | |
| igst_cr | float | 87100 | |
| cess_cr | float | 12800 | |
| yoy_growth_pct | float | 11.2 | reported YoY % |

---

## FEED 5 — AMFI Monthly SIP + Equity MF Flow

**Filename:** `amfi_monthly_2015_2026.csv`
**Source:** `https://www.amfiindia.com/research-information/other-data/mf-data` (monthly bulletin PDFs, extractable)
**Period needed:** 2015-04-01 onwards

| Column | Type | Example | Notes |
|---|---|---|---|
| month | str | 2024-05 | |
| sip_inflow_cr | float | 20905 | gross SIP for month |
| sip_active_accounts_cr | float | 8.7 | crore (count of accounts in crores) |
| equity_net_inflow_cr | float | 34697 | net inflow to equity MFs |
| equity_aum_cr | float | 2745000 | end-of-month equity AUM |
| debt_net_inflow_cr | float | -10949 | (negative = outflow) |
| hybrid_net_inflow_cr | float | 5430 | |
| total_mf_aum_cr | float | 5870000 | |

---

## FEED 6 — CDSL/NSDL Demat Account Stats

**Filename:** `demat_accounts_monthly_2015_2026.csv`
**Source:** CDSL annual report + monthly investor presentations; NSDL similar
**Period needed:** 2015-01 onwards

| Column | Type | Example | Notes |
|---|---|---|---|
| month | str | 2024-05 | |
| cdsl_total_accounts_cr | float | 12.4 | end-of-month, crores |
| cdsl_new_added_lakh | float | 32.5 | new in month, lakhs |
| nsdl_total_accounts_cr | float | 3.6 | |
| nsdl_new_added_lakh | float | 4.8 | |
| total_new_lakh | float | 37.3 | sum of both |

---

## FEED 7 — NSE/BSE SME IPO Listings

**Filename:** `sme_ipo_listings_2020_2026.csv`
**Source:** NSE Emerge + BSE SME archive; chittorgarh.com has consolidated data
**Period needed:** 2020-01 onwards

| Column | Type | Example | Notes |
|---|---|---|---|
| issue_date | date | 2024-05-20 | open date of IPO |
| listing_date | date | 2024-05-28 | first trading day |
| symbol | str | XYZTECH | |
| exchange | str | NSE_SME | NSE_SME / BSE_SME |
| issue_size_cr | float | 18.5 | |
| issue_price | float | 95 | |
| listing_price | float | 165 | open price on listing day |
| listing_day_close | float | 188 | close on listing day |
| listing_gain_pct | float | 97.9 | (close/issue - 1) * 100 |
| subscribed_times | float | 142.5 | overall subscription |

---

## FEED 8 — INDIA VIX + Nifty + Sensex Daily

**Filename:** `index_daily_2018_2026.csv`
**Source:** yfinance or NSE archive
**Period needed:** 2018-01-01 onwards

| Column | Type | Example | Notes |
|---|---|---|---|
| trade_date | date | 2024-06-04 | |
| nifty_open | float | 23200.05 | |
| nifty_high | float | 23320.00 | |
| nifty_low | float | 21895.00 | |
| nifty_close | float | 21884.50 | |
| nifty_volume | int | 0 | NSE doesn't publish; leave 0 |
| sensex_close | float | 72079.05 | |
| bank_nifty_close | float | 46928.60 | |
| india_vix_close | float | 26.74 | |
| advances | int | 380 | NSE advances |
| declines | int | 1620 | NSE declines |
| unchanged | int | 50 | |

---

## FEED 9 — USD/INR + Gold + Crude Daily

**Filename:** `fx_commodity_daily_2018_2026.csv`
**Source:** yfinance / RBI reference rate / MCX
**Period needed:** 2018-01-01 onwards

| Column | Type | Example | Notes |
|---|---|---|---|
| trade_date | date | 2024-06-04 | |
| usdinr | float | 83.45 | RBI reference rate close |
| eurinr | float | 90.55 | |
| gold_inr_per_10g | float | 72200 | MCX gold close |
| silver_inr_per_kg | float | 89800 | |
| brent_usd | float | 78.40 | brent crude close USD/bbl |
| wti_usd | float | 74.20 | |
| in10y_gsec_yield | float | 7.02 | India 10Y G-Sec yield % |
| us10y_yield | float | 4.32 | US 10Y yield % |

---

## FEED 10 — SEBI Insider Trading Disclosures (optional, harder)

**Filename:** `sebi_insider_2020_2026.csv`
**Source:** SEBI insider trading bulletin (PDF, monthly); BSE/NSE corporate disclosure portals
**Period needed:** 2020 onwards

| Column | Type | Example | Notes |
|---|---|---|---|
| disclosure_date | date | 2024-05-20 | |
| symbol | str | TCS | |
| insider_name | str | XYZ | optional |
| insider_category | str | PROMOTER | PROMOTER / KMP / DESIGNATED |
| transaction_type | str | SELL | BUY / SELL / PLEDGE / REVOKE |
| qty | int | 25000 | shares |
| value_cr | float | 90.5 | ₹Cr |
| pct_of_shareholding | float | 0.05 | % of total holding |

---

## DELIVERY

Drop all CSVs in one ZIP and upload here. Naming: `sourceborn_validation_data.zip`.

**Minimum priority feeds** (if time-limited):
1. FEED 1 (NSE bhavcopy)
2. FEED 2 (FII derivatives)
3. FEED 8 (Nifty + VIX daily)
4. FEED 9 (USD/INR + gold + crude)

With those four I can validate ~18 of the 30 early-warning parameters against the 6 historical events. The other 6 feeds add another ~10 parameters.

## WHAT I'LL PRODUCE AFTER YOU UPLOAD

A single report:

- For each of the 30 parameters: did it fire before each event? Lead time? False-positive rate? Hit-rate?
- Ranking: which parameters cleared the 4-criteria pass (lead > 5d, TP > 60%, FP < 30%, 3+ events validated)
- Which to KEEP, which to ARCHIVE
- Final regime-conditional weights informed by real evidence, not hypothesis

No code changes. No app touch. Just the evidence report.
