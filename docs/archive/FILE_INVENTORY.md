# MASTER FILE INVENTORY — everything you have fed (deduplicated)

**81 unique files**; 173 duplicate upload copies detected and ignored.
Column-level schemas: `docs/DATA_INDEX.jsonl` (greppable).
Already extracted into the app: `data/history_15stock/` (real 10y OHLCV), `data/upstox_nse_equities.csv` (universe), `data/fii_dii_cash.csv` (flows).

## Chityy micro-split tables (pyramid engine CSVs) — 22 files

| File | Size | Dup copies | Contents |
|---|---|---|---|
| micro_02_parameter_pileup_full.csv | 7689 KB | 2 | 22891 rows: pile_id, raw_param_id, parameter_name, block_name, family_or_layer, parameter_no |
| micro_02_parameter_pileup_full.csv | 4275 KB | 0 | 12798 rows: pile_id, raw_param_id, parameter_name, block_name, parameter_no, definition |
| micro_06_stock_slots_random.csv | 2019 KB | 4 | 10000 rows: slot_id, engine_category, category_seq, symbol, company_name, exchange |
| micro_05_stock_slots_lower.csv | 1999 KB | 4 | 10000 rows: slot_id, engine_category, category_seq, symbol, company_name, exchange |
| micro_04_stock_slots_top.csv | 1938 KB | 4 | 10000 rows: slot_id, engine_category, category_seq, symbol, company_name, exchange |
| micro_03_duplicate_groups_full.csv | 1673 KB | 2 | 4046 rows: duplicate_group, duplicate_key, canonical_parameter_name, count, source_files, source_sheets |
| micro_03_duplicate_groups_full.csv | 845 KB | 0 | 2408 rows: duplicate_group, duplicate_key, canonical_parameter_name, count, source_files, source_sheets |
| micro_14_distinct100_sample.csv | 61 KB | 2 | 198 rows: Segment, Segment_Rank, Symbol, Yahoo_Ticker, Company_Name, Exchange |
| micro_09_n200_available_batch.csv | 57 KB | 4 | 198 rows: symbol, shortName, sector, industry, country, currency |
| micro_13_industry_150.csv | 31 KB | 2 | 150 rows: Industry_ID, Category, Industry_No, Industry_Name, Stock_Slots, Status |
| micro_13_industry_150.csv | 28 KB | 0 | 150 rows: Industry_ID, Category, Category_Industry_No, Industry_Name, Suggested_Sector, 15Y_Performance_Class |
| micro_15_filled_top50_sample.csv | 20 KB | 2 | 50 rows: Rank, Category, Industry_Slot, Yahoo_Ticker, Company_Name, Asset_Class |
| micro_01_file_pileup_map.csv | 15 KB | 2 | 27 rows: file_id, file_name, file_type, size_kb, source_family, sheet_or_columns_found |
| micro_10_sourceborn_picks_sample.csv | 8 KB | 4 | 50 rows: rank, ticker, score, last_close, timing_pass, reasoning |
| micro_01_file_pileup_map.csv | 7 KB | 0 | 18 rows: file_id, file_name, file_type, size_kb, source_family, sheet_or_columns_found |
| micro_12_asset_master_sample.csv | 4 KB | 0 | 24 rows: Asset_ID, Asset_Class, Asset_Name, Yahoo_Ticker, Yahoo_URL, Currency |
| micro_12_asset_master_sample.csv | 4 KB | 2 | 13 rows: Asset_Code, Asset_Name, Asset_Class, Yahoo_Ticker, Yahoo_URL, Current_Price |
| micro_08_reverse_event_schema.csv | 2 KB | 0 | 20 rows: event_field, required, event_family, logic_or_definition, source_needed, status |
| micro_08_reverse_event_schema.csv | 2 KB | 2 | 21 rows: event_field, required, event_family, logic_or_definition, source_needed, status |
| micro_07_upstox_schema.csv | 1 KB | 4 | 19 rows: field_name, required, data_type, example_or_status, micro_landing, notes |
| micro_11_next_loop_control.csv | 1 KB | 0 | 7 rows: loop_id, halt_or_task, sourceborn_stage, urr_micro_pass, status, required_input |
| micro_11_next_loop_control.csv | 1 KB | 2 | 8 rows: loop_id, halt_or_task, sourceborn_stage, urr_micro_pass, status, required_input |

## Chityy parameter workbooks (other-AI taxonomy) — 15 files

