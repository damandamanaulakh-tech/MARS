# Parameter Search v2 — The Breadth Referendum (2026-07-19)

> **CORRECTION (same day, v2.1):** the panel contains ~4,476 corrupt prints
> (found by a third-party data-quality audit, confirmed here). After surgical
> repair the verdict is **4/5 windows positive, avg OOS alpha +9.3pp**
> (was 5/5, +10.5) — see `docs/archive/REVIEW_AI_BACKTEST_SUITE.md` and
> `docs/param_search_v2_1_results.json`. v2.1 supersedes the numbers below.

Walk-forward momentum search over the committed 1991→2024 adjusted-close
panel (1,417 symbols; 687 with ≥15y). 96 configurations, 5 out-of-sample
windows including the 2008 crash. Vectorized monthly engine, costs
0.15%/side on turnover, selection by Sharpe on PAST data only, judged on
unseen windows. Script `scripts/param_search_v2.py`; raw results
`docs/param_search_v2_results.json`. Runtime 0.9 min.

## The verdict

**Positive out-of-sample alpha in 5 of 5 windows vs the investable-universe
benchmark (average +10.5 pp/yr), and 4 of 5 vs the mega-cap proxy
(average +13.5 pp/yr).** After v1's 0-of-4 on ten mega-caps, this is the
first real evidence of edge in this project — and it lives exactly where
theory says: cross-sectional breadth.

| Unseen window | Strategy CAGR | Universe EW | Mega-cap EW | α vs universe | Strategy maxDD | Universe maxDD |
|---|---|---|---|---|---|---|
| 2004–07 bull | 49.9% | 44.1% | 40.4% | **+5.9** | −29.1% | −22.6% |
| 2008–10 GFC | 6.6% | 4.7% | 9.3% | **+1.9** | **−25.7%** | **−60.7%** |
| 2011–15 | 32.3% | 15.3% | 10.3% | **+17.0** | −9.6% | −28.2% |
| 2016–19 | 13.9% | 9.6% | 9.2% | **+4.3** | −19.5% | −37.7% |
| 2020–24 | 67.4% | 43.8% | 33.2% | **+23.7** | −17.4% | −31.1% |

## The configuration the walk-forward kept choosing

The five independent window-winners CONVERGE — this is the "final set"
candidate, and three of its four pillars are already the app's own design:

| Pillar | Winning choice | In the app today? |
|---|---|---|
| Signal | **6m+12m momentum, vol-adjusted (sharpe-style), rank-blended** | YES — this is the nifty200_momentum scorer |
| Regime brake | **ON in 5/5 windows** — all-cash when the universe index < its 200DMA. Cut the GFC drawdown from −61% to −26% | YES — the app's regime master-switch concept |
| Portfolio | **Top 20, equal weight, monthly rebalance** | Close — app holds up to 50; 20 concentrated scored better |
| Per-stock absolute-momentum gate | **OFF in 5/5 windows** — the P0 gate only dragged; the regime brake + cross-sectional ranking already do the work | App has it ON — candidate for removal |

## Why v1 failed and v2 didn't (the lesson)

Same math family, same discipline, one variable changed: **breadth**.
On 10 mega-caps there is nothing to rotate into — momentum churned and paid
costs. Across 400–1,000 investable names, the strategy owns the winners'
tail while the regime brake steps out of the crashes. Momentum is a breadth
strategy; the app has been running it in a 192-stock large-cap pond.

## Honest caveats — the alpha is REAL but the numbers are optimistic

1. **Survivorship is the big one.** The panel skews to symbols alive in
   2024; dead names (momentum's worst losers) are underrepresented. True
   alpha is lower than measured — both benchmarks share the bias, which
   protects the SIGN of the alpha more than its size. Point-in-time
   membership (DATA_NEEDED #2) is the cure.
2. **No liquidity screen exists in a close-only panel** (penny filter ≥₹20
   only). A top-20 book can include names too thin to trade at size. The
   Upstox OHLCV download enables a turnover screen — expect some alpha to
   survive it, not all.
3. Close-only: candle gate and volume factors untested here.
4. Mega-cap benchmark = 76 oldest names, fixed membership — crude.

## What this means for the app (proposals, not yet applied)

P1. Switch the scan universe to full breadth (the 2,376-symbol machinery
    already exists: `universes.load_full_universe` + rotating buckets),
    with a liquidity floor once volume data lands.
P2. Turn OFF the per-stock absolute-momentum gate in momentum mode.
P3. Tighten the book toward ~20 concentrated positions in momentum mode.
P4. Keep the regime brake exactly as is — it is the proven pillar.
P5. Harden the result: PIT membership + liquidity screen + OHLCV
    (Upstox downloader) → search v3 on the constrained universe.
