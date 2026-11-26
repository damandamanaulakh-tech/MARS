# RUN-20261126-02 / OPS-09 — the app could not reconnect to its database

Status: **fix implemented and locally tested on the PR234 branch; NOT merged,
NOT deployed.** The live process is still in the failed state described here
at the last observation. Recovery of the live app needs an owner action
(restart or deploy); see "Live recovery".

## Observed (live, read from outside; nothing was changed)

| When (UTC, 26 Nov) | Source | Reading |
|---|---|---|
| 12:28–12:51 | `/healthz` | writes refused, "over your space quota, using 513 MB of 512 MB"; storage statistics present (database 299 MB + 14 MB index, `journal_archive` 216 MB) |
| 13:04:53 | `/healthz` `write_error_at` | the last time a write error was recorded |
| 13:43 | `/healthz` | `write_error_at` still 13:04:53; `mongo: null`; statistics refresh took 12.128 s |
| 15:09 (20:39 IST) | owner screenshot | red banner still printing the 513 MB message; "Render & Mongo is paid with full access" |
| 15:11 | `/healthz` | `connected: true`; `write_error_at` still 13:04:53 (2 h 6 min old); `mongo: null`; `history_tickers: 0`; statistics refresh 12.12 s; last confirmed portfolio save 08:53:56 |

The statistics refresh makes four database reads; pymongo's server-selection
timeout here is 3 s; 4 × 3 s = the observed 12.1 s. Every read is timing out
without finding a server. A cluster that is merely over quota answers reads.

**Inference, not verified in the Atlas console:** the cluster's plan was
changed at about 13:05 UTC (18:35 IST). A plan change moves the cluster to
new hosts; the SRV name follows, but a driver client that is already running
keeps the old hosts. MongoDB's own guidance and community reports say running
applications must reconnect after such a change.

## Faults in the app (reproduced in isolated tests)

1. **No reconnect while "connected".** `db._CONNECTED` was set only at connect
   time. `ensure_connected()` rebuilds the client only when that flag is
   false, and nothing ever cleared it. After the hosts moved, the process
   could never reach the database again without a restart — and a restart
   discards whatever the book holds only in memory.
2. **A stale message.** The save wrapper reads the closed-history policy
   before writing. When that READ failed it returned without recording
   anything, and the checkpoint only records a reason when none is present.
   So the dashboard kept the hours-old storage-quota text, and the banner's
   "retries every minute" was true only in the sense that each retry stopped
   at the same read.

## Repair

- `db.note_link_failure(exc, where)`: a pymongo `ConnectionFailure` (no server
  selected, network timeout, no primary) drops the client, marks the
  connection down and records "database unreachable (…) — reconnecting with
  a fresh connection" with the current time. A refused operation
  (`OperationFailure`: over quota, not authorised) is **not** a link failure
  and leaves the connection alone.
- Called from the portfolio save, the closed-history policy read and the
  storage-statistics read (the health thread asks every minute, so a dead
  link is noticed even when nothing is saving).
- `is_connected()` then builds a new client at most once per 60-second
  cooldown (existing behavior). On success the existing heal pass saves the
  in-memory book and clears the block. Boot-during-outage reconciliation
  rules are unchanged.
- The save wrapper records the current reason when it stops before the
  write.
- Dashboard: a separate red "Database CANNOT BE REACHED" banner that says the
  app reconnects by itself and that a restart would discard unsaved changes;
  both red banners now print when their error was recorded.

No trading rule, threshold or saved-data format changed.

## Validation

`web/tests/test_db_reconnect_2026_11_26.py` (12): three link-failure types
drop the client; three refused/other errors do not; a quota refusal keeps its
own message; the stale quota message is replaced; after the hosts move the
one-minute job reconnects and saves the book held in memory; while the
cluster stays away it attempts once per cooldown, not in a loop; the health
read notices a dead link; the dashboard banner. On baseline `dc50354` the
same file gives 11 failed, 1 passed. The 81 save-guard, quota, startup
recovery and health tests pass with it.

## Live recovery (owner action; not performed from this session)

The running process has the old code, so it cannot reconnect. It must be
restarted, by a Render restart or by a deploy. Either one reloads the book
from the last confirmed save (26 Nov 08:53:56 UTC = 14:23 IST): changes after
that exist only in the running process. Before restarting, the Reports page
of the running app can still export positions, trades and journal from
memory. Whether the database holds the saved book after the plan change must
be confirmed in the Atlas console first; this session cannot see it.

## Open

- Runtime acceptance: observe, on a deployed revision that contains this
  repair, a forced or natural link loss followed by an automatic reconnect
  and an acknowledged save. Not to be induced on the production book.
- Journal growth and retention remain RUN-20261126-01.
