# Volume Gate 2 outside Intraday — NOT SUPPORTED

Run 2026-11-27, `scripts/volume_gate2_study.py`, result in
`run_2026-11-27/` with its identity sidecar. The plan
([PLAN.md](PLAN.md)) was committed before the script was run.

**Verdict: do not extend Gate 2 beyond Intraday.** The rule shipped on the
PR234 branch stays as the owner set it:
`volume_gate2_block_modes = ("intraday",)`. The owner decides either way.

## The one number that settles it

The gate's own claim is that a buy on a volume spike does better than a buy
without one. On the Momentum picks of the committed 191-stock, 12-year panel
it does — **in the second half only, and the first half says the opposite.**

| Mean outcome of a buy | All picks | Gate 2 passes | Gate 2 refuses | Passes minus refuses |
|---|---|---|---|---|
| To 2020-06-30 (3,930 picks) | +2.213% | **+1.659%** | +2.385% | **−0.726 pts** |
| After 2020-06-30 (7,125) | +4.104% | **+4.555%** | +3.958% | **+0.597 pts** |
| Whole period (11,055) | +3.431% | +3.547% | +3.395% | +0.152 pts |

The whole-period line is positive only because the second half is larger and
richer. The reading rule fixed in the plan needs both halves; the first fails
both of its tests, so `supported: false` is recorded in the run file.

## The gradient disagrees with itself too

The plan asked for the ratio as a gradient rather than one cell, because one
good cell is not evidence. The two halves have opposite shapes:

| Volume ratio bucket | To 2020-06-30 | After 2020-06-30 |
|---|---|---|
| under 0.75 | +2.879% | +3.979% |
| 0.75 to 1 | +1.899% | +3.767% |
| 1 to 1.25 | +2.329% | +4.027% |
| 1.25 to 1.5 | +1.861% | +4.201% |
| 1.5 to 2 | +2.248% | +3.878% |
| 2 to 3 | +1.522% | +5.181% |
| 3 and above | **+0.700%** (the worst bucket) | +4.970% |

In the first half the biggest spikes are the worst buys; in the second they
are among the best. By calendar year the gate helps in 6 of 12 and hurts in
6, with no run of either.

## What it would cost

At the owner's 1.5 mark the gate refuses **76% of Momentum buys** (pass rate
24.2%, stable across both halves). So extending it would remove three buys in
four to gain an edge that is not there in half the record.

The sensitivity table (marks 1.2 / 1.3 / 1.5 / 2.0) is in the run file for
completeness. It is deliberately **not** used to pick a better mark: at 2.0
the second half reads +5.089% and the first half +1.187%, i.e. the same sign
flip, worse. Choosing a threshold on the holdout destroys the holdout
(the T12W_0040 lesson, 2026-08-13).

## Honest limits (all listed in the plan before the run)

- Month-end entry points; the live scan runs daily.
- The outcome bracket is the stored table's fixed −5% / +20% over 200
  sessions, not the live ATR stop and ATR / valuation targets.
- **Momentum only.** This says nothing about Intraday, where the gate is
  live on 5-minute bars and no stored history exists to measure, and nothing
  about Short, Swing or Positional with their own scores and floors.
- The panel is today's survivors, which flatters every row equally.
- A gate can only remove buys, so it can only help by removing
  worse-than-average ones. On this record it does not.

## Method notes

- Population identical to the VIX sizing test (`am07 >= 62`, `abs_ok`, a
  resolved outcome): 11,055 picks, none dropped — every observation date is
  a bar in the panel.
- The ratio is computed vectorised for speed and was proved equal to the
  production `trading.timing.passes_volume_spike` on **382 real (ticker,
  date) pairs** before any of it was used; the script exits on any
  disagreement.
- This is the eighth entry-side signal measured here and the eighth to fail;
  the pre-rise pair (sixth) and VIX sizing (seventh) are the two before it.
  The danger side remains the only part of this family that ever measured an
  edge.
