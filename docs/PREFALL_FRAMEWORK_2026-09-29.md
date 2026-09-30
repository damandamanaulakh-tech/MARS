# Pre-fall indication framework — measured on the Jun–Sep 2026 slide

Owner ask (2026-09-29): "the framework for pre fall indications so i can
incorporate in the code in future … no code writing but share the pre fall
indications parameter … may be we can find few new parameters, some
corrections in existing formula which can make roadmap of pre fall for
this crash."

This is a DESIGN document. No code, no config, no behaviour change. Every
number below was computed on 2026-09-29 from live tokenless Upstox daily
candles (242 stocks = the committed 191-name panel + the 83-name owner
list; 17 indices), the NSDL FII archive, Yahoo (Brent, US 10y, USDINR)
and tradingeconomics (India 10y). Scratch scripts: `crash_idx.py`,
`crash_stocks.py`, `prefall_params.py` (session scratchpad, not
committed — the numbers are what matter and they are all here).

Rule that governs everything below: **a parameter enters the money path
only after the owner's REQUIRES_15Y_BACKTEST test on the committed
191×12y panel with a train/test split.** The 2026-09-09 pre-drop study
already rejected 24 of 25 candidates on the holdout, INCLUDING breadth
divergence and distribution days. The parameters marked NEW here were not
in that study. Expect most to fail; the point is to find the two or three
that do not, and to test the LADDER (several gauges in sequence with a
confirmation step), which was never tested either.

---

## 1. What this fall actually was (corrected)

| Date | Nifty | Event |
|---|---|---|
| 2026-01-02 | 26,328.55 | 52-week high |
| 2026-06-17 | — | Close below 200DMA. **Never reclaimed since.** |
| 2026-07-06 | 24,430 | June–July high |
| 2026-07-24 | 23,767 | July dip low |
| **2026-08-03** | **24,774.30** | Rally high. 200DMA that day = 24,776.32. **Stopped 2 points under the 200DMA and reversed.** A lower high vs January (−5.9%). |
| 08-04 → 09-11 | −5.5% | The grind: 28 sessions, 20 red, none worse than −0.86% |
| 09-15 / 09-24 / 09-28 | −1.19 / −1.64 / −1.56% | The break. 22,780 = −8.05% from the rally high, −13.5% from January, −6.6% under the 200DMA |

