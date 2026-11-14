# Free-tier lossless archive plan — prepared, not executed

Owner decision, 14 November 2026: **keep the free tier; prepare a lossless
archive plan**. A paid Atlas upgrade is not authorized. No production record
has been changed, removed, compressed or migrated by this preparation.
Goal06 remains open for durable source/context verification.

## Measured target

Read-only Atlas aggregates on `sourceborn.journal_archive`, 14 November:

| Data | Rows | BSON bytes | Meaning |
|---|---:|---:|---|
| SCAN_DECISION | 33,718 | 274,265,872 (261.56 MiB) | Main space consumer; 33,718 distinct decision IDs, zero duplicate IDs |
| EXIT_DECISION | 1,561 | 6,237,299 (5.95 MiB) | Preserve full exit explanations |
| BUY | 435 | 834,090 | Preserve entry evidence |
| SELL | 395 | 276,473 | Preserve exit records |
| Scan evidence field with measurement wrapper | 33,718 | 267,360,929 | Most of the scan document size; wrapper overhead means this is not an exact removable-byte count |

The scan rows span **11 November 19:09:59 UTC through 13 November 06:24:29 UTC**.
They are recent audit history, not disposable old data. Repeated-looking checks
are not duplicate records. The earlier aggregate used a nonexistent `date`
field; null first/last in that action summary means unknown. The corrected
measurement uses `ts` and is preserved alongside the original query output.
[Exact measurements](../evidence/2026-11-14-journal-storage-measurements.json).

A read-only sample of the earliest five and latest five scan records compressed
73,423 evidence BSON bytes to 10,791 bytes with deflate level 9, restoring the
sample bytes exactly. Separate groups saved **82.66%** and **87.97%**. Connector
transport can normalize BSON types and IDs were excluded: this proves a useful
size experiment, not a complete backup or raw-BSON migration. Full-dataset
savings, BSON fidelity, memory and latency remain to be proved.
[Sample method and hashes](../evidence/2026-11-14-journal-compression-sample.json).

For planning only, reducing approximately 255 MiB of evidence by 80% could
recover roughly **204 MiB before envelope/index overhead**. Do not treat that
as achieved capacity or use it to choose a deletion count. Measure the complete
proposed representation with `$bsonSize` and include new indexes before approval.

## Recommended representation and every affected caller

Keep the archive online in the same free Atlas cluster using a versioned,
compressed BSON payload per record. Preserve the original BSON document,
including its original `_id`, unknown fields, ordering-sensitive values,
null/absent differences, integer widths, dates, decimals and binary subtypes.
Use raw BSON bytes from the Mongo driver for backup and checksums, not JSON
with `default=str`, CSV exports or the connector sample above.

Keep a small query envelope outside the payload: `_id`, `ts`, `action`,
`decision_id`, `trade_id`, `ticker`, `mode`, `pre_mars`, and explicitly versioned
query metadata. Store BSON Binary payload, codec/version, raw-byte length and
SHA-256. Require decompression size limits, exact byte/hash checks and envelope
consistency. If a record does not get smaller, retain its original representation.
No field is removed from the reconstructed original record.

| Actual code path | Required change and proof before migration |
|---|---|
| `web/db.py:archive_journal` | Write new archive format; preserve old-format reads. Replace random-insert retry duplication with stable event identity and per-record acknowledged outcomes; preserve legacy rows with no ID. |
| `web/state.py:_save_with_mirror` / `_spill_journal_overflow` | Trim RAM only after durable archive acknowledgement or verified local spill; a local spill on an ephemeral host is not remote durability. Preserve current unsaved state before any restart. |
| `web/state.py:heal_after_reconnect` | Quota recovery while still connected must retry spills, not depend solely on disconnected-to-connected transition. Make replay resumable without duplicate or lost events. |
| `web/db.py:list_journal_archive` | Decode both formats; preserve ordering and the explicit 20,000-row export limit. Streaming/pagination needed for a full backup; the existing report is not a complete backup. |
| `web/db.py:read_decision` | Look up unchanged decision ID, decode verified original and return the same evidence object. |
| `web/db.py:read_trade_amendments` | Preserve trade/action/date filtering and decoded amendment evidence. |
| `web/db.py:search_decision_archive` | Replace queries inside compressed evidence with validated query metadata, OR old uncompressed predicates. Keep ticker/mode filtering before limit; never insert fake empty gates just to satisfy the existing query. |
| `web/db.py:count_pre_mars` and MARS migration | Keep the original flag in the envelope and preserve existing phase/row-count semantics. |
| `/decisions`, `/decisions/{id}`, `/reports` journal CSV | Same original values and ordering for old, compressed and mixed archives; corrupt payloads explicitly unavailable, not empty successful results. |

No change to portfolio quantities, cash, trades, stop/target values, timestamps,
fees, score weights or entry/exit policy is part of this representation change.
Other databases, price histories and original audit files remain outside the
migration manifest. Compression only delays future quota pressure; it is not
an unlimited retention solution.

