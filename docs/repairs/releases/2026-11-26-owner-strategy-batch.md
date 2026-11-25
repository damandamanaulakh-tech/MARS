# BATCH-001 — dated owner requests through the paper strategy engine

Status: **PR232 shipped; exact live deployment and save verified. Actual owner batch execution remains pending.**

## Owner decision and baseline

The owner requested all eligible names fitting their segment allocations, excluding
Intraday, with normal automatic entry/exit management rather than owner-deck
positions. A one-time measured-performance entry-veto exception was explicitly
approved. The original 25 November batch was not submitted. During this
continuation the owner renewed authority for **the next open session's fresh
selections**, with the same scope. No production batch or broker order has been
submitted by this work. A saved code change is not execution evidence.

Implementation starts from reviewed saving-repair head
37c90fe1f4e3ef6d3d602295faf10563abf34dc8. PR231 saving recovery was required and
was verified before this release. Six-segment allocation remains Momentum 30%, Intraday 10%, Short 24%,
Swing 24%, Positional 10%, Ocean 2%; ordinary reserve and exposure rules still apply.

## Implemented behavior

- Owner-only explicit POST from the same origin; GET status and page views never
  start a batch. Session identity supplies the actor. UI checkbox records the
  specific exception, unchecked by default. No client-supplied segment expansion.
- Sequential Momentum, Short, Swing and Positional engine scans with the existing
  Ocean overlay when enabled. No Intraday scan, pending owner order processing,
  pyramid adds or artificial 3/5-position cap. Existing eligible stop/target exits
  run in protective refreshes; positions retain their actual segment and tags.
- One worker owns rotation/mode locks. Temporary presets restore in finally;
  thread-local authority cannot leak into a concurrent or later ordinary scan.
- Only the measured-performance new-entry veto can be excepted. Its observed
  failed result remains failed and the owner exception is separately recorded.
  Positive performance sizing caps, actual stop risk, losses, costs, cash,
  segment room, macro/event/sector and executable-price checks remain effective.
- Actual wall-clock date/session checks occur before passes, rotation funding
  and final booking; stale captured scan clocks cannot extend authority. Quote
  source and same-session receipt must be proved. Each candidate receives a new
  routed quote immediately before final sizing; the receipt must belong to that
  request. Fill, quantity, stop/target, costs and budgets use its returned price.
  Failed or cached responses never reuse the scan quote. Existing executable
  price policy is reused; no new global age threshold or previous-close fallback.
- Dated UUID request IDs are idempotent. Current-day history is bounded to 16
  requests without evicting IDs; next accepted day starts a new current ledger.
  Acknowledged checkpoints precede execution/passes. Failure stops new work;
  unfinished results stay inspectable but never automatically replay on restart.
  An interrupted in-flight count remains unknown, never a false zero.
- Persist the ledger in JSON and Mongo. Request and terminal decisions are
  journaled; each actual entry and later exit retain original entry authority.
  Automatic exits on preexisting normal holdings keep their original attribution.
- The scan page reports actual full result counts and blocking reasons, separate
  actual opened segments and unknown partial outcomes. A transport failure polls
  status and never automatically resubmits a buy request.
- Forward validation separates owner-batch cohorts from untouched automatic
  strategy evidence. Authorization differs from an applied exception. Recorded
  economics retain losses, partial realizations and original costs, including
  historical unverified values; missing values stay unknown. No historical ledger,
  Kelly loss sample, strategy setting or sealed study is rewritten.

## Validation and limitations

Focused tests cover real scans, negative-Kelly authority, positive caps, budget,
stop risk, cash, save failures, quote evidence, late clocks, Ocean, protective
stops/costs/loss sampling, thread isolation, request concurrency, interruption,
JSON/Mongo round trips, owner routes, read-only status and actual rendered UI.
Attribution tests include a normal holding closed protectively while an owner
batch is active. UI tests expose unknown counts/costs and retained losses.

At the implementation checkpoint, full local validation, all six CI jobs, merge
and live observation were pending. The completed release proof follows below;
owner-authenticated page observation and actual batch execution remain pending. Existing Free-host sleep and slow-scan issues
remain separately open; this feature does not make an always-running guarantee.
No performance improvement or actual batch entry is claimed. Research validity
and genuine future automatic-strategy evidence remain separate acceptance work.
Existing rotation remains nontransactional: a funding sale can complete before
a subsequent fresh-quote request or entry check refuses its proposed replacement.
This batch does not claim atomic funding and entry; the refused buy never uses
the older scan price. Broader rotation preflight remains separately open.

