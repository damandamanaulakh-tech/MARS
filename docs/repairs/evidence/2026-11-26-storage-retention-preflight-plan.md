# Complete archive export and retention preflight — proposed next batch

**Status: proposed, not implemented.** This plan performs no production mutation
and authorizes no new deletion, retention period, paid upgrade or trading-policy
change. The report date is **26 November 2026 IST**. Its supporting inventory
started at **25 November 2026 19:45:04 UTC**, which is **26 November 2026
01:15:04 IST**. [Measured inventory](2026-11-26-journal-growth-inventory.md)
and [exact aggregate evidence](2026-11-26-journal-growth-inventory.json) remain
separate from a complete, consistent backup.

The next implementation should deliver a complete resumable exporter, verified
manifest and reference-aware dry run. Any later removal is a separate operation
against an exact reviewed manifest. Compression remains useful, but it does not
bound an append-only archive.

## Current foundations and gaps

The owner journal CSV calls
[`web/db.py:iter_journal_archive()`](../../../web/db.py), which uses
[`web/journal_storage.py:iter_latest()`](../../../web/journal_storage.py).
That iterator selects the newest **20,000** archive rows and reads payloads in
100-document batches. Its bounded report is not a complete backup. The default
report also excludes pre-MARS archive records.

[`scripts/journal_archive_preflight.py`](../../../scripts/journal_archive_preflight.py)
and [`web/journal_archive_preflight.py`](../../../web/journal_archive_preflight.py)
already verify unlimited raw-BSON input. They require an existing complete
`.bson` source, never connect to MongoDB and explicitly distinguish byte
verification from database restoration or durable backup coverage. The current
builder creates a new private output directory; it is not a resumable native
source exporter.

[`web/journal_archive_codec.py`](../../../web/journal_archive_codec.py) already
validates mixed legacy/compressed records, BSON framing, codec/version, bounded
decompression, original SHA256 and the query envelope. Its `decode_record()`
documentation correctly states that decoded dictionaries cannot establish an
exact source-BSON backup claim.

## Execution sequence and acceptance evidence

| Step | Concrete implementation | Proof before completion |
|---|---|---|
| **1. Capture a complete source snapshot** | Add native raw-BSON export for `journal_archive`, collection options and indexes. Include every archive phase, not only the default report scope. Identify all writers and obtain a controlled quiesced capture, or separately verify that an available native snapshot mechanism supplies the required consistency. Preserve the current portfolio, journal tail, pending validation outbox and local spill separately before maintenance or restart. | A source manifest identifies exact namespaces, capture time, consistency method, source counts, typed IDs, raw document hashes, collection options and indexes. An active-writer aggregate or saved Mongo book alone cannot prove coverage of current unsaved RAM/spills. |
| **2. Resume against fixed boundaries** | Use an immutable snapshot-manifest ID plus manifest ordinal/chunk number as the cursor. Store each typed `_id`, source hash and chunk boundary. Fsync completed chunks and checkpoint only after their hashes verify. Appends after the sealed snapshot belong to a separate delta manifest. | Restart at every chunk boundary reproduces the same count and manifest hash without omissions or duplicate members. A changed or missing source row invalidates the affected snapshot rather than being skipped. |
| **3. Independently verify every byte** | Reuse `unpack_raw()` from the codec and `iter_bson()`, `_details()`, `build()` and `verify()` from the existing preflight module. Record stored representation hashes separately from reconstructed original hashes. Extend the verifier to sealed chunks without relaxing its existing checks. | Reopening every chunk verifies BSON frames, typed ID uniqueness, metadata, original hashes, envelope consistency, counts and aggregate hashes. Corrupt, truncated, oversized, unknown-codec or inconsistent records produce failure and no success marker. File-level success remains `byte_verified_not_database_restored`. |
| **4. Build a complete reference map** | Read full positions and closed trades, archived `trades`, `trade_evidence`, journal events, validation runs/events and the pending validation outbox. Follow trade IDs, entry/exit decision IDs, amendments and research/run references. Identify external research references that cannot be proved locally. | Every proposed candidate has an explicit reference classification. Unknown or unparseable references remain retained. Missing top-level `trade_id` never proves that a scan decision is unreferenced. Existing bounded UI queries are not treated as complete reference inventories. |
| **5. Prove restoration and produce a dry run** | Preserve the archive outside the full cluster and verify a second independent durable copy. Restore into isolated native MongoDB, reread every restored row as raw BSON, compare hashes and collection metadata, and exercise decision/trade readers. Generate a dry-run manifest containing exact typed IDs, source hashes, stored bytes, preservation location, reference reason and restore receipt. | Separate receipts prove file integrity, source completeness, independent-copy availability and database restoration. Report measured candidate bytes and shared-cluster headroom. No retention period, quota threshold or deletion count is invented by this plan. |
| **6. Rehearse conditional removal and recovery** | A later authorized operation may act only on reviewed manifest members and must refuse changed source content. Record outcomes durably and reread after ambiguous acknowledgements. If evidence leaves MongoDB, provide a compatible archive locator/reader so existing explanation links remain usable. | Tests cover interruption, partial batches, disconnect before/after mutation, lost acknowledgement after commit, duplicate retry, changed or missing rows, new references, unavailable archive copies, quota refusal and restoration into measured available capacity. Financial book, stops, targets, costs, loss controls and protective exits remain preserved. |

