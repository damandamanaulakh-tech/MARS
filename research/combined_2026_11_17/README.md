# Goal 11 — Combined actual-strategy validation

**Decision: no settings promotion. Forward performance acceptance remains pending.** The historical observations below are the actual engine's decisions on a bounded, newly acquired panel. They do not establish full-universe or future profitability.

## Version and data evidence

- Frozen source commit: `89a8e9baa02241efe4ee48f5bb3efc1449864c69`; [archived source](source.zip) contains every sealed strategy/input member.
- Source archive SHA256: `528adcd95cdb73a2b8ef93c47a058066b5e378d511beeee532ca6f9f516bba73`.
- The first reproduction exposed omitted non-Python dependencies in the source ZIP. [runtime-assets.json](runtime-assets.json) pins all 459 supplemental assets (203,326,589 bytes already in the repository) to the same pre-acquisition Git commit. Their bytes were verified against the original Git blob IDs when the manifest was created; reproduction checks SHA256 before isolated copying. Trigger parameters, measured side weights and IFR thresholds cannot silently fall back when reproducing. The original source ZIP, seal and outcomes remain unchanged; this packages existing inputs rather than adding market data or retuning settings.
- Raw provider archive SHA256: `6a75caed7524b4e4667001ebfec07c9e636e6de1a0703d9dc9497154d7604a19`; [manifest](manifest.json) records URLs, request/receipt times and response digests.
- The response digest hashes the canonical JSON encoding of the response-text string; the archive digest hashes the complete compressed file bytes.
- [Protocol](protocol.json), [saved settings](saved_settings.json), [source/runtime seal](seal.json) and [acquisition marker](opened.json) were fixed before price acquisition at 2026-11-17T09:27:02.836045+00:00. Consumed data is never relabelled unseen. Saved overrides combine with defaults and mode presets in the sealed source; absent saved fields are not silently set to zero.
- Window: **2026-09-01 through 2026-11-16, 51 regular sessions, 20/1205 configured names**. The 20 current survivors are disjoint from the 30 Goal08–10 names. This is not point-in-time full-universe membership or a claim that the companies never appeared in other research.
- 106/106 provider requests succeeded. 12,056 daily rows and 89,775 completed regular-session five-minute rows are retained, including warm-up. 294 rows outside the supported schedule/validity rules are excluded explicitly.
- Inputs come from [Upstox V3 historical candles](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/). Current published Basic charges are a versioned paper assumption, not a claim about a historical account's contract notes; see [Goal05 cost evidence](../../docs/repairs/releases/2026-11-12-product-trading-costs.md).

## What actually ran

`TradingEngine.scan`, `refresh` and `square_off_intraday` execute the production scoring, timing, sizing, risk, rotation, exit and accounting code. Only provider I/O, clock, alerts and isolated-book persistence are adapted. No fixed-clock entries or patched eligibility scores replace strategy selection.

A fresh INR 5 crore shared book starts without inherited trades or measured-Kelly history. Both real daily rotations run at 09:20 and 14:30 IST, with 30-minute refreshes and refreshes between categories, and intraday closing at 15:25. The simulation assumes instantaneous scans and complete supplied requests within the 120-name fetch cap. It excludes optional five-minute scans and unscheduled owner actions; it does not reproduce provider latency, outages or production's opening ledger.

Daily indicators see only prior dates; five-minute indicators see only completed bars. Executions use the contemporaneous boundary open with adverse slippage on each side. Missing boundary quotes cannot fill an order. No future high/low path, prior close, old stop or retained valuation mark becomes an execution price. A gap can therefore exit beyond the stop. Network access during replay is forbidden.

## Combined results after costs and slippage

Net expectancy is the mean net INR result per fully closed economic trade; partial legs are grouped. Realised-leg P&L includes partial exits of still-open trades and is shown separately in metrics.csv. Marked equity includes cash, open holdings and fees already debited. The hypothetical liquidation estimate includes another adverse sell fill and estimated exit fees, without creating fabricated terminal trades.

| Slippage each side | Closed trades | Net expectancy INR | Fully closed net P&L INR | Daily marked drawdown % | Scheduled drawdown % | Marked return % | Open holdings | Estimated liquidation equity INR |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 5 bps | 171 | -4,295.05 | -734,453.57 | 1.6620 | 1.6811 | -1.1598 | 14 | 49,411,533.58 |
| 15 bps | 174 | -5,436.72 | -945,989.43 | 1.9379 | 1.9549 | -1.5939 | 14 | 49,189,355.48 |
| 30 bps | 176 | -6,975.63 | -1,227,710.66 | 2.3380 | 2.3502 | -2.1739 | 14 | 48,891,792.48 |

Both drawdown series can miss unobserved intrabar lows. These three scenarios reuse the same market history; they are sensitivity tests, not independent samples. No scenario is selected after seeing its result.

## Segment results at 5 bps per side

| Segment | Closed trades | Exit legs | Net expectancy INR | Fully closed net P&L INR | Win % | Daily marked drawdown % | Still open |
|---|---:|---:|---:|---:|---:|---:|---:|
| Momentum | 11 | 11 | -52,077.14 | -572,848.53 | 9.09 | 3.3931 | 8 |
| Intraday | 152 | 152 | -780.66 | -118,660.95 | 38.82 | 2.6338 | 0 |
| Short | 5 | 5 | -943.96 | -4,719.81 | 60.00 | 0.8366 | 2 |
| Swing | 3 | 3 | -12,741.42 | -38,224.27 | 33.33 | 0.5412 | 4 |
| Positional | 0 | 0 | Unknown / no sample | Unknown / no sample | Unknown / no sample | -0.0000 | 0 |

