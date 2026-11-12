# Ordered repair programme — 11 November 2026

Owner instruction: show the sequence, start one by one, and track completion in
the repository. This is the current execution order for G4/G13 and the related
data/risk work. It does not remove any of the 170 backlog records or the two
separate incidents. [Goals](GOALS.md), [full backlog](BACKLOG.md),
[machine-readable sequence](execution-plan.json).

## Current batch

**SEQ-04 is implemented and locally tested; publication, CI and deployment are pending.**
The actual intraday scan and held-position refresh use completed five-minute bars.
Wrong timeframe/session, future or unfinished bars, stale quotes, gaps and missing
provider data are explicitly rejected or marked unavailable. Saved coverage is
visible on Scan and decision explanations. 3,063 full-suite tests pass with
zero failures or skips; 150 focused checks include 69 new cases.
[Completed-intraday release](releases/2026-11-12-completed-intraday-data.md).

SEQ-01, SEQ-02 and SEQ-03 remain shipped and health verified with their ordinary
runtime observations pending. The last verified functional revision before this
batch is `5bee374a6c9de71b2bb15203434f90fa0a7eb7c1` (PR201), health checked
12 November 2026 00:34:03 IST. No production scan or trade is induced for testing.

After SEQ-04 shipment, SEQ-05 is next. Five-minute strategy profitability remains
unproven; later cost, research and combined-validation goals keep their own scope.

## Sequence and acceptance

| Step | Goal / finding IDs | Work | Required completion evidence | Status |
|---|---|---|---|---|
| SEQ-01 | G4; AUD-002 | Preserve last observed holding values through quote gaps and restarts; show source/time and unknown values | Unchanged INR 52 lakh stays 52 lakh under a partial response; real loss still halts; no retained mark fills an exit; JSON/Mongo round trip and split handling pass | SHIPPED; health verified; normal quote-gap observation pending |
| SEQ-02 | G13; INTRA-002; related OPS-04 | Durable intraday square-off deadline, retry and consistent segment/stop treatment | Missed job, restart, missing quote and late scan fixtures; original protection preserved; ordinary deadline outcome observed when available | SHIPPED; health verified; normal recovery observation pending |
| SEQ-03 | G13/G9; INTRA-004, ROOT-AM07-04, ROOT-ROUTE-09 | Immutable entry/exit gate results and quote provenance | Values, thresholds, pass/fail/skip/unavailable, provider/receipt time, bar interval, stop amendments and exit trigger attached to each new trade | SHIPPED; 2994 local/CI passed, zero skips; health verified; normal lifecycle observation pending |
| SEQ-04 | G13; INTRA-001, ROOT-AM07-04 | Connect completed intraday bars to the actual intraday data path | Correct interval, session alignment, no future/unclosed bars, explicit missing-data handling and recorded comparison with the daily baseline | LOCAL_TESTED; CI/deployment and normal runtime observation pending |
| SEQ-05 | G13; XR-059 | Product-appropriate intraday cost model and separate historical comparison | Dated broker/exchange schedule and account-plan assumptions; buy/sell/partial-exit cases; original ledger preserved | QUEUED |
| SEQ-06 | G9/G13; INTRA-004, XR-009, XR-061 | Sector identity, input coverage and same-window benchmark/sector context | Unknown never counts as measured strength; timestamped stock/sector/index comparisons; context does not silently become a new gate | QUEUED |
| SEQ-07 | G13; XR-081; owner candle decision | Candle patterns assist selection, with recorded owner controls | Advisory contribution is explicit; absence alone does not veto an otherwise eligible entry; protective exits remain independent; invalid candles remain unavailable | QUEUED |
| SEQ-08 | G13; INTRA-003 | Compare net-profit-aware trailing exits and reachable targets | Replay with costs, slippage and intrabar ambiguity; unseen-period comparison of expectancy and drawdown; exact candidate settings reviewed before promotion | QUEUED |
| SEQ-09 | G13; INTRA-003 | Fresh-setup/cooldown experiments after a failed trade | Record the changed setup; measure losses avoided and recoveries missed; prevent same-bar hindsight or automatic score reuse | QUEUED |
| SEQ-10 | G7/G13; INTRA-003, XR-026, XR-031 | Intraday sizing, loss accounting and measured-edge controls | All relevant net outcomes and exact stop risk included; empty/all-loss samples explicit; policy thresholds justified by held-out evidence | QUEUED |
| SEQ-11 | G9/G13; XR-078, XR-103, INTRA-001, INTRA-003 | Combined replay and forward paper validation | Versioned data/code/settings, unseen periods, realistic execution/costs, adequate sample coverage, net expectancy/drawdown and no profitability guarantee | QUEUED |

## How each step advances

1. Reproduce the current behavior and pin the baseline revision.
2. Implement one bounded repair and retain failure-path regressions.
3. Pass focused and full checks, the repair-record validator and zero-skip CI.
4. Verify the exact reviewed head, merge and matching deployment.
5. Check health and the affected behavior when it can be observed safely.
6. Record the evidence and mark the next step active. Runtime behavior that has
   not occurred naturally remains pending; no production trades are created to
   manufacture completion.

An implementation can be shipped while a particular production scenario is
still awaiting observation. Those are separate columns in the JSON record.
An audit or experiment alone does not count as a shipped strategy change.
Steps 08–11 produce tested candidates; they do not authorize arbitrary new
thresholds or a promise of positive returns. The owner has already requested
the candle-advisory behavior in step 07.

## Remaining programme

The broader G5–G10 persistence, owner concurrency, risk, job, data and valuation
backlog remains open according to each original record. G12's five failing
FRED numerical feeds and other documented source limitations remain open.
Completing this sequence does not close those findings by implication.
