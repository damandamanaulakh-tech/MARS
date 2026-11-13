# MARS repair work

This repository is the source of truth for repair progress. The user directed
on 2 November 2026 that updates belong in the repo and workflow, not Desktop
folders. Existing local copies are historical exports and are not maintained.

## Current recorded status

Earlier verified batches (2 November):

| Batch | Finding | Recorded result |
|---|---|---|
| 1 | ROOT-ROUTE-03, numeric-input subclaim | Merged/deployed in [PR #187](https://github.com/damandamanaulakh-tech/MARS/pull/187); broader mutation concerns remain open |
| 2 | RUN-20261102-01, daily category recovery | Merged/deployed in PR #187; Momentum look-only pass completed at 16:18 IST; 185 candidates blocked after close; full rotation remains unverified |
| 3 | AUD-001, unfinished drawdown liquidation | Merged/deployed in [PR #188](https://github.com/damandamanaulakh-tech/MARS/pull/188); local and CI suites each 2,647 passed, zero skipped; production health verified |

Latest verified functional revision: `29ca4aa338f3836b6f5c41335e2b06868823ce71` ([PR205](https://github.com/damandamanaulakh-tech/MARS/pull/205)), Render deployment `dep-dbralp142hec73bd2o1g`, live `2026-11-13T06:16:36.920728Z`; health/readiness verified `2026-11-13T06:25:12Z`. Final PR CI passed **3,127 canonical tests with zero skips**. Local 3,128 included one additional syntax parameter for an unrelated duplicate script. [Cost release and exact evidence](releases/2026-11-12-product-trading-costs.md). Documentation-only commits can advance main without changing the deployed application.

**SEQ-05 is shipped and health verified; SEQ-06 is in progress.**
Upstox Basic fees are versioned by product, side and effective NSE schedule.
Buy charges are allocated once, daily DP persists, and original financial
history remains intact. [Separate 74-trade comparison](reviews/2026-11-12-intraday-cost-comparison.csv).
Normal fee-bearing lifecycle observation remains separate from deployment health.
The [11-step programme](EXECUTION_PLAN.md) retains all earlier pending runtime
observations and future research. [SEQ-04 evidence](releases/2026-11-12-completed-intraday-data.md),
[decision evidence](releases/2026-11-11-decision-evidence.md),
[intraday recovery](releases/2026-11-11-intraday-recovery.md) and
[quote-gap protection](releases/2026-11-11-partial-quote-equity.md) retain their scope.

The [11 November intraday audit](reviews/2026-11-11-intraday-trade-audit.md) reconciles 74 closes and records four new findings. [Full trade CSV](reviews/2026-11-11-intraday-trades.csv).

## Where to work

- [Workflow and completion requirements](WORKFLOW.md)
- [Prioritized goals](GOALS.md)
- [Ordered execution programme](EXECUTION_PLAN.md) and [machine-readable progress](execution-plan.json)
- [All 165 original records](BACKLOG.md), with [complete JSON](backlog.json)
  and [CSV](backlog.csv) for every finding, acceptance criterion and status
- [Additional incidents](incidents.json), tracked separately from the original count
- [Release evidence for batches 1–3](releases/2026-11-02.md)
- [2 November buy-gate audit: all 197 Momentum rows](evidence/2026-11-02-buy-gates.md)
- [Historical audit evidence](evidence/MARS_REVIEW.md), with baseline caveats

The 165 records are not 165 completed fixes. `updated_state` retains the audit's
evidence classification; `workflow` records repair progress. An unresolved
compound record stays partial even when one subclaim has shipped. Historical
line numbers and draft claims must be rechecked against the current source.

Run `python scripts/check_repair_records.py` to check record retention, unique
IDs, CSV/JSON/table consistency, repository evidence paths and release links.
GitHub Actions runs this check alongside the existing zero-skip application suite.

SEQ-06 update, 13 November: verified official identity and exact-window context are implemented; 3186 local checks pass with zero skips; CI and shipment are in progress. [Batch record](releases/2026-11-13-market-sector-context.md). Legacy score policy and normal runtime observation remain separate.
