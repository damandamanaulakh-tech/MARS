# Parameter Search v1 — Walk-Forward Verdict (2026-07-19)

The first systematic parameter search this project has had. 36 configurations
× 4 walk-forward windows = 148 cost-inclusive backtests on the committed
10-year clean set (`scripts/param_search_v1.py`; raw log
`docs/param_search_v1_progress.jsonl`; full results
`docs/param_search_v1_results.json`). Runtime 35.7 min.

## The verdict, in one line

**No configuration in the grid produced positive out-of-sample alpha.
0 of 4 windows. Average out-of-sample alpha: −10.9 points.**

## Per-window: what the optimizer chose vs what then happened

| Window | Best config (chosen on PAST data only) | In-sample | Out-of-sample (unseen year) | Basket buy-hold | OOS alpha |
|---|---|---|---|---|---|
| 2019 | pure momentum, floor 50, default exits, candle ON | +28.6%, Sharpe 1.57 | **−0.7%** | +12.2% | **−12.9** |
| 2020 (COVID) | pure momentum, floor 60, tight trail, candle OFF | +28.7%, Sharpe 1.21 | +11.0% | +24.2% | **−13.2** |
| 2022 | pure momentum, floor 60, tight trail, candle OFF | +61.4%, Sharpe 1.13 | **−5.0%** | +5.1% | **−10.0** |
| 2024→mid-26 | pure momentum, floor 60, tight trail, candle OFF | +64.3%, Sharpe 0.94 | +15.0% | +22.6% | **−7.5** |

## What this actually means (read this twice)

1. **The in-sample/out-of-sample gap is the whole story of this project.**
   Every optimization window produced beautiful numbers — +28% to +64%,
   Sharpe 0.9–1.6. Every unseen window then lost to lazily holding the same
   10 stocks. Any AI (or backtest) that ever showed a great historical run
   of this strategy was showing the left column. Live paper trading kept
   delivering the right column. Both were real; only one was the future.
2. **The failure is consistent, not unlucky.** Four different market years
   (grind-up, crash-recovery, sideways-down, strong bull) — the same sign
   every time. The optimizer even converged on the same family of configs
   (pure momentum + tight trail) and that family kept losing OOS.
3. **On 10 mega-caps, momentum-rotation with these ingredients does not
   beat buy-and-hold after costs.** Churn (19–53 round trips/window) pays
   costs and misses recoveries; the concentrated large-cap pond gives
   cross-sectional momentum nothing to grip.

## Caveats (cut both ways)

- Self-basket benchmark (no index series in the committed data) — but this
  makes the test HARDER to fail upward, not easier: the basket is the exact
  opportunity set the strategy picks from.
- SURVIVORSHIP-BIASED tag on every run: all 10 tickers lived to 2026, which
  flatters both strategy and basket equally.
- 10-ticker breadth is momentum's worst habitat. This verdict is about the
  strategy ON THIS UNIVERSE — it is not yet the final word on the strategy.

## What changes next — search v2 (the real referendum)

`data/nse_all_stock_data.csv.zip` (committed the same day) provides an
adjusted-close panel of **1,417 symbols, 1991→2024-07, 687 symbols with
≥15 years** — the breadth where cross-sectional momentum actually operates,
with room for a proper large-cap index proxy, the 2008 crash, and rotating
mid-cap winners. Search v2 = the same walk-forward discipline over that
panel (close-only momentum core; candle/volume legs excluded until OHLCV
lands via the Upstox downloader). If v2 also comes back negative in every
window, the honest conclusion is that the engine's proven value is its
DEFENSE (regime + drawdown ladder over an index core), not stock selection —
and the product should be shaped accordingly.