India VIX: 10.4 to 13.6 through the whole period; its 120-day p90 is
18.67. **No fear gauge moved.** This was not a shock; it was a failed
reclaim followed by distribution. Shock gauges (VIX, the damage cluster,
the danger tripwires, IFR's −3% day) are the wrong instruments for it and
they duly said nothing until the break.

---

## 2. Timeline — when each gauge first tripped

"From rally high" = Nifty vs the 3 Aug close on that day. Trips before
3 Aug fired in the JULY dip and were then RESET by the 3 Aug rally; that
whipsaw is the central design problem (see §5).

| First trip | Gauge | Reading | Nifty from rally high |
|---|---|---|---|
| 06-17 | Close below 200DMA (regime CORRECTION) | — | (before) |
| 07-20 | Distribution days ≥ 6 of 25 | 6 | −6.6% (July dip) |
| 07-23 | Breadth decay: stocks >20DMA fell ≥25 pts from 20d max | 69→38% | −8.0% (July dip) |
| 07-23 | Sectors above own 50DMA ≤ 6 of 12; above 20DMA ≤ 3 | 6 / 3 | −8.0% (July dip) |
| 07-23 | US 10y +25 bp in 20 sessions | 4.47→4.69 | (July dip) |
| 07-16, 07-23 | USDINR at a 120-day high (rupee low) | ~95.6 | (July dip) |
| **08-03** | **Failed reclaim: rally closes within 0.5% of the 200DMA from below, next day reverses** | 24,774 vs 24,776 | 0.0% |
| 08-03 | Leader reversal: top-3 sectors of last 25d (IT +17.3, Realty +11.5, Auto +10.4) are all ≤0 over the next 10d | IT −2.9, Realty −0.5, Auto 0.0 | 0.0% (known 10 days later) |
| 08-10 → 08-17 | Owner's dbi (dispersion ÷ \|EW return\|, 5d) above its 250d p90 (12.2) while Nifty within 3% of its rally high | 19 → 28 | −4.8 to −5.6% vs Jan; −0.8 to −2.0% vs 3 Aug |
| 08-18/19 | Breadth decay ≥25 pts again (69 → 43%) | 43% | −2.5% |
| 08-27 | A/D line makes a 20-day low while Nifty within 3% of rally high | −1,078 | −2.8% |
| 08-27 | Down-volume days: up-vol/down-vol < 0.5 on 3 of last 10 | 0.26 / 0.33 / 0.47 | −2.8% |
| 08-27 | Small-cap divergence: SML100 20d return ≥ +5 pp over Nifty while Nifty < 200DMA | +5.0 pp | −2.8% |
| 08-31 | New 52w lows ≥ 10 | 11 | −2.8% |
| 09-01 | Sectors above own 20DMA ≤ 2 of 12 | 2 | −2.9% |
| 09-07 | Names down ≥10% in 20d ≥ 8% of panel | 8.7% | −4.0% |
| 09-09 | Distribution days = 7 of 25 | 7 | −5.4% |
| 09-10 | Stocks above 50DMA < 35% | 35% | −5.2% |
| 09-11 | New lows > new highs on 3 consecutive days | 19 vs 3 | −5.5% |
| 09-15 | Danger tripwires fire on 4.5% of the panel (11 of 242) | 11 | −6.7% (the break) |
| 09-15 | Brent peaks at 108.75 (+25% since 3 Aug) | 108.75 | −6.7% |
| ~09-22 | FII 10-day net ≤ −10,000 Cr | −14,781 | −5.8% |
| never | Damage cluster tail_down3_5d above its OWN 250d p90 (0.70) | max 0.66 | — |
| never | India VIX above 120d p90 (18.67) | max 13.6 | — |
| unknown | India 10y +25 bp in 20 sessions (the 09-09 study's one survivor) | +21 bp over the past month to 7.17% | DATA_NEEDED: no daily series reachable; committed file ends 2026-06-08 |

FII cash (NSDL, report-date shifted): +12,460 Cr in the run-up (Jul 1 →
Aug 3), −588 Cr in the grind (5-day minimum −15,116 around Aug 31 →
Sep 4), −13,711 Cr in the 9 sessions since Sep 12. **Flows followed the
fall; they did not lead it.**

---

## 3. The parameter set

Columns: ID · name · formula · data the app already stores (Y/N) · what
it read here · evidence status · proposed role. Roles: STATE (sets the
regime), WARN (stop adding), REDUCE (cut exposure), BREAK (confirms the
fall has started), ADD (re-entry).

### A. Structure

| ID | Name | Formula | Stored | This episode | Evidence | Role |
|---|---|---|---|---|---|---|
| PF-01 | Failed reclaim | Nifty below 200DMA ≥ 20 sessions; a rally close within 0.5% of the 200DMA from below; the next 3 sessions close lower than that rally close. | Y (index_daily + regime) | 3 Aug: 24,774 vs 24,776, reversed 4 Aug | **NEW — not in the 09-09 study** | STATE → WARN |
| PF-02 | Lower-high count | Each swing high (20-day max) below the previous swing high while the 52w high is > 120 sessions old. | Y | Jan 26,328 → Jul 6 24,430 → Aug 3 24,774 (lower than Jan, higher than Jul 6 — a bear-market bounce) | NEW | STATE |

**Correction to the existing regime module:** `regime.classify` knows only
above/below 200DMA and the 52w-high distance. It has no EVENT for the
reclaim attempt failing, which is the single most informative moment in
this whole episode. CORRECTION has been true since 17 June; a flag that
is true for 100 days carries no timing.

### B. Participation (breadth)

| ID | Name | Formula | Stored | This episode | Evidence | Role |
|---|---|---|---|---|---|---|
| PF-03 | Breadth decay | % of panel above its 20DMA, drop from its own 20-session max ≥ 25 points, while Nifty is within 3% of its 20-session high. | Y (scan panel / store) | 69 → 43% by Aug 19 with Nifty −2.5% | NEW (the study tested LEVEL divergence, which failed; this is the RATE) | WARN |
| PF-04 | Intermediate breadth | % above 50DMA < 50% (warn) / < 35% (reduce). | Y | 49% Aug 24; 35% Sep 10 | untested | WARN / REDUCE |
| PF-05 | New lows | 52w new lows ≥ 10 of the panel (warn); new lows > new highs 3 consecutive days (reduce). | Y | 11 on Aug 31; 19 vs 3 on Sep 11 | untested | WARN / REDUCE |
| PF-06 | Sector participation | Count of the 12 sector indices above their own 50DMA ≤ 6 (warn) / ≤ 3 (reduce); above 20DMA ≤ 2 = reduce. | Y (sector_index store) | 6 on Jul 23; 5 on Sep 1; 2 on Sep 8 | NEW | WARN / REDUCE |
| PF-07 | Casualty share | % of panel down ≥ 10% over 20 sessions ≥ 8%. | Y | 8.7% on Sep 7 | untested | REDUCE |
| PF-08 | A/D line lower low near the high | Cumulative (advances − declines) makes a 20-session low while Nifty is within 3% of its rally high. | Y | Aug 27 at −2.8% | NEW (classic, but never measured here) | WARN |

**Correction to the breadth gate:** the live gate is a LEVEL band on the
200DMA count (block inside 0.40–0.65). Through this whole grind that
reading sat at 59–64% and blocked entries — correctly, but only because
the band happened to cover it. The 200DMA count is the slowest breadth
ruler there is (it fell from 64% to 49% over five weeks while the 20DMA
count fell from 69% to 23%). For a pre-fall gauge the 20DMA and 50DMA
counts and their RATE of change are the rulers; the 200DMA level stays as
the entry band it was measured for.

### C. Volume

| ID | Name | Formula | Stored | This episode | Evidence | Role |
|---|---|---|---|---|---|---|
| PF-09 | Distribution days | Nifty down ≥ 0.2% on panel volume higher than the prior day; count over 25 sessions ≥ 6. Index volume is 0 on Upstox, so the panel's summed volume is the proxy. | Y | 6 on Jul 20; 7 on Sep 9 | **REJECTED on the 12y holdout (0.55 / 0.84 lift = below chance)** | none — listed so it is not re-proposed |
| PF-10 | Down-volume days | Up-volume ÷ down-volume < 0.5 on ≥ 3 of the last 10 sessions. | Y | Aug 19 0.26, Aug 27 0.33, Aug 26 0.47 → tripped Aug 27 | NEW | WARN |

### D. Leadership and rotation

| ID | Name | Formula | Stored | This episode | Evidence | Role |
|---|---|---|---|---|---|---|
| PF-11 | Leader reversal | The top-3 sectors by trailing 25-session return all return ≤ 0 over the next 10 sessions. Known with a 10-day lag by construction. | Y | Aug 3 leaders IT/Realty/Auto → −2.9 / −0.5 / 0.0% | NEW | WARN |
| PF-12 | Small-cap divergence | SML100 20-session return minus Nifty 20-session return ≥ +5 pp while Nifty < 200DMA. | Y | +5.0 pp on Aug 27; SML100 peaked Sep 8, five weeks after Nifty | NEW | WARN |
| PF-13 | No shelter | FMCG and Pharma both below their 50DMA while Nifty < 200DMA (defensives not defending). | Y | FMCG −9.8% in the grind; Pharma held (+0.4%) — half-tripped | NEW | REDUCE (confirming, not leading) |

### E. Dispersion — the owner's own gauge, re-purposed

| ID | Name | Formula | Stored | This episode | Evidence | Role |
|---|---|---|---|---|---|---|
| PF-14 | Churn under a flat index | dbi (cross-sectional dispersion ÷ \|equal-weight return\|, 5-day mean — the PX-01 definition recovered 2026-08-11) above its 250-session p90 (12.2 here) while Nifty is within 3% of its 20-session high. | Y (register PX-01/05 already computes dbi) | 19 → 28 on Aug 10–17, index flat: money rotating OUT of names while the index hid it | NEW as a timing gauge (PX-01 is display-only today) | WARN |

### F. Macro

| ID | Name | Formula | Stored | This episode | Evidence | Role |
|---|---|---|---|---|---|---|
| PF-15 | India 10y | +25 bp in 20 sessions. | **N** (file ends 2026-06-08; tradingeconomics answers the current level only) | +21 bp over the past month to 7.17% (+62 bp YoY) — near the line, NOT confirmable daily | the 09-09 study's ONLY holdout survivor (1.75 train / 3.78 test, ~6 events in 12y, lead 10–16 d) | WARN (weak; needs a daily feed) |
| PF-16 | US 10y | +25 bp in 20 sessions. | N (Yahoo ^TNX works from the sandbox) | tripped Jul 23; 4.47 → 5.24% Jul → Sep | untested | WARN (context) |
| PF-17 | Oil shock | Brent +20% over 40 sessions. | N (Yahoo BZ=F works) | tripped Mar 2 (earlier shock); +25% Aug 3 → Sep 11, peak 108.75 on the break day | untested; India is an importer, so the sign is right | WARN (context) |
| PF-18 | Rupee | USDINR at a 120-session high. | Y (usdinr_daily.csv, W-025) | Jul 16, Jul 23 | untested | WARN (context) |

### G. Flows

| ID | Name | Formula | Stored | This episode | Evidence | Role |
|---|---|---|---|---|---|---|
| PF-19 | FII 10-day net | ≤ −10,000 Cr. | Y (fii_daily) | −14,781 Cr, reached ~Sep 22, AFTER the break | **all 7 FII signals failed the holdout (2026-08-25 study)**; here it lagged | none as a lead; confirmation only |

### H. Shock gauges already in the app — and the corrections they need

| Gauge | What it read | Correction |
|---|---|---|
| Damage cluster `tail_down3_5d` vs static 0.213 | 0.23–0.66 EVERY day since July; 36 of 38 days > 0.213 since Aug 3. On this 242-name panel the normal level is 0.2–0.5 and its own p90 is 0.70. | The 0.213 was the p90 of a 1996–2024 broad-market file; it is not the p90 of the live panel. As a MARKET gauge use the live panel's rolling 250-day q90 (the `q90_cluster` half already exists) and drop the static line. Even so it never tripped here (0.66 < 0.70): it is a shock detector, and this was not a shock. Keep it as Ocean's rail R2 only. |
| Danger tripwires (12, per stock) | 0–6 fires/day before the break; 11 (4.5% of the panel) on Sep 15; 0 on Sep 28. | Per-stock by design and coincident by measurement. NEW panel-level use: share of the scanned panel firing ≥ 4% in one session = BREAK confirmation (stage 4 below). Not a lead. |
| India VIX / VIX-crush | 10.4–13.6 the whole way; p90 18.67 never approached. | No correction possible — a grind does not print fear. VIX-crush re-entry has nothing to crush in this shape; the ADD side must have a breadth-based washout rule too (PF-A2). |
| IFR (any single −3% day in 5) | same class as the damage cluster | same correction: shock only |
| Sector strip (1d / 5d moves) | showed IT −5% in 5d on Sep 9, i.e. late | add PF-06 (participation count) and PF-11 (leader reversal) beside the moves |
| Regime CORRECTION | true since Jun 17 | add PF-01 / PF-02 events |

### I. The ADD side (re-entry)

| ID | Name | Formula | Evidence | Role |
|---|---|---|---|---|
| PF-A1 | VIX crush | VIX ≤ 75% of its 120d spike peak after a spike above the p90 | measured +2.3 pp (v4), wired | ADD |
| PF-A2 | Breadth washout | % above 200DMA < 40% (the U: measured buyable on the engine's own picks, +1.61 pp holdout, 2026-09-09) AND a reclaim day: % above 20DMA rises ≥ 15 points in one session from below 20%. | first half measured and wired (the band); the reclaim-day half is NEW | ADD |
| PF-A3 | Casualty peak | PF-07 above 15% then falling for 3 sessions. | NEW | ADD (confirming) |

Today (Sep 28 close): 41.1% above 200DMA, 19% above 20DMA, casualties
19.4%. PF-A2's first half is one point from tripping; the reclaim day has
not come.

---

## 4. The ladder — roadmap of this crash, stage by stage

Stages are entered on COUNTS of gauges, never on one, because single
gauges whipsawed here (see §5). The action column is what the ladder is
FOR; the sizes (how much to cut) are the owner's numbers and must come
from the backtest, not from this document.

| Stage | Entered when | Date here | Nifty then | Action (proposed, to be measured) |
|---|---|---|---|---|
| 0 CORRECTION | Nifty < 200DMA | Jun 17 | 24,0xx | Existing regime; brake OFF by owner order 2026-08-09. No change. |
| 1 WARN | Stage 0 AND ≥ 2 of {PF-03, PF-06, PF-10, PF-14, PF-15/16} | **Jul 23** (PF-03 + PF-06 + PF-16) — then the Aug 3 rally reset PF-03/PF-06 | 23,870 | Freeze NEW Everest entries (today: breadth band did this by accident). No selling. |
| 2 CONFIRMED | PF-01 failed reclaim OR (Stage 1 AND PF-11 leader reversal) | **Aug 3–4** (PF-01) ; Aug 13 (PF-11 known) | 24,774 → 24,615 | Tighten every Everest stop to the owner's number (proposal: −3% off live, never loosened — the danger layer's mechanism, market-level trigger). No new entries at all. |
| 3 DISTRIBUTION | Stage 2 AND ≥ 3 of {PF-04 warn, PF-05 warn, PF-08, PF-10, PF-12} | **Aug 27–31** (PF-08, PF-10, PF-12 on Aug 27; PF-05 on Aug 31) | 24,091 → 24,080 (−2.8%) | REDUCE: exit the weakest half of Everest by score progress; keep owner_pick names (their rule). The −5.5% grind and the −8% total were still ahead. |
| 4 BREAK | ≥ 2 of {PF-04 reduce, PF-05 reduce, PF-07, danger share ≥ 4%} | **Sep 7–11** (PF-07 Sep 7, PF-04 Sep 10, PF-05 Sep 11); danger share Sep 15 | 23,779 → 23,398 | Exit remaining Everest except owner_pick; Ocean rails stay as they are (R1 VIX knife is irrelevant here, R2 damage is not tripped, R3/R4 per stock). |
| 5 WASHOUT / ADD | PF-A1 or PF-A2 or PF-A3 | not yet (41% vs 40%; no reclaim day) | 22,780 | Re-enable Everest entries; Ocean at its own ticket. |

What the ladder would have done with the −8%: stopped adding on Jul 23
(and missed the +4.2% rally into Aug 3 — the cost), tightened on Aug 4,
cut half on Aug 27–31 at about −2.8% from the rally high, cut the rest
Sep 7–11 at about −4 to −5.5%. Whether that is better than the current
"hold to −5% per stock" is EXACTLY the backtest question; on one episode
it is, and one episode is not evidence.

---

## 5. The whipsaw problem — why a ladder and not a switch

Every breadth gauge that tripped on Jul 23–24 was reset by the rally into
Aug 3 (20DMA breadth went 38% → 69%, sectors above 50DMA 6 → 11, new highs
19, new lows 0). A switch that sold on Jul 24 would have sold the low of
the July dip and watched a 4% rally. That rally was the trap: it was a
bear-market bounce INTO a falling 200DMA, and the only gauge that said so
in real time was PF-01 (the failed reclaim) on Aug 3–4.

Design consequences:

1. Stage 1 is "stop adding", never "sell". Being early costs nothing when
   the book is not being added to.
2. Stage 2 requires the STRUCTURE gauge (PF-01) or a lagged confirmation
   (PF-11). Structure does not whipsaw on a 4% bounce.
3. Stage 3 requires three independent families (participation, volume,
   rotation), not three breadth cousins that move together.
4. Every stage must be re-armable: after Stage 5 the ladder returns to
   Stage 0 with counts cleared, so a new slide starts a new count.

---

## 6. What must happen before any of this touches money

1. **Pre-register** the parameter formulas and thresholds above (they are
   the pre-registration; do not tune them on this episode any further).
2. Run them on the committed 191×12y panel + index_daily_15y.csv with the
   2020-06-30 train/test split the other studies used. Label = Nifty
   forward-20 return ≤ −5% (the pre-drop study's label) AND, separately,
   the ENGINE'S OWN PICKS' forward-20 return (the breadth re-cut's label,
   which is the one that actually matters for the book).
3. Score each PF alone (lift, lead time, false-alarm rate), then the
   ladder as a whole (return and drawdown of a book that follows it vs the
   live "-5% per stock" exits), episode-counted so 2020 cannot dominate.
4. Report the holdout numbers whatever they say. The 09-09 study says the
   base rate for "leading" gauges is failure; a ladder that only matches
   the current exits is a reason to keep the current exits.
5. Only then: the owner's word, and one PR per stage, each with the
   measured numbers in the PR body.

Data gaps that block a full test today: a daily India 10y series after
2026-06-08 (PF-15); the sector index history before 2026-09-09 (PF-06/11
can be rebuilt from the equal-weight panel sectors, as T12W_0094 does);
US 10y and Brent daily history (Yahoo reachable from the sandbox, not
committed).

---

## 7. Sheet-vs-tape check, 2026-09-30 — the gaps and the rules they add

Owner ask (2026-09-30, market open): "compare your response vs reality n
share the gaps" → "update the framework with these gaps". The response
was the Wednesday trader sheet written at 00:16 IST on 2026-09-29/30
(Tuesday close 22,716.20; levels, overnight inputs, seven labelled
assumptions). Reality = the live tape at 12:27 IST on 2026-09-30 from
tokenless Upstox 1-minute (indices) and 30-minute (242-name panel)
candles, Yahoo closes and the Indian press.

### 7.1 Scorecard

| Item | Sheet | Tape at 12:27 IST | Verdict |
|---|---|---|---|
| US close | S&P −1.1% / Nasdaq −1.5% (read at 2:45 pm ET, session open) | closed −0.16% / −0.09% | **Wrong input.** The session recovered after the reading. |
| GIFT Nifty 08:30 | DATA_NEEDED; flip level 22,750 | 22,991 | Flip condition met. The sheet's own rule said drop the weak-open view. |
| Brent | ~$103.6 | Dec contract ~$96, flat; Nov expired at $105 | Sources disagree on whether a real pullback occurred (press says yes). DATA_NEEDED. |
| Nikkei | futures +0.6% | cash +1.94% | Understated. |
| 1. Weak open −0.3 to −0.7% | assumed | open −0.23%, low 22,660 in the first minute, green by 10:00 | **Missed.** |
| 2. Gap-down does not fill (34% do) | base rate | filled within minutes; Nifty +0.16% | Minority case happened. Base rate stands. |
| 3. Bounce lean +0.38%, wide day 1.7× ATR | base rates | +0.16%; range 112 pts = **half** the 223 ATR | **My error.** The 1.7× rule is conditioned on a −1.5% prior day. Monday was −1.56%; Tuesday −0.28%. Wrong day. |
| 4. Engine opens nothing (RBI halt) | assumed | RBI halt removed by owner order the same night (PR #136) | Superseded. |
| 5. Exits run only if the app is awake | stated | app slept ~09:46–12:10 IST; the 10:15 / 10:45 / 11:15 / 11:45 stop passes did not run | **Confirmed system gap.** |
| 6. Pharma + Metal leadership = first buy signal | watch | Pharma **−1.27% (worst sector)**, Metal −0.22%; leaders PSU Bank +1.72%, Bank +1.35%, IT +1.06%, Media +3.45% | **Missed.** One day's rotation read as leadership; Tuesday's leaders were Wednesday's laggards. |
| Pivots S1 22,606 / P 22,680 / R1 22,790 | measured | low 22,660 held above S1; high 22,772 stalled 18 pts under R1 | Held. |
| VIX 13.64, no fear premium | measured | 13.11 (−3.9%) | Held. |
| Breadth >200DMA 37.6–38.0% (below the 40% band edge) | measured | 39.2%, still below | Held. |
| Breadth >20DMA 16.4%; reclaim day needs +15 pts | measured | 20.2% (+3.8 pts) → **no ADD trigger** | Held. |
| Adv/dec, up-vol/down-vol | not on the sheet | 154 / 84 advancing, median stock +0.54%, **up-vol ÷ down-vol 0.91** — volume did not confirm the advance | Added below (PF-20). |
| New 52w highs / lows | 1 / 33 (Tue) | 2 / 12 | Held (fewer lows, no highs = bounce, not turn). |

Pattern: **every miss was a direction call; every hold was a level, a
state reading or a trigger condition.** That is the 12-year result of
this project again (2026-09-09 pre-drop study: 24 of 25 leading candidates
failed the holdout): the data reads the market's STATE, it does not
predict its DIRECTION.

### 7.2 Rules added to the framework

| # | Rule | Why |
|---|---|---|
| R1 | **A daily sheet carries levels, state readings and trigger conditions only. No open/close lean, no "expect weak/strong".** | Every directional assumption on 2026-09-30 missed; every state reading held. |
| R2 | **Overnight US numbers are closes only.** The US cash session closes 01:30 IST (00:30 IST in US summer time). A reading before that is a mid-session print and must be labelled "session open — not a close". | The −1.1% / −1.5% read at 2:45 pm ET became −0.16% / −0.09% at the bell. |
| R3 | **A base rate is conditioned on a specific prior day. Name the day it applies to before quoting it.** "Range 1.7× normal after a −1.5% day" applies to the session AFTER the −1.5% day, not two days later. | The wide-day call was two days late and inverted (actual range 0.5× ATR). |
| R4 | **One day's sector move is rotation, not leadership.** PF-11 already uses a 25-session window for leaders; never shortcut it to yesterday's winners. A sector that led on a down day is as likely to lag next day as to lead. | Pharma/Metal led Tuesday, were worst/flat Wednesday; banks and IT (Tuesday's laggards) led. |
| R5 | **The ADD trigger (PF-A2) is a state condition and it worked: no reclaim day, no add.** Keep the +15-point 20DMA jump as the reclaim definition; a +3.8-point drift with up-vol/down-vol below 1 is a bounce. | 20DMA breadth 16.4 → 20.2 on 0.91 up/down volume. |
| R6 | **Levels from pivots and ATR are arithmetic, not forecasts, and they held. Keep them.** | Low held above S1, high stalled under R1, within the ATR band. |
| R7 | **A sheet is only as good as the machine that acts on it.** While the live service sleeps when idle, stop checks between wake probes do not run; a sheet that says "exits run only if awake" is describing a broken guard, not a plan. | Four 30-minute exit passes missed on 2026-09-30. Owner action: Render Instance Type → Starter. |

### 7.3 Parameter added

| ID | Name | Formula | Stored | 2026-09-30 | Evidence | Role |
|---|---|---|---|---|---|---|
| PF-20 | Unconfirmed bounce | Nifty up on the day AND panel up-volume ÷ down-volume < 1.0 AND % above 20DMA rises < 15 points. | Y (panel volumes) | Nifty +0.16%, up/down vol 0.91, 20DMA breadth +3.8 pts → **tripped** | NEW, untested | Do NOT treat the day as PF-A2's reclaim day. Confirming, never a sell. |

PF-13 (No shelter) correction: Pharma's Tuesday gain (+0.6%) did not make
it a shelter — it was the worst sector the next day. PF-13 stays on its
50DMA definition; a one-day gain never flips it.

### 7.4 Readings at 12:27 IST, 2026-09-30 (for the timeline in §2)

| Gauge | Value |
|---|---|
| Nifty | open 22,665 (−0.23%), low 22,660 at 09:16, high 22,772 at 10:23, last 22,752.50 (+0.16%); range 112 pts |
| Bank Nifty | +1.35% (the day's engine); PSU Bank +1.72% |
| India VIX | 13.11 |
| Breadth >200 / 50 / 20 DMA | 39.2% / 18.5% / 20.2% (n = 237–238) |
| Adv / dec | 154 / 84; ≥ +3%: 24 names; ≤ −3%: 5 names |
| Up-vol ÷ down-vol | 0.91 |
| New 52w highs / lows | 2 / 12 |
| FII | Monday −5,538 Cr (NSDL); Tuesday not yet published (T+1 evening) |
| Ladder stage | 4 BREAK, unchanged; PF-A2 first half true (39.2 < 40), reclaim day not met; PF-20 tripped |

---

## 8. Second read of 2026-09-30 at 14:29 IST — the bounce failed; PF-21..28 added

Owner (14:30 IST): "Market slips again. Review it again with all new n old
and add more parameters" → "Ship all". Same sources as §7 (Upstox public
1-minute index candles, 30-minute candles on the 242-name panel, Yahoo,
tradingeconomics, NSDL). State readings only (rule R1).

### 8.1 What changed between 12:27 and 14:29 IST

| | 12:27 | 14:29 |
|---|---|---|
| Nifty | 22,752 (+0.16%) | 22,643 (−0.32%), a new day low |
| Day high | 22,772 | 22,809 at 12:49, then −167 pts |
| Shape | bounce | outside-day down close: high above Tuesday's high, close below Tuesday's close, closed at 0.04 of the range |
| Hourly path | | +0.38 / −0.35 / +0.17 / +0.31 / −0.36 / −0.26 |
| R1 22,790 | untouched | touched and rejected |
| S1 22,606 / Tue low 22,570 | | both intact (37 / 73 pts above) |
| Range vs ATR-14 | 0.50× | 0.78× — a normal day, not a wide one |

### 8.2 Old parameters, 14:29 values

| Gauge | Reading | Note |
|---|---|---|
| Breadth >200 / 50 / 20 DMA | 37.6 / 18.9 / 16.4% | 200DMA breadth unchanged from Tuesday; below the 40% band edge |
| PF-03 breadth decay (20DMA, 6 sessions) | 38.8 → 30.2 → 32.2 → 18.6 → 16.4 → 16.4 | flat at the floor, no reclaim |
| Adv / dec | 132 / 106 | more names up than down while the cap-weighted index fell |
| Up-vol ÷ down-vol | 0.68 | worse than 12:27 (0.91) |
| New 52w highs / lows | 3 / 18 | |
| PF-07 casualties (−10% in 20d) | 15.7% | easing from 20.2% |
| Damage cluster tail_down3_5d | **0.719** | first reading above the live panel's own q90 (~0.70) this episode; a shock gauge reporting damage done |
| India VIX | 13.47 | no fear |
| RSI-14 / Bollinger z | 28.8 / −1.89 | |
| Consecutive down closes | 3 | |
| Distance to 200DMA | −7.1% | |
| PF-11 leaders (25 sessions) | Pharma +2.3, Metal −3.2, Media −3.9 | the "best" sector was the worst today (−1.24%) |
| PF-12 small-cap divergence | +2.0 pp (SML100 vs Nifty, 20d); SML100 +0.83% today | |
| PF-13 no shelter | Pharma −1.24%, FMCG −0.50% | tripped |
| PF-15 India 10y | 7.15%, +20 bp over the month | near the +25 bp line, not over; Sep bond outflow ₹10,200 Cr (largest of FY) |
| PF-17 Brent | Dec ~$96, flat; Nov expired at $105 | |
| PF-19 FII | Mon −5,538 Cr; Tue unpublished (T+1) | |
| PF-20 unconfirmed bounce | tripped at 12:27; the bounce then failed | first live confirmation of the parameter's purpose |
| PF-09 distribution days | 9 of 25 | REJECTED gauge, listed only |
| A/D line, cumulative 30 sessions | −1,088 | last 10: +116, +97, −9, −61, +101, −173, +33, −198, −108, +26 |
| Sector participation (EW, n≥4) | 9 of 14 sectors UP | Construction +1.9, Defence +1.3, Auto +1.1; Pharma −1.8, FMCG −0.9 |

Concentration note: the afternoon fall was hospitals (Fortis −7.4%, Max
−7.1%, Apollo −6.5%), pharma and FMCG, while banks, PSU banks and small
caps held green. The reason for the hospital-chain move is DATA_NEEDED
(the news pass runs at 09:20; this was an afternoon move).

### 8.3 New parameters PF-21..28 (intraday participation family)

All computable from the public 30-minute candles the app already reads;
all NEW and untested; all WARN/confirming, never a sell.

| ID | Name | Formula | 2026-09-30 14:29 | Role |
|---|---|---|---|---|
| PF-21 | Failed intraday rally | Index high > prior high AND close < prior close AND close in the bottom 15% of the day range, while < 200DMA and RSI-14 < 35 | **tripped** | WARN (exhaustion marker; base rate in 8.4) |
| PF-22 | Midday reversal share | Names above the prior close at 12:30 that are below their 12:30 print at the read | 117 of 153 morning gainers (76%) | WARN |
| PF-23 | Morning gain given up | Open > prior close AND now < open | 75 of 242 (31%) | WARN |
| PF-24 | Weak closers | Names closing in the bottom quarter of their own day range | 40.5% | WARN |
| PF-25 | Below prior day's low | Share of names now below yesterday's low | 22.3% | WARN |
| PF-26 | Fresh 20-day lows | Share of names at a new 20-session low | 22.7% | WARN |
| PF-27 | Three-day losers | Share of names down three sessions running | 21.5% | WARN |
| PF-28 | Bank-vs-tape split | Bank Nifty and PSU Bank up while Nifty, Pharma and FMCG fall (rate-sensitives bid into an RBI decision, broad tape not) | Bank +0.94 / PSU +1.04 vs Nifty −0.32 | context |

Thresholds for PF-22..27 are deliberately NOT set here: they need the
pre-registered test (§6). Today's values are the first recorded points.

### 8.4 PF-21 base rate, measured on 15 years of Nifty (data/index_daily_15y.csv)

Condition = today's shape (rule R3: the day it applies to is the session
AFTER the failed-rally bar). n = 16 bars, 13 separate episodes (2015-06,
2015-08, 2016-02, 2018-10 ×2, 2022-05 ×3, 2022-06, 2023-03, 2024-12,
2025-03).

| Horizon | Median | p25 / p75 | Up | All-days median |
|---|---|---|---|---|
| Next day | +0.19% | −0.52 / +0.92 | 62% | +0.06% |
| 5 days | +1.07% | −0.49 / +2.69 | 69% | +0.34% |
| 20 days | +3.85% | +0.67 / +5.28 | 75% | +1.04% |

Reading: in a market already this oversold, this bar has more often marked
exhaustion than the start of a new leg. Sixteen instances is a count, not
an edge; it is recorded so the pre-registered test can judge it, and it is
NOT a direction call for tomorrow.

### 8.5 Ladder reading at 14:29 IST

Stage 4 BREAK, unchanged. PF-A2: breadth < 40% true; reclaim day not met
(20DMA breadth flat at 16.4%). No ADD trigger. The new fact of the day is
the damage cluster crossing its own q90 (0.719 vs ~0.70) — and it reports
damage already done, consistent with §3H.

App state: momentum entries allowed by the breadth band (37.6 < 40) and
RBI no longer halts; positions stand on the −5% stop and k×ATR target.
The service slept from ~12:12 to the 15:20 IST wake, so no 30-minute stop
check ran during the afternoon slide (rule R7).

### 8.6 Close, 2026-09-30 (addendum written 15:50 IST from the last public candles)

| Gauge | 14:29 read | Close |
|---|---|---|
| Nifty | 22,643 (−0.32%) | **22,620.45 (−0.42%)**; day low 22,595 at 14:43, a brief dip through S1 22,606; close at 0.12 of the range; range 214 pts = 0.96× ATR-14 |
| Bank Nifty / VIX | +0.94% / 13.47 | +0.69% / 13.46 |
| Sectors | | Pharma −1.84, Metal −1.53, FMCG −0.63; Realty +1.64, Bank +0.69, Media +2.65 |
| Breadth >200 / 50 / 20 DMA | 37.6 / 18.9 / 16.4% | **37.1 / 17.2 / 13.0%** (20DMA breadth at a new low for the episode) |
| Adv / dec | 132 / 106 | 120 / 116 |
| Up-vol ÷ down-vol | 0.68 | **0.55** |
| New 52w highs / lows | 3 / 18 | 3 / 20 |
| Damage cluster tail_down3_5d | 0.719 | **0.727** (above the panel's own q90 ~0.70) |
| PF-22 midday reversal share | 117 of 153 | 110 of 153 |
| PF-23 morning gain given up | 31.0% | 35.1% |
| PF-24 weak closers (bottom quarter of own range) | 40.5% | **46.7%** |
| PF-25 below prior day's low | 22.3% | 26.0% |
| PF-26 fresh 20-day lows | 22.7% | **28.1%** |
| PF-27 three-day losers | 21.5% | 24.8% |
| PF-28 bank-vs-tape split | +0.94 vs −0.32 | +0.69 vs −0.42, still split |
| Sector participation (EW, n≥4) | 9 of 14 up | 8 of 14 up |
| Consecutive down closes (Nifty) | 3 | **4** |

Panel figures are from the last 30-minute candle (15:00–15:30 bar, stamped
15:15 IST). PF-21 (failed intraday rally) stands at the close: high above
Tuesday's high, close below Tuesday's close, bottom 15% of range, RSI < 35
below the 200DMA. Ladder stage 4 BREAK, unchanged; PF-A2 first half true
(37.1% < 40), reclaim day not met (20DMA breadth fell, did not jump).

Machine note (rule R7): the service was woken by session probes at 09:11,
12:10, 14:50 and 15:22 IST and slept between them; the 15:25 square-off and
15:35 pass ran on a live process (memory 167 → 220 MiB while they ran); the
30-minute stop checks between 09:46–12:10 and 12:12–14:50 IST did not.