Segment curves start from each configured capital share and include net realised and marked open P&L; they are accounting attribution, not independent funded books. The original sealed segment curve mixed open budget categories with closed stored modes. A [declared reporting amendment](attribution-amendment.json) makes both use stored mode. [Corrected attribution](attribution.json.gz) is separate from the preserved original results; full replay proves every strategy decision, fill, fee, closed-trade count, expectancy and shared-book result unchanged. This correction uses consumed data and makes no new unseen-data claim. Ocean uses its real budget path but holdings retain their stored segment; it is not counted as another independent strategy sample. No closed sample means unknown expectancy, never a zero-loss or successful strategy.

## Exit and cost attribution at 5 bps per side

These are observed exit triggers, not proof that a different exit would have made money. Gross P&L already reflects adverse fill prices; allocated charges are then deducted. [trades.csv](trades.csv) lists every simulated closed leg with its entry, exit, quantity, reason, gross P&L, costs and net P&L. [exit_reasons.csv](exit_reasons.csv) covers every declared slippage scenario.

| Segment | Exit trigger | Legs | Losing legs | Gross P&L INR | Charges INR | Net P&L INR |
|---|---|---:|---:|---:|---:|---:|
| Intraday | SQUARE_OFF | 64 | 41 | -68,506.32 | 14,210.75 | -82,717.07 |
| Intraday | STOP_LOSS | 8 | 8 | -115,219.03 | 2,720.17 | -117,939.20 |
| Intraday | TAKE_PROFIT | 32 | 0 | 224,430.54 | 7,891.18 | 216,539.36 |
| Intraday | TRAILING_STOP | 48 | 44 | -121,969.15 | 12,574.90 | -134,544.05 |
| Momentum | STOP_LOSS | 10 | 10 | -639,264.12 | 25,645.78 | -664,909.90 |
| Momentum | TAKE_PROFIT | 1 | 0 | 93,476.69 | 1,415.32 | 92,061.37 |
| Short | TRAILING_STOP | 5 | 2 | 3,982.65 | 8,702.46 | -4,719.81 |
| Swing | SCORE_DECAY | 2 | 1 | -32,866.12 | 2,999.21 | -35,865.33 |
| Swing | TRAILING_STOP | 1 | 1 | -1,906.93 | 452.01 | -2,358.94 |

## Coverage and unresolved limitations

- Each scenario executed 510 category scans across 51 sessions. All declared scenarios, gate states, unavailable inputs and remaining holdings are preserved in the compressed result.
- [metrics.csv](metrics.csv) includes every segment/scenario, partial-leg P&L, allocated realised-leg costs, unknown costs and mark coverage. [gates.csv](gates.csv) contains every recorded ranked-candidate gate/state count. [coverage.csv](coverage.csv) records requested/supplied provider observations. Counts repeat symbols over scans; they are not unique stocks or independent trades. Unreached candidates are not silently treated as passed gates.
- Missing historical contexts: point_in_time_news, earnings_growth, analyst_revisions, futures_oi, FII_DII_publication_times, policy_announcements, sector_identity_and_indices, historical_valuations, historical_bid_ask, full_universe_and_delisted_names, provider_adjustment_certification, corporate_action_announcement_times, event_calendar_publication_lineage.
- Missing context follows the existing production fallback policy, with explicit unknown coverage. Neutral fallback values are not measured neutral market conditions. A 20-name cross-section changes breadth, correlation, rotation and IFR. Provider revisions, corporate adjustments and announcement-time lineage are not independently certified.
- The daily /backtest route remains an approximation even after its sizing and terminal-fee fixes. It is not the source of this actual-engine study.

## Forward paper observation

The app's authenticated `/validation` page and `/api/validation` expose append-only observations from ordinary paper activity. Code/runtime/settings identities, compressed own-symbol entry bars, economic-trade and exit-leg identities, actual stored fees/fills, pending exports and capture failures remain inspectable. Manifests are stored once; each event waits for an acknowledged portfolio checkpoint and durable run manifest. Failed exports retry across restart. Inherited positions and earlier closes are baseline, not new forward trades. Owner entries, incomplete quantities, unverified costs and mixed exit versions are explicit.

Forward cohort expectancy excludes inherited/owner entries. Cohort drawdown stays unknown when equity belongs to a shared mixed-version book; operational shared-book drawdown is labelled separately and removes recorded capital transfers. Own-symbol entry data does not reconstruct the full peer/provider snapshot.

Acceptance remains open for at least 30 genuinely new regular sessions and 30 fully closed economic trades in each claimed segment, with complete cost/identity/coverage evidence. These floors are not statistical proof. No production scan/trade is induced and no setting is automatically promoted. Release and current observation status belong in the [release ledger](../../docs/repairs/releases/2026-11-17-combined-validation.md).

## Exact offline reproduction

```sh
# Python 3.12.16
pip install -r requirements.txt -r research/combined_2026_11_17/requirements.txt
python -m research.combined_2026_11_17.reproduce
python -m research.combined_2026_11_17.reproduce --attribution
python -m research.combined_2026_11_17.report --check
```

Reproduction extracts and hashes the original source archive in an isolated directory, checks the frozen numerical runtime, and compares the entire numerical result byte-for-byte. Independent slippage scenarios run in separate fresh processes. The input acceleration removes older intraday rows which the original validator would discard anyway; accepted bars and all numerical results must still match the original sequential, unaccelerated run. The archive is preserved when later production instrumentation changes. Original raw data, consumed-marker and outcomes are never overwritten.
