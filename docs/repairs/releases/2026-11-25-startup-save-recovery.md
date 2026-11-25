# Startup portfolio save recovery — 25 November 2026

Status: full local validation passed; publication, CI and release pending.

## Scope and observed state

The owner supplied a 15:08 IST dashboard screenshot showing 39 open positions
and portfolio_save_unconfirmed. Render logs show process startup at
09:38:28.431474112 UTC and a scan halt at 09:47:34.701730349 UTC.
The database subsequently contains all 39 positions and 94 closed records,
cash INR 24,552,636.26745173, with last_save 10:06:55.997175 UTC.
A later health check confirms a current-process acknowledgement at
12:06:33.247442 UTC, no entry block and no write error. Cluster use was about
401 MB against 512 MB. This is a recovered startup incident, not evidence of
a current quota failure. The exact earlier lock holder and first recovery time
are not known.

The 39 held positions include 20 opened today by the normal engine: 12 Short
and 8 Swing, all without owner_pick tags. They are not attributed to the
requested one-time performance override. That separate request has not been
executed or confirmed; market close does not authorize backdated fills or
automatic next-day replay. No diagnostic trade or storage deletion was made.

Baseline: 19bf37cec87c26775afebfb04e2847cd37ced810 (PR230 documentation).
Prior functional allocation release remains
240e8711cce48a09e359250531a238c58dcee7f9.
The local checkout was restored from all 1,203 tracked blobs, verified against
the pinned GitHub tree and then attached to the fetched original Git commit.
The interrupted unpublished entry-batch work was not present in that checkout
or a remote strategy branch; this repair does not claim that work shipped.

## Repair

- Strictly read and fully hydrate the durable book before granting write authority.
  A successfully read empty store may initialize normally. Failed reads or partial
  hydration require reconciliation before any later checkpoint can overwrite
  the fallback. After engine construction, checkpoint before broker/provider work.
- Keep the entry guard for refused, unavailable and unacknowledged saves.
- Retry an eligible blocked checkpoint even when today's warning already exists.
  Recheck real acknowledgement after the save so a recovered pass can proceed.
- Preserve mode/book locks, prompt busy deferral and unresolved recovery protection.
  Saving concurrently with partially applied scan settings is not safe.
- Register the minute persistence maintenance job even when trading schedules are
  disabled. No entry schedule is enabled by that change.
- Keep original ledger, cash, segment, stop, target, performance gates and
  protective exits unchanged.

## Validation and shipping

- Baseline reproduction: 12 failed / 3 passed new cases before the repair.
- Final focused validation: 58 passed (25 new + 33 existing), zero skips.
  Actual bootstrap ordering and subsequent direct-save, retry and scan-gate
  failure/recovery paths are exercised. Tests leave old migrations inactive;
  recovery-guard placement before migration writes was verified in source.
- Repair records: 171 backlog records and seven incidents valid; all original
  165 finding IDs retained. Full application suite: **3,639 passed, zero skips,
  failures or errors**, 260.20 seconds, Python 3.12.16. Independent review found
  no blocking issue. All six GitHub CI jobs remain required before merge.
- New functional merge, exact deployment and live startup acknowledgement:
  pending. A healthy predecessor does not prove this repair is deployed.
- Natural production write-refusal recovery remains distinct from isolated
  failure tests. No artificial outage or trade will be induced.
