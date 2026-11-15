# Temporary storage recovery and durable sector-source proof

**The quota refusal is relieved. MARS now persists normal portfolio saves and
the official sector-source snapshot.** The initial 14 November verification
left ordinary comparison and controlled restart observations pending. The
15 November restart result is linked below; SEQ-07 remains queued. Permanent retention and prior-process state reconciliation are
separate open items; this is not a completed online compression migration.

Later verification, 15 November: [PR212](2026-11-14-context-use-time.md) is live,
and confirms acknowledged sector saves and unchanged financial fields across
the release restart. Normal market-session comparison remains pending. The
original observations below remain dated recovery evidence.

The owner declined a paid tier and subsequently requested a private download
and limited cleanup. The repository is public, so only source and sanitized
proof are published. Database BSON, broker/session settings, exact cleanup IDs
and private manifests remain in owner-controlled private storage.

## Verified changes and preserved history

| Check | Observed result |
|---|---|
| Full native backup | 19 collections; 82,740 documents; 364,090,190 raw BSON bytes; all source collections matched on a second independent read |
| Native restore | Every original document, collection option and index restored and checked locally; a self-contained download helper passed another full restore |
| Exact cleanup | 10,000 older SCAN_DECISION copies; 77,755,460 bytes / 74.15 MiB; 11 November 19:09:59 through 12 November 06:26:14 UTC |
| Exclusions | Every BUY, SELL, EXIT_DECISION and non-scan row; rows linked by 1,797 known trade-reference IDs; at least latest 20,000 archive rows |
| Execution | Full local rehearsal; changed-record/repeated-delete guard tests; live read-only BSON preflight; 500-row canary followed by 9,500 rows; 100-row acknowledged and read-back batches |
| Untargeted journal | All 30,486 remaining records independently matched their original raw BSON; 435 BUY, 395 SELL and 1,561 EXIT_DECISION rows preserved |
| Financial book | 53 open positions and 214 closed trades retained; closed-trade entries identical; archived trades collection's 181 rows retain the original hash |
| Position updates | Only `intraday_close` metadata changed during normal startup recovery; no quantity, entry, stop, target or segment change was introduced by cleanup |
| Actual application persistence | Durable portfolio `last_save` advanced from `2026-11-13T06:23:54.436016` to `2026-11-14T17:34:24.979162` UTC |
| Sector-source persistence | One saved registry; attempted at `2026-11-14T17:34:38.675187+00:00`; 14 of 15 official sources observed; Cement remains unavailable with ReadTimeout |
| Owner page | `/market-context` shows retained source timestamps; Cement and historical decisions without recorded context remain Unknown |
| Capacity / health | Rounded cluster logical estimate fell from about 508 to 434 MiB; HTTP 200, ready, connected, `write_error=null`; actual source/portfolio read-back establishes write recovery |

Cleanup touched only `sourceborn.journal_archive`. No other database, price
history, financial ledger, trading threshold, paid plan or credential changed.
No production scan or trade was manually induced. The cold-only scan records
remain fully recoverable in the private native backup; their individual online
explanations can be unavailable until a compatible archive reader/restoration
is wired. These records were unique recent evidence, not duplicate trash.

The immutable private cleanup manifest has SHA-256
`c45ee94ca88e314a47219c9646a1a41ac0661cbc8dca4d8c862be97026217fc7`.
Conditional deletes compared the current complete document values at execution;
raw bytes were independently checked before deletion. Acknowledgements and
read-back were checkpointed. The final untargeted-byte comparison completed
at `2026-11-14T17:40:52.058234+00:00`.

The private recovery bundle is **25,678,572 bytes** (24.49 MiB), with all
74 packaged files independently rehashed. Its SHA-256 is
`99e3545a44538043e1edf4f1665a6cc8c13888bac20e275949cb9561f4f3e125`.
It includes the original full BSON capture, later recovery versions, exact
private cleanup manifest, checkpoints and the tested localhost restore helper.

## Archive and live-state boundaries

The pre-cleanup native capture is complete for **stored MARS documents**. It
is not a quiesced point-in-time snapshot or a capture of Render memory/spills.
The raw folder and download currently reside on the same owner Mac, so two
formats do not satisfy the plan's independent off-device backup requirement.
The owner's later bounded-cleanup request is recorded separately from approval
for the full A1–A9 online migration; the remaining backup conditions stay open.

Ordinary navigation woke a Render free instance; startup completed at
`2026-11-14T17:33:59.051015438Z`. No manual deployment or restart was triggered.
Any state that existed only in the earlier process has not been proved captured.
The post-recovery portfolio/settings/trade versions were also independently
reread, privately captured and restored into a new local scratch database.
Do not infer that freeing capacity replays all older local spills: the current
code requires a disconnected-to-connected recovery transition.

The full **40,486-record** original journal compressed offline from
**282,995,386 to 62,355,043 bytes**, with every original byte recovered. This
is **220,640,343 bytes / 210.42 MiB** of demonstrated potential savings across
the original full archive. It is not additional live space already freed, and
cannot be added to the 74.15 MiB cold-archive relief. No compressed records have
been inserted online; all readers/writers must support the versioned format
before that migration.

## Source shipment and tests

The live functional revision remains
`47e7e0536b6c84f9f9f6698e5d1f76e80b315b14` from
[PR207](https://github.com/damandamanaulakh-tech/MARS/pull/207), Render
`dep-dbrcergjo6nc73ev2ug0`; its original CI passed 3,185 tests with zero skips.

The offline archive foundation is merged in
[PR210](https://github.com/damandamanaulakh-tech/MARS/pull/210) as
`24f7fe0d22e7c41d4aa3d42ccf95b38a85048996`, from reviewed head
`60f9acd22f785a5101c6e1f35692941e41b54399` and tree
`b56809af88c9ac05bd3e5d8de9da6e5cb508be22`.
[Canonical CI](https://github.com/damandamanaulakh-tech/MARS/actions/runs/43620592692)
passed **3,247 tests, zero failures/errors/skips** in 297.06 seconds. Both the
repair-record job and no-skip guard passed. Local validation was 54 focused
and 3,248 broader tests; the one local surplus is an unrelated untracked
script's syntax check. Original audit files remain unchanged.

This recovery follow-up updates evidence and workflow, with `[skip render]`.
It does not activate the codec or silently redeploy diagnostics. CI, merge and
any later deployment must remain distinct. The original 165 finding IDs and
all 170 backlog rows are retained; RUN-20261114-02 is an additional incident.

## Open work

1. Observe an ordinary completed-bar stock/sector/market decision. The controlled
   release restart and source persistence are now verified in PR212. Preserve
   unknown inputs; do not create a trade to obtain the remaining observation.
2. Wire and test every compressed archive reader/writer, resumable acknowledged
   migration, connected-quota spill replay, sustainable retention and the
   XR-071 refused-write entry guard. Temporary relief alone does not fix these.
3. Establish an independent private off-device copy and reconcile prior-process
   state coverage before representing recovery as lossless across live RAM.
4. Restore broker authorization through its normal owner flow. Render logs show
   repeated Upstox quote HTTP 401 responses after startup, while the header
   retains a green stored-profile indicator. Storage recovery does not verify
   broker authentication or fresh quotes. ROOT-ROUTE-09 retains this subclaim;
   no provider secret is published.

[Sanitized machine-readable proof](../evidence/2026-11-14-storage-recovery.json)
retains counts, dates, source hashes, exact preserved scope and limitations.
