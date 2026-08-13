# External-trigger calibration (BT01-BT08) — run 2026-08-13

The 21 triggers wired on 2026-08-13, put through the SAME design as the
2026-08-10 OHLCV run so the numbers are comparable. Production code
throughout: `triggers.ext_fn` against `external_panels.PanelSet.externals_at`,
the live call with a past as-of date.

Panel: 191 tickers, 2981 bars
2014-06-30 -> 2026-07-17. Grid: every 5
trading days, 94,479 windows. Sectors: 17.

Event base rates per 10d window: RISE 32.1%, FALL 19.5%, REVERSAL_UP 3.8%, COLLAPSE 3.4%

## Verdicts
- VERIFIED (lift>=1.5, fires>=30): 2
- SUPPORTED (lift>=1.1):           1
- REJECTED (lift<1.1):             16
- NOT_ENOUGH_EVIDENCE (<30 fires): 2

Each trigger's own usable window is in the CSV (`first_eval`, `last_eval`,
`evaluations`) because the panels start on different dates — F&O open
interest only exists from 2024-07, delivery from 2019-06, while the
sector/RS family spans the whole panel. Read a verdict against its sample.

## Ranked by measured lift
- **T12W_0095** Index Risk Overlay [COLLAPSE] — lift 2.28, 3272 fires of 94479 evals, lead 3d → VERIFIED
- **T12W_0094** Sector Contagion Count [COLLAPSE] — lift 1.546, 6982 fires of 92950 evals, lead 3d → VERIFIED
- **T12W_0052** Peer Breakdown Lead [FALL] — lift 1.063, 15081 fires of 92950 evals, lead 4d → REJECTED
- **T12W_0051** Weak RS Before Fall [FALL] — lift 1.026, 23601 fires of 92947 evals, lead 5d → REJECTED
- **T12W_0101** Long Unwinding OI [FALL] — lift 1.0, 1924 fires of 15519 evals, lead 6d → REJECTED (F&O open interest only exists from 2024-07 in this repo)
- **T12W_0055** Industry Rotation Exit [FALL] — lift 0.999, 27324 fires of 92926 evals, lead 5d → REJECTED
- **T12W_0100** F&O OI Short Build [FALL] — lift 0.973, 4311 fires of 15519 evals, lead 6d → REJECTED (F&O open interest only exists from 2024-07 in this repo)
- **T12W_0053** Sector Divergence Warning [FALL] — lift 0.969, 11845 fires of 92950 evals, lead 5d → REJECTED
- **T12W_0056** Crowded Leader Fatigue [FALL] — lift 0.925, 416 fires of 92950 evals, lead 5d → REJECTED
- **T12W_0054** RS Failure At New High [FALL] — lift 0.892, 9195 fires of 92950 evals, lead 6d → REJECTED
- **T12W_0112** Institutional Sell Warning [FALL] — lift 0.489, 21 fires of 26008 evals, lead 14d → NOT_ENOUGH_EVIDENCE (bulk-deal source truncated at NSE's 70-row monthly cap — the panel reports DATA_NEEDED, so there is nothing to calibrate)
- **T12W_0039** RS Reclaim Before Price Break [REVERSAL_UP] — lift 1.061, 6215 fires of 92950 evals, lead 9d → REJECTED
- **T12W_0111** Institutional Bulk Deal Followthrough [RISE] — lift 1.559, 4 fires of 26008 evals, lead 1d → NOT_ENOUGH_EVIDENCE (bulk-deal source truncated at NSE's 70-row monthly cap — the panel reports DATA_NEEDED, so there is nothing to calibrate)
- **T12W_0050** Leader Rotation Early Signal [RISE] — lift 1.115, 12692 fires of 90183 evals, lead 4d → SUPPORTED
- **T12W_0059** Dollar Volume Rank Jump [RISE] — lift 1.099, 811 fires of 90121 evals, lead 4d → REJECTED
- **T12W_0048** Sector Breadth Lead [RISE] — lift 1.009, 40623 fires of 92950 evals, lead 4d → REJECTED
- **T12W_0046** RS Rank Jump 2 Weeks [RISE] — lift 0.992, 10216 fires of 92916 evals, lead 4d → REJECTED
- **T12W_0060** Delivery Spike Pre-Move [RISE] — lift 0.992, 13071 fires of 59195 evals, lead 4d → REJECTED (delivery percentages only exist from 2019-06 in this repo)
- **T12W_0047** Industry Peer Confirmation 3 of 5 [RISE] — lift 0.983, 53842 fires of 92941 evals, lead 4d → REJECTED
- **T12W_0049** Stock Holds While Sector Falls [RISE] — lift 0.946, 10730 fires of 92947 evals, lead 5d → REJECTED
- **T12W_0045** Stock RS Leads Sector 10D [RISE] — lift 0.842, 10696 fires of 92947 evals, lead 5d → REJECTED

## What this does NOT license
Nothing here moves money. The owner's own rule (F-006, and the
REQUIRES_15Y_BACKTEST column in their sheet) keeps the entry side out of the
money path; the danger layer that went live on 2026-08-11 is sell-side only
and was earned by the OHLCV run's strict-label verdicts. A VERIFIED tag here
is a candidate for that same treatment, on the owner's word, not an automatic
promotion.
