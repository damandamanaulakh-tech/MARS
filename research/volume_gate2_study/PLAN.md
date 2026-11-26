# Volume Gate 2 outside Intraday — test plan, written before any result was read

Committed 2026-11-27, before `scripts/volume_gate2_study.py` was run and
before any outcome was grouped by the volume ratio. The only things looked at
beforehand are the column lists of the two committed files and the fact that
their 191 ticker names match exactly.

## Why this test exists

Gate 2 shipped on this branch (VOL-001) refusing a buy in the **Intraday**
segment only, on the owner's instruction ("hard 1.5x spike, blocking Intraday
only"). Its release record states plainly that the gate has not been tested
against returns in any segment, and that extending it to Short, Swing,
Positional or Momentum is a test-first item. This is that test, for the one
segment the committed panel can measure: **Momentum**.

## The rule under test (the owner's; nothing to tune)

`trading.timing.passes_volume_spike` — the production function, called on the
panel's bars:

    volume[t] / mean(volume[t-20 .. t-1]) >= 1.5

The latest bar is not part of its own average. Fewer than 21 bars, a zero
base or an unreadable value is "unknown", never a pass. The 1.5 and the 20
are the owner's numbers, so there is no free parameter to choose on either
half of the data.

## Data (both committed, nothing fetched)

- `research/ash_formula_study/observations.csv.gz`: 23,507 month-end
  observations of the 191-stock, 12-year panel, built by
  `scripts/ash_formula_study.py` with the production scoring functions. Each
  row carries the AM07 score, the absolute-momentum flag and the outcome of a
  buy at that close under a fixed bracket (stop -5%, target +20%,
  200-session limit, close basis, first touch).
- `data/history_upstox_export_2026-07-19.zip`: the 191 x 12-year OHLCV panel
  the observation table was built from. The volume ratio is computed from it
  for each observation date, using bars up to and including that date only.

## Population

The same population as the VIX test, so the two are comparable: rows the
Momentum scan would have bought on score — `am07 >= 62`, `abs_ok` true, with
a resolved outcome. A row whose date is not a bar in the panel, or which has
fewer than 21 prior bars, is dropped and counted.

## What will be reported (all of it, whatever it shows)

1. Pass rate of Gate 2 across these picks — whole period and each half
   (to 2020-06-30, after) — i.e. how many buys the gate would refuse.
2. Mean outcome, share stopped and share reaching target, for the picks
   Gate 2 **passes** against the picks it **refuses** — whole period and each
   half.
3. The same three figures for the whole pick set (the gate off), so the
   comparison is against what the engine does today.
4. The ratio as a gradient, not one cell: the same figures by ratio bucket
   (under 0.75, 0.75-1, 1-1.25, 1.25-1.5, 1.5-2, 2-3, 3 and above), each half.
   A monotone gradient is evidence; one good cell is not.
5. Sensitivity at marks 1.2, 1.3, 1.5, 2.0: pass rate and mean outcome of the
   kept set, each half. (Reported, not chosen — see the reading rule.)
6. By calendar year: pass rate and the kept-set mean against the all-picks
   mean.

## Reading rule, fixed now

Extending Gate 2 to Momentum is supported only if, **at the owner's 1.5 mark
and in BOTH halves**, the picks it passes have a higher mean outcome than the
picks it refuses AND a higher mean outcome than the whole pick set. The
sensitivity table in item 5 is reported for completeness and must not be used
to pick a mark: choosing a threshold on the holdout destroys the holdout
(the T12W_0040 lesson, 2026-08-13). If either half fails, the result is
"not supported", the recommendation is to leave
`volume_gate2_block_modes = ("intraday",)` as shipped, and the owner decides.

## Limits known in advance

- Month-end entry points only; the live scan runs daily.
- The outcome bracket is the fixed -5% / +20% of the stored table, not the
  live ATR stop and ATR / valuation targets.
- **Momentum only.** The committed panel holds daily bars, so this says
  nothing about Intraday (5-minute bars, where the gate is live), and nothing
  about Short, Swing or Positional, whose own scores and floors differ.
- The panel holds today's survivors, which flatters every row equally.
- A gate can only remove buys. It cannot add one, so it can only help by
  removing worse-than-average ones.
