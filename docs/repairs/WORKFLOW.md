# Repository repair workflow

## Canonical records

Update `docs/repairs/backlog.json`, its CSV and readable table, the relevant
goal, and a batch/release record in the same PR as each repair. Additional
incidents go in `incidents.json`. Preserve baseline IDs in `baseline-ids.json`;
append new work with provenance rather than renumbering or deleting old work.
Desktop exports are not a second source of truth and receive no ongoing updates.

## Repair sequence

1. Fetch current main and record its SHA. Read the finding, acceptance criteria,
   actual callers and relevant historical evidence. Keep unproven claims explicit.
2. Reproduce the failure using isolated inputs. Record the failing behavior and
   the tests that distinguish it from the correct behavior.
3. Make one bounded repair. Record any policy decision separately; a bug fix
   does not authorize changing risk thresholds or placing real orders.
4. Run focused regression and applicable broader checks. Resolve every failure
   and skipped test. Record the actual test totals, not merely that a command ran.
5. Update the tracker, goals and implementation/batch record. Open a PR with
   the problem, resulting behavior, test evidence and remaining limitations.
6. Require successful CI, including `repair-records` and the no-skipped-tests
   step. Verify the reviewed head before merge. Record the resulting merge SHA.
7. If deployed, record the deployment ID, deployed SHA, terminal status and
   post-deploy health evidence. Observe automatic deployments rather than
   triggering duplicates. A merged change alone is not proof it is live.
8. Verify the affected runtime behavior when safe and observable. If a scenario
   cannot be induced safely, retain its isolated regression proof and record the
   production observation as pending. Do not create trades just to close a card.

## Status meanings

| State | Required evidence |
|---|---|
| NOT_STARTED | Finding retained with acceptance criteria; no repair claimed |
| REPRODUCED / IN_PROGRESS | Reproduction and pinned baseline; implementation underway |
| LOCAL_TESTED | Bounded implementation and actual passing checks |
| PUBLISHED / CI_PENDING | Reviewable branch/PR exists; CI result not yet complete |
| MERGED | Merge SHA and PR verified |
| DEPLOYED_HEALTH_VERIFIED | Matching deployed SHA is live and health/readiness checked |
| RUNTIME_VERIFIED | The affected workflow's acceptance evidence has been observed |
| PARTIAL_* | Only a stated subclaim meets the corresponding state |

Use a clear pending suffix for an unresolved runtime check. Rejected, obsolete
or duplicate findings remain in the tracker with their disposition and evidence.
Do not equate health verification with an observed Momentum pass or liquidation.

## Keeping records current after merge

Deployment evidence is only known after merge. Add that evidence in a follow-up
documentation PR with the exact observed revision and timestamp. A subsequent
docs-only revision does not erase the earlier functional release evidence.
At the start of new work, refresh live state when needed rather than treating
an old release note as a real-time status page.

## CI and local commands

```sh
python scripts/check_repair_records.py --sync  # after editing backlog.json
python scripts/check_repair_records.py         # read-only CI check
pip install -r requirements.txt pytest openpyxl
python -m pytest trading/tests/ web/tests/ -q -ra --junitxml=pytest-results.xml
```

The GitHub workflow rejects any skipped test in that report. Do not bypass
this check, suppress a failure, delete a test, or mark an unresolved record
complete to move to the next batch. Keep production credentials out of tests.
Run full suites sequentially when they share the default temporary portfolio
directory. Preserve failed-run state separately and use a fresh isolated test
book for release validation; never reuse production state.

This document records the workflow; it does not grant new permissions beyond
the user's instructions for the current task.

## Online source batches

- Trace provider → fetch/parse → acknowledged storage → actual page or formula.
  A source link or an existing adapter alone is not a completed connection.
- Record publisher URL, observation/publication date, attempt time and scope.
  Keep unavailable, empty, partial, stale, login-required and insufficient-history
  outcomes distinct. Never substitute zero or a successful verdict for missing data.
- Use bounded requests and retained good observations. An unreadable prior cache
  must not be replaced with an empty snapshot. Completion markers require mapped
  data and acknowledged writes; independently failing sources retry independently.
- Test parsers, source/consumer wiring, data retention, invalid inputs and failed
  writes offline. Observe normal deployed ingestion separately: development-host
  access does not prove Render access, and health alone does not prove a feed landed.
- Keep source-derived news/context separate from changes to trading thresholds or
  valuation assumptions. Filing-specific financial facts require their own period,
  issuer, units and document evidence; general headlines cannot stand in for them.

## Decision-evidence batches

- Capture each gate at the branch that evaluates it. Record path, segment,
  value, threshold, state and reason; a check not reached is skipped and a
  missing input is unavailable. Do not rerun current settings to explain an
  old decision. An owner instruction and an automatic selection are different
  paths, linked by reference when appropriate.
- Keep provider time, receipt time and bar interval distinct. Preserve the
  per-price source through caches and concurrent requests. LTP without an
  exchange timestamp must say that it is unavailable. A source label or age
  observation alone does not authorize a new trading threshold.
- Verify entry, amendment and each partial/final exit identity across restarts,
  storage failures and owner read pages. Archive bulky snapshots separately
  from financial samples; require acknowledgement before discarding a copy.
- Keep historical coverage and view limits explicit. After deploy, observe a
  normal scan and trade lifecycle when available; never induce a production
  trade solely to turn a pending evidence check into completed status.
