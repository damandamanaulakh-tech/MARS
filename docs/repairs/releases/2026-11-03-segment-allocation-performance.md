# Strategy allocation and per-segment performance — 3 November 2026

## User decision and baseline

The owner explicitly requested that owner-added positions and their funds belong
to Momentum, Intraday, Short, Swing or Positional, with no separate Owner deck.
They also requested each segment's number of trades, win/loss percentages and
time frame. This supersedes the October 21 Owner-budget exception, not the
strategy thresholds or the existing source-specific exit protections.

Baseline main: `ddaca63cf393b7454a0f32d46e136a5d79cdcb38`.
The earlier gate review remains pinned to that source and is historical evidence.

## Resulting behavior

- Classification, open-position labels and allocation use the position's stored
  mode. Owner/Buy Desk tags remain source markers. Existing blank modes retain
  the documented Momentum fallback; Ocean retains its separate engine budget.
- Owner tickets offer five segments. Market entries use a copied configuration;
  limit orders persist their segment through fill. Legacy limit orders without
  mode retain their original Momentum semantics. Actual entry cost must fit cash
  and that segment's available allocation. Auto quantity is capped by these funds.
- The Momentum Buy Desk keeps its Momentum selection recipe. Its plan includes
  slippage; execution rechecks cumulative batch cost and segment room per fill.
  No additional scanner signal gates are imposed on manual tickets.
- Existing holdings are not sold, redistributed, retargeted or restopped by this
  change. Reclassifying an existing overfull segment does not free funds; Risk
  and dashboard display its excess. Current risk percentages remain unchanged.
- Manual holding imports carry the chosen segment and import provenance. These
  reconcile an already-owned holding, so they may account an overfull segment.
  Notes edits preserve existing system/source flags, cannot invent Ocean or owner
  flags, and serialize against entry/exit work with the shared mode lock.
- Closed Trades shows Momentum, Intraday, Short, Swing, Positional and Ocean
  separately: completed records, wins, losses, breakevens, rates, net P&L, first
  and last exit dates in IST, and actual average elapsed holding time.
- Performance periods: all retained history, last 30, 90 or 365 calendar days.
  Rates divide by all completed records in that segment/period; open positions do
  not count. Partial exits are separate closed records. Legacy attribution uses
  recorded entry evidence; unknown history remains explicitly unknown. Archived
  pre-MARS and overflow records are excluded and coverage is labeled accordingly.
- Future owner strategy exits enter their strategy loss counters. Existing
  historical same-day `owner` loss counters remain unmodified and visibly legacy.
  Measured Kelly's estimator and exclusion rules are unchanged.

## Reproduction and validation

- A baseline `owner_pick` with `mode=swing` counted as Owner and reported zero
  Swing usage instead of its actual entry cost.
- The initial 16 owner-entry cases produced 15 failures and one pass before the
  implementation: mode lost, segment overrun accepted, invalid mode accepted,
  limit mode lost, slippage overrun, and nonfinite inputs.
- The 13 notes/provenance cases failed before the source-tag repair. They now
  prove no source loss, no forged Ocean allocation, and no mutation while busy.
- Focused checks cover five modes, category accounting, actual engine closes,
  import validation, legacy orders, raw/filtered performance, archive exclusion,
  IST boundaries, net-cost rates, partial records and empty samples.
- Browser verification uses a synthetic, GET-only local preview with no production
  database or scheduler. The 30-day selector changes counts and shows its inclusive
  IST period; all six segment rows, rates and holding durations render.
- Full local suite: **2,784 passed, zero failures/errors/skips**, Python 3.12,
  217.90 seconds. Repair-record validator passes with all 165 original IDs and
  two separate incidents preserved; `git diff --check` clean.
- Independent review found no new blocking errors. Final CI, merge and
  deployment evidence is recorded below.

## Backlog scope and remaining work

OPS-07 records the new explicit policy. XR-083 is the actual-fill budget repair.
ROOT-ROUTE-03 gains the selected-mode/import and BUY validation subclaims;
ROOT-ROUTE-04 gains preserved tags and serialized notes updates. The broader
manual SELL concurrency and manual-import locking concerns remain open. Limit
cancellation/expiry concurrency and late-order expiry are not repaired here.
No protective-exit or candle-gate policy is changed by this batch. No production
order or scan was induced for verification. Production entry/fill observations
remain pending until ordinary authorized use occurs.

## Release state

- [PR #192](https://github.com/damandamanaulakh-tech/MARS/pull/192) merged on
  3 November 2026 at 10:24:24 UTC (15:54:24 IST).
- Reviewed/tested head: `f6c1bff48fb286441fb860267c31a27894dbeb3a`.
- Merge/deployed revision: `0ce4fdd7e5398a981e580d6eaa8ed27e24560492`.
- Tree shared by tested head and merge: `514cd4b25ffc21fc37059b91a286ea9e8880a552`.
  All 37 changed files were verified byte-for-byte against the local tested tree.
- [CI run 41617965910](https://github.com/damandamanaulakh-tech/MARS/actions/runs/41617965910):
  **2,784 passed**, Python 3.11, 341.26 seconds. Test job, repair-records job and
  explicit zero-skipped-tests guard all succeeded.
- Render service `srv-d8cspdnavr4c738lbrd0`, confirmed Research Engine workspace:
  deploy `dep-dbkrcmss728c73919k0g` automatically deployed the matching merge.
  Status **live**, finished 10:26:12 UTC / **15:56:12 IST**, 3 November 2026.
- Post-deploy GET `/healthz` succeeded at approximately 10:27 UTC: `ok=true`,
  database connected, `degraded=false`, `boot_degraded=false`, no write error,
  production readiness true with no problems.
- A read-only saved closed-trade snapshot reconciled under the new performance
  helper, with unknown legacy attribution retained rather than guessed.

State: **DEPLOYED_HEALTH_VERIFIED** for the release. Entry/fill behavior has
isolated regression proof; ordinary production execution remains unobserved.
No scan/order was induced to manufacture a runtime-completion claim. This
follow-up record changes documentation only and skips a redundant Render deploy.
