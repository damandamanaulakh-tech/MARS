# VIX size multiplier — result, 2026-11-26

Plan: [PLAN.md](PLAN.md), committed before the script was run. Script:
`scripts/vix_sizing_study.py`. Rule: the owner's ladder
(`trading.sizing.vix_size_multiplier`) — x1.15 under 12, x1.00 for 12 to
under 16, x0.80 for 16 to under 20, x0.65 for 20 to under 25, x0.50 at 25
and above.

Population: 11,055 Momentum picks (score at least 62, own 6-month return
positive, resolved outcome) at month-end points from July 2015 to June 2026,
out of 23,507 observations; 178 picks with an unresolved outcome were left
out; no pick lacked a VIX reading.

## Result against the reading rule fixed in the plan: NOT SUPPORTED

| Check (each half must pass) | To June 2020 | After June 2020 |
|---|---|---|
| Return per rupee with the multiplier at least the plain mean | yes (+0.145 points) | **no (-0.071 points)** |
| Worst 6-month run less negative, per unit deployed | yes (-8.65 v -9.07) | **no (-25.63 v -23.15)** |

## Outcome of a buy by VIX band

Mean outcome of one buy under the table's bracket (stop -5%, target +20%):

| India VIX | Multiplier | Buys | Mean outcome | Stopped | Reached target |
|---|---|---|---|---|---|
| under 12 | 1.15 | 1,797 | +5.74% | 56.0% | 41.9% |
| 12 to under 16 | 1.00 | 4,837 | +2.46% | 69.6% | 29.3% |
| 16 to under 20 | 0.80 | 2,693 | +2.71% | 68.4% | 29.5% |
| 20 to under 25 | 0.65 | 1,325 | +4.22% | 62.6% | 36.4% |
| 25 and above | 0.50 | 403 | +7.07% | 50.9% | 47.9% |

The ladder assumes buys get worse as VIX rises. They did not: the best band
is 25 and above, where the ladder halves the size, and the 20 to under 25
band is the third best. Only the lowest band agrees with the ladder. By
half, the 20 to under 25 band was poor before July 2020 (-2.18%, 209 buys)
and good after (+5.42%, 1,116 buys); 25 and above was positive in both
(+4.48%, 66 buys; +7.58%, 337 buys).

## What the multiplier does to the total

| | Whole period | To June 2020 | After June 2020 |
|---|---|---|---|
| Buys | 11,055 | 3,930 | 7,125 |
| Mean outcome per buy | +3.431% | +2.213% | +4.104% |
| Per rupee deployed, with the multiplier | +3.423% | +2.358% | +4.032% |
| Sum of outcomes, plain | 379.4 | 87.0 | 292.4 |
| Sum of outcomes, with the multiplier | 346.4 | 86.9 | 259.5 |
| Average multiplier | 0.916 | 0.938 | 0.903 |

Over the whole period the ladder deploys 8.5% less and earns 8.7% less: it
changes how much is at work, not how well it works.

## The worst stretch

The worst six months of summed outcomes were July to December 2024 (-23.15).
India VIX at those month-ends was 12.8 to 15.6, so the multiplier was 1.00 in
every one of them: the ladder would not have reduced a single buy in the
worst run of the record. Before July 2020 the worst run (2015) was slightly
smaller with the ladder.

By year the per-rupee difference is between -0.49 and +0.20 points, negative
in 8 of 12 years (`run_2026-11-26/vix_sizing_by_year.csv`).

## Recommendation

Leave the VIX size switch off. The switch is built (`vix_sizing_enabled`,
default off) so the owner can turn it on; this result is what it would be
turned on against.

## Limits (as stated in the plan)

Month-end entry points only; the fixed -5% / +20% bracket of the stored
table, not the live ATR stop and targets; Momentum only; no cash limit,
segment share, minimum or cap modelled; a survivor panel. A market-wide
reading cannot rank one stock over another.

Files in `run_2026-11-26/`: `vix_sizing.json` (every figure and the reading
rule's four checks), `vix_sizing_by_band.csv`, `vix_sizing_by_year.csv`,
`vix_sizing.json.identity.json` (hashes of the code and data behind the
result).
