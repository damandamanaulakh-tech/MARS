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

Latest verified functional revision: `51e075457480715b111ea35c780baf477b4a8aa0` ([PR #203](https://github.com/damandamanaulakh-tech/MARS/pull/203)), live 12 November 2026 11:38:24 IST; health/readiness verified 12 November 2026 11:39:02 IST. Local and PR CI suites each passed 3,067 tests with zero skips. The [completed-intraday release ledger](releases/2026-11-12-completed-intraday-data.md) records the exact deployment and pending normal five-minute scan/data-gap observations. The [decision-evidence release](releases/2026-11-11-decision-evidence.md), [intraday recovery release](releases/2026-11-11-intraday-recovery.md), [partial-quote release](releases/2026-11-11-partial-quote-equity.md), [resource ledger](releases/2026-11-04-online-resources.md) and [segment allocation evidence](releases/2026-11-03-segment-allocation-performance.md) retain their separate scope and limitations. These are timestamped observations; documentation-only commits can advance main without changing the application.

**SEQ-04 is shipped and health verified; SEQ-05 is locally tested (3,128 passing, zero skips), pending CI and deployment.**
[Cost repair and preserved historical comparison](releases/2026-11-12-product-trading-costs.md).
Completed five-minute signal bars, session/interval/freshness checks, explicit
missing-data coverage and independent protective exits passed 3067 local and CI
tests with zero skips. PR203 is merged and its exact functional revision is live.
[Deployment and test evidence](releases/2026-11-12-completed-intraday-data.md).
The [11-step programme](EXECUTION_PLAN.md) retains ordinary runtime observations
and Steps 05–11. Daily-history studies do not validate five-minute performance.

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
