# MARS — work status and pending list, 2026-10-14 (owner: "give me all work status and pending lists in table")

Every status read from the code and the live box on 2026-10-14 (main `e782ce4` + this
branch). "DONE" = merged to main and deployed. "CODE" = built and merged, display only,
no money impact. "BUILT (this PR)" = on PR #171, not merged yet. "NOT BUILT" = nothing
exists. Numbers in the task book of 2026-10-12 (39 rows) are kept; rows 40-47 are new
since then.

## A. The task book rows, status today

| # | Phase | Your item | Status 2026-10-14 | What is still pending | Needs from you |
|---|---|---|---|---|---|
| 1 | 0 | 42/45/46 MARS branding, welcome ribbon, thicker mark | DONE (PR #162, 10-12) | — | Display name: set `OWNER_NAME` in Render if you want a name other than the Upstox profile name |
| 2 | 0 | 37 Real-time date on dashboard | DONE — one IST clock (`web/clock.py`) | — | — |
| 3 | 0 | 38 Orange triangle legend + days held | DONE (/positions legend, days held) | — | — |
| 4 | 0 | 29 Purge + Refresh on dashboard | DONE | — | — |
| 5 | 0 | 1 Remove the Signal page | DONE (route, template, tests, links deleted) | — | — |
| 6 | 0 | 3 V1 / V1.1 / V1.2 docs clubbed | DONE (docs/MARS_LIVE_SET.md index; V1.3 stays the pinned record) | — | — |
| 7 | 0 | 5/7 Entries before 15 Sep out of sight | DONE as ARCHIVE, not delete (Mongo `trades` / `journal_archive` flagged pre_mars; /trades `?archive=1` shows them) | — | Confirm archive-not-delete is what you meant (built that way) |
| 8 | 0 | 9 Excel of all tasks | Delivered 10-12 (scratchpad); this file is the 10-14 version | Commit the xlsx under docs/ | Your word to commit it |
| 9 | 0 | 15 Universe moves recorded | DONE (`universe_ledger`, /scan batch card) | — | — |
| 10 | 1 | SEQ 05-08 Locked financial snapshot | CODE (`trading/valuation/snapshot.py`: currency lock incl. the ADR dollar trap, units, point-in-time, lock hash) — 200 of ~1,205 names have statements | Statements for the other ~1,000 names + NSE result dates | OK to batch-fetch 1,205 names' statements from Yahoo (~35 min/run) + NSE result dates |
| 11 | 1 | SEQ 07/10 Share-capital bridge | CODE (`shares.py`: split/bonus/rights/buyback bridge, ±3% reconcile, ESOP DATA_NEEDED) | Diluted count (ESOP pool) has no source | — |
| 12 | 1 | SEQ 08/11 Normalised earnings + quality | CODE (`normalise.py`: matches Yahoo's own normalised income to 0.0% on TCS) | — | — |
| 13 | 1 | 11 Company + parent/group linkage | NOT BUILT | Promoter-entity parse → group map | — |
| 14 | 2 | SEQ 09 Valuation basis by sector FIRST | CODE (`route.py`: your 21-row sector table verbatim; unmapped names read UNDECLARED with a nearest suggestion) | ~950 scan names still carry no sector label | OK to derive labels from the NSE industry file, or send your list |
| 15 | 2 | SEQ 13 Company / sector / market / macro conditions | PARTLY — stage 17b (sector β, rates via the discount rate, VIX, FII, breadth, damage, news) is LIVE; Brent / repo / CPI / GDP have no feed; 10y typed | The 4 macro fetches | OK for the 4 small macro fetches (Brent, repo, CPI, GDP) |
| 16 | 2 | SEQ 14 Thesis + falsifier | NOT BUILT (placeholder fields only) | Record round-trip | — |
| 17 | 2 | Post-result / order / cash-flow event loop | NOT BUILT (news tags results/orders; no versioned re-run) | Event triggers + version chain | — |
| 18 | 3 | SEQ 15-17 Scenarios → EPS → justified range | CODE (`scenarios.py`: your Tata Tech sheet reproduced cell for cell, 164 cells, zero difference; three cases; justified range per family) | Peers = same-route median (ASSUMED) | Peer groups per sector, or OK to derive from sector + size |
| 19 | 3 | Finance terms (ROI, IRR, NPV, WACC, ROIC …) | CODE (52 terms in `terms.py`) + now PRINTED with formulas in Den §13 | The review's extra 110 proposed terms (48 of 158 already exist) | Which of the extra terms you want |
| 20 | 3 | 19 Per-entry target and stop from the valuation | NOT BUILT — CHANGES EXITS, must be measured first | 191×12y exit study with valuation-derived levels | Accept the 5-year test window (statements start 2021) |
| 21 | 4 | 4 / SEQ 13-21 Valuation is the base; momentum is context | NOT BUILT — LARGE money impact; waits on the pre-registered test + your go | Pre-registered test (train ≤2020-06-30 / test after) | Go/no-go after the test |
| 22 | 4 | 2 Candle as one block | NOT BUILT | Timing block = structures + timing + volume | — |
| 23 | 4 | 17/32/33 Not buying in all segments; "no fund"; rotation | Shares 40/15/15/15/15 live since 10-04; the "why not today" block was built then REMOVED on your 10-12 order ("doesn't make sense") — the reader stays, one word puts it back | Buy Desk category | Which category Buy Desk buys count under (today: momentum) |
| 24 | 4 | 18 Rotation working? | Executes since 09-04 (ROTATION_EXIT journal); dashboard card shows the suggestions and says they execute | — | — |
| 25 | 4 | 21 Strategy weights editable + versioned | PARTLY — per-category weights live (10-04), shown in Den; sandbox drafts never touch cfg; no version log / train-test per version | Version log | — |
| 26 | 4 | SEQ 22-24 Decision record + URR | NOT BUILT (BUY journal carries config_snapshot, sizing, stop, target) | analysis_versions + decisions collections | — |
| 27 | 4 | 20/31 59,045 piano hits; candle + piano in one | Counted (1,205 × 49 lanes; ~4,820 placeholder-prone cells); NOT rebuilt | One candle lane + valuation lanes | — |
| 28 | 5 | 23/25/26/13/22/40/44 MARS Den | DONE — 12 sections live; §13 "Formulas & assumptions" BUILT (this PR) | — | — |
| 29 | 5 | 30 a-j Dashboard rebuild | DONE (PR #162) + your 10-12 trim (PR #163) | — | — |
| 30 | 5 | 28/41 Reports: dated watchlist capture | DONE (`watchlist_snapshots`, newest 120 + CSVs) | — | — |
| 31 | 5 | 35 Share the dashboard only | DONE (viewer role: /, /positions, /trades read-only) | — | Set `VIEWER_PASSWORD` in Render (= a restart; log in to Upstox after) |
| 32 | 5 | 43 1-2 international markets | DATA_NEEDED chip — no free feed reachable from Render (Yahoo blocked, stooq silent, 10-12 check) | A feed | Name a feed you have, or accept the chip |
| 33 | 5 | 12 Correction engine shown before it begins | DONE (Den Engines prints the inputs, "state reading, not a forecast") | — | — |
| 34 | 5 | 16 Trigger column not moving | PENDING on the memory lever — the trigger layer loads on top of the ~500 MiB rotation and the morning process is OOM-killed (5 mornings: 10-05/06/07/13/14) | — | Pick b (trigger layer after a cool-down) / a (shrink the four non-momentum passes) / c (Standard 2 GB) |
| 35 | 6 | memory Render 512 MB | PENDING (same as row 34); the 14:30 chain fits (peak 495-502), the 09:20 chain dies | — | Same pick |
| 36 | 6 | 34 Weekend push of heavy Mongo files to Drive | NOT BUILT | Saturday job → Drive | Drive service account + folder share |
| 37 | 6 | 39 DATA_NEEDED discipline | PARTLY — the valuation chain names the reason on every DATA_NEEDED; register items name the unlock; not yet the {source, path, last_try, owner_action} shape everywhere | Label shape | — |
| 38 | 7 | 8/24 Live trade provisions (orders, GTT, qty edits) | NOT BUILT — PAPER_ONLY lock stands | Order surface | Go-live word; Upstox app with order scope |
| 39 | 8 | 10/27 Many parameter mixes; intraday focus | NOT BUILT (harness exists per study) | Mix runner | Your mix list |

## B. New since the task book (10-12 → 10-14)

| # | Item | Status 2026-10-14 | Pending | Needs from you |
|---|---|---|---|---|
| 40 | Valuation context stage 17b ("both, indian sector only, discount rate — go") | DONE, LIVE (PR #167, 10-13): sector leg 1:1 and measured β, rates leg through the discount rate (−19.2% per +1pp), sentiment as a wider buy-discount, move decomposition | Sector store backfill (760 days) lands on the live box at the next 09:20/15:35 slot; before that, older-dated readings say DATA_NEEDED on the sector leg | — |
| 41 | Fundamentals time budget on the weekly block | DONE, LIVE (PR #164); first live run 10-13 | The weekly block STILL stalls after the 16:45 precompute — four evenings in a row (10-09, 10-12, 10-13, 10-14: 190 → 331 → 245 → 230 MiB flat, `last_rebuild` still 2026-09-30, store 2,233). The budget was not the culprit, or not the only one. Only the journal names the step (needs your login) | Word on the lever that does not need the journal first: a TOTAL time cap per weekly step (thread + join, journal DATA_REFRESH_STEP_TIMEOUT) |
| 42 | Valuation chain stages 07-18 as code + Den §12 | DONE, LIVE (PR #169 + #170, 10-14) | Stage 07 covers 200 of ~1,205 names (row 10); stage 14 placeholder; stage 21 not yet | Row 10's OK |
| 43 | Review of your Valuation_Sequence_Review_2026-10-11.xlsx | DONE read-only (docs/UPLOAD_REVIEW_2026-10-14.md, PR #171): 149 current-code findings checked one by one — 104 confirmed, 0 wrong | Three live faults it confirmed await your word: (1) danger layer reads `is True` on numpy booleans → 8 of 10 tripwires never fire on held names (XR-060); (2) Settings saves refused while the universe is 1,205 > 1,200 (ROOT-ROUTE-07); (3) entries can open while Mongo refuses writes (XR-070). Plus the Tata-sheet formulas TT01 (post-tax capex deduction), TT02 (debt line static), TT03 (no floor on the closing P/E) — keep as your sheet or correct | Fix / leave, per item |
| 44 | Den §13 Formulas & assumptions — every formula printed, every ASSUMED number editable | BUILT (this PR): 50 editable numbers (29 sheet sensitivities, 6 case deltas, 3 range, 5 verdict bands, 2 snapshot, 1 shares, 3 quality, 1 DCF cap) stored as a DRAFT in the settings doc, never in cfg; §12 recomputes with the draft and shows a DRAFT banner; 52 term formulas, 21 routes, 82 chain cells, family + stage rules printed | Merge after 17:45 IST on a quiet box | Your review of each formula (you said "further i'll review each") |
| 45 | 09:20 chain memory | Fifth morning OOM kill today 12:10 IST (peak 525 MiB; the catch-up stacked universe refresh + top-up + sector backfill before the rotation) | — | Row 34's pick |
| 46 | App sleeps when idle (free tier) | Unchanged: 09:20 lost today (asleep), 14:30 ran on a fresh process, the 16:30 block only runs when a probe wakes it | — | Render Instance Type → Starter (asked 5 times; "will consider") |
| 47 | Companion sheet (Blocks A-G) for the Tata model | Not received | — | The sheet |

## C. Open asks in one list (nothing builds until you answer; nothing is lost if you don't)

1. Memory lever: b / a / c (rows 34, 35, 45).
2. Weekly-block per-step time cap: yes / no (row 41).
3. Upload-review fixes: XR-060 danger `is True`, ROOT-ROUTE-07 Settings cap, XR-070 entries while writes refused; TT01 / TT02 / TT03 (row 43).
4. Batch fetch of ~1,000 names' statements + NSE result dates (row 10).
5. Sector labels for ~950 names: derive or your list (row 14).
6. The 4 macro fetches (row 15).
7. Peer groups or derive (row 18).
8. Accept the 5-year test window for valuation legs (rows 20, 21).
9. Buy Desk category (row 23).
10. International index feed (row 32).
11. Drive service account (row 36).
12. Go-live word (row 38). 13. Mix list (row 39). 14. Companion sheet (row 47).
15. Render Starter (row 46). 16. Commit the task-book xlsx (row 8). 17. Archive-not-delete confirmed (row 7).
