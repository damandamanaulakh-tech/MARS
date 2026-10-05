# Stop width (A) and re-entry wait (B), 2026-10-05

Owner order: "test A and B on the 12 year panel". These two fixes came out of
the 19-name replay of 2026-10-04 (a fixed −5% stop is less than one normal day
of movement for MTAR, and the app re-buys right after a stop).
Harness: `scripts/stop_cooldown_study.py`; raw rows are in this folder, one
JSON file per arm.

## What was tested

| Arm | Rule |
|---|---|
| base | live rule: fixed −5% stop, re-buy allowed the next day |
| A15 / A20 | stop = max(5%, k × ATR14 / price), k = 1.5 / 2.0, capped at 15% |
| B5 / B10 / B20 | after a STOP_LOSS exit, the same stock cannot be bought for 5 / 10 / 20 trading days |

Every arm runs through the same live-like replay:
- stops and targets checked against the day's low/high (live checks every 30 minutes);
- k × ATR targets;
- live floor 62;
- real costs;
- the committed 191-ticker panel.

## Pre-registered rule (written before any run)

1. Pick the A and B settings on TRAIN only (2015-01 → 2020-06).
2. A pick passes only if it beats base on total return in TRAIN **and** TEST (2020-07 → 2026-06), with worst drawdown no more than 2 points worse.

## Results: book return %, per window

| Arm | 2015-16 | 2017-18 | 2019-20H1 | 2020H2-22 | 2023-24 | 2025-26H1 | TRAIN | TEST |
|---|---|---|---|---|---|---|---|---|
| base | −2.44 | 4.51 | −1.19 | 8.33 | 11.99 | −0.99 | 0.88 | 19.33 |
| A15 | −2.13 | 5.01 | −1.14 | 8.63 | 12.30 | −0.69 | 1.74 | 20.24 |
| **A20** | −2.12 | 6.17 | −1.23 | 9.39 | 12.69 | −0.94 | **2.82** | **21.14** |
| B5 | −1.74 | 4.65 | −1.50 | 7.18 | 9.18 | −1.27 | 1.41 | 15.09 |
| **B10** | −0.63 | 4.05 | −1.15 | 5.45 | 8.75 | −1.47 | **2.27** | **12.73** |
| B20 | −0.84 | 4.07 | −1.04 | 5.16 | 8.31 | −1.41 | 2.19 | 12.06 |

Returns are on the whole ₹5 crore book. The backtest sizes every trade at the
₹1.5 lakh slab, so most of the book sits in cash and the percentages are small;
compare the arms with each other, not with Nifty.

| Arm | Half | Worst DD | Trades | Stops | Win % | Avg per trade % | Net ₹ |
|---|---|---|---|---|---|---|---|
| base | train | −4.66 | 2,548 | 1,886 | 22.5 | 0.14 | 4.4 L |
| base | test | −4.28 | 3,780 | 2,507 | 29.4 | 2.04 | 96.6 L |
| A20 | train | −4.74 | 2,085 | 1,349 | 27.8 | 0.54 | 14.1 L |
| A20 | test | −3.94 | 3,109 | 1,760 | 35.2 | 2.71 | 105.7 L |
| B10 | train | −2.84 | 2,088 | 1,506 | 24.0 | 0.43 | 11.4 L |
| B10 | test | −3.62 | 3,288 | 2,247 | 27.8 | 1.55 | 63.7 L |

## Verdict

**A (k = 2.0): EARNED.**
- Picked on train.
- Beats base in train (+2.82 vs +0.88) and in test (+21.14 vs +19.33).
- Better in 5 of 6 windows; lost 2019-20H1 by 0.04.
- Test drawdown is shallower (−3.94 vs −4.28); train drawdown is 0.08 deeper.
- k = 1.5 also beats base in both halves, and the wider setting is better in both, so this is a direction, not one lucky setting.
- Mechanism: 30% fewer stop-outs (1,760 vs 2,507 in test), and the win rate rises from 29.4% to 35.2%.
- The average stop is about 6.8% wide, so most stocks stay at the 5% floor and only the jumpy ones widen.

**B: NOT EARNED.**
- B10 was picked on train.
- It loses badly in test: +12.73 vs +19.33. All three cooldowns lose in test.
- It does cut drawdown (fewer trades, less money at work), but it gives up much more return.
- Mechanism: a re-entry right after a stop often catches the continuation of a strong trend (MTAR in the replay). Blocking re-entry blocks those as well.

## What A changes, if wired

- The stop on a volatile name moves from −5% to as much as −15%, sized at entry.
- Trade sizes do not change (slab sizing is untouched).
- **A losing trade on a jumpy stock can therefore cost more than today's −5%.** On average about 6.8% instead of 5%; at worst 15%.
- Nothing is wired yet. This needs the owner's word.

## Limits (same for every arm)

- Panel = today's survivors.
- Not modelled:
  - breadth gate;
  - category shares;
  - live slab ladder;
  - danger layer;
  - rotation;
  - Ocean.
- The backtest keeps the regime brake ON (live has it OFF).
- k = 2.0 was the wider of the two settings tested. Wider values were not tested. Choosing one now by its test result would destroy the holdout.
