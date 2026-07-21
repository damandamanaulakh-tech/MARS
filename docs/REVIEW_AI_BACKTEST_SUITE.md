# Review — the 5-workbook AM07 backtest suite (other AI), 2026-07-19

Files reviewed: Full_Backtest_Documentation, Realistic_Costs_Limits,
Comparison_Report, Option2_Clean_ActivityFilter, Data_Quality_Investigation.
Same data file as this repo's committed panel (`data/nse_all_stock_data.csv.zip`).

## Headline verdict

Their central claim — **₹1.0 Cr → ₹210.8 Cr in 4.5 years (≈230%/yr net), Sharpe
4.88** — is not a strategy result; it is a data artifact wearing a costs-and-
governors costume. Their own Data Quality sheet proves it (and that sheet is
the one genuinely valuable thing in the suite — see Credits).

## Flaws (ranked)

1. **Impossible headline kept after being diagnosed.** Their own investigation
   found corrupt prints (HINDZINC +5,575%/day, BEL +3,200% then −97%,
   CIPLA +1,2xx%). Real NSE circuit limits cap days near ±20%. They then
   RE-published results on the same engine instead of retracting.
2. **No out-of-sample structure at all.** One window (2020-01→2024-06),
   parameters chosen and scored on the same span. This repo's search v1
   measured exactly what that does: in-sample +28..64% → out-of-sample
   LOSSES in 4/4 windows. In-sample-only reporting is the project's
   original sin, repeated.
3. **20-day momentum, top-10, 5-day rebalance** — short-horizon noise-chasing
   that maximally loves glitch spikes and churns costs. Cross-sectional
   momentum's evidence base is 6–12 months. Their signal family is the one
   most likely to be pure data-error harvesting.
4. **Their Top-Selected list convicts the run**: DBREALTY, SARVESHWAR, BCG,
   XPROINDIA, TTML, SPMLINFRA, DIACABS — micro/penny/circuit names (BCG =
   Brightcom, trading-restricted fraud case). "Liquidity filter = >60% data
   coverage" filters *data presence*, not tradability.
5. **±10–12% daily clipping as 'cleaning'** — destroys real crashes and
   rallies (legit ±15–20% days) while leaving the survivor drift and most
   glitch compounding intact. The right repair is surgical (see below):
   4,476 bad prints (0.04% of the panel), not a blanket distortion of
   every large move.
6. **Costs understated**: 0.08%/side vs India cash-equity reality (STT 0.1%
   sell-side alone + slippage; this repo models 0.15%/side).
7. **Cosmetic parameters**: 10% position cap on a top-10 equal-weight book
   (10 × 10% = 100%) can never bind; Sharpe 4.88 beside a −26.4% maxDD is
   internally incoherent for a daily series.
8. **Survivorship unacknowledged in the headline** (only "may be elevated"
   buried in notes).

## Credits (what they did right)

- **The Data Quality Investigation is genuinely valuable** — it identified
  corrupt prints in the shared panel that this repo's search v2 had NOT
  handled. Their finding triggered our v2.1 self-correction below.
- Comparison-report discipline (previous vs current, parameter diff table)
  and the parameter documentation format are good practice.
- FII/volume filters honestly marked NOT USED.

## The self-correction their finding triggered (v2 → v2.1)

Applied their discovery to OUR published result, with a surgical repair
(any |daily move| > 35% — beyond every NSE circuit — frozen at prior price;
4,476 prints touched; real crashes untouched). Same grid, windows, costs,
walk-forward (`scripts/param_search_v2_robust.py`,
`docs/param_search_v2_1_results.json`):

| Unseen window | v2 alpha (pre-repair) | **v2.1 alpha (repaired)** |
|---|---|---|
| 2004–07 | +5.9 | **+5.9** |
| 2008–10 GFC | +1.9 | **+2.5** |
| 2011–15 | +17.0 | **+17.0** |
| 2016–19 | +4.3 | **−1.1** |
| 2020–24 | +23.7 | **+22.4** |
| **Summary** | 5/5 positive, avg +10.5 | **4/5 positive, avg +9.3** |

**The breadth-momentum edge survives honest data repair** — one window flips
mildly negative, the average drops ~1pp, and the drawdown protection stands
(GFC: −49.8% vs universe −60.7%; 2016-19: −18.6% vs −37.8%). Config
convergence weakens slightly: 6+12 vol-adjusted + regime brake still wins
the three biggest windows; the two early windows preferred the absolute-
momentum gate instead — regime-vs-absgate is less settled than v2 stated.
Survivorship and the missing liquidity screen remain the open caveats
(search v3 after Upstox OHLCV).

## Their parameters vs this repo's evidence

| Theirs | This repo's walk-forward evidence |
|---|---|
| 20d momentum | 6m+12m (vol-adjusted) — the only family with OOS support |
| Top-10, 5-day rebalance | Top-20/50, MONTHLY — turnover an order of magnitude lower |
| ±10% daily clip | surgical >35% glitch repair (0.04% of prints) |
| 0.08%/side | 0.15%/side |
| coverage% as liquidity | penny floor now; real turnover screen needs OHLCV |
| DD governor −20%→50% | regime brake (200DMA) — chosen by the optimizer in the 3 biggest OOS windows |
| verdict: ₹210 Cr in-sample | verdict: +9.3pp/yr average OOS alpha, caveats attached |

## Fix list

- THEIRS: retract the ₹210 Cr line; rerun their engine at 6–12m horizon,
  monthly, walk-forward, surgical repair, real costs — or adopt this repo's
  committed scripts, which already do all of it.
- OURS (done in this commit): v2.1 published; PARAM_SEARCH_V2.md carries a
  correction notice. OPEN: liquidity screen + PIT membership (search v3).
