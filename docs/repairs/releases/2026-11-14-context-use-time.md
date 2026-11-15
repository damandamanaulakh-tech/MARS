# SEQ-06 follow-up: verify source freshness when a decision uses it

Status: LOCAL_TESTED; publication, CI and deployment pending. Existing PR207
remains the deployed context release. The temporary storage recovery is recorded
in [its separate release](2026-11-14-storage-recovery.md).

Base: `6c4024f79e0512bec9f6a99a1bbad5a37c519828` (PR211); live at the start
of this batch: `47e7e0536b6c84f9f9f6698e5d1f76e80b315b14` (PR207).
Related findings: INTRA-004, XR-009 and XR-061. All original finding IDs and
historical trades remain preserved.

## Reproduction and correction

The sector identity was resolved when a scan/refresh batch started. A source
one second short of its seven-day limit could expire during a slow fetch and
still be labelled verified in a decision made two seconds later. Separately,
a naive/date-only stored observation time was silently treated as UTC. Five of
the eight new source-validity cases failed on the baseline; three passed.

Each batch now takes one frozen source snapshot and resolves it again at each
actual observation time. This adds no provider or instrument-master reads per
stock. Original instrument ISIN and configured label survive the recheck;
a conflict cannot disappear during revalidation. The resolved identity records
its evaluation time. Missing, invalid or timezone-unproved source receipt times
remain unknown. The full registry stays internal to the batch and is not copied
into every decision.

The seven-day operational limit, existing trading thresholds, protective exits
and original ledger are unchanged. Expired sector context cannot make an otherwise
valid stock/market comparison disappear and cannot become a new entry veto.

## Meaningful checks

- Eight source-validity cases cover expiry during work, exact expiry boundary,
  fresh stock identity with expired sector membership, immutable batch sources,
  instrument conflict and three forms of unproved source time.
- Six integration cases run the real official CSV parser, stored OHLCV and sector
  readers, comparison calculation, scan/refresh paths, saved decisions and JSON
  reload. The only fixtures replace time and external I/O. All provider history
  calls are forbidden; no precomputed context result is injected.
- The integration window contains 22 identical completed NSE closes. Stock
  100 to 130 is +30%, sector 300 to 330 is +10%, and market 200 to 210 is +5%.
  Relative results are +20, +25 and +5 percentage points. Missing prices or a
  source expiring during the read yields an explicit unknown sector result.
- The same refresh path still executes its isolated stop-loss and retains the
  complete context on the closed trade and after reopening the saved book.
- Focused Goal06 suite: **79 passed, zero skipped**. Broader validation and
  canonical CI are recorded below once complete.
- Full local suite: **3,262 passed, zero failures/errors/skips** in 229.10 seconds.
  This includes one unrelated untracked duplicate-script syntax check; the
  committed suite is expected to collect one fewer. CI will establish its own total.
- Pre-release read-back confirms all 53 position financial fields and 214 closed
  trade financial fields, plus cash, match the native recovery backup. The newer
  durable save is 2026-11-14T17:41:03.479914 UTC. This does not recover prior
  process-only spills or establish a complete capture of current RAM.

## Release and remaining observation

The live database health read at 2026-11-14T18:05:33.398327+00:00 returned HTTP
200, ready, connected, not degraded and no write error. This is readiness
proof for the existing release, not proof that this follow-up is deployed.
The saved book has 53 open positions and 214 closed trades; storage recovery
has advanced its durable save. No production scan or trade was induced.

Normal comparison/trade observations remain pending. Upstox quote authorization
still needs its normal owner reconnect; Cement remains unknown where its source
cannot be obtained. The private backup and temporary storage relief do not close
permanent retention or prior-process spill reconciliation. SEQ-07 stays queued.

[Sanitized checks and hashes](../evidence/2026-11-14-context-use-time.json).

## 15 November: CI fixture correction

The first [CI run](https://github.com/damandamanaulakh-tech/MARS/actions/runs/43682247182)
passed 3,260 tests and failed one existing drawdown retry test; no pytest tests
were skipped, but the downstream no-skip step did not run after the failure.
No merge or deployment occurred on that result.

On Sunday its fixture's entry was Saturday ("yesterday") while supplied daily
history ended Friday. The real unknown-mark safeguard correctly abstained, so
its expected cut could not occur. The same failure was independently reproduced
with the baseline context modules. The test now places its entries before the
supplied stored closes. All existing exit, pending-work and restart assertions
remain; no production mark/exit behavior or threshold changed.

Expanded focused checks: **126 passed, zero failures/errors/skips**. The full
suite and canonical CI are rerun on the corrected revision; the earlier local
3,262-pass result remains dated evidence, not a substitute for those checks.
