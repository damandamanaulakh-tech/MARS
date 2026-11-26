# Pre-rise: compression + volume ignition as one signal — measured 2026-11-26

Owner decision (26 November 2026): show compression (T12W_0005, 5 or more
inside bars among the last 10) and volume ignition (T12W_0057, five quiet bars
then a bar above 1.5x the 20-bar mean volume) as ONE signal, the ignition
within 5 trading sessions of the compression.

Each half was measured on 2026-08-09 (`research/bt_calibration/`). This run
measures the combination. Script: `scripts/pre_rise_study.py`.

## Method

- Panel: the committed 191 stocks x 12 years of daily bars
  (`data/history_upstox_export_2026-07-19.zip`). It holds today's survivors,
  so every rate below is flattered equally for the signal and for chance.
- Every trading day is evaluated (469,947 stock-days after a 300-bar
  warm-up), not the 5-day grid of the earlier run: the pair is rare.
- Signal day t: T12W_0057 fires on t and T12W_0005 fired on any of t-5 .. t.
- Label day d: close 20 sessions later is at least +10% (main) or +15%
  (strict) above close[d]. Hit: a label day in (t, t+10]. Chance: the same
  hit rule over every evaluated stock-day. Lift = hit / chance.
- Split at 2020-06-30. Nothing was tuned: the rule has no free number beyond
  the owner's 5 sessions, so there is no setting to choose on either half.
- The two conditions are evaluated with vectorised copies of the production
  formulas, proven equal to `trading.triggers._t0005` / `_t0057` on 6,000
  bar-by-bar checks before use. The halves measured alone reproduce the
  committed 2026-08-09 lift for T12W_0005 exactly (1.063 main, 1.146 strict).

## Result

| Signal | Labels | Half | Fires | Hit % | Chance % | Lift |
|---|---|---|---|---|---|---|
| Pre-rise (pair) | +10% | all | 373 | 29.49 | 32.11 | 0.919 |
| Pre-rise (pair) | +10% | to Jun 2020 | 238 | 31.51 | 31.63 | 0.996 |
| Pre-rise (pair) | +10% | after | 135 | 25.93 | 32.44 | 0.799 |
| Pre-rise (pair) | +15% | all | 373 | 17.16 | 17.15 | 1.001 |
| Pre-rise (pair) | +15% | to Jun 2020 | 238 | 18.49 | 16.83 | 1.099 |
| Pre-rise (pair) | +15% | after | 135 | 14.81 | 17.38 | 0.853 |
| T12W_0005 alone | +10% | all | 3,288 | 34.12 | 32.11 | 1.063 |
| T12W_0005 alone | +15% | all | 3,288 | 19.65 | 17.15 | 1.146 |
| T12W_0057 alone | +10% | all | 12,628 | 32.10 | 32.11 | 1.000 |
| T12W_0057 alone | +15% | all | 12,628 | 17.41 | 17.15 | 1.015 |

373 fires fall in 332 separate stock-months, so the count is not a few
episodes repeated.

Mean 20-session return after the pair: +1.88% against +1.59% for any day
(all years); +1.45% against +1.99% in the later half. In 2023, 2024, 2025 and
2026 (58 fires) no fire was followed by a +10% 20-session return from the
fire day itself.

## Reading

The pair is not better than chance. On the main labels it is slightly below
chance over the whole period and clearly below it after June 2020; on the
strict labels the small edge in the first half reverses in the second. The
compression half alone keeps a small edge in both halves (lift 1.08 / 1.05
main, 1.12 / 1.19 strict); adding the volume condition removes it.

This is consistent with the earlier finding that the rise-side triggers do
not predict rises. The signal is therefore shown as a label with this record
beside it, and is not used by any buy, size or exit rule.

Files: `pre_rise_calibration.csv` (every cell), `pre_rise_calibration.json`
(the same, read by `trading/pre_rise.py` for the on-screen record),
`pre_rise_fires.csv` (each fire with its 20-session return).
