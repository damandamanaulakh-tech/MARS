# Exit study, live era (floor 62) — Node 12 evidence refresh, 2026-08-24

Owner order "node 12" reopened the exit decision. The committed study
(docs/exit_variant_study_results.json, 2026-07-27) is era-pinned at entry
floor 55; the live preset has been 62 since 2026-07-30. This run replays
the three decision-relevant variants through the SAME study machinery
(same loader / make_cfg / real run_backtest / 191-ticker panel / 4
windows) with only the floor pin removed. Scripts:
`scripts/exit_study_live_era.py`; per-variant JSONs beside this file.

## Return per window (%, engine replay, real costs)

| window    | A −5/+12/t15 (live) | D −5/+20/t15 | D15 −5/+15/t12 | A @55 | D @55 |
|-----------|--------------------:|-------------:|---------------:|------:|------:|
| 2019      | −6.69 | **−5.65** | −5.80 | −2.37 | −1.80 |
| 2020-21   | +40.21 | **+42.81** | +38.61 | +40.77 | +46.41 |
| 2022-23   | +14.32 | **+23.30** | +17.97 | +13.43 | +21.18 |
| 2024-26H1 | +6.38 | **+9.44** | +7.23 | +5.61 | +8.90 |
| **TOTAL** | +54.22 | **+69.90** | +58.01 | +57.44 | +74.69 |

## The verdict survives the era change

D beats the live bracket in ALL FOUR windows at floor 62, exactly as it
did at floor 55 — the edge is not a floor artifact. Total gap at 62:
**+15.7pp over ~7.5 years** (was +17.3pp at 55). D15 sits between.

## What D changes in character (the honest trade-offs)

* **The trail comes alive**: D's trailing stop fired 8/56/31/48 times per
  window vs the live bracket's 4/9/2/11 (dead by arithmetic — it can only
  arm in the 0.24pp gap between +11.76% and the +12% target).
* **Fewer trades** (2,146 vs 2,891 over the four windows) — winners run
  instead of being cut at +12 and re-entered.
* **Lower win rate** (26–38% vs 32–42%): fewer quick +12% wins, more
  giveback from peaks; the money comes from the tail, not the hit rate.
* **Slightly deeper worst drawdown in one window**: 2024-26H1 −20.75 vs
  −18.93. Elsewhere DD is the same or better.

Caveats carried from the harness (same for every variant, so the
COMPARISON is fair even where levels are optimistic): survivorship-biased
panel (current members only), fundamentals neutral, no news; the owner's
50-name custom universe is not replayable offline — the floor is the only
era delta under test.

**No engine change ships with this evidence.** The owner's "+12 fixed"
(2026-07-27) stands until their explicit word.
