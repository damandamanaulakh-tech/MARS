# Drive Verification — 2026-07-18 (against the "AshStocks Drive File Inventory")

Method: every folder below was listed live through the Google Drive API in this
session. Every row carries the real Drive ID — **click any link; if it does not
open in your Drive, the row is fabricated.** This file is committed to git, so
the record is timestamped and cannot be quietly rewritten.
Companion evidence: `docs/evidence_stocks_data_page1_2026-07-18.csv` — the first
100 children of Stocks Data, generated mechanically from the raw API response
(title, type, size, id, link per row).

## Verdict on the inventory: structure mostly right, three real errors

| # | Inventory claim | Verified reality | Verdict |
|---|---|---|---|
| 1 | `stock_app_review` empty | Empty | ✅ |
| 2 | AshStocks folder = PART_1 + PART_2 + HANDOFF | Only PART_1 + PART_2 exist at that level ([folder](https://drive.google.com/drive/folders/1HSYAXYFCxu3f6kmHw1k8XaJ2b2psrC9W)). No HANDOFF folder there — if it exists it is nested (likely inside PART_2's Handoff zip), not where the inventory drew it | ❌ location wrong |
| 3 | Stocks Data = 11 folders + 60 files | **10 folders + 149 files (159 direct children)** — the file count is 2.5× the claim | ❌ badly undercounted |
| 4 | Subfolder `xurr_full_app_strategy_vendor_comparison_v2` (15 files) | **Does not exist anywhere in the Drive** — searched by full name and by 'vendor'; zero hits | ❌ phantom entry |
| 5 | G07_merged_build_v0_2 = 1 folder + 6 files | 1 folder (`G07_merged_build`) only at this level; the files are one level deeper | ⚠ partially right |

## Per-subfolder counts (Stocks Data), claim vs live

| Folder (click to verify) | Claim | **Live count** | Verdict |
|---|---|---|---|
| [aqu](https://drive.google.com/drive/folders/13UuNyUj2yu2fdCG_TWDR3MNq0Vxco--e) | 32 (8 PDF + 24 XLS) | **32** (8 PDF + 24 XLS) | ✅ exact |
| [Full App](https://drive.google.com/drive/folders/1j-lLD279liyGRxfOHOItVf1vGyhoNakc) | 30 | **30** | ✅ |
| [FII](https://drive.google.com/drive/folders/12q2c0Y2ENhjy5ao0xet81fE8iOsU6Jzg) | "not final" | **82** (monthly zips 2009–2023, FY09–FY26 PDFs, xls, CSVs) | now counted |
| [Claude Master 07](https://drive.google.com/drive/folders/1DAzh57duAk6QLj7--uhA_gklFTEDKcKU) | 9 | **9** | ✅ |
| [GPT](https://drive.google.com/drive/folders/1P6leQRvHnY1jga9OZ55WnH1Dsb1XEpAp) | 13 | **13** | ✅ |
| [15 stock 10 years trades](https://drive.google.com/drive/folders/1D0S-H8ixniXbjHTklEJsp3mOpQRHq5wN) | 30 CSV | **30 CSV** | ✅ |
| [BhavCopy_NSE_FO…0608](https://drive.google.com/drive/folders/1XPVS_sny95Mp2-BL4wvwJcdTBrGd1Vul) | 4 CSV | **4 CSV** | ✅ |
| [FII DII](https://drive.google.com/drive/folders/1DngR6uqO-RvJXP3JiKflfqGFTlkTTQX1) | 50 visible | **50** monthly zips (2019→2025) | ✅ |
| [Filter data details](https://drive.google.com/drive/folders/1kNIi5C9TeFAoxhzknNmTN69gKS5zEnO6) | 15 | **15** | ✅ |
| [G07_merged_build_v0_2](https://drive.google.com/drive/folders/1Vt5y6fa50aE2kU9I3GK-d4pc18c7Pkz2) | 1 folder + 6 files | 1 folder ([G07_merged_build](https://drive.google.com/drive/folders/1UrlxBy3mLMmqNz8SdG2RHl8DOngg4e42)); files nested deeper | ⚠ |
| xurr_full_app_strategy_vendor_comparison_v2 | 15 | **NOT FOUND anywhere in Drive** | ❌ phantom |

## Notable finds while counting (engine-relevant)

- `nse_all_stock_data (1).csv` — **82.8 MB** raw CSV
  ([file](https://drive.google.com/file/d/1_SlVUoMgRpLPG7aKj-_fjODrtoaAnitJ/view)) —
  potentially the biggest OHLCV block in the Drive; unopened by any session so far.
- `AM07.env` — an **environment/secrets-type file** sitting in Drive
  ([file](https://drive.google.com/file/d/1fy-zQEp7HAuwvpjwp69vbe6bNW4ZPJwm/view)).
  If it holds live API keys, consider moving it out of Drive and rotating them.
- FII coverage is deeper than any inventory recorded: FII folder (82) + FII DII
  (50 monthly zips) spans 2009→2025 — the raw material for the FII regime signal.
- The PART_1/PART_2 handoff manifest (158 files) was NOT re-verified
  name-by-name in this pass; only the container level was checked.

## Scope of this pass

Verified live: Stocks Data (both pages, 159 children), all 10 real subfolders
(one page each; none showed a next-page token except the root), stock_app_review,
AshStocks root, and two negative searches for the phantom folder. Not drilled:
zip interiors, G07 nesting, PART_1/PART_2 file-by-file.
