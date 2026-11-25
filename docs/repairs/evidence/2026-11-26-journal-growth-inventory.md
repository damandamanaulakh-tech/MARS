# Storage follow-up: scan evidence dominates journal growth

This read-only inspection supplements the earlier storage recovery. It does not
delete records, implement retention, or establish a new recoverable backup.
[Measured aggregates and exact queries](2026-11-26-journal-growth-inventory.json)
were captured while writers remained active. The first aggregate started at
2026-11-25 19:45:04 UTC. Counts increased between reads, so these observations
are not one atomic snapshot.

| Area | Observed evidence | Impact | Next bounded action |
|---|---|---|---|
| Journal volume | 79,249 records; 155,273,748 BSON document bytes (148.08 MiB), excluding indexes | The journal is the main MARS storage consumer | Prepare a complete resumable export and dry-run retention manifest |
| Scan decisions | 70,114 records; 138.81 MiB, 93.74% of journal bytes | Deleting old closed trades alone cannot materially control this volume | Preserve gate evidence and trade references while reviewing repeated scan generation |
| Compression | 73,654 records carry `zlib/v1` metadata; current append path still uses the compression codec | Compression reduces individual rows but cannot bound an append-only archive | Keep compressed readers/writers; independently verify any archive before removal |
| Nighttime scans | 3,231 archived scan decisions and 6.10 MiB in one measured 15-minute window | Storage can grow while entry execution is blocked by market hours | Review startup/off-hours work using completed-input identity and durable progress |
| Startup catch-up | Two `SCAN_CATCHUP` records classify their trigger as startup, about 120 seconds after corresponding Render starts | Repeated starts can schedule additional analysis work | Test restart and incomplete-pass recovery without suppressing the next market session or protective exits |
| Current RAM tail | A separate 156-row Intraday subset was blocked with unavailable history/intraday data | Failed data preparation still generates evidence; this subset does not describe every archived decision | Keep unavailable outcomes explicit; avoid regenerating identical evidence unnecessarily |
| Existing bounds | 5,000-row/2 MiB RAM journal bounds spill to Mongo; archive has no TTL or retention limit | Lowering RAM bounds would move data into Mongo sooner, not free Mongo storage | Design storage retention separately from application-memory limits |
| Export coverage | Current journal CSV reads the newest 20,000 archived rows | The CSV is a report, not a full journal backup | Add resumable complete coverage, stable IDs, counts, checksums and restore proof |

The later live health read at 2026-11-25 20:00 UTC reported approximately
421 MiB across the shared cluster against its 512 MiB free-tier limit. Other
applications share that quota. That rounded health estimate is a different
observation from the collection aggregate above. Neither the 15-minute window
nor the comparison with the 20 November archive is a constant growth forecast.

## Other large collections and shared databases

The post-release health observation, verified by **25 November 20:09:59 UTC**
(26 November 01:39:59 IST), supplies these rounded figures. They are later than
the exact journal aggregate above and include subsequent writes.
[Sanitized size-only snapshot](2026-11-26-storage-health.json).

| Location | Data MiB | Index MiB | Interpretation |
|---|---:|---:|---|
| MARS `journal_archive` | 152 | 7 | Largest MARS collection; 81,327 records at this later read |
| MARS `ohlcv` | 63 | 0 rounded | Price-history inputs; removal requires a separate coverage/reacquisition review |
| MARS `portfolio` | 4 | 0 rounded | Current financial book; preserve it |
| MARS `fundamentals` | 3 | 0 rounded | Fundamental inputs; not an approved deletion target |
| MARS `market_info_raw` | 2 | 0 rounded | Raw context evidence; references and refresh behavior need review |
| MARS `watchlist_snapshots` | 2 | 0 rounded | Scan snapshots; reference-aware retention review required |
| Other database `ashstock` | 156 | 0 rounded | Separate app; MARS cleanup does not authorize modifying it |
| Other database `test` | 12 | 1 | Name alone does not establish that its contents are disposable |
| Other database `site_control` | 9 | 0 rounded | Separate app; preserve pending its own review |

MARS's database totals approximately **241 MiB including indexes**; all shared
databases total approximately **421 MiB**, leaving about **91 MiB** against the
512 MiB limit at that observation. These are logical sizes, not the separate
on-disk compressed `storage_mb` metric. The health collection list is limited to
12 entries; zero rounded size does not prove an empty collection. No other
application's data was changed by this work.

The inspected compressed payloads were not decoded or individually hash-verified
in this inventory. Declared original-byte metadata is not a substitute for a
native export and independent restore check. Missing a top-level `trade_id`
does not prove a scan row is unreferenced: positions, trades or research may
link its decision identity.

Current implementation pointers:

- [Archive writer and read bounds](../../../web/journal_storage.py).
- [Compression codec](../../../web/journal_archive_codec.py).
- [Mongo archive persistence](../../../web/db.py).
- [RAM journal bounds](../../../trading/paper.py).
- [Startup catch-up and trading schedules](../../../web/scheduler.py).
- [Earlier preserved archive and approved history cleanup](../releases/2026-11-20-storage-and-history.md).

The next storage batch should produce a reviewable, reference-aware archive plan
with complete coverage and restore evidence before any additional deletion.
[The concrete preflight plan](2026-11-26-storage-retention-preflight-plan.md)
defines the existing code to reuse, complete-export gaps and proof required.
No paid hosting change, trade deletion, scan-policy change or new retention
threshold was applied by this inspection.
