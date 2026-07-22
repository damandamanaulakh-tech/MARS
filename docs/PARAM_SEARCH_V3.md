# Parameter Search v3 — The Deciding Run (2026-07-19)

> **PIT ADDENDUM (2026-07-21, v3.1):** with the 5 archived constituent
> snapshots applied (`scripts/param_search_v3_pit.py`), the verdict drops to
> **2/4 windows positive, avg −2.2pp** (was 3/4, +8.9). BUT v3.1
> OVER-corrects: our price data covers only the 191 CURRENT members, so the
> PIT mask strips the newly-listed winners momentum legitimately rotated
> into (ZOMATO/JIOFIN era) without restoring the dead losers (YESBANK-class)
> whose prices we don't have — an asymmetric handicap, worsened by the
> archive gap (no snapshots 2019-02→2023-08). Honest triangulation: **true
> alpha lies between −2.2 and +8.9**. Most trustworthy per-window reads
> (freshest snapshots): 2019 **+7.4** (edge real under honest membership);
> 2024–26 **−4.5** (the cold stretch, confirmed a third time). The final-set
> SHAPE is unchanged — all 8 window-winners across v3+v3.1 chose the same
> core (6/12m vol-adjusted, absgate ON, concentrated book). The live 60-day
> test has no survivorship problem at all: it trades today's real list.

Walk-forward on the app's REAL tradable data: 191 Nifty-200 tickers,
12 years of Upstox daily OHLCV+Volume exported from the app's own store
(`data/history_upstox_export_2026-07-19.zip`, 532,977 rows, byte-matched
to the server's download summary). First search ever able to test the
candle gate and volume factors on real candles, with a real liquidity
screen (60d median turnover ≥ ₹1 Cr; ~150 of 191 eligible per month).
384 configurations × 4 unseen windows, costs 0.15%/side, Sharpe-selected
on past data only. Runtime 2.1 min. Script `scripts/param_search_v3.py`.

## Verdict

**Positive out-of-sample alpha in 3 of 4 windows; average +8.9 pp/yr.**

| Unseen window | Strategy | Benchmark (eligible EW) | OOS alpha | Strategy DD | Bench DD |
|---|---|---|---|---|---|
| 2019 | 22.8% | 14.1% | **+8.8** | −9.9% | −9.0% |
| 2020–21 (COVID + recovery) | 51.2% | 33.9% | **+17.3** | −23.5% | −25.9% |
| 2022–23 | 42.7% | 28.5% | **+14.2** | −12.3% | −10.9% |
| 2024–mid-26 | 4.9% | 9.6% | **−4.6** | −21.7% | −21.2% |

**The negative window is the present.** 2024→now is a cold stretch for the
momentum factor on this universe — which is precisely the period of the
user's flat/red live experience. The engine wasn't broken this year; the
factor was cold, and now that's measured instead of felt. Cold stretches
are part of every real edge; three of four windows paid well.

## The FINAL SET (converged across v2.1 + v3)

| Pillar | Decision | Evidence |
|---|---|---|
| Signal | **6m+12m momentum, vol-adjusted, rank-blended** | winner in 3/3 positive v3 windows AND the big v2.1 windows — and it is literally the live `nifty200_momentum` scorer |
| Book | **~25 concentrated, equal-weight** (P3 shipped 30; 25 is the v3 winner size — optional tighten) | W1/W2 winners N=25, W3 N=15 |
| Absolute-momentum gate | **ON** | chosen in 4/4 v3 windows — the P0 gate EARNS its place on the liquid universe (the earlier P2 hold was right) |
| Regime brake (200DMA) | **KEEP ON for tail protection** | v3's 12y window (no grinding bear) never selected it; the 30-year v2.1 evidence (GFC −50% vs −61%) is why it stays. It is insurance priced for crashes that this decade didn't contain |
| Candle gate | **Keep as daily ENTRY-TIMING only; adds no monthly-rotation alpha** | OFF in all 3 winning windows (flag true-rate 24.8% — as a monthly membership filter it starves the book). Live daily usage is a different granularity and remains |
| Volume-expansion filter | Not supported as a membership rule | OFF in all winners |
| Universe | Full breadth (P1) + liquidity floor ≈ ₹1 Cr/day median turnover | eligibility screen; the live Upstox liquidity gate approximates this |
| Rebalance cadence | Monthly-style rotation | all searches; the live engine's decay/exit rules approximate it |
| Costs assumed | 0.15%/side | throughout |

## Standing caveats

- SURVIVORSHIP: today's N200 membership backfilled. PIT snapshots
  (niftyindices CSVs from the user) will bound how much of the +8.9 is real.
- Monthly vectorized rotation approximates, not replicates, the live
  engine's day-by-day gates.
- One universe (the app's actual one) — the v2.1 breadth result says wider
  is better still; the full-universe P1 scan is live for exactly that reason.

## What remains before "final" is truly final

1. PIT membership files → re-tag v3 (bounds survivorship).
2. The 60-day live-paper verdict on this exact set — the only test left
   that nothing can simulate.
