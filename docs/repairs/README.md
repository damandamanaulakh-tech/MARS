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

Latest deployed functional revision: `47e7e0536b6c84f9f9f6698e5d1f76e80b315b14` ([PR207](https://github.com/damandamanaulakh-tech/MARS/pull/207)), Render `dep-dbrcergjo6nc73ev2ug0`, live `2026-11-13T08:18:20.424623Z`. CI: **3185 passed, zero skips**. HTTP liveness is available; database writes fail at the shared Atlas quota. [Evidence](releases/2026-11-14-context-storage-incident.md).

**SEQ-06 is deployed; persistence verification is blocked by Atlas quota.**
Official sector identities, matching completed comparison windows and explicit
unknowns are implemented. The storage blocker prevents durable runtime proof;
Goal07 remains queued. SEQ-05's fee model and the original financial history
remain unchanged, including the [separate 74-trade comparison](reviews/2026-11-12-intraday-cost-comparison.csv).
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

SEQ-06 is deployed in PR207 (3185 canonical CI tests, zero skips), but live verification found Atlas quota blocking sector and portfolio writes. Goal06 remains active; Goal07 remains queued. [Shipment](releases/2026-11-13-market-sector-context.md) and [storage incident](releases/2026-11-14-context-storage-incident.md).

Owner storage decision, 14 November: keep the free tier. [Lossless archive plan](releases/2026-11-14-lossless-archive-plan.md) prepared with measured sizes, preservation and restore requirements; no production migration performed.

Diagnostic follow-up and the owner-selected free-tier archive plan are merged in [PR208](https://github.com/damandamanaulakh-tech/MARS/pull/208): **3192 canonical CI tests, zero skips**. Its deployment awaits safe capacity recovery; live remains PR207. Goal06 is still open.

The [offline archive verifier](releases/2026-11-14-archive-preflight.md) passes 54 focused and 3,248 local tests with zero skips. Live readers/writers are not changed. Private native capture and restore checks are in progress; capacity recovery and Goal06 remain open.
