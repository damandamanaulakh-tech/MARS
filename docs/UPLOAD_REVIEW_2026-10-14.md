# Upload review — `Valuation_Sequence_Review_2026-10-11.xlsx` (owner: "review this too", 2026-10-14)

Read-only. Nothing in the app changed because of this file. Every statement
below was checked against the repo at main `e782ce4` (2026-10-14). The
per-finding verdict tables with file:line evidence are in
`docs/upload_review_2026-10-14/`.

## 1. What the file is

| Fact | Reading |
|---|---|
| Size | 53 sheets, ~70,000 rows (3.0 MB). Produced by another AI tool on the owner's machine; "compilation completed 13 Oct 2026". |
| What it looked at | AM07 **as of Friday 10-09** — every AM07 file row pins git ref `fdcf2a2` (the PR #161 commit). It has NOT seen PR #162–#170: the MARS screens, the fundamentals time budget, Den §11 (valuation context) or Den §12 (the valuation chain shipped today). Also the owner's two other repos, the owner's local parameter workbooks (`/Users/sourceborn/Downloads/AM07 2/…`, 75 workbooks, 31 no longer on their disk), the sector resilience screen and the Tata Technologies IPO model. |
| What it produced | A 13-step valuation sequence (S01–S13), 21 sector routes, 17 valuation methods, 52 finance-term guards, 158 proposed parameter definitions, a 3,688-row parameter register, 405 findings, a review graph (173 nodes / 330 edges / 12,665 reachability rows), and a toy P/E calculator with 42 self-tests (all pass). |
| What it did NOT produce | **A single valuation number for any stock.** Its own "Company review" sheet marks all 14 supplied names `SOURCE_REQUIRED — no current directional verdict`; every rubric cell reads `SOURCE_REQUIRED`. The worked model is an "illustrative user case" (revenue 2,500 units, P/E 4). It states it did not run code, tests, the live app or Mongo. |
| Its own honesty | Good. "A parsed row is not a semantic review", "EXECUTABLE labels are not runtime proof", "no synthetic weights promoted", UNKNOWN kept apart from zero throughout. |

## 2. Accuracy of its extraction from our repo (spot-checked)

| Sheet | Check | Result |
|---|---|---|
| Current knobs (322 rows) | all 90 `Config` dataclass defaults vs `trading/config.py` on main | 90 of 90 fields present, 81 values identical, the other 9 the same values printed as JSON lists / factory expressions. No wrong number. |
| Runtime sequence (22 AM07 call-path edges) | engine order on main | Matches: breadth gate → gate pass (`cleared`, correlation gate inside) → rotation → `can_open` → throttle → open (`engine.py` 537 / 734–832 / 864 / 927 / 962 / 984). |
| Scoring flows | composite, momentum (`50 + 25 × mean(r126/σ, r252/σ)`), quality blend, trigger side score, slab/Kelly split, measured Kelly (25/25 prior) | Quoted correctly. |
| Document coverage | 44 AM07 docs "full text read" | Its findings quote exact lines from them. |

The extraction layer is reliable. The judgement layer was checked finding by finding, below.

## 3. Its findings about our code, verified one by one

405 findings; 255 name AM07. **106 are about things not in the live app** (the owner's local workbooks and docx files, the retired branch and modules deleted in August — `trading/auto.py`, `autoswap.py`, the USFDA calendar, the viewer account; the file itself marks them HISTORICAL/BRANCH_ONLY). List: `findings_historical_not_in_app.txt`.

**149 are about current code.** Each was checked against main by reading the code (three readers in parallel, then the highest-impact ones re-read by hand):

| Area | Rows | CONFIRMED | JUDGMENT (design choice) | Not in this repo (ASH08 paths) | Wrong |
|---|---|---|---|---|---|
| Money path (`trading/`) | 58 | 54 (5 with qualifiers: one describes no defect, two are off by default, one has no caller, one partly fixed by S-4) | 4 | 0 | 0 |
| Web layer (`web/`) | 18 | 17 (4 in part) | 1 | 0 | 0 |
| Scripts, data, docs | 73 | 33 | 2 | 38 | 0 (three sub-claims wrong, noted in the table) |
| **Total** | **149** | **104** | **7** | **38** | **0** |

Nothing was found "already fixed" because the review pinned 10-09 and the only later change in its scope (the fundamentals time budget, 10-12) closed just one case of XR-069.

### 3a. The ones that touch money, ranked (all CONFIRMED on main; nothing changed yet — owner's word)

| # | ID | What the code does today | Side | Fix size |
|---|---|---|---|---|
| 1 | **XR-060** | `danger.py:143-147` tests `fn(x) is True`; 8 of the 10 OHLCV tripwires return a NumPy boolean, which never passes that test (`_t0068/72/80/85/89/91/92/93`; only `_t0079/96` wrap in `bool()`). Reproduced: on a synthetic shock series five VERIFIED tripwires fire in `triggers.evaluate` and `danger.evaluate_stock` returns `[]`. **The sell-side guard ordered on 08-11 has only ever been able to act on 0079, 0096, 0094, 0095.** `/triggers` shows fires the danger layer ignores because the page converts with `bool()`. | EXIT (protective) | one line. Effect: stop tightenings and half-exits on held names start firing for the first time. |
| 2 | **XR-061** | `danger.externals_for` calls `externals_at(t)` with no date; the panel loader reads the committed zip (`_PANEL_ZIP`, last bar 2026-07-17) and cuts the index off at that date. 0094/0095 judge today's held names against mid-July peers and index. | EXIT (protective) | small: pass today's date and read the store's tails. |
| 3 | **XR-071** | `db._CONNECTED` is set only at connect time; refused writes never clear it; the entry halt (`scheduler._degraded_halt`) reads only that flag. **Entries kept opening while Atlas refused every save in September** — this is that episode, named in code. The banner shows the error but gates nothing. | BOOK | small: halt entries while `LAST_WRITE_ERROR` is set. |
| 4 | **XR-050** | `regime.fetch_regime` catches its own fetch failure and returns NEUTRAL with Everest on and reserve 0 (`regime.py:78-87`, `_neutral_fallback`). The C7 "sticky brake" in `engine.py:216-241` runs only if `fetch_regime` raises, which it does not. A dead feed in a panic re-enables Everest and drops the 40% reserve. | ENTRY | small: return a status flag, keep `last_regime` on failure. |
| 5 | **XR-041** | `ifr.py:210-232` returns `active=False, multiplier=1.0` on a thin reference panel before reading the saved fire dates; a data outage lifts an active 50% throttle. | SIZING | small. |
| 6 | **XR-026** | `risk.py:156,172-174` counts a NEW entry's heat at the flat `stop_loss_pct` (5%); the real ATR stop (up to 15%) is set later in `_open`. A buy can pass the heat cap at 5% and then carry a 15% stop. | SIZING | small: compute the stop before `can_open`. |
| 7 | **XR-052** | Rotation (`engine.py:864`) runs after the gate pass (S-4) but before `can_open` (927): kill switch, sector cap, heat, category room and the minimum size after throttling are checked only there. A winner can still be sold to fund a pick that is then refused. | EXIT→ENTRY | medium: dry-run `can_open` before selling. |
| 8 | **XR-051** | The correlation gate's held-returns table is built once before the pass (`engine.py:667-672`); same-pass fills are never added, so two correlated new picks both open. | ENTRY | small. |
| 9 | **XR-027** | `risk_v2.passes_liquidity`: an all-NaN 5-day volume tail gives a NaN mean, `NaN < 5 Cr` is False, the stock passes. Reproduced. | ENTRY | one line. |
| 10 | **XR-055** | `fii_net_last_n_days` sums the first 5 rows, not the last 5 trading days; mixes settlement and provisional bases; missing category = 0. | ENTRY (composite catalyst) | small. |
| 11 | **XR-056** | Ocean survival check caches fundamentals for the process life, picks debt/equity/FCF from independently chosen latest dates, NaN passes both tests. | ENTRY (Ocean) | small. |
| 12 | **XR-083** | Buy Desk sizes quantity on the raw quote, fills at quote × 1.0005, checks only cash — the batch can overspend the typed budget by up to 5 bps (₹12,500 on ₹2.5 Cr). | SIZING | tiny. |
| 13 | **ROOT-ROUTE-03** | Owner SELL accepts a typed price of `nan`/`inf` and `_close` removes the position before any check; manual add accepts a negative quantity. | EXIT | small validation. |
| 14 | **ROOT-AM07-04** | The 1-minute delayed fallback drops the candle timestamp; the price feeds entry fills and exit checks with no age check. | ENTRY/EXIT | small. |
| 15 | **XR-042** | Policy keywords match raw substrings ("EV" in "development", "API" in "capital"), always a positive boost, into the composite (the four non-momentum categories). | ENTRY | small. |
| 16 | **XR-054** | Pyramid adds (off by default, exposed in Settings) skip every risk gate and can push cash slightly negative. | SIZING (off) | leave off, or gate it. |

### 3b. Live bugs with no money impact that the owner will hit

| ID | What |
|---|---|
| **ROOT-ROUTE-07** | **Every Settings / Den algo-settings save on the live box is refused right now.** The form always posts the universe textarea; the cap is `max(100, universe_target_size)` = 1,200; the live list has held 1,205 names since the 09-30 rebuild (target + held + owner names) → `universe_too_big` on every save. Fix: skip the check when the textarea is unchanged, or cap at the current list length. |
| ROOT-ROUTE-06 | Settings save sets `cfg.mode` before validating the universe; a rejected edit leaves the in-memory mode changed. |
| XR-070 | The Mongo-recovery CONFLICT branch journals "NOTHING was overwritten" and then calls `p.save()` on the next line, which overwrites the Mongo book with the local one. |
| ROOT-ROUTE-01 | `/backtest` download: an existing but empty/partial Mongo window blocks the 12-year zip fill — bites now, the store was refilled with 1–2-year docs after 09-29. |
| ROOT-ROUTE-08 | OAuth callback: `if state and expected_state and state != expected_state` — a missing state value passes (app login still required). |
| XR-069 | Success ledgers stamped despite partial saves (FII/DII day, fundamentals month, constituents month, daily top-up). |
| ROOT-ROUTE-02 | A web backtest runs on the shared live `Config` object with no copy and no mode lock while the 09:20/14:30 rotation changes its mode. |
| ROOT-ROUTE-12/13 | Copy drift: factor cells go green at 60 while the help text says 62; momentum called "percentile-ranked" (it is absolute); quality described as profitability (it is volatility + liquidity); negative ages accepted on /risk and /register freshness checks. |
| XR-078 | The backtest sizes every trade at the fixed ₹1,25,000 (not the slab) and records the equity series before the final liquidation — known since 09-12 (CLAUDE.md said ₹1.5L; the code reads ₹1,25,000). |
| DOC-AM07-01 | `docs/ALGO_V1_V2.md`'s worked Kelly example still uses the floor-55 era; at floor 62 the quarter-Kelly is ≈ ₹68.6k, not ₹1,38,825 — the "Kelly matches your ₹1.25L" line no longer holds. |

### 3c. Studies (research only; no committed conclusion changes)

- XR-092: outcome windows cross the 2020-06-30 split in 1.6–3.1% of train rows (BT06 1,074 of 38,727; ash formula study 305 of 9,771). The leak favours the test half, and those results were test failures or "AM07 wins", so they stand.
- XR-091: ~0.4% of the 94,479 calibration windows are cut at the data end; baselines cut the same way; period-matched recompute moves 0060/0100/0101 by ≤0.05 and all stay REJECTED; 0094/0095 unaffected.
- XR-093: the param-search scripts shift each window by one month (a relabel, not a leak); v2's "GFC" validation window omits January 2008, which the optimiser saw.
- XR-094: the empty-PIT branch exists but never runs on committed data.
- XR-103: study resume caches are keyed by name only and pin only the entry floor — a rerun today under the same labels would not reproduce.

## 4. Its findings about the owner's two sheets, and what that means for today's code

The review found 24 defects in the sector screen (SR01–10) and the Tata model (TT01–14). The sector screen is not in the app. **The Tata model is: `trading/valuation/scenarios.py` reproduces that sheet cell for cell on the owner's order (164 cells, zero difference).** So four sheet findings are now findings about our code:

| ID | Sheet defect | In our code? | Reading |
|---|---|---|---|
| TT01 | Extra depreciation + interest (E118) is deducted from PAT **after tax**, in full, no tax shield | **Yes** — `E22 = (E19×E23/100 − E118) × (1 − Δtax/(100−t))` | Correct finding. ₹100 cr extra capex cuts PAT ₹24.5 cr where a deductible charge at 28.5% cuts ₹17.5 cr. Reproduced by order; fixing it changes the sheet's EPS 18.28. Owner's call. |
| TT02 | Debt-funded capex changes interest but never debt or cash (E35 = D35) | **Yes** | Correct. Same status. |
| TT03 | Section H sums nine coefficients into one P/E change; a total below −100% gives a **negative closing P/E** | **Yes** — `E134 = E133 × (1 + E132/100)`, no floor | Already labelled "the owner's ADDITIVE what-if" on every record; the negative-multiple case is real and unguarded. One-line floor. |
| TT12 | 100% bid discount / zero useful life divide by zero; the sheet's `IFERROR` prints 0 | **No** — `_Chain.f` catches `ZeroDivisionError` → DATA_NEEDED naming the cell | Handled. |
| TT06 | EPS on period-end shares, not weighted diluted | Yes (E28 = D28) | Stage 10 carries the bridge; the scenario column does not consume it yet. |
| TT07/08/09 | 1× volume / 45% delivery neutrals; FX rule of thumb; order revenue on top of growth | Yes, as sheet inputs | Printed as `SHEET_ASSUMPTIONS` with their row source. |
| SR01 | Missing inputs become neutral points (SAIL 50/100 with no data) | Not in the app; the same shape exists in the AM07 composite's neutral-50 fallbacks (recorded 10-12) | The valuation chain never does this. |

## 5. Its sequence, sectors and methods vs what shipped today

| Theirs | Ours (Den sequence / `trading/valuation`) | Gap |
|---|---|---|
| S01 source & identity, S02 classification, S03 routing | 00, 08 (`snapshot.py`: currency, units, point-in-time, lock hash), 09 (`route.py`) | Same spine. They gate on ISIN / legal entity; we key on the Upstox symbol. |
| S04 capital & ownership | 10 (`shares.py`) | They add 12 corporate-action branches (options, SARs, convertibles, partly-paid, trust-held shares) — not coded. |
| S05 normalisation | 11–12 (`normalise.py`) | Same. |
| S06 drivers & exposures | 13/14 placeholder + 17b context (sector β, rates) | They want FX, policy, tax and cross-sector paths as inputs (MAC001–015, IMP1–15); we store none of those feeds (said 10-12). |
| S07–S10 scenarios, branches, per-share bridge, reconciliation | 15–17, 17b | Same shape. |
| S11 separate rubric families | 18 (`assess.py`, `not_a_trade_permission: True`) | Same principle. |
| S12 portfolio constraints, S13 monitor & revalue | 19–24 | Only the live engine's gates exist; the re-run loop (23/24) is not built. |
| 21 sectors | 21 routes | Different cut: they split ER&D, SaaS, life vs general insurance, REITs, early-stage/loss-making, agriculture; we have hospitals, auto, cement, PSU overlay. **Our `ev_sales` family has no sector mapped to it.** |
| 17 methods | 7 families | Not coded as routes: FCFF/FCFE DCF, dividend discount, residual income, EV/EBIT, property cap-rate, REIT FFO/AFFO, pipeline rNPV, liquidation, replacement cost, reverse valuation. DCF and DDM exist as single terms only. |
| 158 proposed definitions | 52 terms | **48 already code**; of the 110 not, ~28 are formula-bearing and computable from stored statements (EBIT, PBT, FCFE, economic profit, residual income, incremental margin, operating leverage, CAGR/XIRR/MIRR/MOIC/payback, rights/buyback/split arithmetic, margin of safety); ~80 are gates with "no universal threshold" and no data source. |

## 6. What is worth adopting (all display or protective; owner's word needed for each)

1. The money-path fixes in §3a, in that order — #1 and #2 first (the danger layer is the owner's own measured-edge feature and it has been mostly blind).
2. The two live bugs in §3b that bite today (Settings saves refused; the recovery branch that overwrites the book).
3. TT01/TT02 in `scenarios.py` (owner decides: sheet-faithful or corrected) and the TT03 floor.
4. A sector mapped to the coded-but-unused `ev_sales` family (loss-making / early-stage / SaaS).
5. Reverse valuation (implied growth at the current price) as a diagnostic column; residual income beside the bank P/B.

## 7. Not adoptable or out of scope

- The graph machinery (nodes, edges, reachability, rule links, link control) is a review tool, not an app feature.
- The 58 AshStocks rows and the 38 ASH08-path rows are about the other apps — out of scope by the owner's 10-02 rule.
- The 3,688-row register is 2,979 rows of the owner's local workbook definitions + 202 of our implemented rules + 349 from the other apps + 158 proposals: an inventory, not 3,688 formulas.
- Its Tata figures from primary filings (FY26 revenue ₹5,505.57 cr, PAT ₹546.59 cr; Q1 FY27 revenue ₹1,664.6 cr, PAT ₹180.75 cr) agree with the owner's sheet; TATATECH is still absent from our statements zip (N200 only), so the chain reads DATA_NEEDED for it until the batch fetch is approved.