## Execution order and completion proofs

| Order | Work | Proof required | Current state |
|---|---|---|---|
| A1 | Inventory and candidate measurement | Exact collections, rows, ID uniqueness, sizes, actual readers/writers | Complete, read-only evidence above |
| A2 | Prepare compatible codec, readers and new writer in an isolated environment | Raw BSON round trips; every consumer above; corrupt/truncated/bomb payload rejection; bounded memory; 20000-row export parity; partial batch failure and restart retries | Partial: offline codec and file verifier tested; live adapters/writer still pending |
| A3 | Choose and verify two durable backup destinations outside the full cluster | Encrypted archive copies, manifest/checksums, successful independent restore to scratch Mongo, permissions/retention and access after deployment restart | Not selected; no full export/backup claimed |
| A4 | Quiesce MARS writers and capture unsaved state | Live in-memory portfolio and local book/spill files preserved separately from older Mongo copy; counts/hashes and conflicts reconciled before restart | Not performed; requires controlled owner/server access |
| A5 | Generate immutable migration manifest | Original IDs, raw BSON hashes/lengths, proposed payload hashes/lengths, field/type/row parity, actual net capacity recovered; full dry run and rollback restore pass | Not performed |
| A6 | Review exact canary and quota bootstrap | Owner reviews affected IDs, verified backups, expected net bytes and restore route; no implicit deletion or paid tier change | Not authorized by plan preparation |
| A7 | Migrate bounded batches | Re-read and compare against manifest, conditional update, acknowledgement, read-back/decode/hash, checkpoint after each record; stop on mismatch or refusal | Not performed |
| A8 | Verify preservation and resume | All original IDs/counts/bytes reconstruct, all queries and exports match, capacity margin measured, ordinary saves acknowledged, restart recovery and spills replay | Not performed |
| A9 | Complete Goal06 proof | Ordinary sector refresh acknowledged/read back with original source dates, later saved decision comparisons and controlled restart verification | Blocked by current storage incident |

A3 must use durable owner-approved storage, not Render `/tmp`, a developer's
temporary sample, a public repository, a backup button that writes to the same
full cluster, or an unverified export. Mongo native backup tools/raw BSON must
preserve data types and index definitions. There is no currently verified full
backup in this plan. The existing `/reports/backup` calls the same Mongo save;
it does not solve quota or preserve a separate recoverable copy.

## Full-quota bootstrap and rollback

The live writer refuses writes. Do not assume a smaller replacement is exempt
from quota enforcement. After A2–A5 and explicit canary approval, try one
conditional smaller replacement only if it preserves the original record from
the reviewed manifest. If Atlas refuses it, stop. Do not automatically switch
to delete-and-reinsert, `drop`, TTL pruning or deduplication.

If a temporary removal is necessary to create headroom, prepare a separate
review of the **exact IDs and total bytes**, with two verified raw-BSON copies,
a working reader/locator or controlled maintenance window, and a tested restore
into available capacity. Only remove the exact owner-approved bootstrap set;
never clear a whole collection. Reinsert its verified compact representations,
read them back, and prove all originals reconstruct before proceeding.
Plan preparation does not authorize that removal.

Pause on hash mismatch, changed source row, refused write, missing backup,
unacknowledged operation or unknown codec. Keep original/raw copies and completed
checkpoints so interruption can resume per ID. Rollback must retain the compatible
reader: restoring all uncompressed rows onto a full free cluster is not a viable
rollback. Restore a bounded canary only if measured headroom fits, or restore the
entire raw archive into a preverified scratch destination for recovery. Never
redeploy an old reader while compressed rows remain.

The maintenance run must not induce trades. Reconcile Mongo, live RAM and spill
history without silently replacing a newer book with an older one. The broader
XR-071 write-failure entry halt is a required separate acceptance item before
unattended resumption; a green connection alone does not prove saves work.

## Capacity policy to review

Measure cluster-wide logical bytes plus indexes and the new archive's daily
growth. Proposed warning thresholds are 70%, 80% and 90%; these are unapproved
operational choices, not trading rules. Set a required post-migration margin
from measured daily growth and backup cadence. A scheduled lossless cold archive
may eventually be required; do not introduce TTL deletion or drop evidence.

The Atlas free-tier cap is logical data plus indexes, so storage-engine disk
compression/compaction is not this plan's capacity mechanism. The proposed
Binary payload changes the BSON size measured by Mongo; prove that reduction
on the complete proposed data before claiming relief.
[MongoDB storage accounting](https://www.mongodb.com/docs/atlas/reference/faq/storage/).

Offline A2 foundation is now [implemented and locally tested](2026-11-14-archive-preflight.md). The owner subsequently requested a private downloadable backup and bounded cleanup. Native capture/restore verification is in progress; this does not yet satisfy A3 or authorize arbitrary history loss.
