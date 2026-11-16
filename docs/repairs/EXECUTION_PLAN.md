# Ordered repair programme — 11 November 2026

Owner instruction: show the sequence, start one by one, and track completion in
the repository. This is the current execution order for G4/G13 and the related
data/risk work. It does not remove any of the 170 backlog records or four
separate incidents. [Goals](GOALS.md), [full backlog](BACKLOG.md),
[machine-readable sequence](execution-plan.json).

## Current batch

SEQ-08 research is evaluated; no settings promotion. The fixed-clock exit-only study used 15 stocks and 690 paired holdout entries over 23 sessions, with costs, adverse fills and observation-cadence sensitivity. The training-selected combined candidate failed net-expectancy and robustness criteria. PR216 merged; 3359 canonical local/CI tests pass with zero skips, with full offline outcome/report reproduction verified. [Study and release record](releases/2026-11-16-profit-protection.md). SEQ-09 remains queued.

SEQ-07 engineering is shipped in [PR214](https://github.com/damandamanaulakh-tech/MARS/pull/214). All **3311 canonical local and CI tests pass with zero skips**. Revision `deb07a316007e40bf5765462d2194b169d70409f` is live; readiness and the owner candle-assistance page are verified. Upstox returned six of six requested LTP quotes before and after deployment. Normal advisory decision/trade observation remains pending; SEQ-08 research is recorded above. [Release evidence](releases/2026-11-16-candle-assistance.md).

**SEQ-06 engineering is shipped in PR212. Health, acknowledged source saves and release-restart preservation are verified; normal market-session comparison remains pending.**
[PR212](https://github.com/damandamanaulakh-tech/MARS/pull/212) rechecks sector identity at decision time
against one frozen source snapshot, preserving unknowns and instrument conflicts.
All **3,261 canonical CI tests pass with zero skips**; 126 expanded focused and
3,262 local full tests also pass. Revision `520ec29d6b0f02c8e23ebdd339f2e652509bbc52`
is live. The new process acknowledged 14/15-source saves; Mongo read-back and
unchanged 53-position/214-trade financial fields verify release-restart persistence.
[Release, CI, deployment and five-stock missing-data evidence](releases/2026-11-14-context-use-time.md).

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
| SEQ-06 | G9/G13; INTRA-004, XR-009, XR-061 | Sector identity, input coverage and same-window benchmark/sector context | Unknown never counts as measured strength; timestamped stock/sector/index comparisons; context does not silently become a new gate | DEPLOYED_PERSISTENCE_VERIFIED_RUNTIME_PENDING; PR212; 3261 canonical CI tests, zero skips; release restart verified |
| SEQ-07 | G13; XR-081; owner candle decision | Candle patterns assist selection, with recorded owner controls | Advisory contribution is explicit; absence alone does not veto an otherwise eligible entry; protective exits remain independent; invalid candles remain unavailable | DEPLOYED_HEALTH_VERIFIED_RUNTIME_PENDING; PR214; 3311 local/CI passed, zero skips; owner page verified |
| SEQ-08 | G13; INTRA-003 | Compare net-profit-aware trailing exits and reachable targets | Replay with costs, slippage and intrabar ambiguity; unseen-period comparison of expectancy and drawdown; exact candidate settings reviewed before promotion | RESEARCH_COMPLETE_NO_PROMOTION |
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

Initial SEQ-06 delivery was PR207; temporary storage recovery was verified on 14 November. SEQ-06 engineering is shipped in PR212 with 3261 canonical CI tests and zero skips. The matching Render deployment is live; health, acknowledged 14/15-source saves and financial-book preservation across the release restart are verified. A five-stock stored-data replay correctly reports missing-window inputs as unknown. Normal market-session comparison observation remains pending; SEQ-07 engineering is shipped; see the 16 November candle-assistance release. [Goal06 release](releases/2026-11-14-context-use-time.md).

Owner storage decision, 14 November: keep the free tier. [Lossless archive plan](releases/2026-11-14-lossless-archive-plan.md) prepared with measured sizes, preservation and restore requirements; no compressed online migration performed; the separately authorized temporary cold archive is recorded below.

Diagnostic follow-up and the owner-selected free-tier archive plan are merged in [PR208](https://github.com/damandamanaulakh-tech/MARS/pull/208): **3192 canonical CI tests, zero skips**. These diagnostics are now live in the PR212 release; the historical deployment deferral and prior-process state limits remain recorded separately.

The [offline archive verifier](releases/2026-11-14-archive-preflight.md) is merged in PR210 with 3,247 canonical CI tests and zero skips (54 focused / 3,248 local). The [recovery record](releases/2026-11-14-storage-recovery.md) proves all 82,740 stored documents were backed up/restored and exactly 10,000 scan-only copies removed for 74.15 MiB of temporary relief. All 30,486 remaining archive records are byte-identical. Live compression, sustainable retention and prior-process spill reconciliation remain open.

Decision-time freshness follow-up: source identity is rechecked against the frozen
batch snapshot when used, with unproved receipt times unknown. [Repair and
real-adapter evidence](releases/2026-11-14-context-use-time.md): 79 focused tests,
zero skips; expanded focused suite 126 and corrected full local suite 3262 passed.
PR212 canonical CI passed 3261 tests, zero skips; exact deployment and release
restart persistence verified. Normal market-session comparison remains pending.
