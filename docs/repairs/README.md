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

Latest deployed functional revision: `deb07a316007e40bf5765462d2194b169d70409f` ([PR214](https://github.com/damandamanaulakh-tech/MARS/pull/214)), Render `dep-dbt893vf3r2c73f6rclg`, live `2026-11-16T04:22:13.237036Z`. CI: **3311 passed, zero skips**. Health, owner-page behavior and post-deploy Upstox acceptance verified. [Evidence](releases/2026-11-16-candle-assistance.md).

**SEQ-06 engineering is shipped in PR212. Health, acknowledged source saves and release-restart preservation are verified; normal market-session comparison remains pending.**
Official sector identities, matching completed comparison windows and explicit
unknowns are implemented. The temporary storage recovery is [verified](releases/2026-11-14-storage-recovery.md);
Goal07 engineering is shipped; normal market-session comparison evidence is pending. SEQ-05's fee model and the original financial history
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

Initial SEQ-06 delivery was PR207; temporary storage recovery was verified on 14 November. SEQ-06 engineering is shipped in PR212 with 3261 canonical CI tests and zero skips. The matching Render deployment is live; health, acknowledged 14/15-source saves and financial-book preservation across the release restart are verified. A five-stock stored-data replay correctly reports missing-window inputs as unknown. Normal market-session comparison observation remains pending; SEQ-07 engineering is shipped; see the 16 November candle-assistance release. [Goal06 release](releases/2026-11-14-context-use-time.md).

Owner storage decision, 14 November: keep the free tier. [Lossless archive plan](releases/2026-11-14-lossless-archive-plan.md) prepared with measured sizes, preservation and restore requirements; no compressed online migration performed; the separately authorized temporary cold archive is recorded below.

Diagnostic follow-up and the owner-selected free-tier archive plan are merged in [PR208](https://github.com/damandamanaulakh-tech/MARS/pull/208): **3192 canonical CI tests, zero skips**. These diagnostics are now live in the PR212 release; the historical deployment deferral and prior-process state limits remain recorded separately.

The [offline archive verifier](releases/2026-11-14-archive-preflight.md) is merged in PR210 with 3,247 canonical CI tests and zero skips (54 focused / 3,248 local). The [recovery record](releases/2026-11-14-storage-recovery.md) proves all 82,740 stored documents were backed up/restored and exactly 10,000 scan-only copies removed for 74.15 MiB of temporary relief. All 30,486 remaining archive records are byte-identical. Live compression, sustainable retention and prior-process spill reconciliation remain open.

SEQ-07 engineering is shipped in [PR214](https://github.com/damandamanaulakh-tech/MARS/pull/214). All **3311 canonical local and CI tests pass with zero skips**. Revision `deb07a316007e40bf5765462d2194b169d70409f` is live; readiness and the owner candle-assistance page are verified. Upstox returned six of six requested LTP quotes before and after deployment. Normal advisory decision/trade observation remains pending; SEQ-08 is queued. [Release evidence](releases/2026-11-16-candle-assistance.md).