Event time is not a sufficient export watermark. `ts` can repeat, be missing or
arrive late. `_id` also cannot be assumed chronological:
`journal_storage.original()` assigns random string UUIDs, and legacy records
can use different BSON types. A fixed manifest preserves typed identity and
avoids treating a changing latest-row query as a consistent snapshot. If the
source cannot provide the required capture consistency, report completeness as
unknown and stop before deletion eligibility.

## Reference and recovery paths to preserve

- [`web/db.py`](../../../web/db.py): `read_decision()`,
  `read_trade_amendments()`, `read_archived_trade_evidence()` and
  `save_validation_event()` connect archived decisions, trade evidence and
  immutable forward observations. Their existing read limits are display
  bounds, not complete export guarantees.
- [`web/routes/evidence_routes.py`](../../../web/routes/evidence_routes.py):
  `decision_view()` and `trade_view()` resolve current, archived and local-spill
  evidence. Retention must preserve the meaning and availability of those links.
- [`trading/decision_evidence.py`](../../../trading/decision_evidence.py):
  entry, exit and amendment identities connect the event trail to positions and
  closed trades. Identical-looking events remain distinct events.
- [`trading/forward_validation.py`](../../../trading/forward_validation.py):
  `stage()` preserves entry, exit and book observations. Retention cannot erase
  supporting evidence or change inherited/owner-batch attribution.
- [`web/state.py`](../../../web/state.py): the portfolio save wrapper and
  `replay_journal_spill()` preserve overflow and retry work. A file on an
  ephemeral host is not an independent durable backup.
- [`trading/history_retention.py`](../../../trading/history_retention.py): the
  current policy honors exact approved closed-trade fingerprints. It must not
  become a rolling journal purge or extend the earlier owner cutoffs.

Existing codec, preflight and storage-recovery tests are useful foundations:
[`test_journal_archive_codec_2026_11_14.py`](../../../web/tests/test_journal_archive_codec_2026_11_14.py),
[`test_journal_archive_preflight_2026_11_14.py`](../../../web/tests/test_journal_archive_preflight_2026_11_14.py)
and [`test_storage_recovery_2026_11_20.py`](../../../web/tests/test_storage_recovery_2026_11_20.py).
The new exporter still needs its own interruption, cursor, complete-coverage and
native restore tests; passing the current tests does not prove those features.

## Access and operational prerequisites

This continuation has no currently verified native export connection. The
connected query tool cannot establish raw-BSON backup fidelity. The
[20 November native backup](../releases/2026-11-20-storage-and-history.md)
is historical preservation evidence, not coverage of the newly appended rows.
Independent off-device backup destinations remain unverified.

A future exporter can use a securely configured native driver, or a tested
owner-authenticated raw-BSON export route using the application's existing
database client. Normal access and snapshot consistency must be verified before
a production export. No credentials need to be placed in chat or the repository.
Raw BSON, private record identities, full manifests and private financial data
remain outside this public repository; repository evidence should contain only
sanitized measurements and verification receipts.

The existing reports backup button saves to the same database; it is not a
separate recoverable copy. Multiple files on one Mac are also not independent
off-device backups. A rollback must retain compatible compressed readers and
use a destination with measured capacity; expanding every original into a full
free-tier cluster is not a viable assumed recovery path.

**First delivery boundary:** complete exporter, resumable manifest, independent
verification and a reference-aware dry run, with no deletion. Actual retention
requires verified recoverability and an explicit policy tied to exact records.