| File | Size | Dup copies | Contents |
|---|---|---|---|
| ARD_Chityy_Sourceborn_Stock_Parameter_Master_Upstox_Ready_v1.xlsx | 1838 KB | 2 | 18 sheets: 05_Stock_Slots_30000(30000r), 03_Master_Parameter_Sheet(1336r), 06_Industry_Master_150(150r) |
| Chityy_all_filled_REVIEW_FIXED_v0_3.xlsx | 299 KB | 11 | 22 sheets: 00_READ_ME(0r), 01_SOURCE_REVIEW_MAP(0r), 02_DATA_TO_COLLECT_NEXT(0r) |
| Chityy_Sourceborn_FINAL_MICRO_SPLIT_ENGINE_v0_6.xlsx | 293 KB | 5 | 17 sheets: 00_FINAL_STATUS(0r), 01_SOURCE_FAMILY_COUNTS(0r), 02_MICRO_SPLIT_INDEX(0r) |
| Chityy_all_copilot_filled_v2_DISTINCT100.xlsx | 252 KB | 0 | 19 sheets: 04_PARAMETER_BANK_1200(1200r), 06_STOCK_UNIVERSE_30K(198r), 18_SEGMENT_DISTINCT_PLAN(198r) |
| Chityy_REVERSE_PATTERN_PARAMETER_ENGINE_v0_4.xlsx | 233 KB | 7 | 14 sheets: 00_READ_ME(0r), 01_FILE_SOURCE_AUDIT(0r), 02_DUPLICATE_VS_MASTER_MAP(0r) |
| Chityy_all_copilot_filled.xlsx | 223 KB | 3 | 18 sheets: 04_PARAMETER_BANK_1200(1200r), 06_STOCK_UNIVERSE_30K(198r), 03_UPSTOX_RAW_IMPORT(186r) |
| Chityy_Stock_Master_Parameter_Workbook_v0_1.xlsx | 215 KB | 7 | 13 sheets: 00_READ_ME(0r), 01_SOURCE_REVIEW_MAP(0r), 11_DROPDOWNS(0r) |
| Chityy_all_filled.xlsx | 214 KB | 11 | 17 sheets: 04_PARAMETER_BANK_1200(1200r), 06_STOCK_UNIVERSE_30K(198r), 03_UPSTOX_RAW_IMPORT(186r) |
| Chityy_Stock_Master_Parameter_Workbook_v0_2_1200.xlsx | 165 KB | 7 | 17 sheets: 00_READ_ME(0r), 01_SOURCE_REVIEW_MAP(0r), 02_DATA_TO_COLLECT_NEXT(0r) |
| Chityy_Stock_Master_Parameter_Workbook_v0_1_COPILOT_FILLED_v2_DISTINCT100.xlsx | 148 KB | 3 | 15 sheets: 03_TECH_DERIVED_TEMPLATE(499r), 08_PAPER_TRADE_GURU_STOCK(199r), 13_SEGMENT_ASSIGNMENT(198r) |
| Chityy_Stock_Master_Parameter_Workbook_v0_1_COPILOT_FILLED.xlsx | 135 KB | 3 | 14 sheets: 03_TECH_DERIVED_TEMPLATE(499r), 08_PAPER_TRADE_GURU_STOCK(199r), 02_UPSTOX_RAW_IMPORT(186r) |
| Chityy_Sourceborn_PYRAMID_SEQUENCE_PAPER_TEST_v0_1.xlsx | 63 KB | 1 | 8 sheets: 00_READ_ME(0r), 01_PYRAMID_SEQUENCE(0r), 02_LAYER_COUNTS(0r) |
| Chityy_master_extended (1).xlsx | 34 KB | 4 | 1 sheets: Chityy Master Table(31r) |
| Chityy_master_one_table_comparison.xlsx | 30 KB | 1 | 1 sheets: Chityy Master Table(19r) |
| Chityy_Paper_Trading_Guide.md | 5 KB | 1 | — |

## ARD opportunity/asset masters — 9 files

| File | Size | Dup copies | Contents |
|---|---|---|---|
| ARD_Opportunity_Stock_Asset_Master_v0_8_COPILOT_FILLED_v2_DISTINCT100.xlsx | 1921 KB | 3 | 10 sheets: Stock_Slots_30000(30000r), Parameter_Dictionary_1200(1200r), SEGMENT_100_DISTINCT_MAP(198r) |
| ARD_Opportunity_Stock_Asset_Master_v0_8_COPILOT_FILLED1.xlsx | 1902 KB | 8 | 9 sheets: Stock_Slots_30000(30000r), Parameter_Dictionary_1200(1200r), Industry_150(150r) |
| ARD_Opportunity_Stock_Asset_Master_v0_8_FILLED.xlsx | 1889 KB | 3 | 8 sheets: Stock_Slots_30000(30000r), Parameter_Dictionary_1200(1200r), Industry_150(150r) |
| ARD_Opportunity_Stock_Parameter_Master_v0_6.xlsx | 1731 KB | 2 | 9 sheets: 03_Stock_Slots_30000(30000r), 01_Parameter_Dictionary(1100r), 04_Stock_Parameter_Long(300r) |
| ARD_Google_Live_Stock_Fill_v1.xlsx | 218 KB | 2 | 4 sheets: Google_Live_30000(0r), Source_Nifty500_List(0r), Dhan_Top50_Current(0r) |
| ARD_Opportunity_Stock_Asset_Master_v0_8.xlsx | 123 KB | 2 | 18 sheets: Parameter_Dictionary_v0_7(1500r), Industry_Master(150r), Realtime_Price_Queue(124r) |
| ARD_Opportunity_Stock_Asset_Master_v0_7.xlsx | 100 KB | 2 | 15 sheets: Parameter_Dictionary_v0_7(1200r), Industry_Master(150r), Realtime_Price_Queue(124r) |
| ARD_Parameter_Master_Compiled_Upstox_Ready_v1.xlsx | 33 KB | 2 | 4 sheets: Master_Parameter_List(399r), Block_Structure(30r), README_Compiled(16r) |
| ARD_3.0_Review.xlsx | 14 KB | 1 | 1 sheets: ARD 3.0 Review(13r) |

