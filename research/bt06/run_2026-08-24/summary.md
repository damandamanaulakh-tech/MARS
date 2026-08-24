# BT06 side-score recalibration — run 2026-08-24

Windows: 94,479 (train 38,727 to 2020-06-30, test 55,752
after). Labels/panel/grid identical to the 2026-08-09 main run; SAME
production trigger code. Weight mapping: max(0, train_lift - 1), zero
under 30 train fires (their BT08 bar); score shape
unchanged (weighted % of computable fired).

Weights: 38 of 79 triggers carry measured weight
> 0 (the rest measured at or below chance on train — weight 0).

## GATE_RISE_ENTRY_01 replay on TEST (rise>=65 & fall<35 & dq>=80,
-5/+12/20d bracket)

 formula  entries_test  win_rate_pct  expectancy_pct  worst_pct
 assumed             1           0.0           -5.00       -5.0
measured             8          25.0           -1.49       -5.0

Read score_calibration.csv for the full precision/recall/lift grid.
