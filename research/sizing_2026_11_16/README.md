# Goal10 — Position sizing and loss-control comparison

**Result: no experimental sizing policy promoted.** The training-selected `measured_stop_risk` fails the predeclared positive-expectancy and executed-trade requirements. It made zero holdout trades. Avoided losses are not proof of a profitable trading method.

This is an isolated, fixed-clock intraday comparison. Application correctness fixes for the actual entry stop and all-loss Momentum reading are tracked separately in [the release](../../docs/repairs/releases/2026-11-16-sizing.md).

## Data and chronology

- Protocol/runtime/universe committed before acquisition: `7bdb92fb0923467b8ca6939e7269fef27a7037e9`.
- Source, training choice and outcomes committed before holdout: `cd0eb2d8355494b81c91a2eb250eb5c7f37bd154`.
- Holdout access marked at `2026-11-16T14:53:59.748424+00:00`; first request `2026-11-16T14:53:59.760363+00:00`.
- Train: 3–28 August 2026 (20 sessions). Validation: 1–30 September (21). Holdout: 1 October–13 November (29). July supplies prior observations.
- 75/75 requests succeeded: 105960 source five-minute bars; 105750 accepted bars in 1410 complete regular stock-sessions. Excluded: 210 bars from the unverified Sunday special session on 8 November. Zero missing regular-session outcome exclusions.
- 15 new symbols, selected deterministically from the archived Nifty200 list without consulting prices: 360ONE.NS, ASHOKLEY.NS, BEL.NS, COALINDIA.NS, EICHERMOT.NS, GRASIM.NS, HYUNDAI.NS, IREDA.NS, LAURUSLABS.NS, MAZDOCK.NS, NMDC.NS, PIDILITIND.NS, SBICARD.NS, TATACONSUM.NS, TVSMOTOR.NS.
- No symbol/timestamp overlaps Goal08/09. The calendar period was already studied through other names; this is a fresh historical panel, not a new market era or forward evidence. Archived membership and provider adjustments can bias results.

## Frozen comparison

Each regular session offers one 10:00 long opportunity per stock. Every arm shares the same price rules: 1.5% initial stop, 1% trail, completed-5m-ATR target clamped to 1–6%, and 15:25 square-off. Signal features have a five-minute publication lag. Two OHLC ordering scenarios and a declared 30-minute observation scenario model execution uncertainty; they are not known ticks or mathematical bounds. There is no liquidity guarantee.

Whole shares use a ₹150,000 minimum ticket. The experimental risk budget is ₹2,500 per trade, with a ₹300,000 notional ceiling against a fixed ₹5,000,000 reference book. Planned risk includes adverse entry/stop fills and dated buy/sell charges. If the minimum ticket cannot fit, the trade is refused. Higher slippage can therefore prevent a trade; the budget is not relaxed after seeing results.

The measured arm uses all net shadow-reference outcomes from the preceding 20 sessions, with no exit-reason exclusions or return clipping. It requires 50 trades and five complete sessions; Beta prior 50 at 0.5, multiplier 0.29, confidence ramp 300. Empty, incomplete, all-loss, all-flat and no-loss samples are explicit. The loss-budget arm uses preceding five-session net P&L and a ₹12,500 loss limit. Same-day and future outcomes cannot affect size.

Shadow observations are the completed counterfactual reference trades, including opportunities a candidate did not execute. They are not recorded as candidate fills. Original MARS trades and settings are unchanged by this research. Buy/sell fees use the selected published Upstox Basic assumption and the dated NSE schedule; historic account charges are not certified.

## Holdout at 5 bps adverse slippage per side

Each row covers the same 435 opportunities. Amounts are INR. Drawdown is the maximum decline of cumulative day-aggregated closed P&L, not intraday equity drawdown or a compounded portfolio backtest.

