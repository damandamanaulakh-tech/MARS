# AUD-001 — resume unfinished protective drawdown cuts

Approved batch 3, 2 November 2026, based on main `ac6e174`.

Previously, a cut marked its level complete after any sale. A full halt with
quotes for A but not B therefore closed A, saved `full_halt` as done, and
never closed B when its quote returned. Simply leaving the level incomplete
would create another problem: each retry could recalculate the fraction
against the smaller book, repeatedly reducing holdings.

The portfolio now stores `dd_cut_pending` with the triggered level, fraction,
start time, original remaining exit count, and eligible holdings identified
by ticker and entry timestamp. Each successful cut removes one obligation.
Later passes rank available finite positive quotes within that original pool;
marks are never used as fill prices. Missing or invalid quotes leave the work
pending. The level is complete only when the required reduction is complete.

The existing rules are preserved: floor(position count × cut fraction), at
least one exit, worst quoted return first, each level once, and peak reset
after an empty halted book. This is a position-count reduction, as in the
existing implementation; it is not a new notional-exposure calculation.

- A deeper level creates a new quota from the remaining book, never smaller
  than the unfinished quota.
- A stop or owner exit from the original pool satisfies an obligation; a
  reopened ticker with a different entry timestamp is not sold for the old
  obligation.
- A recovery in marks does not cancel an already triggered cut. New entries
  remain halted until the pending reduction is completed. The governor's
  reason and the append-only journal describe the outstanding count.
- Mark-only/outside-hours passes do not fill cuts. Existing market-hours
  controls still apply.
- JSON and Mongo save/hydration include pending state together with the book.
  Older snapshots without the field load with no pending plan. Existing
  historical `dd_cut_level` flags cannot reconstruct a lost original quota;
  this change does not invent historical obligations or reset those flags.

Regression coverage includes all three cutting levels, partial/no/invalid
quotes, JSON restart, Mongo round trip, real refresh execution, outside-hours
refresh, deeper drawdowns, other exits, reopened tickers, and legacy snapshots.
The initial new regression run produced 15 failures and one pass on the old
implementation. Local and CI final results are recorded in the release report.

CI also requires `openpyxl` for the three workbook checks. The import is no
longer optional, and CI rejects a test report containing any skipped tests.
Missing prerequisites must be resolved rather than counted as passing coverage.

Limits: persistence uses the existing portfolio snapshot boundary at the end
of refresh/scan; this is not transactional exactly-once execution across
process crashes or concurrent writers. A refused persistence write still
belongs to the broader global durability backlog. Live readiness does not
prove the missing-quote scenario occurred in production, and no production
liquidation is deliberately triggered for verification.