## App specs & reviews (from other AIs) — 8 files

| File | Size | Dup copies | Contents |
|---|---|---|---|
| SB & URR.docx | 88 KB | 0 | — |
| ME_ONLY_Paper_Trading_App_Review.docx | 42 KB | 1 | — |
| App_Parametrs.docx | 36 KB | 2 | — |
| Stock App Gemini.docx | 30 KB | 1 | — |
| Stock_Industry_Asset_Database.docx | 28 KB | 2 | — |
| Am07 Stock App.docx | 28 KB | 1 | — |
| am07.xlsx | 22 KB | 1 | 2 sheets: Sheet2(120r), Sheet1(28r) |
| Stock App.txt | 11 KB | 1 | — |

## Prototype code (Chityy/AM07 paper engines) — 8 files

| File | Size | Dup copies | Contents |
|---|---|---|---|
| chityy_paper_proto.py | 10 KB | 1 | — |
| paper.py | 4 KB | 0 | — |
| engine.py | 3 KB | 0 | — |
| pyramid.py | 2 KB | 0 | — |
| run_paper_test.py | 1 KB | 0 | — |
| io_utils.py | 1 KB | 0 | — |
| config.py | 0 KB | 0 | — |
| __init__.py | 0 KB | 0 | — |

## Picks / parameters / universe CSVs — 7 files

| File | Size | Dup copies | Contents |
|---|---|---|---|
| master_30000_stock_list.csv | 1584 KB | 2 | 30000 rows: symbol, company_name, exchange, industry, market_cap_cr, pe_ratio |
| parameters_v0_7.csv | 207 KB | 1 | 1200 rows: Parameter_ID, Block_ID, Block_Name, Parameter_No_In_Block, Parameter_Name, Applies_To |
| n200_live_report.csv | 120 KB | 6 | 198 rows: symbol, shortName, sector, industry, country, currency |
| Claude_200_N_data.csv | 111 KB | 2 | 198 rows: symbol, shortName, sector, industry, country, currency |
| sourceborn_today_picks.csv | 4 KB | 1 | 50 rows: rank, ticker, score, last_close, timing_pass, reasoning |
| Stock.csv | 2 KB | 1 | 13 rows: Capability, AM07 Today, AM07 After Build (Target), Gap, Priority, Impact if Fixed |
| proof_gap_ledger.csv | 1 KB | 2 | 3 rows: gap_id, gap_type, gap, impact, next_loop, status |

## Pyramid/URR sequence maps — 5 files

| File | Size | Dup copies | Contents |
|---|---|---|---|
| mapped_parameters_to_pyramid_full.csv | 8800 KB | 2 | 22891 rows: pile_id, raw_param_id, parameter_name, block_name, family_or_layer, parameter_no |
| mapped_parameters_to_pyramid_full.csv | 8710 KB | 2 | 22891 rows: pile_id, raw_param_id, parameter_name, block_name, family_or_layer, parameter_no |
| sample_paper_trading_running_map.csv | 55 KB | 2 | 198 rows: symbol, shortName, sector, industry, data_status, currentPrice |
| sample_paper_trading_running_map.csv | 55 KB | 2 | 198 rows: symbol, name, sector, industry, data_status, current_price |
| pyramid_sequence_running_map.csv | 2 KB | 2 | 16 rows: stage_id, stage_name, stage_purpose, pyramid_layer, status |

## Zerodha action plan + gap tables — 3 files

| File | Size | Dup copies | Contents |
|---|---|---|---|
| Zerodha_Stock_Trading_App_Action_Plan_META_RGL.docx | 52 KB | 1 | — |
| table.csv | 3 KB | 1 | 27 rows: #, System / Proposal, Category, Execution Level, Risk Class / Target, Selection Strength |
| table (1).csv | 2 KB | 1 | 10 rows: #, Capability / Fix, What AM07 Currently Has, What's Missing / Weak, Adopt? (Priority), Expected Change / Impact |

## Session carryforwards & prompts — 2 files

| File | Size | Dup copies | Contents |
|---|---|---|---|
| SOURCEBORN_AM07_CARRYFORWARD_v1.docx | 39 KB | 0 | — |
| README.md | 1 KB | 0 | — |

## GURU trading rules + comparisons — 2 files

| File | Size | Dup copies | Contents |
|---|---|---|---|
| SYNTHETIC_GURU_technical_comparison.xlsx | 31 KB | 1 | 7 sheets: README(0r), App_vs_SYNTHETIC(0r), Tech_Weights(0r) |
| Guru.xlsx | 20 KB | 1 | 2 sheets: Sheet1(22r), Sheet2(18r) |
