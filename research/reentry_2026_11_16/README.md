# Goal 09 — Changed-setup re-entry after an intraday loss

**Decision: retain production policy; no candidate promoted.** The locked rule improves the matched retry comparison, but fails the declared sample and cross-scenario expectancy criteria. Almost never trading is not evidence of a reliable recovery strategy.

This is an **isolated, one-next-retry experiment after a common failed fixed-clock trade**, not actual MARS signals, recursive retries, cross-day state or portfolio P&L. All modeled trades are long intraday shares. Existing risk gates, protective exits, settings and the original financial ledger are unchanged.

## Fresh data and chronology

- Baseline: `9ba36523fe0df77adbee49dbe40a6201118db4b5`. The production scan checks category loss halts and existing holdings, but has no dedicated post-loss setup-transition gate. The older Momentum cooldown experiment also lost on its holdout; it is not intraday evidence.
- [Protocol commit](https://github.com/damandamanaulakh-tech/MARS/commit/75e3ac34ba8de8f702d16b73e36bbbc5b6bfa331) precedes the first training request.
- [Training-choice/code seal](https://github.com/damandamanaulakh-tech/MARS/commit/2fe824533ec300e76ab373ff177827f31c5fc645) precedes the first holdout request. Selection did not change after validation or holdout.
- Holdout access marked at `2026-11-16T09:34:28.649915+00:00`; first request `2026-11-16T09:34:28.652588+00:00`. Raw bytes, URLs, timestamps, hashes and failures are retained in the separate acquisition folders.
- 105 successful public Upstox requests; 121,500 source bars; 1,605 complete regular stock-sessions / 120,375 accepted five-minute bars. The 1 February Sunday special session is explicitly outside the verified regular-session schedule, not forward-filled.
- All dates precede Goal08's earliest raw bar, 15 June 2026. The 15-stock universe was fixed before new acquisition; no price/outcome from Goal08 is fitted here.
- **Historical-market caveat:** older repo research used daily prices through June 2026. These are fresh, previously unexamined five-minute observations and a separately sealed intraday holdout; the market period itself is not claimed to be unknown history. Forward paper evidence is still required.

| Stage | Dates (2026) | Complete calendar sessions | Paired failed-seed episodes* | Days with episodes* |
|---|---|---:|---:|---:|
| train | 2026-02-02 to 2026-03-13 | 29 | 64 | 23 |
| validation | 2026-03-16 to 2026-04-10 | 17 | 37 | 10 |
| holdout | 2026-04-13 to 2026-06-12 | 42 | 104 | 34 |

*At 30-minute observations and 5 bps per side. Seed losses and remaining retry opportunities vary by fill scenario; comparisons are paired within each stratum, not across different cohorts. Late losses without a remaining retry slot are reported separately, not credited as avoided losses.

## Rules and causal decision evidence

| Policy | Requirement | Selection role |
|---|---|---|
| Next scan (baseline) | Control: take the first later 30-minute slot, with no changed-setup requirement. | Eligible |
| Wait 30 minutes (control) | Diagnostic time-only control: wait at least 30 minutes after the loss is known; this is not evidence of a changed setup. | Diagnostic control only |
| Post-loss breakout reclaim | Freeze threshold=max(failed slipped entry, six pre-entry completed-bar highs). Observe a fully post-loss close at or below it, then a later post-loss close above it. At retry, the latest completed close must still be above it. | Eligible |
| Post-loss VWAP reclaim | Observe a fully post-loss close at/below its causal session VWAP, then a later crossing above; require the two most recent fully post-loss closes above their own causal VWAPs and the latest close higher than the preceding close. Zero-volume/invalid VWAP is unavailable, not a pass. | Eligible |
| Both reclaims | Both breakout_reclaim and vwap_reclaim must pass at the same decision slot. | Eligible |
| No retry (control) | Diagnostic control: no same-session second trade; not eligible for selection. | Diagnostic control only |

- Failure means negative net P&L after both order charges and adverse fills, regardless of exit label. Each arm receives the same failed seed within its scenario/slippage stratum.
- Retry scans run on half-hour boundaries, strictly after the loss is known, through 14:30 IST. Features end at least five minutes before entry. An intrabar exit is known only at its bar end.
- Both reset and reclaim must be witnessed in complete post-loss bars. A same-bar rebound, a pre-loss crossing, elapsed time or an old score cannot qualify. Missing prefixes and zero-volume VWAP remain unavailable.
- The breakout threshold is frozen at the higher of the failed slipped entry and six pre-entry completed highs. VWAP is a causal cumulative HLC3-volume proxy, not certified tick VWAP.
- Enter at the first qualifying slot or do not retry that session. Every entry retains current hard-stop/trail/5m-ATR-target formulas and exits by the 15:25 open. A second failure does not start another retry in this bounded study.

## Holdout: losses avoided and recoveries missed

The table uses **30-minute observations, 5 bps (0.05%) adverse slippage per side**, dated Goal05 Upstox Basic buy/sell charges, and at most INR100,000 including buy fees per isolated entry. Totals sum counterfactual observations; they are not a capital-constrained portfolio or booked MARS trades.

| Policy | Retries made | Losing immediate retries withheld | Loss amount withheld INR | Profitable immediate retries missed | Profit missed INR | Delayed retry net INR | Total retry net INR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Next scan (baseline) | 104 | 0 | 0.00 | 0 | 0.00 | 0.00 | -28,981.39 |
| Wait 30 minutes (control) | 104 | 0 | 0.00 | 0 | 0.00 | 0.00 | -28,981.39 |
| Post-loss breakout reclaim | 2 | 67 | 46,469.41 | 37 | 17,488.02 | 2,024.37 | 2,024.37 |
| Post-loss VWAP reclaim | 6 | 67 | 46,469.41 | 37 | 17,488.02 | -7.28 | -7.28 |
| Both reclaims | 2 | 67 | 46,469.41 | 37 | 17,488.02 | 2,024.37 | 2,024.37 |
| No retry (control) | 0 | 67 | 46,469.41 | 37 | 17,488.02 | 0.00 | 0.00 |

**Attribution identity:** paired net change = losing immediate retry amounts withheld − profitable immediate retry amounts missed + delayed retry net P&L. A delayed retry can itself lose; those losses remain in the calculation. No-trade decisions contribute zero retry P&L, not a winning trade.

The locked **Post-loss breakout reclaim** rule withheld 67 losing immediate retries totaling INR46,469.41, but missed 37 profitable immediate recoveries totaling INR17,488.02. Its 2 later trades contributed INR2,024.37. Paired improvement: INR31,005.75 in aggregate, or 29.813 bps per failed-seed episode; 95% day-cluster interval [8.069, 57.771] bps.

**The initial failed trades still happened.** Adding those common losses gives INR-132,826.16 for the selected policy's complete seed-plus-one-retry episodes in this scenario. Retry-only gains must not be presented as total-strategy profits.

## Every modeled retry of the locked candidate in the main holdout scenario

| Stock | Seed entry | Loss known | Retry entry | Net retry INR | Exit |
|---|---|---|---|---:|---|
| HINDUNILVR.NS | 2026-04-21 10:00 IST | 2026-04-21T12:00:00+05:30 | 2026-04-21T14:30:00+05:30 | 2,037.25 | SQUARE_OFF |
| ONGC.NS | 2026-06-02 10:00 IST | 2026-06-02T12:00:00+05:30 | 2026-06-02T14:00:00+05:30 | -12.89 | SQUARE_OFF |

The compressed episode file retains each decision, frozen threshold, reset/cross witness timestamps, feature cutoff, selected fill, initial loss and counterfactual outcome. These are all selected-candidate retries for this scenario, not chosen winners.

## Execution and slippage sensitivity — locked candidate

| Scenario | Slippage bps/side | Paired episodes | Candidate retries | Candidate retry net INR | Mean paired gain bps | 95% day-cluster interval bps |
|---|---:|---:|---:|---:|---:|---|
| open_high_low_close | 5 | 290 | 23 | -5,805.27 | 26.433 | [15.680, 37.197] |
| open_high_low_close | 15 | 266 | 23 | -14,470.22 | 42.279 | [29.920, 54.725] |
| open_high_low_close | 30 | 255 | 26 | -21,340.18 | 68.400 | [52.992, 82.435] |
| open_low_high_close | 5 | 286 | 23 | -5,805.27 | 26.933 | [16.095, 37.774] |
| open_low_high_close | 15 | 262 | 22 | -13,111.77 | 43.322 | [30.781, 55.845] |
| open_low_high_close | 30 | 251 | 24 | -21,139.72 | 68.892 | [52.814, 83.140] |
| sampled_30m | 5 | 104 | 2 | 2,024.37 | 29.813 | [8.069, 57.771] |
| sampled_30m | 15 | 99 | 2 | 1,626.70 | 47.168 | [24.639, 75.188] |
| sampled_30m | 30 | 105 | 4 | -1,021.18 | 70.753 | [46.255, 101.296] |

Both OHLC orderings are sensitivity paths, not known tick sequences or mathematical bounds. Gaps fill at the observed opening price. Sampled observations do not fill a target merely because an intervening high touched it. Confidence intervals resample whole session days (2,000 fixed-seed draws).

## Decision, coverage and limits

- Statistical pass: **False**. Reasons: `holdout_coverage_below_predeclared_minimum`, `holdout_retry_expectancy_not_positive`, `training_coverage_below_predeclared_minimum`, `validation_coverage_below_predeclared_minimum`, `validation_retry_expectancy_or_gain_not_positive`.
- The selected rule is too sparse in the declared 30-minute scenario; positive paired improvement mostly reflects avoiding the unfiltered retry. It does not establish that the rule consistently identifies profitable recoveries. Validation had no breakout-reclaim trades at that cadence.
- Required floors (fixed before acquisition): 25 training / 15 validation / 20 holdout days with episodes, 12 symbols, 150 holdout paired episodes and 50 candidate retries; positive candidate retry expectancy and gain across all scenarios at 5/15 bps, with positive paired confidence bounds. Failed floors were retained, not relaxed.
- The CSV contains all 162 stage/policy/scenario/slippage summaries, including gross, fees, slippage, no-trade counts, payoff, and explicitly defined cumulative daily-mean retry drawdown. This drawdown is not portfolio or mark-to-market drawdown.
- Current-name selection, provider revisions/corporate-action adjustments, actual spread/impact/queues, scanner entry gates, category budgets/loss halts, dynamic score/sector/danger exits and capital concurrency are not reconstructed. Missing coverage is reported, not replaced with a successful result.
- The 30-minute scenario matches the code default; the live owner scheduler setting was not read for this study.
- Same-session one-retry evidence is not recursive/cross-day re-entry or evidence for Momentum, Swing, Short or Positional. Full-strategy and forward paper validation remain separate.
- Holdout is consumed. No retuning or selecting another arm from these results. Future candidates need a new untouched dataset; no apply-to-settings or ledger operation exists.

## Reproduce offline

Use **CPython 3.12.16** and the study-specific `requirements.txt` for exact numerical reproduction. The full application CI remains on Python 3.11. `runtime.json` preserves the initial CI failure: one aggregate differed by about INR0.00000001 because Python 3.12 changed floating-point summation. Pinning the original runtime preserves all frozen source, inputs, decisions, outputs and strict equality checks; no result was regenerated or tolerance widened.

```sh
python -m pip install -r research/reentry_2026_11_16/requirements.txt
python -m pytest trading/tests/test_reentry_research_2026_11_16.py -q -ra
python -m research.reentry_2026_11_16.study verify
python -m research.reentry_2026_11_16.report --check
```

CI reproduces every episode and decision, signed loss/recovery attribution, all periods and the frozen training choice, alongside the full application suite and zero-skip guard.

## Source contracts

- [Upstox V3 historical candles](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/): five-minute intervals, month-bounded retrieval and timezone-bearing bar-start timestamps; rechecked 16 November 2026.
- [Published Upstox charges](https://upstox.com/brokerage-charges/) and [NSE FA73061](https://nsearchives.nseindia.com/content/circulars/FA73061.pdf); existing Goal05 dated fee model. Historical Basic is an explicit counterfactual tariff assumption, not proof of the old account plan.
- [Earlier Momentum cooldown result](../stop_cooldown_study/summary.md) and [Goal08 consumed intraday study](../profit_protection_2026_11_16/README.md) remain unchanged.
