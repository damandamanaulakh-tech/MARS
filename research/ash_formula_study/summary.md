# Item 5 — the three-formula combination: MEASURED, NOT EARNED

**Owner order (2026-09-10, item 5):** *"adopt all 3 formula n show me here how it
could with there 3 and your own (in own add all old new for review) be, more
formula means for filters - wire it in last after discussion and confirmation
from me."* Then, 2026-09-11: *"go with ash formula wiring."*

This is the discussion half. **Nothing was wired.** The evidence says the two
outside formulas do not earn a place in the money path, and one of them —
AshStocks' actual live selection rule — would have **cost** money in both halves
of the test.

Run: `python scripts/ash_formula_study.py` · 191×12y committed panel ·
146 month-end observation points · **23,507 observations** ·
train ≤ 2020-06-30, test after · outcome = realised return under the **live
bracket** (−5% stop / +20% target / 200-day max hold, close basis, first touch).

---

## 1. Do the formulas actually disagree with ours?

| pair | Spearman ρ | reading |
|---|---|---|
| AM07 score vs **ASH08** score | **+0.820** | mostly the same opinion |
| AM07 momentum leg vs ASH08 | +0.858 | ASH08 is a plain 6-month-return scale |
| AM07 score vs **AshStocks 20-day rank** | **+0.299** | genuinely a different opinion |
| AM07 score vs **AshStocks python brain** | **+1.000** (measured 2026-09-10) | *the same formula* |

**AshStocks' python brain is AM07's formula.** Read from its own `lib/`: momentum
= `50 + avg(r6/v6, r12/v12) × 25`, quality = `(low-vol + liquidity)/2` — byte-for-byte
the arithmetic in `trading/strategies/nifty200_momentum.py` and `trading/quality.py`.
A formula that ranks identically cannot filter anything. It is not tested further
here; AshStocks' **live** rule (`SEL_MOMENTUM_20_TOP10`, a 20-session momentum rank
with no floor) is tested in its place.

**ASH08's floor is a gate AM07 already has.** Its score is
`0.65 × clip(50 + mom6×200) + 0.35 × quality`, where its "quality" is
`100 × n_bars / 126` — **data coverage, not company quality** (all 191 names carry
100.0 in its own committed snapshot). So its floor of 68 solves to
*"6-month return above +0.385%"*, which is AM07's absolute-momentum gate
(`own 6m return > 0`) with a rounding difference.

Measured: of the **11,233** AM07 candidates, ASH08's floor passes **97.0%** and
removes **334**. It is a near no-op.

---

## 2. As an extra filter on AM07's own picks

Realised return under the live bracket. `edge` = versus AM07 alone.

