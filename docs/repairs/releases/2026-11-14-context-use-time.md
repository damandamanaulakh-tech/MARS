# SEQ-06 follow-up: verify source freshness when a decision uses it

Status: DEPLOYED_PERSISTENCE_VERIFIED_RUNTIME_PENDING. PR212 is live, with
health, acknowledged source saves and financial-book preservation across the
release restart verified. Normal market-session comparison remains pending. The temporary storage recovery is recorded
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
  canonical CI are recorded below.
- Initial local suite: **3,262 passed, zero failures/errors/skips** in 229.10 seconds.
  This includes one unrelated untracked duplicate-script syntax check; the
  committed suite collected 3,261 tests in final canonical CI.
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

Normal comparison/trade observations remain pending. Broker quote acceptance
remains unverified after the earlier 401; the normal owner reconnect is its recovery
path. Cement remains unknown where its source cannot be obtained. The private backup and temporary storage relief do not close
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
suite and canonical CI were rerun on the corrected revision; final results are
below. The earlier local 3,262-pass result remains separately dated evidence.

## Verified corrected revision and five-stock stored-data check

[PR212](https://github.com/damandamanaulakh-tech/MARS/pull/212) merged as
`520ec29d6b0f02c8e23ebdd339f2e652509bbc52`, from reviewed head
`8c7fd93746292c0d3489e072d5fcc70d135fd83b`. The local, published and merged
trees all match `468fd94367ad4f6a8a2e4fa672f213c83898c9a6`.
[Final canonical CI](https://github.com/damandamanaulakh-tech/MARS/actions/runs/43682948103)
passed **3,261 tests, zero failures/errors/skips**, in 343.44 seconds. Both the
repair-record check and no-skipped-tests guard passed. The corrected local
full rerun passed **3,262 tests, zero failures/errors/skips**, in 236.81 seconds;
the one extra local case parses the unrelated untracked duplicate repair script.

At 2026-11-15T05:46:20 UTC, a read-only replay used current Mongo source identity
and stored prices through the real readers/comparison. This is separate from a
normal engine decision and did not write production data or call a broker.
The 21-session lookback requires 22 completed closes from 13 October through
13 November 2026, at 15:30 IST. Current source observation is 15 November
05:43:37.135455 UTC; source publication/effective dates remain unproved.

| Stock | Verified observed sector membership | Latest stored stock / sector close | Required last close | Result |
|---|---|---|---|---|
| TCS | IT | 12 Nov / 12 Nov | 13 Nov | Unknown: missing exact-window bars |
| Infosys | IT | 12 Nov / 12 Nov | 13 Nov | Unknown: missing exact-window bars |
| Reliance | Oil & Gas | 12 Nov / 12 Nov | 13 Nov | Unknown: missing exact-window bars |
| HDFC Bank | Bank | 12 Nov / 12 Nov | 13 Nov | Unknown: missing exact-window bars |
| Maruti | Auto | 12 Nov / 12 Nov | 13 Nov | Unknown: missing exact-window bars |

The named OHLCV store has no `^NSEI` series for this read-only replay, so its
market leg is also unknown. The runtime can fetch a provider fallback; that
fallback was not invoked or judged by this probe. Missing data was not replaced
by zero, an older common end date, a made-up sector, or an investment signal.
The probe does not establish a live entry gate or reconstruct old decisions.
[Timestamped results](../evidence/2026-11-15-stored-context-probe.json).

## Observed deployment and restart persistence

Render deployment `dep-dbskknvf3r2c73eh4f5g` became live at
`2026-11-15T06:01:43.286781Z` (11:31:43 IST), running the exact merged revision
`520ec29d6b0f02c8e23ebdd339f2e652509bbc52`. Startup completed at
`2026-11-15T06:01:36.913692229Z`. No duplicate deployment or manual production
scan/trade was triggered.

The post-release health response at `2026-11-15T06:03:05.749657+00:00` was HTTP
200, ready, connected, not degraded and had no write error. Its new diagnostic
reports **acknowledged** sector-source persistence, 14 of 15 sources, at
`2026-11-15T06:02:17.059620+00:00`. Mongo independently contains the same
snapshot timestamp. Cement remains unavailable with ReadTimeout, not a guessed
sector value. This also verifies the previously merged PR208 diagnostics are live.

The durable portfolio saved after restart at `2026-11-15T06:02:02.551063` UTC.
All checked financial fields match the pre-release book: cash; ticker, quantity,
entry price/date, stop, target and segment for 53 open positions; and ticker,
quantity, entry/exit dates and prices, gross/net P&L, costs, segment and reason
for 214 closed trades. These also match the native recovery backup's financial
projection. No claim is made that metadata and ordinary observation timestamps
are byte-identical or that earlier process-only spills have been recovered.

The last observed rejected broker quote requests were at 05:43 UTC before this
deployment. An accepted post-release quote response has not been verified;
RUN-20261114-02 remains open. No 401 was observed in the queried post-release
log window, which is not evidence of a successful authenticated quote request.

The archive codec/preflight files are now present in the deployed tree but are
still offline tools: no production compressed-record reader/writer or migration
was activated. Temporary cleanup and private backup remain documented in the
[storage recovery record](2026-11-14-storage-recovery.md). Normal market-session
comparison, quote recovery, sustainable retention and older spill reconciliation
remain separate open follow-ups. SEQ-07 is queued, not started by this batch.
