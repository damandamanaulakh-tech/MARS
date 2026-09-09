# Pre-drop calibration + the breadth-gate re-cut (2026-09-09)

Owner: *"as app it should have to predict what is there from data and give
inputs to exist or add before fall and rise, else everyone know what
happened"* → then **"fix it"**.

Scripts: `scripts/predrop_calibration.py`, `scripts/breadth_gate_recut.py`.
Panel: the committed 191×12y OHLCV + `index_daily_15y.csv` + FII cash + FII
index-futures positioning + the 10y yield. Split: train < 2020-07-01, test
from 2020-07-01. Nothing was fitted on the test half.

---

## 1. Can anything predict a market FALL? Almost nothing.

Event: Nifty forward-20-session return ≤ −5%. 25 candidate signals, judged
by lift over the same half's base rate, counted per *episode* (a signal on
for 30 days is one warning, not thirty).

**24 of 25 failed the holdout.** The pattern is the one this project has
now hit five times: the bigger the fitted-half score, the more complete the
holdout collapse.

| Signal | Train lift | Test lift |
|---|---|---|
| Momentum-crash setup (2y return < 0 and vol high) | 10.47 | 0.00 |
| Realised vol 10d > 1.5× 60d | 2.62 | 0.00 |
| Breadth < 40% (as a *drop* warning) | 2.62 | 0.00 |
| Defensives leading cyclicals | 2.24 | 0.00 |
| Nifty ATR 5d > 1.5× 60d | 2.46 | 0.68 |
| New lows − new highs > 20pp | 1.84 | 0.65 |
| **Distribution days ≥ 5 in 25** (the O'Neil rule) | 0.55 | 0.84 |
| **Breadth divergence** (index high, breadth rolling over) | 0.00 | 0.00 |
| Owner's damage cluster `tail_down3_5d ≥ 0.213` | 1.09 | 1.18 |
| FII index-futures net short and worsening | 1.69 | 0.73 |

Two readings worth keeping:

- **The owner's own damage cluster is COINCIDENT, not leading.** 1.09 and
  1.18 against chance is chance. It reports damage that has happened. It is
  wired as Ocean's rail R2, where it blocks dip buying; that is a design
  choice about risk, not a prediction, and the numbers should not be quoted
  as foresight.
- **The only survivor: the 10y yield rising ≥ 25bp in 20 days** (train 1.75,
  test 3.78, median lead 10–16 sessions). NOT wired, and the episode list is
  why: the holdout hits are almost all the 2022 rate shock, while the Oct-2022
  and Mar-2026 firings were followed by *rises* of 5–7%. Roughly six
  independent events in twelve years, from a file that ends 2026-06.

## 2. A slow-grind exit trigger: measured, failed, NOT wired.

"Name down X% over N sessions with no single-day shock" — the gap the danger
layer leaves, since its 12 tripwires only catch shocks (they fired on zero of
220 names while Nifty IT fell 10% in six sessions). Same labels/panel/split as
BT01-BT08: FALL ≤ −10%, COLLAPSE ≤ −20% over 20 sessions.

**All 12 variants failed.** Every one scores well in train and *below chance*
in test — a name that has bled steadily is LESS likely than average to fall
another 10%.

| Variant | Train FALL lift | Test FALL lift |
|---|---|---|
| down ≥ 20% over 15d | 6.70 | 0.00 |
| down ≥ 15% over 15d | 2.18 | 0.44 |
| down ≥ 12% over 15d | 1.59 | 0.80 |
| down ≥ 8% over 10d | 1.39 | 0.97 |

## 3. The breadth gate WAS pointed the wrong way. Re-cut and shipped.

The gate blocks momentum ENTRIES, so it must be judged on the forward return
of **the engine's own picks**, not the index. Picks rebuilt with the
production selection (blend score ≥ 62, absolute-momentum gate, timing gate),
540 weekly scan points 2015-07 → 2026-06, equal-weight forward 20 sessions.

**The live rule (block at ≤ 50%) is worth nothing, in both halves:**

| | Blocked days | Allowed days | Verdict |
|---|---|---|---|
| Train | +1.02% | +0.88% | blocks the BETTER days |
| Test | +2.03% | +2.05% | +0.02pp, i.e. noise |

The real shape is a **U**: both extremes pay, the middle does not.

| Breadth | Train | Test |
|---|---|---|
| 10–20% | +3.36% | +4.40% |
| 20–30% | +1.80% | +0.74% |
| 40–50% | −5.52% | +1.97% |
| 50–60% | +1.16% | +0.33% |
| 60–70% | −1.20% | +1.04% |
| 80–90% | +1.30% | +3.40% |

Band edges chosen on TRAIN only (the 2026-08-13 rule). Train picked
**40–65%**: train edge +3.54pp, **holdout +1.61pp**. Pick quality on allowed
days: train +1.52% vs +0.97% ungated, test +2.61% vs +2.04% ungated.

**Robustness: 33 of 33 candidate band pairs are positive in BOTH halves.**
A plateau, not a lucky cell. By contrast every single-threshold "block below
X" rule is negative in train for X ≤ 45%.

### Bug found in my own first cut

The first run put newly-listed names (price, but no 200-bar mean yet) in the
breadth *denominator* and never in the numerator, crushing early-year breadth
toward zero and inventing a fake washout regime. Caught before any decision
was taken; both scripts now count only names that HAVE the moving average.
Same fault class as the Node 10 weak-assertion lesson.

### Caveat, stated

Band edges are sample-dependent. This study measured on the 191-name panel;
the live gate reads the Mongo store (~573 names, growing toward ~2,400) or
falls back to the ~50-name scan bucket. The gate keeps its `min_sample` guard
and still fails OPEN when no reading is available.