| filter | n train | ret train | **edge train** | n test | ret test | **edge test** |
|---|---:|---:|---:|---:|---:|---:|
| (none) AM07 alone | 3,930 | 2.21% | — | 7,125 | 4.10% | — |
| + ASH08 ≥ 68 (their floor) | 3,792 | 2.16% | −0.05 | 6,938 | 4.11% | +0.00 |
| + ASH08 ≥ 75 | 3,387 | 2.06% | −0.15 | 6,331 | 4.16% | +0.06 |
| + ASH08 ≥ 80 | 3,017 | 2.05% | −0.16 | 5,746 | 4.27% | +0.16 |
| **+ 20d top 10% (AshStocks' live rule)** | 589 | 1.44% | **−0.77** | 1,051 | 3.65% | **−0.45** |
| + 20d top 25% | 1,366 | 1.87% | −0.34 | 2,329 | 3.90% | −0.21 |
| + 20d top 50% | 2,481 | 2.05% | −0.17 | 4,245 | 3.96% | −0.15 |
| + 20d BOTTOM 50% | 1,456 | 2.50% | +0.28 | 2,896 | 4.34% | +0.23 |
| **+ 20d BOTTOM 25%** | 570 | 2.70% | **+0.49** | 1,253 | 4.60% | **+0.50** |
| + ASH08≥75 AND 20d top 25% | 1,258 | 1.74% | −0.47 | 2,188 | 4.06% | −0.05 |

**ASH08 fails honestly.** Its best-looking row (≥80, +0.16 test) is **negative in
train** (−0.16), so it would never have been selected. Choosing it by the holdout
destroys the holdout — the exact error corrected on T12W_0040 (2026-08-13).

**AshStocks' live rule is negative in BOTH halves** (−0.77 train, −0.45 test).
Buying the most-extended names makes our picks worse, consistently.

**Its inverse is a clean monotone gradient, in both halves:**

| bucket | edge train | edge test |
|---|---:|---:|
| top 10% (extended) | −0.77 | −0.45 |
| top 25% | −0.34 | −0.21 |
| top 50% | −0.17 | −0.15 |
| bottom 50% | +0.28 | +0.23 |
| bottom 25% (pulled back) | +0.49 | +0.50 |

Monotone at every rung, same sign, near-identical magnitude out of sample — the
"gradient, not a lucky cell" standard that earned T12W_0066 its place.

**The mechanism is legible** (pooled): a name that has run +16.8% in 20 days hits
the −5% stop **68.4%** of the time and reaches target 30.6%; a name that has pulled
back −5.9% hits the stop **63.4%** and reaches target **35.4%**. An extended name is
simply nearer to a 5% pullback. *Note in passing: under the live −5%/+20% bracket
about two-thirds of entries touch the stop first. That is a property of the owner's
own Node-12 exit decision, not a finding of this study.*

---

## 3. Could a combination replace our score?

Top 30 each month by each combined rank:

| combination | ret train | ret test | edge test |
|---|---:|---:|---:|
| AM07 alone (live) | 2.57% | 4.17% | — |
| AM07 + ASH08 (50/50) | 2.54% | 4.12% | −0.05 |
| AM07 + AshStocks-20d (50/50) | 2.74% | 4.08% | −0.09 |
| all three equal (⅓ each) | 2.35% | 4.16% | −0.01 |
| AM07 60 / ASH08 20 / 20d 20 | 2.42% | 4.24% | +0.08 |
| AM07 + **inverse** 20d (50/50) | 2.55% | 4.25% | +0.08 |

Every combination sits within ±0.09pp of AM07 alone. **Combining the three
formulas adds nothing.** The two marginally positive rows are *below* AM07 on
train, so neither would be selected honestly.

Ranking by each score alone, top 30/month: AM07 2.57 / **4.17**, ASH08 2.69 / 3.87,
20-day 2.00 / 3.51. **AM07 wins the holdout.** ASH08 wins train only — the
classic shape.

---

## 4. Robustness on the one survivor — and why it still does not get wired

The bottom-25% filter passes the split. It does **not** pass the bar that killed
the sector-heat basket and FII-tide S5.

**Year by year: positive in 7 of 12 years.** 2022 −1.54, 2025 −1.13, 2026 −1.02.

**Month by month, equal-weighted** (so one busy month cannot carry the result):

| half | months | mean edge | **median edge** | months positive |
|---|---:|---:|---:|---:|
| train | 55 | +0.30pp | **+0.08pp** | 52.7% |
| test | 71 | +0.93pp | **−0.04pp** | **47.9%** |

On the test half the filter helps in **fewer than half of months**; the positive
mean comes from a handful of stretches, chiefly 2020 (+4.13pp). Strip 2020 and the
pooled edge falls +0.50 → +0.30pp; 2022-onward it is +0.25pp.

**And it starves the book.** Candidates per month fall from a median of **76 to 11**,
with **22 of 133 months under 5 names**. Discarding 85% of the candidate pool to
chase ~+0.3pp per trade — at a ₹5 crore book with a 222 ceiling — is a bad trade in
itself.

---

## Verdict

| formula | verdict |
|---|---|
| AshStocks python brain | **Identical to AM07** (ρ +1.000). Nothing to adopt. |
| ASH08 score | **Not earned.** ρ +0.82; its floor duplicates our absolute-momentum gate and removes 3% of candidates; every threshold is negative in train. |
| AshStocks live 20-day rule | **Rejected — measured negative in both halves** (−0.77 / −0.45). Wiring it as built would cost money. |
| Its inverse (prefer pulled-back names) | **Real but small.** Clean monotone gradient holding out of sample, legible mechanism — but month-median ≈ 0, positive in 7/12 years, and as a hard filter it cuts candidates by 85%. **Not recommended as a filter.** Recommended only, if at all, as a ranking tie-break that discards no candidates. |

Fifth independent time a rise/entry-side signal has failed its holdout on this
repo (v1 OOS, BT01-BT08, threshold sweep, FII tide, pre-drop calibration). The
measured edge in this project keeps landing on the **danger/exit** side, never on
entry timing.

**No engine, risk, order or danger module was touched by this study.**
