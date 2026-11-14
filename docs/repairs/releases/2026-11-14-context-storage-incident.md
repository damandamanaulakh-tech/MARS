# SEQ-06 runtime blocker — Atlas storage quota

Status: capacity recovery required. Goal06 remains active; Goal07 stays queued.
The core [SEQ-06 implementation](2026-11-13-market-sector-context.md) is merged
and live. This record does not claim that a successful HTTP response proves
persistence. The original ledger and all source evidence are retained.

## Observed failure

At 16:26 UTC on 14 November 2026, `/healthz` returned 200 with ok/ready/connected
true, but its write_error reported AtlasError 8000: **513 MB used / 512 MB**.
Render also reports refused settings and portfolio saves. The independent
sector refresh logs `OperationFailure`; the saved identity document is absent.
The observed app revision is `47e7e0536b6c84f9f9f6698e5d1f76e80b315b14`.
[Sanitized live evidence](../evidence/2026-11-14-context-storage-incident.json).

Read-only Mongo at 16:29 UTC confirms the last portfolio save remains
`2026-11-13T06:23:54.436016`, with 53 open positions and 214 closed records.
Later in-memory mutations are not proved durable. No scan, trade, outage,
production deletion or plan change was induced by this work.

The health snapshot reports approximately 269 MiB in journal_archive, 62 MiB
OHLCV and 3 MiB portfolio. The cluster is shared with ashstock and five other
application databases. Its cached, rounded dbStats total is 508 MiB; that
measurement does not override the actual writer's quota refusal.

This confirms OPS-05. XR-071 remains open: connected=true/degraded=false is not
an entry-write safety contract. The diagnostic repair below does not silently
claim to fix the broader persistence/recovery gate.

## Bounded diagnostic repair

A separate current-process record names quota/permission/generic write failure
without exposing raw server text, credentials or a URI. Only an explicit
acknowledgement marks a save successful. A seed cannot masquerade as a stored
snapshot. The owner comparison page, JSON export and health endpoint expose
the last refresh status. These reads do not perform network/database work
for diagnostics. Restart resets status to unknown. Successful sector saves
do not clear a separate portfolio failure, and previous source timestamps
remain unchanged when a refresh cannot be stored.

Six new regression cases failed before the repair. Seven new cases cover the
failure classifications, no raw error leakage, acknowledgement recovery,
owner/export/health visibility, unknown after restart and fresh-seed handling.
Focused and full validation evidence is recorded below after completion.

## Capacity options and owner decision

Verified cluster: `sourceborn`, FREE, AWS `AP_SOUTH_1` (Mumbai), MongoDB 8.0.37.
A considered capacity option was an in-place **Flex** upgrade preserving
provider, region and data. MongoDB publishes **5 GB storage**, **USD 8–30 per
30 days based on usage**, billed hourly. This is a new recurring cost; no
purchase is authorized or performed by the current repository repair.
[Official pricing](https://www.mongodb.com/docs/atlas/billing/atlas-flex-costs/).

MongoDB requires downtime for Free-to-Flex scaling, halting writers during the
operation and restarting application clients afterward. The cluster hosts
other applications, so their writers must be included in maintenance. Do not
start the upgrade until the owner authorizes cost and shared maintenance,
current recoverable data is preserved, and active writers can be controlled.
[Official scaling requirements](https://www.mongodb.com/docs/atlas/scale-cluster/).

Owner selected **keep free tier; prepare a lossless archive plan** on 14 November.
The paid upgrade option is not approved. [Prepared archive plan](2026-11-14-lossless-archive-plan.md) includes live sizes, sample compression, every caller, backup/restore requirements and exact migration approval boundaries. Preparation does not authorize production changes.

After capacity is restored: verify an ordinary acknowledged portfolio save;
allow the scheduled sector refresh, read it back, compare observation times and
source failures; then check across a coordinated restart. Do not induce a
production trade for proof. Until then, live persistence remains blocked.

## Publication and restart boundary

Publish the diagnostic code and truthful workflow records after zero-skip CI.
Use a Render-skip merge for this follow-up while the cluster refuses saves;
the currently deployed Goal06 implementation remains live. This is not a
test skip. Deploy the diagnostic code after coordinated capacity recovery
and a confirmed save, when restarting cannot silently discard the current
in-memory book. Mark the follow-up deployed only after exact-revision proof.

Local follow-up validation: **65 focused and 3193 full-suite tests passed**, zero failures/errors/skips; full suite 235.52 seconds on Python 3.12. Canonical CI expected 3192 because the same unrelated duplicate-script syntax parameter adds one locally. CI/merge pending for this follow-up.
