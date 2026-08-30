# Sector heat — does clustering justify exiting the whole segment?

Panel 191 names, evaluated 92,950 stock-days, split at 2020-06-30 (train <= split < test).

`k` = how many names in the stock's own sector had T12W_0094 (sector contagion) fire on that date.

## The claim being tested

If cutting the whole segment beats cutting the named names, then the NOT-FIRED holdings in a high-k sector must fall too. If their forward return is no worse than at k=0, the basket exit sells healthy positions for nothing.

## Not-fired names — the ones a basket exit would additionally sell

| half | k | n | mean fwd20 % | P(fall>=10%) | P(collapse>=20%) |
|---|---|---|---|---|---|
| train | k=0 | 33,738 | +1.22 | 0.099 | 0.018 |
| train | k=1 | 86 | -0.93 | 0.209 | 0.105 |
| train | k=2 | 152 | +1.24 | 0.158 | 0.066 |
| train | k>=3 | 1,155 | -1.44 | 0.209 | 0.090 |
| test | k=0 | 49,289 | +1.98 | 0.075 | 0.007 |
| test | k=1 | 141 | +1.46 | 0.099 | 0.028 |
| test | k=2 | 171 | +0.59 | 0.135 | 0.041 |
| test | k>=3 | 1,236 | +2.41 | 0.063 | 0.003 |

## Fired names — the incumbent per-stock signal, for scale

| half | k | n | mean fwd20 % | P(fall>=10%) |
|---|---|---|---|---|
| train | k=1 | 196 | -0.13 | 0.148 |
| train | k=2 | 280 | +1.00 | 0.154 |
| train | k>=3 | 2,481 | -0.38 | 0.175 |
| test | k=1 | 400 | +1.42 | 0.102 |
| test | k=2 | 442 | +3.20 | 0.086 |
| test | k>=3 | 3,183 | +1.92 | 0.074 |

## Verdict

Not-fired names, k>=3 vs k=0: train -1.44% vs +1.22% (gap -2.66pp), test +2.41% vs +1.98% (gap +0.43pp).

A basket exit is earned only if BOTH halves show the not-fired names materially worse in a hot sector. A gap that appears in train and vanishes in test is the curve fit this repo has already been bitten by (threshold sweep, 2026-08-13).
