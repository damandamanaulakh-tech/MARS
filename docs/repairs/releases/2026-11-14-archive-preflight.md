# Archive preparation — offline verification, not a live storage migration

SEQ-06 / OPS-05 / RUN-20261114-01 remains open. The owner retained the free
tier and then requested a downloadable backup and bounded cleanup. MARS's
GitHub repository is public; database exports and connection credentials must
stay outside it. Only code, instructions and sanitized proof belong here.

## Implemented scope

`web/journal_archive_codec.py` preserves an entire original BSON record in a
versioned zlib/Binary envelope, including its `_id`, field types and unknown
fields. It retains the original representation if compression would grow it.
Readers verify the original length, SHA-256 and query envelope before returning
data. Unknown formats, reserved-field collisions, corrupt/truncated/trailing
streams and excessive output fail explicitly. They cannot look like successful
empty evidence.

`web/journal_archive_preflight.py` and its command-line wrapper stream a native
BSON file into a new private directory, record every original ID/hash/length,
retain index metadata, and independently reopen and verify every source,
candidate and manifest record. SQLite bounds duplicate-ID tracking; individual
BSON, manifest and metadata reads have size limits. Existing directories cannot
be overwritten. Failed preparation retains its input and an explicit failure
record, with no success marker. The verifier has no 20,000-record report cap.

These modules cannot connect to MongoDB and are **not wired into live archive
readers or writers**. They neither delete nor migrate production data. A successful
file verification is explicitly not a database restore, a complete source
inventory, or proof of multiple durable backup copies. A2 of the
[archive plan](2026-11-14-lossless-archive-plan.md) is therefore partial.

## Usage

Use a private native `.bson` export, never a JSON/CSV conversion or the earlier
ten-record connector experiment. Keep all input and output paths outside Git.
Replace the illustrative private paths with the actual approved locations:

```sh
python scripts/journal_archive_preflight.py prepare \
  --source /private-backups/journal_archive.bson \
  --metadata /private-backups/journal_archive.metadata.json \
  --expected-rows 40486 \
  --directory /private-backups/new-candidate

python scripts/journal_archive_preflight.py verify \
  --source /private-backups/journal_archive.bson \
  --directory /private-backups/new-candidate
```

The example count is the incident inventory, not a permanent expected count;
re-measure the actual source. The CLI does not accept a MongoDB URL or perform
deletion. `.bson`, `.bson.gz` and the private preflight directory are ignored by
Git as an extra guard; a public repository is not a backup destination.

## Validation and limits

- 54 focused regressions passed with zero skips. Cases cover BSON integer
  widths, decimals, out-of-range dates, binary subtypes, NaN, null/absent fields,
  raw duplicate/unknown fields, corrupted envelopes, source changes, duplicate
  IDs, artifact tampering, no-overwrite behavior and 20,005-row verification.
- The final local application suite passed **3,248 tests, zero failures/errors/
  skips** in 240.19 seconds. Only untracked duplicate `* 2.py` test files were
  excluded from this canonical run; the existing syntax collector still saw
  one untracked duplicate script. GitHub CI must provide the canonical count.
- The earlier full run including duplicate local test files passed 3,700 tests;
  it preceded the three added unknown-marker cases and is not final-code proof.
- Review reproduced an unknown-format envelope being returned as a legacy
  record. The corrected reader rejects it, a malformed marker and a missing
  marker with query metadata; the final regression suite includes all three.
- The original 74-trade audit CSV retains SHA-256
  `6d50ab7bfbb6563634805ae64fb0e3f235dae2b5df47388bd3abab402e59107c`.

[Exact local validation and source hashes](../evidence/2026-11-14-archive-preflight-validation.json).
Publication, CI, merge and live capacity recovery are recorded separately.
No production space recovery or SEQ-06 completion is claimed by this batch.

## Cleanup findings

The 33,718 SCAN_DECISION records occupy 274,265,872 BSON bytes, with zero
duplicate decision IDs. They are recent decision evidence, not duplicate trash.
The 80 raw market-information captures use 2,299,960 bytes and retain source
observations. The OHLCV store contains 2,233 ticker documents; the code uses it
for breadth, scanning, valuation, charts and research, including stale histories.
Clearing it would remove inputs, not merely flush an unused cache. The database
named `test` belongs to a different app and is outside MARS cleanup scope.

PR210 merged as `24f7fe0d22e7c41d4aa3d42ccf95b38a85048996`; [CI](https://github.com/damandamanaulakh-tech/MARS/actions/runs/43620592692) passed **3,247 canonical tests, zero skips**, including repair-record and no-skip guards. Deployment of these offline tools remains unnecessary.

The later [storage recovery](2026-11-14-storage-recovery.md) records the completed native capture/restore, full-dataset compression measurement and separately authorized exact temporary cleanup. These are distinct from the offline tooling mutation count above.
No database records have been removed by this preparation.
