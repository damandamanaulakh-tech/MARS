# VIX size multiplier — test plan, written before any result was read

Committed 2026-11-26, before the script below was run and before any outcome
was grouped by VIX. The only thing looked at beforehand is the column list of
the observation table.

## The rule under test (the owner's, 26 November 2026; nothing to tune)

Size of a new automatic buy is multiplied by the India VIX band:

| India VIX | Multiplier |
|---|---|
| below 12 | 1.15 |
| 12 to under 16 | 1.00 |
| 16 to under 20 | 0.80 |
| 20 to under 25 | 0.65 |
| 25 and above | 0.50 |

The rule has no free number, so there is nothing to choose on either half of
the data.

## Data (both committed, nothing fetched)

- `research/ash_formula_study/observations.csv.gz`: 23,507 month-end
  observations of the 191-stock, 12-year panel, built by
  `scripts/ash_formula_study.py` with the production scoring functions. Each
  row has the AM07 score, the absolute-momentum flag and the outcome of a buy
  at that close under a fixed bracket (stop -5%, target +20%, 200-session
  limit, close basis, first touch). That table was read in full, both halves,
  for the three-formula study; VIX was not part of it.
- `data/index_daily_15y.csv`: India VIX daily close.

## Population

Rows the Momentum scan would have bought on score: `am07 >= 62` and
`abs_ok` true, with a resolved outcome. VIX = the India VIX close on the
observation date (known before the next morning's scan). A row with no VIX
close on or within 5 days before its date is dropped and counted.

## What will be reported (all of it, whatever it shows)

1. Per VIX band: number of buys, mean outcome, share stopped, share reaching
   target — whole period, and each half (to 2020-06-30, after).
2. Return per rupee deployed: sum(multiplier x outcome) / sum(multiplier),
   against the plain mean outcome — whole period and each half.
3. Total result at changed deployment: sum(multiplier x outcome) against
   sum(outcome), and average multiplier (how much less or more is deployed).
4. Loss clustering: for monthly cohorts, the worst single month and the worst
   6-month run of summed outcomes, with and without the multiplier — whole
   period and each half.
5. By calendar year: rows 2 and 3.

## Reading rule, fixed now

The ladder is supported only if, in BOTH halves, (a) return per rupee with
the multiplier is at least the plain mean and (b) the worst 6-month run is
less negative with the multiplier, after dividing by the average multiplier
so that simply deploying less does not count as an improvement. If either
fails in either half, the result is "not supported" and the recommendation
is to leave the switch off. The owner decides either way.

## Limits known in advance

- Month-end entry points only; the live scan runs daily.
- The outcome bracket is the fixed -5% / +20% of the stored table, not the
  live ATR stop and ATR / valuation targets.
- Momentum segment only. No cash limit, segment share, Rs 1.5 lakh minimum or
  5% cap is modelled: each buy is one unit before the multiplier.
- The panel holds today's survivors, which flatters every row equally.
- Sizing by a market-wide reading cannot rank one stock over another; it only
  changes how much is at work when.
