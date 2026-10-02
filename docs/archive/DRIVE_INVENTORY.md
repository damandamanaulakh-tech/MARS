# WIRED FILES & PARAMETERS — what actually feeds the engine (2026-07-11)

## 1. Files WIRED into the running app

| Repo file | Built from (your data) | Drive link | Wired into | Status |
|---|---|---|---|---|
| `data/upstox_nse_equities.csv` (2,376 stocks) | NSE.json.gz + NSE_MIS.json.gz (Upstox masters) | [NSE.json.gz](https://drive.google.com/file/d/14Vjvp8znDwLG40p411svW4PRebcHmFxP/view) · [NSE_MIS](https://drive.google.com/file/d/1TzZN-4SZrLNpo16usk5Ho7bYt5C-JJ0V/view) | Scan-universe loader + instrument-key map that powers ALL Upstox data (quotes, history, candles, backtests) | ✅ WIRED |
| `data/history_15stock/` (16 tickers × 10y, corp-action adjusted) | 26 CSVs in Drive "15 stock 10 years trades" | [folder](https://drive.google.com/drive/folders/1D0S-H8ixniXbjHTklEJsp3mOpQRHq5wN) | The 10 real backtests + all future validation (Guru head-to-head, walk-forward) | ✅ WIRED (validation layer) |
| `data/fii_dii_cash.csv` (daily FII/DII net, 2024) | sample_fii_dii_cash.csv | [file](https://drive.google.com/file/d/1c0WWLJgTVXjmLNk2lrhTZ_Fwr9-4amoe/view) | Committed durable; regime hookup = pending proposal | 🟡 COMMITTED, not yet wired |

## 2. Files DESIGN-WIRED (their ideas became engine code, file not read at runtime)

| Source file(s) | What was absorbed |
|---|---|
| SOURCEBORN_AM07_CARRYFORWARD_v1 (.md/.docx) | IFR overlays: −3%/+3% tails · 5d window · 10d cluster ≥5 · 90th/85th pct · 22d active · 0.5× throttle (VALIDATED 28y) |
| Chityy workbooks (quality/value tilt) | Quality co-scorer: low-vol 63d + liquidity tiers 90/70/55/30, 35% blend |
| Chityy proof/honesty layer | /proof gap ledger G001-G004 |
| SB & URR.docx + your ask | Candle engine: breakout / URR upthrust-result-retest / engulfing gate |
| Zerodha action plan + your audits | MIN(strength, IFR) sizing · floor 55 · dead-ticker cleanup |
| Chityy pyramid maps | F15 partial-buy pyramiding (queued, not yet built) |

## 3. Parameters wired RIGHT NOW (counted from code, not claimed)

| Layer | Count |
|---|---|
| Config runtime parameters (each with real formula/use) | **45** |
| Mode-preset values (14 fields × 5 modes: stops, targets, decay, min-score, square-off…) | **70** |
| Engine constants (IFR 8 · drawdown ladder 10 · dynamic reserve 4 · RAM caps 4 · pool/corr 3) | **29** |
| **Total wired values** | **144** |

vs your files' "1,500+ parameter" taxonomy: those are mostly parameter NAMES
(placeholders, no formulas). Every one of the 144 above has a formula, a data
feed, and most have test coverage (292 tests).

## 4. Duplicates — 81 unique files from ~254 uploaded copies (173 duplicate copies ignored)

## Similarity clusters (>=50% same sheets/columns)

**Cluster (5 files):**
- Chityy_Stock_Master_Parameter_Workbook_v0_2_1200.xlsx
- Chityy_all_copilot_filled.xlsx
- Chityy_all_copilot_filled_v2_DISTINCT100.xlsx
- Chityy_all_filled.xlsx
- Chityy_all_filled_REVIEW_FIXED_v0_3.xlsx

**Cluster (3 files):**
- ARD_Opportunity_Stock_Asset_Master_v0_8_COPILOT_FILLED1.xlsx
- ARD_Opportunity_Stock_Asset_Master_v0_8_COPILOT_FILLED_v2_DISTINCT100.xlsx
- ARD_Opportunity_Stock_Asset_Master_v0_8_FILLED.xlsx

**Cluster (3 files):**
- Chityy_Stock_Master_Parameter_Workbook_v0_1.xlsx
- Chityy_Stock_Master_Parameter_Workbook_v0_1_COPILOT_FILLED.xlsx
- Chityy_Stock_Master_Parameter_Workbook_v0_1_COPILOT_FILLED_v2_DISTINCT100.xlsx

**Cluster (3 files):**
- micro_04_stock_slots_top.csv
- micro_05_stock_slots_lower.csv
- micro_06_stock_slots_random.csv

**Cluster (2 files):**
- ARD_Opportunity_Stock_Asset_Master_v0_7.xlsx
- ARD_Opportunity_Stock_Asset_Master_v0_8.xlsx

**Cluster (2 files):**
- Chityy_master_extended (1).xlsx
- Chityy_master_one_table_comparison.xlsx

**Cluster (2 files):**
- Claude_200_N_data.csv
- n200_live_report.csv

**Cluster (2 files):**
- Guru.xlsx
- am07.xlsx

**Cluster (2 files):**
- mapped_parameters_to_pyramid_full.csv
- micro_02_parameter_pileup_full.csv

**Cluster (2 files):**
- micro_10_sourceborn_picks_sample.csv
- sourceborn_today_picks.csv

## Top pairwise matches
- 100% — micro_05_stock_slots_lower.csv  ≈  micro_06_stock_slots_random.csv
- 100% — micro_04_stock_slots_top.csv  ≈  micro_06_stock_slots_random.csv
- 100% — micro_04_stock_slots_top.csv  ≈  micro_05_stock_slots_lower.csv
- 100% — Guru.xlsx  ≈  am07.xlsx
- 100% — Claude_200_N_data.csv  ≈  n200_live_report.csv
- 100% — Chityy_master_extended (1).xlsx  ≈  Chityy_master_one_table_comparison.xlsx
- 100% — Chityy_Stock_Master_Parameter_Workbook_v0_2_1200.xlsx  ≈  Chityy_all_filled.xlsx
- 95% — Chityy_all_copilot_filled.xlsx  ≈  Chityy_all_filled.xlsx
- 95% — Chityy_Stock_Master_Parameter_Workbook_v0_2_1200.xlsx  ≈  Chityy_all_copilot_filled.xlsx
- 94% — Chityy_all_copilot_filled.xlsx  ≈  Chityy_all_copilot_filled_v2_DISTINCT100.xlsx
- 93% — ARD_Opportunity_Stock_Asset_Master_v0_8_COPILOT_FILLED1.xlsx  ≈  ARD_Opportunity_Stock_Asset_Master_v0_8_FILLED.xlsx
- 91% — Chityy_Stock_Master_Parameter_Workbook_v0_1_COPILOT_FILLED.xlsx  ≈  Chityy_Stock_Master_Parameter_Workbook_v0_1_COPILOT_FILLED_v2_DISTINCT100.xlsx
- 91% — Chityy_Stock_Master_Parameter_Workbook_v0_1.xlsx  ≈  Chityy_Stock_Master_Parameter_Workbook_v0_1_COPILOT_FILLED.xlsx
- 90% — Chityy_all_copilot_filled_v2_DISTINCT100.xlsx  ≈  Chityy_all_filled.xlsx
- 90% — Chityy_Stock_Master_Parameter_Workbook_v0_2_1200.xlsx  ≈  Chityy_all_copilot_filled_v2_DISTINCT100.xlsx
- 89% — mapped_parameters_to_pyramid_full.csv  ≈  micro_02_parameter_pileup_full.csv
- 87% — Chityy_all_filled.xlsx  ≈  Chityy_all_filled_REVIEW_FIXED_v0_3.xlsx
- 87% — Chityy_Stock_Master_Parameter_Workbook_v0_2_1200.xlsx  ≈  Chityy_all_filled_REVIEW_FIXED_v0_3.xlsx
- 85% — ARD_Opportunity_Stock_Asset_Master_v0_8_COPILOT_FILLED1.xlsx  ≈  ARD_Opportunity_Stock_Asset_Master_v0_8_COPILOT_FILLED_v2_DISTINCT100.xlsx
- 83% — Chityy_all_copilot_filled.xlsx  ≈  Chityy_all_filled_REVIEW_FIXED_v0_3.xlsx
- 83% — Chityy_Stock_Master_Parameter_Workbook_v0_1.xlsx  ≈  Chityy_Stock_Master_Parameter_Workbook_v0_1_COPILOT_FILLED_v2_DISTINCT100.xlsx
- 79% — Chityy_all_copilot_filled_v2_DISTINCT100.xlsx  ≈  Chityy_all_filled_REVIEW_FIXED_v0_3.xlsx
- 78% — ARD_Opportunity_Stock_Asset_Master_v0_8_COPILOT_FILLED_v2_DISTINCT100.xlsx  ≈  ARD_Opportunity_Stock_Asset_Master_v0_8_FILLED.xlsx
- 74% — ARD_Opportunity_Stock_Asset_Master_v0_7.xlsx  ≈  ARD_Opportunity_Stock_Asset_Master_v0_8.xlsx
- 67% — micro_10_sourceborn_picks_sample.csv  ≈  sourceborn_today_picks.csv

## Exact duplicate uploads (byte-identical copies you sent multiple times)
- Chityy_all_filled_REVIEW_FIXED_v0_3.xlsx — 11 extra copies
- Chityy_all_filled.xlsx — 11 extra copies
- ARD_Opportunity_Stock_Asset_Master_v0_8_COPILOT_FILLED1.xlsx — 8 extra copies
- Chityy_REVERSE_PATTERN_PARAMETER_ENGINE_v0_4.xlsx — 7 extra copies
- Chityy_Stock_Master_Parameter_Workbook_v0_1.xlsx — 7 extra copies
- Chityy_Stock_Master_Parameter_Workbook_v0_2_1200.xlsx — 7 extra copies
- n200_live_report.csv — 6 extra copies
- Chityy_Sourceborn_FINAL_MICRO_SPLIT_ENGINE_v0_6.xlsx — 5 extra copies
- Chityy_master_extended (1).xlsx — 4 extra copies
- micro_05_stock_slots_lower.csv — 4 extra copies
- micro_07_upstox_schema.csv — 4 extra copies
- micro_06_stock_slots_random.csv — 4 extra copies
- micro_04_stock_slots_top.csv — 4 extra copies
- micro_09_n200_available_batch.csv — 4 extra copies
- micro_10_sourceborn_picks_sample.csv — 4 extra copies
- Chityy_Stock_Master_Parameter_Workbook_v0_1_COPILOT_FILLED_v2_DISTINCT100.xlsx — 3 extra copies
- ARD_Opportunity_Stock_Asset_Master_v0_8_COPILOT_FILLED_v2_DISTINCT100.xlsx — 3 extra copies
- Chityy_Stock_Master_Parameter_Workbook_v0_1_COPILOT_FILLED.xlsx — 3 extra copies
- ARD_Opportunity_Stock_Asset_Master_v0_8_FILLED.xlsx — 3 extra copies
- Chityy_all_copilot_filled.xlsx — 3 extra copies
- ARD_Google_Live_Stock_Fill_v1.xlsx — 2 extra copies
- ARD_Opportunity_Stock_Asset_Master_v0_7.xlsx — 2 extra copies
- ARD_Chityy_Sourceborn_Stock_Parameter_Master_Upstox_Ready_v1.xlsx — 2 extra copies
- ARD_Opportunity_Stock_Parameter_Master_v0_6.xlsx — 2 extra copies
- master_30000_stock_list.csv — 2 extra copies
- App_Parametrs.docx — 2 extra copies
- Stock_Industry_Asset_Database.docx — 2 extra copies
- Claude_200_N_data.csv — 2 extra copies
- ARD_Parameter_Master_Compiled_Upstox_Ready_v1.xlsx — 2 extra copies
- ARD_Opportunity_Stock_Asset_Master_v0_8.xlsx — 2 extra copies
- mapped_parameters_to_pyramid_full.csv — 2 extra copies
- pyramid_sequence_running_map.csv — 2 extra copies
- sample_paper_trading_running_map.csv — 2 extra copies
- mapped_parameters_to_pyramid_full.csv — 2 extra copies
- proof_gap_ledger.csv — 2 extra copies
- sample_paper_trading_running_map.csv — 2 extra copies
- micro_02_parameter_pileup_full.csv — 2 extra copies
- micro_13_industry_150.csv — 2 extra copies
- micro_15_filled_top50_sample.csv — 2 extra copies
- micro_08_reverse_event_schema.csv — 2 extra copies
- micro_11_next_loop_control.csv — 2 extra copies
- micro_12_asset_master_sample.csv — 2 extra copies
- micro_01_file_pileup_map.csv — 2 extra copies
- micro_03_duplicate_groups_full.csv — 2 extra copies
- micro_14_distinct100_sample.csv — 2 extra copies
- Chityy_Sourceborn_PYRAMID_SEQUENCE_PAPER_TEST_v0_1.xlsx — 1 extra copy
- sourceborn_today_picks.csv — 1 extra copy
- parameters_v0_7.csv — 1 extra copy
- Stock App Gemini.docx — 1 extra copy
- Guru.xlsx — 1 extra copy
- table.csv — 1 extra copy
- ME_ONLY_Paper_Trading_App_Review.docx — 1 extra copy
- SYNTHETIC_GURU_technical_comparison.xlsx — 1 extra copy
- Chityy_Paper_Trading_Guide.md — 1 extra copy
- Zerodha_Stock_Trading_App_Action_Plan_META_RGL.docx — 1 extra copy
- table (1).csv — 1 extra copy
- Am07 Stock App.docx — 1 extra copy
- ARD_3.0_Review.xlsx — 1 extra copy
- Stock App.txt — 1 extra copy
- Stock.csv — 1 extra copy
- chityy_paper_proto.py — 1 extra copy
- Chityy_master_one_table_comparison.xlsx — 1 extra copy
- am07.xlsx — 1 extra copy

## 5. NOT yet captured (Drive approval gate re-locked mid-session)
Sub-folders **Full App · FII · Claude Master 07 · GPT** — queued F1.
Authorize Drive at the START of the next session (gate clears then).

## 6. Notable finding
**Guru.xlsx structure = 100% match with am07.xlsx** — the "Guru" sheet is
built on your own app's template (same columns), so the Batch E head-to-head
will compare its FILLED VALUES/rules, not a foreign system's design.
