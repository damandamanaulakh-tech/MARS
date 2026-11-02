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

This document records the workflow; it does not grant new permissions beyond
the user's instructions for the current task.
