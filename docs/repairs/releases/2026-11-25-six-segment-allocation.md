# Six-segment allocation — owner order of 21 November, release work 25 November

The owner explicitly requested a change in the real app, not the investor
presentation: Momentum 30%, Intraday 10%, Swing 24%, Short 24%, Positional 10%,
Ocean 2%. Baseline `3d19595605cb81467f2efb751a086d4266d6899f`; live predecessor
`8e894dd39ae393d4589be4103bf226f8ef75445c`. Work interrupted 21 November;
resumed 25 November. No prior deployment of this order is claimed.

## Resulting policy

| Segment | Deployable-equity share |
|---|---:|
| Momentum |30%|
| Intraday |10%|
| Swing |24%|
| Short |24%|
| Positional |10%|
| Ocean |2%|
| Total |100%|

Reserve continues to reduce the deployable base. Existing positions remain
open, spend their actual entry cost and retain protection. Unused room remains
cash. Owner provenance is separate from the selected strategy; Ocean remains
an automatic engine. Ocean's fixed ticket and eight-position ceiling remain
additional limits. Protective exits are independent of entry allocation.

Changes cover Config defaults, the common share map, Ocean scan entry,
locked final booking, pyramids, same-segment funding rotation, owner-entry
callers, dashboard/Risk/Den and sandbox displays, and editable settings.
The former extra Ocean dashboard row is removed to avoid counting it twice.
The boot migration stores all six values, budget enablement and the
`allocation_order_2026_11_21` marker in one atomic settings merge, checks
read-back, and does not overwrite later deliberate edits. Failed persistence
leaves the migration unconfirmed and eligible for retry at the next boot.
No historical trade, research result, broker tariff or strategy threshold is
rewritten. A new allocation version is not evidence of improved performance.

## Validation and release

- Initial focused new engine/rotation/pyramid checks: 32 passed, zero skips.
- Focused UI/reporting cases: 19 passed, zero skips; prior owner validation 41 passed.
- First canonical full run 25 November: 3,603 passed, 2 failed, 0 skipped. Both failures
  were numerical fixtures now exceeding their explicitly changed budgets.
  The Ocean rotation fixture now funds a full ticket within 2%; the cost test
  uses 999 shares to remain inside the 10% budget after slippage. All assertions
  and protection checks remain; 61 cases in affected modules passed.
- Corrected baseline full run: **3,605 passed, zero failures, zero skips**.
- Independent review found handling needed for the final allocation refusal
  in owner/limit/Buy Desk callers and mixed rotation cash reuse. Added fixes
  and regressions: **40 owner/UI cases** and **71 engine/funding cases** passed,
  zero skips. Limit orders remain retryable and protective exits continue;
  copied Ocean signals retain the owner's selected strategy. Mixed rotation
  reserves earlier planned spend without changing actual cash until a sale.
- Final local full suite after review fixes: **3,615 passed, zero failures,
  zero errors and zero skips** in 351.04 seconds. Canonical GitHub suite:
  **3,614 passed, zero skips** in 399.31 seconds. The additional local case
  parses an existing untracked `check_repair_records 2.py`; it is not a
  production or regression-test difference and was not added to Git.
- [PR229](https://github.com/damandamanaulakh-tech/MARS/pull/229) merged as
  `240e8711cce48a09e359250531a238c58dcee7f9` from reviewed head
  `44fdc0dc98803f9830a96baac053f39c9da4c0e5`. Remote content tree
  `3425254d2eebe18c4c06c032462dfd4aac10437d` exactly matches the reviewed local
  commit. [CI](https://github.com/damandamanaulakh-tech/MARS/actions/runs/45555785910)
  passed all six jobs, including both complete combined-strategy replays.
- Matching automatic Render deployment `dep-dc386oc9v7es73fodfkg` is live.
  Read-only verification at `2026-11-25T06:46:18Z` confirmed readiness,
  an acknowledged book save and no entry-block reason. The persisted settings
  read-back contains all six requested shares, enabled budgets and the migration
  completion marker. [Sanitized release proof](../evidence/2026-11-25-allocation-release.json).
- Existing financial state comparison is recorded in that proof. No production
  scan or trade was induced. Normal entry/add/rotation acceptance remains a
  separate pending observation; this release does not resolve the scan incident.

[25 November no-buy investigation](../evidence/2026-11-25-no-new-positions.md)
is a separate open incident: completed intraday data aged out before scoring;
changing allocation does not fix data latency or Momentum's measured-edge gate.