| Arm | Execution | Trades | Net P&L | Net/trade | Paired gain vs control | Losses beyond planned stop risk | Closed-P&L drawdown |
|---|---|---:|---:|---:|---:|---:|---:|
| fixed_slab | open_high_low_close | 435 | -169,739.19 | -390.21 | 0.00 | 0 | 169,739.19 |
| fixed_slab | open_low_high_close | 435 | -168,891.11 | -388.26 | 0.00 | 0 | 168,891.11 |
| fixed_slab | sampled_30m | 435 | -194,178.44 | -446.39 | 0.00 | 65 | 194,178.44 |
| loss_budget_stop | open_high_low_close | 119 | -54,934.55 | -461.63 | 114,804.64 | 0 | 54,934.55 |
| loss_budget_stop | open_low_high_close | 119 | -54,934.55 | -461.63 | 113,956.57 | 0 | 54,934.55 |
| loss_budget_stop | sampled_30m | 133 | -50,370.33 | -378.72 | 143,808.11 | 19 | 50,370.33 |
| measured_stop_risk | open_high_low_close | 0 | 0.00 | unknown | 169,739.19 | 0 | 0.00 |
| measured_stop_risk | open_low_high_close | 0 | 0.00 | unknown | 168,891.11 | 0 | 0.00 |
| measured_stop_risk | sampled_30m | 0 | 0.00 | unknown | 194,178.44 | 0 | 0.00 |
| no_entry | open_high_low_close | 0 | 0.00 | unknown | 169,739.19 | 0 | 0.00 |
| no_entry | open_low_high_close | 0 | 0.00 | unknown | 168,891.11 | 0 | 0.00 |
| no_entry | sampled_30m | 0 | 0.00 | unknown | 194,178.44 | 0 | 0.00 |
| stop_risk | open_high_low_close | 426 | -173,259.91 | -406.71 | -3,520.72 | 0 | 173,259.91 |
| stop_risk | open_low_high_close | 426 | -172,386.11 | -404.66 | -3,495.00 | 0 | 172,386.11 |
| stop_risk | sampled_30m | 426 | -202,568.90 | -475.51 | -8,390.46 | 65 | 202,568.90 |

## What the losses mean

The measured arm stayed out of all 435 holdout opportunities in every scenario. At 5 bps with 30-minute observations it withheld ₹399,401.08 of losing reference trades and also missed ₹205,222.64 of positive returns. The ₹194,178.44 difference comes from zero exposure; net expectancy per executed trade is unknown. It cannot pass the required minimum of 100 executions.

The loss-budget arm improved the 30-minute comparison by ₹143,808.11, but its 133 executed trades still lost ₹50,370.33 after costs. Stop-risk sizing alone produced a larger loss than the control. Neither finding justifies promoting a profitable strategy claim. The other candidates remain diagnostics after the training winner was locked; no switching to a different winner after holdout.

Under 30-minute observations the control and stop-risk arm each had 65 trades losing more than their planned initial-stop amount. A correctly calculated position size cannot guarantee the exit price when the stop is observed late or crossed by a gap. Protective execution cadence, signal quality and portfolio validation remain separate work.

The all-cash control is deliberately ineligible for selection. Statistical promotion also requires positive validation/holdout net expectancy and paired gains at both 5 and 15 bps, a positive day-cluster bootstrap lower bound, no worse closed-P&L drawdown and adequate coverage. All 30 bps results are reported as stress.

Full scalar comparisons: [results.csv](results.csv). Every size, reason, prior sample, stop risk, executed/counterfactual outcome and attribution is retained in compressed outcome files. [Protocol](protocol.json), [selection](selection.json), [holdout](holdout.json), and raw request manifests are immutable study evidence.

## Reproduction

Use the interpreter and numerical versions recorded before acquisition in [runtime.json](runtime.json).
```sh
python -m research.sizing_2026_11_16.study verify
python -m research.sizing_2026_11_16.report --check
```

CI replays all periods and compares each saved decision/outcome and summary exactly; it also retains the full application tests and zero-skip guard. Holdout acquisition refuses uncommitted inputs, replacement snapshots, changed seals and consumed holdouts. Re-tuning requires a new untouched study.

Sources rechecked 16 November: [Upstox candle contract](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/), [published Basic charges](https://upstox.com/brokerage-charges/), [NSE transaction/IPFT circular effective 1 March 2026](https://nsearchives.nseindia.com/content/circulars/FA73061.pdf).
