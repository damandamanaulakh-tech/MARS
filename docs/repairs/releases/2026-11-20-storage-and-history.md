# Storage recovery and approved closed-history cleanup

**Final runtime verification:** Goal11 engineering and storage recovery are shipped in [PR225](https://github.com/damandamanaulakh-tech/MARS/pull/225), revision `87285df71a7cebe6b62c01636aac0eaef5afcd9b`. All **3,539 canonical CI tests**, zero skips, and all six jobs passed, including both complete combined replays. The exact automatic Render deployment is live. Native saves, the approved cleanup, both HTTP exports, same-version restart and code-version transition are verified. All historical scenarios have negative net expectancy; **genuinely new forward performance acceptance remains open**, with no settings promotion. [Release proof](../evidence/2026-11-20-storage-and-history.json).

The storage hotfix is live, native journal migration and the approved trade
cleanup are complete, and ordinary portfolio saves have been independently
read back and restored. The combined Goal11 integration is merged in
[PR224](https://github.com/damandamanaulakh-tech/MARS/pull/224) as
`7337aa29510b3775de44ac0f3df2998cdc7ad85d`. It is live as Render `dep-dc00e86i0phs73bblqrg` at 08:40:32 UTC, after the
normal scan completed and a fresh 33-position/80-trade native checkpoint and
3,939-row journal delta were independently restored. Its forward run identity
and inherited baseline are verified in
[Goal11 live evidence](../evidence/2026-11-20-combined-live.json). The owner resumed Goal11 on
20 November; future performance acceptance stays open.

[Machine-readable evidence](../evidence/2026-11-20-storage-and-history.json)
records observation times, hashes and remaining limits. Private raw BSON,
trade CSVs, record identities, manifests and credentials are excluded from this
public repository.

## Owner cutoffs and verified results

The fixed selection removes closed trades entered through **30 September 2026
IST**, plus closed intraday exits through **17 November 2026 IST**. Intraday
records from 18 November onward remain. Resumption on 20 November did not extend
the cutoffs. Open positions and financial risk state are excluded.

| Evidence | Verified result |
|---|---|
| September-entry CSV | 240 rows; original values and full evidence retained |
| Additional closed-intraday CSV | 95 rows; no overlap with the September export |
| Exact cleanup at 07:26 UTC | 335 rows removed: 154 embedded and 181 archived; 75 later rows retained |
| Financial preservation at cleanup | All 38 open positions, cash, capital, stops, targets, drawdown state and equity history unchanged |
| Later restart/checkpoint, 08:11 UTC | Policy intact, all 335 selected fingerprints absent and all 75 retained rows present |
| Ordinary engine activity | Three subsequent protective exits produced 78 closed rows and 35 open positions; those new exits remain |

The original saved snapshot contained 409 distinct closed rows and 39 open
positions. One normal protective close before deployment increased retained
history to 75 and reduced positions to 38. The three subsequent exits account
for the later 35/78 totals. Cleanup did not close positions, reset cash or erase
loss controls. Counts are timestamped observations, not a promise that the live
book stops changing. Original equity history remains a prefix; later marks and
peak-price updates are ordinary engine activity.

Both CSVs were independently re-read and reconciled by identity, row counts,
gross P&L, costs and net P&L. Exported history is separate from the current
performance sample. Exact fingerprints prevent future saves or overflow archival
from reintroducing removed records; they do not create a rolling date purge.

## Native preservation and journal recovery

The final native backup contains **82,618 documents in 19 collections**, totaling
**377,500,835 raw BSON bytes**. Every source document, collection option and
index was independently re-read and restored into isolated native MongoDB
8.0.38. Failed early captures and timeout logs are retained separately. The
private ZIP's 65 entries were verified individually; its SHA-256 is
`808350eab20766b43e9acd96a8bfca7f396251f815ebc35e0186a899df905af1`.
Credential exports and browser cookies are excluded. All backup copies are on
one owner Mac; an independent off-device copy remains outstanding.

Exactly 10,000 old, unreferenced scan-decision rows were temporarily removed
after the native backup, source comparison, restore and 500-row canary passed.
All 30,333 other originals remained byte-identical. Conditional exact-document
operations and fsynced batch checkpoints prevented broad or changed-source
removal. Two long migration connections failed; bounded re-read/retry handling
was verified with a native disconnect-after-commit test before resumption.

At **08:17:41 UTC**, all **40,333 original rows** were read back and verified as
exactly recoverable, including **all 10,000 temporarily cold rows restored
online**. Compression changes representation, preserving original BSON types,
field order, unknown fields and evidence. Those original records shrink from
**296,559,871 to 66,788,255 bytes**, saving **229,771,616 bytes / 219.13 MiB**.
The collection had 41,902 rows after ordinary new appends. New rows outside the
migration's exact source identities were not overwritten.

The deployed storage revision is
`fd0700ac58c48d47769e25fc8d945ec1315f5154`, Render
`dep-dbvv3quc6qcc73amm5k0`, live at **07:10:16 UTC**. It was isolated from the
then-live Goal10 revision so storage could recover before the resumed Goal11
rollout. Its [CI](https://github.com/damandamanaulakh-tech/MARS/actions/runs/44689081974)
passed all four jobs and **3,489 tests with zero skips**.

## Memory, query and storage findings

Shared-cluster logical data plus indexes fell from **528.38 MiB** to **317.53
MiB** at 08:23:21 UTC, leaving **194.47 MiB** below 512 MiB. This includes a
supporting export index and continuing writes by other applications. Physical
storage allocation differs from logical quota usage; compression savings are
not inferred from a smaller local ZIP.

The owner decision reader returned the exact original evidence for a restored
cold record. The first live CSV test exposed MongoDB error 292: projecting IDs
still allowed a blocking sort to retain full documents and exceed the free
tier's **32 MiB** limit. A native local rehearsal reproduced that failure with
disk spill disabled. A nonunique `(ts DESC, _id DESC)` index removed the blocking
SORT stage, read all 20,000 requested rows and preserved the 40,333-row count.
Traced Python peak allocation was **7,144,268 bytes / 6.81 MiB**. Repeat index
creation was idempotent. The live query plan now uses IXSCAN with no SORT stage.
Automatic preparation on database connection is included in this follow-up;
an index failure cannot discard an otherwise connected financial book.
The live native iterator read all 20,000 rows in 40.07 seconds. The owner HTTP
export timed out after 300 seconds during the ordinary scan; HTTP acceptance was pending at that time; the final repaired-route download below passed. Render showed sustained CPU use around the 0.15-core allocation
and roughly 288–298 MiB memory during that interval, without an observed OOM.
Those samples do not isolate the cause of HTTP latency.

The report writer streams 100 payloads at a time after selecting the newest
IDs, instead of retaining 20,000 complete gate snapshots. This bounds report
allocation; it does not establish a maximum for every application workload.
Sparse Render samples showed no observed OOM and a maximum around 286 MiB
before the hotfix; post-restart health reported roughly 161 MiB. The confirmed
incident was Mongo quota refusal. Permanent retention remains necessary.

| Current large collection | Logical data | Records | Action in this batch |
|---|---:|---:|---|
| MARS `sourceborn.journal_archive` | 67.78 MiB | 41,902 | Losslessly compressed; all original evidence recoverable |
| MARS `sourceborn.ohlcv` | 63.08 MiB | 2,233 | Retained; market/research inputs |
| MARS `sourceborn.fundamentals` | 3.55 MiB | 26,378 | Retained |
| MARS `sourceborn.portfolio` | 3.20 MiB | 1 | Financial book preserved; selected closed history removed |
| MARS `sourceborn.watchlist_snapshots` | 2.50 MiB | 24 | Retained |
| AshStocks `ashstock.scan_ledger` | 129.75 MiB | 875 | Read-only inventory; separate application |
| AshStocks `ashstock.app_state` | 9.63 MiB | 1 | Read-only inventory; active state |
| `test.unrealtorealpasstraces` | 4.64 MiB | 6,787 | Read-only inventory |
| `test.stagetraces` | 3.39 MiB | 6,769 | Read-only inventory |
| `site_control.sites` | 4.24 MiB | 2 | Read-only inventory; active business records |
| `site_control.work_order_reviews` | 3.10 MiB | 2 | Read-only inventory |

The standalone old trade archive occupied only **96,912 bytes** before cleanup;
removing it alone could not solve a 528 MiB cluster quota. AshStocks scan
snapshots are now the largest collection and need their own archive/retention
review. No other database was modified. No paid upgrade was made.

## Tests, deployment and remaining boundaries

The integration head `d4e067dcbfbe17c93e0af176fcc064159cd7ff9b` passed all
**3,535 tests with zero failures/errors/skips** and all six
[CI jobs](https://github.com/damandamanaulakh-tech/MARS/actions/runs/44690960732),
including complete original and attribution Goal11 replays and unchanged
Goal08/09/10 research. Its tree is
`0703c5c382caaf2ecfa374ccfc607e5ab7a593b0`, identical to the PR224 merge tree.
The automatic-index follow-up passed 68 focused storage/combined regressions;
its full Git-tracked suite passed **3,538 tests with zero failures/errors/skips**.
The first directory-wide run also collected unrelated untracked ` 2.py` copies
and failed 11 stale duplicate fixtures; those files were left untouched. Every
Git-tracked test passed in the canonical rerun. That intermediate local check was followed by the completed PR225 CI and runtime evidence below.

Native tests cover source-change refusal, wrong-manifest refusal, incomplete
checkpoints, retry after a committed write loses its acknowledgement, exact
restore, mixed raw/packed readers, conflicting identity refusal, bounded export,
and the real 32 MiB non-spilling sort failure. No production scan or trade was
induced. No fee, stop, target, sizing or entry threshold was changed.

Ordinary backup requests were verified through new Mongo `last_save` timestamps,
raw source read-back and an isolated native restore. The route's `backup_ok`
redirect alone is not evidence: its legacy handler does not inspect a false
return value. Financial changes between snapshots are reconciled as ordinary
activity rather than overwritten with an older book. Natural free-instance
restarts were observed, and the retention policy remained effective.

Prior-process RAM/spill coverage remains unknown; the recovered stored state
cannot prove that every earlier unsaved event survived. A connected-quota
write-refusal entry guard (XR-071), sustainable retention, an independent
backup destination and background-work reliability on Render free instances
remain open. Compression provides finite headroom. Goal11's future acceptance
requires genuinely new observations and does not follow from passing tests or
restoring database writes. All 165 original IDs, 170 backlog rows and five
incidents remain in the canonical tracker.

## Export dependency follow-up

After the native index repair, the HTTP export still timed out during the
ordinary scan. Code inspection found that every individual download generated
all six reports and fetched current quotes, even for a historical journal or
trade CSV. Two route regressions fail against that implementation when quote
access is unavailable. The repaired route generates only the requested report;
historical downloads do not fetch quotes, and small unrelated downloads do not
load the journal archive. All 79 focused storage, report and combined-validation
checks pass. The final full Git-tracked suite passes **3,540 tests**, zero failures/errors/skips
in 219.40 seconds. PR225 CI and live HTTP acceptance subsequently passed, as recorded below. No export row limit was reduced.

## Final deployed export and restart proof

Reviewed head `41728fb6043c01e8591bdd459c03b33c77c899a0` and merge `87285df71a7cebe6b62c01636aac0eaef5afcd9b` have identical tree `eb9444c562217dcc307b1b4e81182777b463adc4`. [CI](https://github.com/damandamanaulakh-tech/MARS/actions/runs/44706052095) passed all six jobs, **3,539 canonical tests**, zero failures/errors/skips. Original combined outcomes matched at 09:32:44 UTC, and segment attribution matched every unchanged strategy outcome at 09:46:09 UTC. The 3,540-case local run included one extra syntax-audit parameter for an existing untracked duplicate script; no canonical test was missing. Earlier failed duplicate-module runs remain recorded above.

Render `dep-dc01k6ou01pc73b9m4d0` deployed the merge automatically at **10:01:32 UTC /15:31:32 IST**. The original On Commit setting was restored at 09:17:48 UTC and independently read back; no duplicate deployment was triggered. Health and secure readiness passed. The ordinary backup at 10:09:21 UTC was independently read back and restored, with all financial fields unchanged.

| Production workflow | Observed result |
|---|---|
| Restored cold decision | HTTP200; exact original evidence, 0.79 seconds |
| Journal CSV | HTTP200; **20,217 rows**, 2,238,839 bytes, **65.16 seconds**; archive limit remains 20,000 |
| Trade CSV | HTTP200; **80 rows**, **1.09 seconds**; native identities and rounded gross/cost/net totals match; all 335 removed rows absent |
| Goal11 version transition | Two old manifests and eight events byte-identical; new source manifest separated; one baseline and original epoch retained |
| Current forward state | Three manifests, ten events, zero pending exports in API and persisted book; zero new closed forward trades |
| Render memory samples | About 171–216 MiB on the new instance from 10:02–10:10 UTC; application health RSS 213 MiB, process peak 241 MiB |

Cluster logical usage at **10:09:15 UTC** is **334.29 MiB**, leaving **177.71 MiB** below 512 MiB. The earlier 317.53 MiB observation remains valid at its timestamp; ordinary new records and other apps account for later growth. This is finite headroom, not permanent retention. AshStocks scan ledger is now 133.54 MiB (898 records); MARS journal 78.27 MiB (46,742), OHLCV 63.12 MiB (2,233), portfolio 4.39 MiB, and fundamentals 3.55 MiB. Other apps were only inventoried. Scan-decision snapshots account for 91.11% of journal bytes in the 09:19 UTC composition check, identifying the main future archive target without deleting evidence.

A separate offline composite recovery rehearsal restores 21 collections and 86,382 documents, with the cleaned book, compressed original journal, one cumulative new-identity delta and immutable forward records. The standalone localhost-only helper independently restores every BSON record, option and index, refuses a nonempty destination and rejects changed files before connecting. It is a dated composite, not an atomic all-writer snapshot. Private final downloads contain updated dated checkpoints; all copies still reside on one owner Mac.

All 165 original IDs, 170 backlog rows and five incidents remain. The bounded storage/export/restart repair is complete; permanent retention, independent backup, earlier process-only gaps, the XR-071 refused-write entry guard and future performance acceptance remain open.

The final separately captured journal delta contains **6,409 new identities /15,289,472 bytes** at 10:13:43 UTC. Every selected source record was independently re-read and restored. The delta is cumulative relative to the 40,333 originals; it replaces the earlier delta in a recovery assembly rather than being appended twice. Two additional ordinary protective exits before the Goal11 baseline account for the change from the earlier 35-position/78-close checkpoint to 33 positions and 80 closes. They were retained.
