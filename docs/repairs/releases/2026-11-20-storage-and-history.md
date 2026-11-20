# Storage recovery and owner-requested closed-history cleanup

Work in progress; no deployment or production deletion is claimed by this
preparation commit. The owner resumed Goal11 on 20 November while retaining
storage recovery as the immediate dependency. The isolated storage hotfix
`fd0700ac58c48d47769e25fc8d945ec1315f5154` is based on live Goal10
`65a58509018fcce86a299328497f1ffe3ee1a142`. Main integration retains the Goal11
recorder and research; deployment remains held until current state is preserved
and the combined revision passes CI.

The owner authorized a private CSV and removal of closed trades entered through
30 September 2026, using IST entry dates, plus closed intraday history through
17 November. Intraday records from 18 November onward remain. These are fixed
cutoffs; resumption on 20 November does not extend them. Open positions, cash,
capital, stops, targets and financial risk-state fields are excluded.

The current stored ledger has 409 distinct closed rows: 240 entered through
September, 95 later closed intraday rows within the reset, and 74 retained rows.
There are 39 open positions. Both prepared CSVs reconcile row identities and
gross P&L, costs and net P&L. Dates missing or inconsistent would be retained for
review; none was found in this stored snapshot. These counts must be refreshed
if recovering an unsaved running book adds records before execution.

## Storage findings

Native dbStats measured 528.3767 MiB of logical data plus indexes across the
shared cluster. MARS/sourceborn accounts for 362.67 MiB including indexes.
Its 40,333-row journal consumes 296,559,871 bytes (282.82 MiB); OHLCV consumes
65,783,043 bytes (62.74 MiB); the entire portfolio is 3,187,773 bytes (3.04 MiB).
The standalone trade archive is only 96,912 bytes. Old-trade removal alone
cannot resolve the quota. Other databases are inventoried, never modified.

The journal candidate preserves all original BSON bytes, including unknown
fields and numeric types, while reducing these 40,333 rows to 66,788,255 bytes
(63.69 MiB), saving 229,771,616 bytes (219.13 MiB). This is offline potential,
not space already freed. The first full Python 3.9 capture aborted; Python 3.12
captured every collection, but long journal rereads hit network timeouts.
Bounded independently checked batches retain progress and refuse changed rows.
Full source verification and native restore then passed: 82,618 documents
across all 19 collections, with every raw BSON document, option and index
verified. Backup/CSV ZIP entries were independently hashed; the private
credential export and app cookies are excluded. All copies remain on one Mac.

## Implementation

- Versioned journal compression is wired to every archive writer and reader.
  Existing raw documents and new packed documents can coexist. Corrupt payloads
  fail decoding; stored hashes and query metadata are checked against original
  BSON. Existing journal documents are never overwritten by normal append calls.
- Stable event identities make repeat writes idempotent. Each write batch needs
  acknowledgement and content read-back before callers may discard RAM/spill
  entries. A conflicting identity or partial write is not successful.
- Journal CSV generation streams batches of 100 documents after selecting only
  the newest record IDs. It no longer materializes 20,000 full gate snapshots
  in the web process. Existing list callers retain their compatibility adapter.
- Exact approved closed-trade fingerprints prevent later saves or overflow
  archival from resurrecting removed history. The policy survives JSON/Mongo
  restarts. A failed/invalid policy read refuses the Mongo checkpoint and keeps
  the local financial book; it cannot silently approve a broad date purge.
- No trading threshold, position size, protective stop, target or tariff changes.
  Clearing history changes the available performance sample; exported history
  remains a separate original-ledger record rather than being rewritten.

## Verification and shipment states

Initial focused regressions: 67 passed. New storage cases: 20 passed, including
IST midnight boundaries, unknown dates/modes, later partial exits, malformed
policies, refusal, duplicate retry, partial acknowledgement and corruption.
MongoDB 8.0.38 localhost verification passed native bulk/write/readback, 210-row
retry without growth, 211 mixed-format query matches, decision/amendment lookup,
newest-limit ordering and bounded export batches. Production was not used for
test trades or scans.

Full canonical suite initially found 15 failures: existing fake persistence
fixtures did not model the newly required policy read. Those fixtures now
explicitly supply an empty cleanup policy without changing their original
assertions. The canonical full rerun passed 3,488 tests before the final identity case;
the final isolated hotfix then passed **3,489 tests with zero skips** and all
four jobs in [CI](https://github.com/damandamanaulakh-tech/MARS/actions/runs/44689081974).
The merged storage/Goal11 focused suite passed 66 tests after installing its
required freezegun dependency and explicitly modeling an empty cleanup policy
in the existing outbox fixture. Local native recovery rehearsed conditional
removal of 10,000 older scan snapshots, preserved 30,333 other originals, then
restored all cold rows in the compressed representation. Every original byte
remained recoverable. Main integration CI, source merge, deployment, live
migration, history removal and durable read-back remain pending.

Database exports, secrets, exact IDs and private manifests stay outside the
public repository. Local files on one owner Mac do not constitute independent
off-device backup copies. Stored-state capture is distinct from prior-process
RAM and spill coverage. A 200 health response is not proof of a successful save.
