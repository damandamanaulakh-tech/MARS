# Startup portfolio save recovery — 25 November 2026

Status: PR231 shipped; all six CI jobs passed. Exact live revision and startup save acknowledgement verified. Natural refused-write recovery remains pending.

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

## Initial validation and shipping checkpoint (historical)

- Baseline reproduction: 12 failed / 3 passed new cases before the repair.
- Final focused validation: 58 passed (25 new + 33 existing), zero skips.
  Actual bootstrap ordering and subsequent direct-save, retry and scan-gate
  failure/recovery paths are exercised. Tests leave old migrations inactive;
  recovery-guard placement before migration writes was verified in source.
- Repair records: 171 backlog records and seven incidents valid; all original
  165 finding IDs retained. Full application suite: **3,639 passed, zero skips,
  failures or errors**, 260.20 seconds, Python 3.12.16. Independent review found
  no blocking issue. At this checkpoint, all six GitHub CI jobs were still required before merge.
- At this checkpoint, functional merge, exact deployment and live startup
  acknowledgement were pending; their observed final results follow below.
- Natural production write-refusal recovery remains distinct from isolated
  failure tests. No artificial outage or trade will be induced.

## First CI attempt and midnight regression

[PR231](https://github.com/damandamanaulakh-tech/MARS/pull/231), first head
eba0bf90a81fda6ebe2386fb4cf85fb857838052, passed local validation but
[CI attempt 45675481416](https://github.com/damandamanaulakh-tech/MARS/actions/runs/45675481416)
reported 3,637 passed and two existing positions-page test failures.
Collection occurred before IST midnight and the requests ran after midnight.
The fixture's module-level current date was one day behind the correct page.

Both failures were reproduced with the unchanged fixture by collecting at
2026-11-25 18:29:59 UTC and requesting at 18:30:01 UTC. The fixture now has
one explicit reference day and a scoped frozen clock. All original assertions
remain. A new test proves all eight held-day values and their average advance
at IST midnight while the UTC date remains unchanged. Production date logic
and trading settings are unchanged. Fourteen focused cases and both original
cross-boundary reproductions pass, with zero skips. Final full-suite rerun and
fresh CI for the updated head are required; the failed attempt is retained.

Final local suite after the clock-fixture correction: **3,640 passed, zero skips, failures or errors**, Python 3.12.16, 180.07 seconds. The JUnit no-skip check and canonical repair-record check also passed. At that checkpoint, updated-head GitHub CI and functional deployment were pending; their final results follow.

## Final release and live startup proof

All six jobs in [CI 45682345922](https://github.com/damandamanaulakh-tech/MARS/actions/runs/45682345922)
passed on reviewed head `37c90fe1f4e3ef6d3d602295faf10563abf34dc8`,
tree `4e50a1c1f0f3b98da09b846a1db02219aaee4a11`: **3,640 application tests,
zero skipped tests**, and both complete frozen combined-strategy replays.
The first failed CI attempt remains documented above.

[PR231](https://github.com/damandamanaulakh-tech/MARS/pull/231) merged as
`e094369d9da5e2c238050cc7e0c32c12bf4e2245`. The merged tree exactly matches the
reviewed tree; the merge preserves ancestry for the separate dependent PR232.
Render automatically deployed `dep-dc3jvm60tbcc73bufji0`, matching that merge
revision, and reported live at **2026-11-25 20:08:37.770293 UTC**.

The pre-release Free-host wake reproduced the original startup gap: startup
completed at 19:58:50 UTC with no acknowledgement; the normal minute retry
confirmed saving at 19:59:54.748464 UTC. Only after that confirmation and all six
CI successes was the release merged. No forced restart, outage, or diagnostic
trade was used. The health read woke the normally sleeping Free service.

In the new process, save acknowledgement is **20:08:32.208029 UTC**, before
startup completion at **20:08:34.559941978 UTC**. Post-release health, verified by
20:09:59 UTC, is ready, connected, not degraded, with no write error or entry
block. The helper's informational log was not returned by the log query; the
acknowledgement timestamp, new-instance startup log and durable Mongo readback
provide the observed ordering evidence.

Mongo readback has 39 open positions and 94 closed trades, last_save
20:08:27.284407 UTC. Cash, selected position quantity/entry/segment/stop/target/fee
fields and selected closed-trade values are unchanged from the private
pre-release projection. Its canonical SHA256 remains
`1171458e35388f01cd5969802f99b2a23f2eff1eb6785e77f846acc1d6f5824b`.
This is a selected financial projection, not a full BSON backup or proof that
mutable observations are byte-identical. Raw book data remains private.

Forward epoch `2026-11-20T08:41:42.997998+00:00` is retained; pending exports=0,
with no capture/export error. Shared cluster health reports approximately 421 MiB
against 512 MiB. [Structured release proof](../evidence/2026-11-26-startup-save-live.json).

**Startup acknowledgement is runtime verified.** A future natural refused-write
recovery remains separate from the passing isolated recovery tests. The saving
repair does not bypass performance, quote, allocation or other entry checks.
The additional non-intraday owner batch is separately tracked in PR232 and is
not execution evidence for this release.
