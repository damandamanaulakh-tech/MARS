# Intraday trade audit — 11 November 2026

Scope: all **74 confirmed intraday ClosedTrade records** in the retained MARS paper book, exits 30 October–11 November 2026, with entries beginning 29 October. Portfolio snapshot last saved **11 November 15:36:25 IST**. The non-pre-MARS archive contained no additional rows. All 74 records match an exact ticker/entry-time BUY and ticker/exit-time SELL journal; no join is missing or duplicated. The 59 legacy blank-mode trades are excluded because their records do not prove intraday attribution. These figures describe the paper ledger, not broker statements.

Baseline and live application at audit: `1a6a96893f34a87887aabd48ad5232d89fed97f3` (PR #194). Read-only Mongo projections supplied retained trades, selected journal entries, current settings and stored benchmark observations. No trading setting, production record or order was changed for this audit. [Full 74-row CSV](2026-11-11-intraday-trades.csv) includes entry/exit times, prices, quantity, costs, every stored component score, target/stop diagnostics and explicitly missing evidence.

## Profit versus loss

| Coverage | Closes | Wins | Losses | Gross P&L ₹ | Recorded costs ₹ | Net P&L ₹ |
| --- | --- | --- | --- | --- | --- | --- |
| All confirmed intraday | 74 | 22 | 52 | -153,714.71 | 70,160.03 | -223,874.74 |
| Same-day entries/exits | 67 | 19 | 48 | -123,450.93 | 64,443.46 | -187,894.39 |
| Overnight exceptions | 7 | 3 | 4 | -30,263.78 | 5,716.57 | -35,980.35 |

Win rate is **29.73%** and loss rate **70.27%**, with no breakeven closes. Winning records sum to ₹75,729.77; losing records sum to −₹2,99,604.51. Costs explain ₹70,160.03 of the net result, but the **gross result was already −₹1,53,714.71** after embedded fill slippage. Correcting fees alone cannot make this observed book profitable. Eight trades had a small positive gross P&L but became losses after recorded costs.

## Recorded exit mechanisms

| Exit reason | Closes | Wins | Losses | Gross ₹ | Costs ₹ | Net ₹ |
| --- | --- | --- | --- | --- | --- | --- |
| STOP_LOSS | 10 | 0 | 10 | -91,142.60 | 10,235.63 | -101,378.23 |
| TRAILING_STOP | 16 | 2 | 14 | -62,197.49 | 16,900.32 | -79,097.80 |
| SQUARE_OFF | 39 | 15 | 24 | -1,577.76 | 35,601.20 | -37,178.96 |
| MAX_HOLD | 7 | 3 | 4 | -30,263.78 | 5,716.57 | -35,980.35 |
| TAKE_PROFIT | 2 | 2 | 0 | 31,466.93 | 1,706.32 | 29,760.61 |

These are the executed reasons, not complete market-causality labels. A SQUARE_OFF fill can be below the original stop because that separate deadline path writes SQUARE_OFF unconditionally. It does not prove the earlier stop was never crossed. The SELL `net_pct` field is actually the fill-price return before costs; this report calculates net return from net_pnl / entry notional instead.

## Daily outcomes and market context

| Exit date | Closes | Wins | Losses | Gross ₹ | Costs ₹ | Net ₹ | Stored Nifty close change |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-10-30 | 8 | 3 | 5 | -32,624.37 | 6,065.48 | -38,689.85 | +0.51% |
| 2026-11-02 | 9 | 1 | 8 | -20,000.43 | 6,314.64 | -26,315.06 | +1.68% |
| 2026-11-03 | 14 | 6 | 8 | -811.19 | 13,461.87 | -14,273.05 | +1.58% |
| 2026-11-04 | 14 | 4 | 10 | -20,846.09 | 13,295.30 | -34,141.39 | +0.63% |
| 2026-11-05 | 14 | 5 | 9 | -16,622.22 | 13,519.04 | -30,141.26 | +2.40% |
| 2026-11-11 | 15 | 3 | 12 | -62,810.42 | 17,503.71 | -80,314.13 | Unavailable for this date |

The retained Nifty series rose on 30 October and 2–5 November while the intraday book lost money. A broad daily market decline therefore does not explain those whole-day losses. This is context from the app's stored daily series, not a tick-by-tick comparison or independent exchange reconciliation. The stored series ended on 9 November: **11 November's benchmark return is unavailable here**. Exact market/sector moves during each holding window were not retained, so their causal contribution is unknown. Entry regime was correction for 59 closes and bull_extension for 15; a regime label is not the current-day index direction.

## Parameters hit, skipped and unproven

| Parameter | Observed status | What it means / limitation |
| --- | --- | --- |
| AM07 score | 74/74 above entry floor 72; range 72.40–94.34 | Recorded BUY evidence. High historical score is a ranking signal, not a probability of intraday profit. |
| Timing / breakout and volume | 74/74 timing_pass=true | Actual scan passes daily OHLCV into timing_ok; it checks proximity to a 20-day close high and 5-day/20-day volume averages. Per-entry ratios were not saved. |
| Five-minute ORB / VWAP | Not connected to this scan path | Preset says bar_interval=5m, but scan() fetches daily data and never uses bar_interval to fetch an intraday frame. ORB/VWAP cannot be credited as passed checks. |
| Candle pattern | Conditional hard gate remains in current code; exact result not retained | Current defaults enable confirmation for intraday. A completed auto buy implies no refusal in that execution path; pattern/inputs/enable-state were not stored on the BUY. Do not invent the winning pattern. |
| Hard stop | 74 entry snapshots at 1.5%; 10 STOP_LOSS exits | Fills moved −1.55% to −2.45% from entry. Sampled checking and slippage can exceed the line; exact crossing/quote-age trace is unavailable. |
| ATR profit target | 74/74 ATR source, k=1; targets 1.41%–6%, mean 3.87% | Only 2 target exits. Uses daily ATR, including overnight gaps, for a same-day horizon. This is a horizon mismatch to validate, not proof a lower target would have profited. |
| Valuation target / monthly re-mark | Skipped by intraday policy in all 74 target diagnostics | No VAL_TARGET_DOWN exit. Valuation is not the recorded reason these trades lost. |
| Trailing stop | 16 exits; 14 losses, 2 wins | Activates when a sampled peak is above entry. No requirement to first cover costs or preserve net profit. The intraday preset is 1%; tightening may apply on score decay, but actual exit threshold/score were not saved. |
| End-of-day deadline | 39 SQUARE_OFF exits; 7 overnight failures | 15:25 IST job closes the remaining intraday book at then-available quotes. A deadline exit does not wait for a profit. |
| Score-decay exit | No SCORE_DECAY exit in this sample | Cannot infer that scores never weakened; score can tighten a trailing stop, and final current_score is absent from SELL. |
| Correction brake | Explicit CORRECTION_BRAKE_OFF journals during correction period | Owner-disabled control, also false in current settings. Some same-pass VIX re-entry messages also appear; this is not a silent bypass proven by an individual gate snapshot. |
| Breadth | Current saved setting false | Historical per-entry enabled state is not in config_snapshot. Do not assert the gate protected every entry. |
| Measured Kelly / no positive edge | Current configuration restricts it to Momentum | Intraday has no measured-edge block under that scope. Momentum was explicitly blocked on 11 November; intraday can still buy. |
| Consecutive loss halt | Fired for intraday on 30 Oct, 2/4/5/11 Nov | Blocked later new candidates (27,27,30,54,46 respectively); does not retroactively close all positions already opened. SQUARE_OFF is excluded from the strategy-loss estimator. |
| Liquidity / bid-ask / correlation | No individual pass snapshots | Code has turnover, optional spread and 0.85 correlation checks. Missing spread and no live market cap do not prove a measured pass. Only refusals are retained; passed values cannot be reconstructed exactly. |
| Sector | 52/74 records have sector Unknown and sector_alignment=50 | Neutral fallback adds 5 weighted points at the 10% sector weight. It is not confirmed sector strength. |
| Fundamental / analyst / short-interest components | Intraday weights are zero for earnings, analyst revisions and short interest | Neutral 50 in those fields did not contribute to the intraday weighted score. |
| Quote source | 74/74 BUY rows say live_quote | No provider observation timestamp is retained. Existing fallback-label/quote-age findings mean that label alone does not prove freshness. |
| IFR flag | 74/74 ifr_active=true; ifr_last_fire=2026-09-29 | BUY sets active from whether any last-fire date exists. This is not proof of fresh damage or a currently active sizing restriction. |
| Trading cost | Same CNC delivery schedule used for all modes | XR-059 remains open. No broker fee statement or validated intraday cost replay is included; recorded P&L is preserved. |
| Full gate evidence | Not stored | BUY retains component scores, timing boolean, sizing and entry levels, but not every passed/skipped gate, quote age, intraday bars, stop amendments or exact exit threshold. Unknown is not a pass. |

## Why the losses happened

1. **Wrong time scale for selection.** The scan's actual data path is daily despite an intraday 5-minute preset. A strong daily breakout can already be exhausted by an entry at 09:27 or 10:45; the retained journals do not contain a fresh ORB/VWAP confirmation. This is a proven wiring mismatch, while the profit impact needs replay on historical intraday data.
2. **Profit was not locked when the trail armed.** Fourteen trailing exits lost money. On 11 November, AARTIDRUGS rose only about 0.54% above entry at the sampled peak, ANTHEM about 0.80%, and AUROPHARMA about 0.48%; a roughly 1% trail can still exit below entry. Twenty-eight of 52 losing trades had a sampled peak more than 0.01% above entry. These peaks are sampled marks, not complete MFE histories or proof an order could have filled there.
3. **Targets were large for the remaining session.** Average target was 3.87%; just 2/74 closes were TAKE_PROFIT. Several stocks ended positive but below the target and were closed at 15:25. Reducing targets without an out-of-sample replay can also cut the few winners; it is not an automatic remedy.
4. **Costs amplified losses and erased eight small gains.** The implementation applies delivery STT/stamp/DP parameters to intraday records. All 74 still sum to a gross loss even before those recorded charges. Profit cannot be restored by relabelling fees.
5. **The same losing setup could re-enter.** AARTIDRUGS closed at 10:00 on 11 November for −₹3,890.41, reopened at 10:45 with the same 88.20 score, then lost another ₹6,409.84 at square-off: −₹10,300.25 across the two records. The later category loss halt did not undo that earlier re-entry. A new intraday setup requirement needs separate testing.
6. **Seven trades breached the same-day contract.** All seven overnight records came from 29 October and closed at 10:51 IST on 30 October. SGMART and FDC had STOP_LOSS due at 18:00 on 29 October, held because the market was closed. At 08:45 next morning, RETAG_EXITS widened all seven to Momentum levels. The position mode stayed intraday, so MAX_HOLD subsequently closed them. This sequence is in the journal and code. Historical Render log queries around 15:25 returned no rows, so the reason the original square-off job did not close them is **unproven**, not automatically attributed to sleep.
7. **Broad market strength did not ensure stock-level profit.** Stored positive Nifty daily changes coexist with losing intraday results. Missing same-window benchmark and sector data prevents an honest per-stock market attribution.

## Overnight stop changes

| Stock | Original stop ₹ | Boot stop ₹ | Original target ₹ | Boot target ₹ | Exit reason | Net ₹ |
| --- | --- | --- | --- | --- | --- | --- |
| INDHOTEL.NS | 797.85 | 769.50 | 832.85 | 972.01 | MAX_HOLD | 2,424.44 |
| SGMART.NS | 882.75 | 851.39 | 937.05 | 1,075.44 | MAX_HOLD | -19,930.68 |
| ORIENTHOT.NS | 157.56 | 151.96 | 165.14 | 191.95 | MAX_HOLD | 1,882.64 |
| AARTIDRUGS.NS | 443.32 | 427.57 | 466.92 | 540.09 | MAX_HOLD | -15,136.02 |
| ITCHOTELS.NS | 165.86 | 159.96 | 173.76 | 202.06 | MAX_HOLD | 1,914.44 |
| NUVOCO.NS | 330.78 | 319.03 | 347.23 | 402.98 | MAX_HOLD | -5,522.12 |
| FDC.NS | 377.89 | 364.46 | 398.26 | 460.37 | MAX_HOLD | -1,613.05 |

## Prioritized fixes — no strategy changes executed by this audit

| Priority / record | Fix | Validation required |
| --- | --- | --- |
| P1 INTRA-001 | Use an explicit, timestamped intraday bar provider and completed-bar ORB/VWAP/volume path; label unavailable inputs. | Historical replay at actual entry times, no daily-to-5m substitution, closed-bar and quote-age regressions. |
| P1 INTRA-002; related OPS-04 | Durable square-off deadline/retry and explicit overdue-position handling. Preserve original strategy identity and audit every stop change. | Missed job/restart/missing quote/late scan tests; no fabricated fill, no silent widening, no stale intraday contract mismatch. |
| P1 XR-059 | A reviewed intraday cost schedule keyed to product/segment, with a separate restatement preview. | Official broker/exchange fee inputs, fee date and actual statements where available; preserve original ledger and approvals for any restatement. |
| P1 INTRA-003 | Test net-break-even-aware trailing activation, volatility-scaled risk, reachable targets and fresh-setup re-entry rules. | Out-of-sample intraday replay including costs/slippage and target/stop order; compare expected value and drawdown, not win rate alone. No threshold change yet. |
| P1 INTRA-004 | Persist entry/exit decision evidence: gate state/value/threshold/reason, input timestamps, bar interval, stop amendments and actual exit trigger. | Every executed trade has an immutable explanation; unknown/skipped distinct from pass; no current-setting substitution for old decisions. |
| P2 related XR-009 / ROOT-AM07-04 / ROOT-ROUTE-09 | Fill sector identity and source-quality gaps, preserve quote timestamp and correct fallback labels. | Coverage/provenance tests and stale/missing inputs remain visibly unknown. |

## Every closed intraday trade

All times below are IST. Prices and P&L are ledger fills rounded for display; CSV keeps full precision. Entry stop/target are the original snapshots; the seven overnight changes are documented separately above.

| ID | Stock | Entry IST | Exit IST | Qty | Buy ₹ | Sell ₹ | Gross ₹ | Costs ₹ | Net ₹ | Exit | Entry stop / target ₹ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ID-001 | INDHOTEL | 2026-10-29 11:07:26 | 2026-10-30 10:51:21 | 896 | 810.00 | 814.54 | 4,065.80 | 1,641.36 | 2,424.44 | MAX_HOLD | 797.85 / 832.85 |
| ID-002 | SGMART | 2026-10-29 11:07:26 | 2026-10-30 10:51:21 | 498 | 896.20 | 858.17 | -18,937.53 | 993.15 | -19,930.68 | MAX_HOLD | 882.75 / 937.05 |
| ID-003 | ORIENTHOT | 2026-10-29 11:07:26 | 2026-10-30 10:51:21 | 2670 | 159.96 | 161.03 | 2,855.58 | 972.94 | 1,882.64 | MAX_HOLD | 157.56 / 165.14 |
| ID-004 | AARTIDRUGS | 2026-10-29 11:07:26 | 2026-10-30 10:51:21 | 955 | 450.07 | 435.23 | -14,174.73 | 961.29 | -15,136.02 | MAX_HOLD | 443.32 / 466.92 |
| ID-005 | ITCHOTELS | 2026-10-29 11:07:26 | 2026-10-30 10:51:21 | 1108 | 168.38 | 170.50 | 2,349.57 | 435.13 | 1,914.44 | MAX_HOLD | 165.86 / 173.76 |
| ID-006 | NUVOCO | 2026-10-29 11:07:26 | 2026-10-30 10:51:21 | 447 | 335.82 | 324.24 | -5,176.27 | 345.85 | -5,522.12 | MAX_HOLD | 330.78 / 347.23 |
| ID-007 | FDC | 2026-10-29 11:54:59 | 2026-10-30 10:51:21 | 411 | 383.64 | 380.61 | -1,246.20 | 366.85 | -1,613.05 | MAX_HOLD | 377.89 / 398.26 |
| ID-008 | BSOFT | 2026-10-30 10:25:35 | 2026-10-30 12:31:59 | 456 | 329.31 | 324.14 | -2,360.59 | 348.91 | -2,709.50 | STOP_LOSS | 324.37 / 346.53 |
| ID-009 | ACUTAAS | 2026-11-02 10:51:20 | 2026-11-02 13:00:06 | 70 | 4,010.60 | 3,925.04 | -5,989.75 | 636.81 | -6,626.55 | STOP_LOSS | 3,950.45 / 4,199.96 |
| ID-010 | NILKAMAL | 2026-11-02 12:20:27 | 2026-11-02 14:30:02 | 161 | 1,939.77 | 1,919.04 | -3,337.43 | 710.07 | -4,047.50 | TRAILING_STOP | 1,910.67 / 2,037.51 |
| ID-011 | SAREGAMA | 2026-11-02 10:51:20 | 2026-11-02 15:25:03 | 821 | 545.27 | 545.48 | 168.00 | 1,016.10 | -848.10 | SQUARE_OFF | 537.09 / 564.91 |
| ID-012 | PARACABLES | 2026-11-02 10:51:20 | 2026-11-02 15:25:03 | 4959 | 60.46 | 60.78 | 1,583.81 | 687.32 | 896.49 | SQUARE_OFF | 59.55 / 64.09 |
| ID-013 | WESTLIFE | 2026-11-02 10:51:20 | 2026-11-02 15:25:03 | 552 | 619.11 | 610.39 | -4,810.54 | 774.31 | -5,584.85 | SQUARE_OFF | 609.82 / 653.34 |
| ID-014 | SATIN | 2026-11-02 10:51:20 | 2026-11-02 15:25:03 | 1421 | 239.31 | 237.30 | -2,853.80 | 772.57 | -3,626.37 | SQUARE_OFF | 235.72 / 249.35 |
| ID-015 | JSWENERGY | 2026-11-02 10:51:20 | 2026-11-02 15:25:03 | 448 | 505.15 | 503.55 | -718.75 | 520.70 | -1,239.45 | SQUARE_OFF | 497.58 / 522.20 |
| ID-016 | GOKULAGRO | 2026-11-02 10:51:20 | 2026-11-02 15:25:03 | 916 | 217.61 | 214.54 | -2,808.52 | 458.26 | -3,266.79 | SQUARE_OFF | 214.34 / 225.62 |
| ID-017 | ATHERENERG | 2026-11-02 12:20:27 | 2026-11-02 15:25:03 | 182 | 1,780.49 | 1,773.71 | -1,233.43 | 738.50 | -1,971.93 | SQUARE_OFF | 1,753.78 / 1,857.69 |
| ID-018 | SYNGENE | 2026-11-03 12:24:26 | 2026-11-03 14:24:10 | 1304 | 412.31 | 405.80 | -8,487.80 | 1,208.07 | -9,695.87 | STOP_LOSS | 406.12 / 430.85 |
| ID-019 | PPLPHARMA | 2026-11-03 10:06:16 | 2026-11-03 15:11:18 | 1876 | 238.92 | 233.46 | -10,235.81 | 1,006.47 | -11,242.29 | STOP_LOSS | 235.34 / 248.92 |
| ID-020 | NAVINFLUOR | 2026-11-03 10:06:16 | 2026-11-03 15:11:18 | 47 | 8,482.24 | 8,502.75 | 963.85 | 907.46 | 56.39 | TRAILING_STOP | 8,355.01 / 8,783.85 |
| ID-021 | CHOICEIN | 2026-11-03 10:06:16 | 2026-11-03 15:11:18 | 347 | 753.18 | 744.18 | -3,122.54 | 596.48 | -3,719.02 | TRAILING_STOP | 741.88 / 775.57 |
| ID-022 | SAREGAMA | 2026-11-03 10:06:16 | 2026-11-03 15:25:00 | 891 | 556.23 | 561.77 | 4,937.03 | 1,128.13 | 3,808.90 | SQUARE_OFF | 547.88 / 576.40 |
| ID-023 | POONAWALLA | 2026-11-03 12:24:26 | 2026-11-03 15:25:00 | 320 | 468.83 | 469.37 | 169.89 | 351.23 | -181.35 | SQUARE_OFF | 461.80 / 490.40 |
| ID-024 | BOROLTD | 2026-11-03 12:24:26 | 2026-11-03 15:25:00 | 559 | 306.50 | 303.70 | -1,568.05 | 397.02 | -1,965.07 | SQUARE_OFF | 301.91 / 323.90 |
| ID-025 | MBAPL | 2026-11-03 12:24:26 | 2026-11-03 15:25:00 | 1870 | 183.32 | 183.40 | 143.32 | 781.84 | -638.53 | SQUARE_OFF | 180.57 / 190.39 |
| ID-026 | JISLJALEQS | 2026-11-03 10:06:16 | 2026-11-03 15:25:00 | 5366 | 27.95 | 28.34 | 2,049.03 | 353.13 | 1,695.90 | SQUARE_OFF | 27.53 / 29.32 |
| ID-027 | TATACONSUM | 2026-11-03 10:06:16 | 2026-11-03 15:25:00 | 235 | 1,050.73 | 1,066.37 | 3,675.74 | 571.33 | 3,104.42 | SQUARE_OFF | 1,034.96 / 1,078.45 |
| ID-028 | IIFLCAPS | 2026-11-03 10:06:16 | 2026-11-03 15:25:00 | 2938 | 347.82 | 347.88 | 153.22 | 2,298.80 | -2,145.59 | SQUARE_OFF | 342.61 / 352.73 |
| ID-029 | AGARWALEYE | 2026-11-03 10:06:16 | 2026-11-03 15:25:00 | 1293 | 511.71 | 512.74 | 1,341.84 | 1,495.28 | -153.43 | SQUARE_OFF | 504.03 / 528.94 |
| ID-030 | ATHERENERG | 2026-11-03 10:06:16 | 2026-11-03 15:25:00 | 179 | 1,786.39 | 1,794.00 | 1,362.15 | 731.63 | 630.52 | SQUARE_OFF | 1,759.60 / 1,861.81 |
| ID-031 | LAURUSLABS | 2026-11-03 10:06:16 | 2026-11-03 15:25:00 | 316 | 2,282.14 | 2,306.85 | 7,806.94 | 1,634.97 | 6,171.97 | SQUARE_OFF | 2,247.91 / 2,356.35 |
| ID-032 | ASAHIINDIA | 2026-11-04 09:57:03 | 2026-11-04 11:00:02 | 702 | 985.04 | 964.12 | -14,689.05 | 1,545.28 | -16,234.34 | STOP_LOSS | 970.27 / 1,016.06 |
| ID-033 | AARTIDRUGS | 2026-11-04 09:57:03 | 2026-11-04 11:29:58 | 1059 | 466.18 | 454.77 | -12,083.69 | 1,106.13 | -13,189.82 | STOP_LOSS | 459.19 / 485.87 |
| ID-034 | CHOICEIN | 2026-11-04 09:36:49 | 2026-11-04 15:08:08 | 403 | 744.97 | 746.43 | 586.08 | 687.17 | -101.09 | TRAILING_STOP | 733.80 / 767.36 |
| ID-035 | AFFLE | 2026-11-04 09:57:03 | 2026-11-04 15:08:08 | 172 | 1,633.72 | 1,622.59 | -1,914.04 | 641.63 | -2,555.67 | TRAILING_STOP | 1,609.21 / 1,697.02 |
| ID-036 | TATACONSUM | 2026-11-04 09:36:49 | 2026-11-04 15:25:01 | 372 | 1,077.24 | 1,065.17 | -4,490.49 | 906.40 | -5,396.89 | SQUARE_OFF | 1,061.08 / 1,105.14 |
| ID-037 | ASIANPAINT | 2026-11-04 09:57:03 | 2026-11-04 15:25:01 | 62 | 2,434.62 | 2,417.79 | -1,043.22 | 352.02 | -1,395.25 | SQUARE_OFF | 2,398.10 / 2,492.85 |
| ID-038 | BAJAJ-AUTO | 2026-11-04 09:57:03 | 2026-11-04 15:25:01 | 23 | 10,571.28 | 10,584.70 | 308.71 | 559.37 | -250.67 | SQUARE_OFF | 10,412.71 / 10,840.93 |
| ID-039 | CDSL | 2026-11-04 09:57:03 | 2026-11-04 15:25:01 | 401 | 1,323.06 | 1,323.14 | 30.84 | 1,201.09 | -1,170.25 | SQUARE_OFF | 1,303.22 / 1,362.58 |
| ID-040 | ITI | 2026-11-04 09:36:49 | 2026-11-04 15:25:01 | 875 | 254.05 | 252.55 | -1,306.64 | 511.12 | -1,817.76 | SQUARE_OFF | 250.24 / 264.08 |
| ID-041 | MBAPL | 2026-11-04 09:36:49 | 2026-11-04 15:25:01 | 3300 | 183.67 | 187.33 | 12,059.85 | 1,382.43 | 10,677.42 | SQUARE_OFF | 180.92 / 190.74 |
| ID-042 | POONAWALLA | 2026-11-04 09:36:49 | 2026-11-04 15:25:01 | 861 | 471.69 | 468.47 | -2,772.48 | 920.23 | -3,692.71 | SQUARE_OFF | 464.61 / 493.25 |
| ID-043 | PPLPHARMA | 2026-11-04 09:36:49 | 2026-11-04 15:25:01 | 2130 | 236.22 | 236.84 | 1,327.99 | 1,141.23 | 186.76 | SQUARE_OFF | 232.67 / 246.52 |
| ID-044 | ATHERENERG | 2026-11-04 09:36:49 | 2026-11-04 15:25:01 | 189 | 1,782.89 | 1,789.11 | 1,174.45 | 769.86 | 404.58 | SQUARE_OFF | 1,756.15 / 1,858.31 |
| ID-045 | AGARWALEYE | 2026-11-04 09:36:49 | 2026-11-04 15:25:01 | 1331 | 522.46 | 523.94 | 1,965.62 | 1,571.34 | 394.28 | SQUARE_OFF | 514.62 / 539.70 |
| ID-046 | AJMERA | 2026-11-05 09:33:40 | 2026-11-05 10:15:50 | 5210 | 136.74 | 134.27 | -12,845.28 | 1,593.91 | -14,439.19 | STOP_LOSS | 134.69 / 144.46 |
| ID-047 | HMAAGRO | 2026-11-05 09:33:40 | 2026-11-05 10:15:50 | 11668 | 28.25 | 27.82 | -5,110.99 | 747.01 | -5,858.01 | STOP_LOSS | 27.83 / 29.76 |
| ID-048 | ICIL | 2026-11-05 09:33:40 | 2026-11-05 10:15:50 | 613 | 506.55 | 497.10 | -5,793.97 | 703.52 | -6,497.49 | STOP_LOSS | 498.95 / 531.45 |
| ID-049 | AGARWALEYE | 2026-11-05 09:33:40 | 2026-11-05 13:19:27 | 1305 | 519.76 | 513.24 | -8,504.03 | 1,522.21 | -10,026.24 | TRAILING_STOP | 511.96 / 537.87 |
| ID-050 | APLLTD | 2026-11-05 09:33:40 | 2026-11-05 13:19:27 | 596 | 916.11 | 906.60 | -5,668.76 | 1,229.67 | -6,898.43 | TRAILING_STOP | 902.37 / 945.09 |
| ID-051 | FRACTAL | 2026-11-05 09:33:40 | 2026-11-05 13:19:27 | 470 | 829.81 | 823.94 | -2,762.13 | 884.26 | -3,646.39 | TRAILING_STOP | 817.37 / 871.44 |
| ID-052 | UNOMINDA | 2026-11-05 09:33:40 | 2026-11-05 13:19:27 | 137 | 1,229.91 | 1,303.45 | 10,074.06 | 402.81 | 9,671.25 | TAKE_PROFIT | 1,211.47 / 1,266.05 |
| ID-053 | KRISHNADEF | 2026-11-05 09:33:40 | 2026-11-05 13:36:14 | 210 | 1,270.63 | 1,275.46 | 1,013.66 | 613.03 | 400.63 | TRAILING_STOP | 1,251.58 / 1,338.76 |
| ID-054 | EMCURE | 2026-11-05 09:33:40 | 2026-11-05 14:00:02 | 260 | 2,178.59 | 2,260.87 | 21,392.87 | 1,303.51 | 20,089.36 | TAKE_PROFIT | 2,145.91 / 2,256.64 |
| ID-055 | PRINCEPIPE | 2026-11-05 09:33:40 | 2026-11-05 15:04:28 | 1577 | 317.56 | 313.44 | -6,490.14 | 1,127.82 | -7,617.97 | TRAILING_STOP | 312.80 / 331.41 |
| ID-056 | ABBOTINDIA | 2026-11-05 09:33:40 | 2026-11-05 15:25:00 | 6 | 26,998.49 | 26,551.72 | -2,680.65 | 374.99 | -3,055.64 | SQUARE_OFF | 26,593.52 / 27,666.35 |
| ID-057 | BOSCHLTD | 2026-11-05 09:33:40 | 2026-11-05 15:25:00 | 5 | 51,570.77 | 52,273.85 | 3,515.39 | 595.58 | 2,919.81 | SQUARE_OFF | 50,797.21 / 52,790.42 |
| ID-058 | CORONA | 2026-11-05 09:33:40 | 2026-11-05 15:25:00 | 271 | 2,397.10 | 2,378.81 | -4,956.03 | 1,461.86 | -6,417.89 | SQUARE_OFF | 2,361.14 / 2,482.26 |
| ID-059 | NAVINFLUOR | 2026-11-05 09:33:40 | 2026-11-05 15:25:00 | 48 | 8,772.88 | 8,818.59 | 2,193.80 | 958.86 | 1,234.95 | SQUARE_OFF | 8,641.29 / 9,093.21 |
| ID-060 | AARTIDRUGS | 2026-11-11 09:27:25 | 2026-11-11 10:00:02 | 891 | 495.85 | 492.60 | -2,890.60 | 999.80 | -3,890.41 | TRAILING_STOP | 488.41 / 523.69 |
| ID-061 | ANTHEM | 2026-11-11 09:27:25 | 2026-11-11 10:54:58 | 663 | 962.68 | 957.12 | -3,686.21 | 1,437.82 | -5,124.03 | TRAILING_STOP | 948.24 / 990.63 |
| ID-062 | AUROPHARMA | 2026-11-11 09:27:25 | 2026-11-11 12:04:50 | 546 | 1,841.12 | 1,823.79 | -9,463.52 | 2,251.58 | -11,715.10 | TRAILING_STOP | 1,813.50 / 1,885.51 |
| ID-063 | ARE&M | 2026-11-11 09:27:25 | 2026-11-11 13:29:59 | 292 | 768.48 | 758.62 | -2,880.16 | 514.18 | -3,394.34 | TRAILING_STOP | 756.96 / 791.87 |
| ID-064 | CORONA | 2026-11-11 10:45:54 | 2026-11-11 13:29:59 | 281 | 2,431.11 | 2,396.10 | -9,838.82 | 1,531.67 | -11,370.49 | TRAILING_STOP | 2,394.65 / 2,547.49 |
| ID-065 | UNOMINDA | 2026-11-11 10:45:54 | 2026-11-11 13:51:07 | 397 | 1,391.40 | 1,380.81 | -4,202.68 | 1,245.46 | -5,448.14 | TRAILING_STOP | 1,370.52 / 1,438.87 |
| ID-066 | MOTILALOFS | 2026-11-11 10:45:54 | 2026-11-11 14:56:06 | 506 | 1,183.49 | 1,156.72 | -13,545.67 | 1,339.51 | -14,885.18 | STOP_LOSS | 1,165.74 / 1,232.87 |
| ID-067 | AIIL | 2026-11-11 09:27:25 | 2026-11-11 15:25:02 | 423 | 601.80 | 592.70 | -3,848.14 | 580.56 | -4,428.69 | SQUARE_OFF | 592.77 / 625.02 |
| ID-068 | GLENMARK | 2026-11-11 10:45:54 | 2026-11-11 15:25:02 | 325 | 2,653.23 | 2,668.17 | 4,855.27 | 1,947.17 | 2,908.10 | SQUARE_OFF | 2,613.43 / 2,749.82 |
| ID-069 | GLAND | 2026-11-11 10:45:54 | 2026-11-11 15:25:02 | 216 | 3,314.36 | 3,254.37 | -12,956.62 | 1,601.60 | -14,558.22 | SQUARE_OFF | 3,264.64 / 3,428.19 |
| ID-070 | ENTERO | 2026-11-11 10:45:54 | 2026-11-11 15:25:02 | 202 | 1,993.00 | 2,002.80 | 1,980.02 | 917.28 | 1,062.74 | SQUARE_OFF | 1,963.10 / 2,093.47 |
| ID-071 | SAILIFE | 2026-11-11 10:45:54 | 2026-11-11 15:25:02 | 259 | 1,911.56 | 1,928.24 | 4,320.15 | 1,126.36 | 3,193.79 | SQUARE_OFF | 1,882.88 / 1,986.77 |
| ID-072 | AARTIDRUGS | 2026-11-11 10:45:54 | 2026-11-11 15:25:02 | 896 | 493.35 | 487.31 | -5,412.13 | 997.71 | -6,409.84 | SQUARE_OFF | 485.95 / 521.19 |
| ID-073 | KALYANKJIL | 2026-11-11 10:45:54 | 2026-11-11 15:25:02 | 433 | 672.99 | 661.67 | -4,900.40 | 661.75 | -5,562.16 | SQUARE_OFF | 662.89 / 696.01 |
| ID-074 | NYKAA | 2026-11-11 10:45:54 | 2026-11-11 15:25:02 | 424 | 354.43 | 353.62 | -340.91 | 351.26 | -692.17 | SQUARE_OFF | 349.11 / 365.43 |

## Source paths and reproducibility

- `trading/engine.py`: scan_universe/scan (daily fetch and entry gates); _open (BUY evidence); _target_for (intraday valuation exemption); refresh, _close, square_off_intraday and mode_cfg.
- `trading/scoring.py`, `trading/timing.py`, `trading/modes.py`: actual daily scoring and configured intraday weights/preset.
- `trading/exits.py`: stop/target/trailing/score/time priority and retag_positions_per_mode; retag changes levels without changing position.mode.
- `trading/costs.py`, `trading/config.py`: one delivery cost schedule and default control scope.
- `web/scheduler.py`: 30-minute session refresh, between-category checks and 15:25 square-off; historical missed-slot cause remains unproven.
- `trading/paper.py`, `web/trade_performance.py`, `web/db.py`: paper ledger schema, per-segment net outcomes, archive semantics.

Checks: all 74 arithmetic identities gross − costs = net passed to sub-paise precision; every trade has exactly one matched BUY and SELL; totals reconcile by date and by exit reason. No external news explanation was substituted for a journaled trigger. No historical fee/P&L record was rewritten.
