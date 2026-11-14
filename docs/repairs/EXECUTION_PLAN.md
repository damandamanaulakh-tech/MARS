# Ordered repair programme — 11 November 2026

Owner instruction: show the sequence, start one by one, and track completion in
the repository. This is the current execution order for G4/G13 and the related
data/risk work. It does not remove any of the 170 backlog records or three
separate incidents. [Goals](GOALS.md), [full backlog](BACKLOG.md),
[machine-readable sequence](execution-plan.json).

## Current batch

**SEQ-06 is deployed; persistence verification is blocked by Atlas quota.**
[PR207](https://github.com/damandamanaulakh-tech/MARS/pull/207) verifies official sector identity and saves stock, sector and market comparisons
on identical completed windows. All **3,185 canonical CI tests pass with zero
skips**. Exact revision `47e7e0536b6c84f9f9f6698e5d1f76e80b315b14` is live.
Read-only verification found Atlas rejects writes at 513 MB / 512 MB, leaving
no saved sector snapshot. HTTP liveness is not healthy persistence.
[Release evidence](releases/2026-11-13-market-sector-context.md) and
[capacity recovery requirements](releases/2026-11-14-context-storage-incident.md).
The prior SEQ-05 cost release remains recorded separately; it is not reopened
or erased by this incident.

Normal fee lifecycle and earlier quote-gap/recovery/decision/data-gap observations
remain separate. No production trade or scan was induced. Net performance remains
unproven; the original ledger, strategy thresholds and research sequence are retained.

## Sequence and acceptance

| Step | Goal / finding IDs | Work | Required completion evidence | Status |
|---|---|---|---|---|
| SEQ-01 | G4; AUD-002 | Preserve last observed holding values through quote gaps and restarts; show source/time and unknown values | Unchanged INR 52 lakh stays 52 lakh under a partial response; real loss still halts; no retained mark fills an exit; JSON/Mongo round trip and split handling pass | SHIPPED; health verified; normal quote-gap observation pending |
| SEQ-02 | G13; INTRA-002; related OPS-04 | Durable intraday square-off deadline, retry and consistent segment/stop treatment | Missed job, restart, missing quote and late scan fixtures; original protection preserved; ordinary deadline outcome observed when available | SHIPPED; health verified; normal recovery observation pending |
| SEQ-03 | G13/G9; INTRA-004, ROOT-AM07-04, ROOT-ROUTE-09 | Immutable entry/exit gate results and quote provenance | Values, thresholds, pass/fail/skip/unavailable, provider/receipt time, bar interval, stop amendments and exit trigger attached to each new trade | SHIPPED; 2994 local/CI passed, zero skips; health verified; normal lifecycle observation pending |
| SEQ-04 | G13; INTRA-001, ROOT-AM07-04 | Connect completed intraday bars to the actual intraday data path | Correct interval, session alignment, no future/unclosed bars, explicit missing-data handling and recorded comparison with the daily baseline | DEPLOYED_HEALTH_VERIFIED_RUNTIME_PENDING; PR203; 3067 local/CI tests, zero skips |
| SEQ-05 | G13; XR-059 | Product-appropriate intraday cost model and separate historical comparison | Dated broker/exchange schedule and account-plan assumptions; buy/sell/partial-exit cases; original ledger preserved | DEPLOYED_HEALTH_VERIFIED_RUNTIME_PENDING; PR205; 3127 canonical CI tests, zero skips |
| SEQ-06 | G9/G13; INTRA-004, XR-009, XR-061 | Sector identity, input coverage and same-window benchmark/sector context | Unknown never counts as measured strength; timestamped stock/sector/index comparisons; context does not silently become a new gate | DEPLOYED_PERSISTENCE_BLOCKED; PR207; 3185 canonical CI tests, zero skips |
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

SEQ-06 is deployed in PR207 (3185 canonical CI tests, zero skips), but live verification found Atlas quota blocking sector and portfolio writes. Goal06 remains active; Goal07 remains queued. [Shipment](releases/2026-11-13-market-sector-context.md) and [storage incident](releases/2026-11-14-context-storage-incident.md).

Owner storage decision, 14 November: keep the free tier. [Lossless archive plan](releases/2026-11-14-lossless-archive-plan.md) prepared with measured sizes, preservation and restore requirements; no production migration performed.

Diagnostic follow-up and the owner-selected free-tier archive plan are merged in [PR208](https://github.com/damandamanaulakh-tech/MARS/pull/208): **3192 canonical CI tests, zero skips**. Its deployment awaits safe capacity recovery; live remains PR207. Goal06 is still open.

The [offline archive verifier](releases/2026-11-14-archive-preflight.md) passes 54 focused and 3,248 local tests with zero skips. Live readers/writers are not changed. Private native capture and restore checks are in progress; capacity recovery and Goal06 remain open.