The owner-approved next-session attempt has a single scheduled Codex task for
**26 November 2026 at 09:20 IST**. Its prompt pins that IST date and refuses a
later-date rollover. The [NSE capital-market holiday circular](https://nsearchives.nseindia.com/content/circulars/CMTR71775.pdf)
supports the planned session; an exceptional closure must still be checked at
execution time. It must verify the exact live release, acknowledged save, current
session and normal authenticated owner route before submitting one fresh dated
request. Missing prerequisites stop that attempt. This is renewed execution
authority, not replay of the expired original request or a broker-live order.

The same-origin check relies on the request scheme already resolved by Uvicorn;
Render's documented Python environment provides FORWARDED_ALLOW_IPS=*.
[Render environment reference](https://render.com/docs/environment-variables),
[Uvicorn proxy settings](https://github.com/Kludex/uvicorn/blob/main/docs/settings.md).
No proxy/security setting was changed by this feature.

## First full local attempt

The first full run completed with 3,742 passed and 8 failed, no skips. Seven source
inspection assertions read shifted file lines because the final engine edit
landed after the Python process imported that file. They are not waived: a new
process checks the unchanged assertions against the stable file. The eighth
failure exposed a test fixture freezing both an open-book and closed-book
observation at exactly the same instant, leaving latest-book ordering ambiguous.
The fixture now gives the real protective exit a later timestamp and retains
its exact completed-loss assertion. A complete stable-source rerun is required.

## Stable local validation

Final full suite: **3,750 passed, zero failures, errors or skipped tests**,
Python 3.12.16, 169.83 seconds. All 560 tracked/new source, template and workflow
file hashes remained unchanged through the run. JUnit independently confirms
all 3,750 cases ran. The first failed attempt and its two causes remain above.
The 110 new cases cover 32 real-engine, 32 lifecycle, 21 attribution, 23 route/storage
and 2 validation-page cases. The seven unchanged source assertions also passed
in a fresh process; the clock-order regression passed after the actual Mongo
save-wrapper predecessor test. Independent review found no remaining blocker
in scope; the rotation atomicity limitation remains explicit.

Published branch: `codex/strategy-entry-batch-2026-11-26`, initially stacked on
PR231's reviewed head. Saving recovery was released and verified before this
feature was retargeted to main and merged. The reviewed feature head did not
change. Publishing code and scheduling an attempt do not prove a paper entry.


## Final CI, deployment and preservation proof

[PR232](https://github.com/damandamanaulakh-tech/MARS/pull/232) passed all six
jobs in [CI45688095000](https://github.com/damandamanaulakh-tech/MARS/actions/runs/45688095000)
on reviewed head `834fa9b3910aa4050d9c7fe2ec82e8fb9d2610f5`: **3,750 application
tests, zero skipped**, 429.36 seconds. Both complete combined replays verified
5/15/30 bps scenarios and their reports. Original reproduction finished at
2026-11-25 20:38:47 UTC; corrected attribution exactly reproduced with unchanged
strategy outcomes at 20:23:15 UTC. These checks preserve existing evidence; they
do not establish profitable future performance.

The merge is `672b094d80a37f83c6c9238a564d735d7269a795`. Its tree
`24492747c66471c47fc9b3ff47d4cbbeb311787f` exactly matches the reviewed tree.
The pre-merge health check confirmed acknowledged saving and no write error.
Render automatically deployed `dep-dc3kf8t9el5s73ad658g`, matching that merge,
and reported **live at 2026-11-25 20:41:53.668758 UTC**
(**26 November 02:11:53 IST**).

The new process acknowledged saving at **20:41:49.071841 UTC**, before its
startup-completion log at **20:41:52.60674552 UTC**. Health verified by
**20:42:43 UTC** is ready, connected and not degraded, with no write error or
entry-block reason. Forward epoch `2026-11-20T08:41:42.997998+00:00` remains
retained; pending exports are zero and capture/export errors are absent. Shared
cluster use is approximately **421 MiB / 512 MiB** at that read.

Mongo readback has **39 open positions and 94 closed trades**, last_save
`2026-11-25T20:41:44.011777` (UTC by the writer implementation). The selected
cash, position and closed-trade projection exactly matches the pre-release
baseline, SHA256
`1171458e35388f01cd5969802f99b2a23f2eff1eb6785e77f846acc1d6f5824b`.
This projection is not a full BSON backup; raw book data stays private.
[Structured live proof](../evidence/2026-11-26-owner-strategy-batch-live.json).

A read-only unauthenticated GET to `/scan/strategy-batch` returned **403** with
`owner_only`. This verifies that the deployed route refuses unauthenticated
access; it does not prove an owner session or rendered owner page. No POST,
paper entry, protective-exit stimulus or real broker order was used for this
release check. Actual owner access and batch outcomes remain unobserved.

The scheduled task may wait for an existing normal category rotation and retry
the same unreserved request ID within its authorized date/session. Once a ledger
record exists, it follows that record and never creates a second request ID. It
also checks for an already accepted batch representing this authorization. No
normal schedule or lock is bypassed. Missing access, unavailable quotes or a
failed save must remain explicit blockers; a scheduled task is not a fill.
