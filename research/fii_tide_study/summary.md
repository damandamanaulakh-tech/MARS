# FII/DII tide study — can positioning data drive selection & scoring?

Owner ask (2026-08-25): "its not about getting in the app, its about to
use data for selection and scoring." Before market-level FII/DII data can
touch entries or scores, the owner's own proof ladder applies (the danger
layer earned its money-path place exactly this way). This run tests the
register's own FII signals — the same numbers the Upstox screenshot
shows — over the committed 13-year panels against forward Nifty returns.
`scripts/fii_tide_study.py`; full grid in `fii_tide_results.csv`.

Data: FII index-futures positioning 2012→2026-08-07 (both committed PWOI
panels), FII/DII cash 2007→2026-08-06. Train ≤ 2020-06-30, test after —
a signal must work in BOTH halves.

## Verdict: none of the seven signals earns a place in selection

| signal (20d horizon) | train edge | test edge | verdict |
|---|---:|---:|---|
| S1 FII extreme short (net < q10) — the FII-04 fire | −0.35pp | +0.63pp | halves flip sign — unstable |
| S2 FII crowded short (share > q80) — FII-05 | −0.64pp | −0.20pp | no edge, mildly inverted |
| S3 FII net falling 5d | −0.04pp | +0.18pp | nothing |
| S4 FII extreme long (net > q90) | −1.74pp | +0.57pp | halves flip sign — unstable |
| S5 DII cash 5d < −5,000 Cr | +2.00pp | +3.73pp | **see below — a 2020 artifact** |
| S6 FII cash 5d < −5,000 Cr | +1.07pp | −0.28pp | halves flip sign — unstable |
| S7 FII cash 5d > +5,000 Cr | −0.14pp | +0.04pp | nothing |

## S5 looked real until the episode count

74 fire-days deduplicate to **13 distinct episodes**. Six of the eight
test-half episodes are all the 2020 post-COVID melt-up (DII sold into a
rebound that ripped regardless), and the two most recent episodes (Feb
and Oct 2021) lost −0.3% and −3.3%. A signal whose out-of-sample proof is
one 2020 regime and whose latest fires lost is not an edge — the same
fault class as threshold 0033 in the sweep (great aggregate, dead on
honest inspection).

## This is the THIRD independent time this conclusion lands

1. The uploaded "IFR+FII alpha" workbook's own table: the edge lives
   entirely in the 2007-10 block, negative in 2 of the last 3 blocks —
   a DD-reduction overlay, not a return edge (uploads review 2026-08-09).
2. The owner's own prior calibration: the damage side (tail > 0.213,
   lift 1.91) is what worked — not direction prediction.
3. This run.

## One live consequence worth the owner's eye

The AM07 composite carries a LIVE FII-cash tilt today (±20 points on the
catalyst component — S6 in this table). S6 fails its holdout: +1.07pp in
train, −0.28pp in test. The composite drives swing/positional picks and
the score-decay exit, not momentum entries. The weights are the owner's
historic spec — nothing changed here; the finding is on the table for
their call.

## Where their data DID earn selection influence (already live)

The calibrated path stands: the 12 danger tripwires (their triggers,
strict-label VERIFIED) guard every held name, T12W_0095 reads the index
leg live since the Node 3 wire, and the canonical damage number journals
beside IFR. The Upstox FII/DII daily feed keeps accumulating a forward
archive — a re-test with years of THAT data (settlement-grade, daily,
per-segment) is possible later; today's committed history says: display
and context, not entry signal.
