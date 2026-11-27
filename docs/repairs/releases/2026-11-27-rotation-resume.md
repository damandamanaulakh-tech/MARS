# The scan resumes itself as soon as it can

**Owner order, 2026-11-27, verbatim:** *"earlier when scan is running it goes to
offline itself, which needs to stop / if half scan isnt saved it should run from
starting / and keep doing considering anything as interruption / even in loss of
network it should start as soon internet connected"*

## What was already there — established before anything was built

Three readers verified this against the code and with reproductions, and most of
the owner's rule turned out to be **already satisfied** since 2026-10-21:

* **Progress is saved after every one of the six segments**, and the save must
  be *acknowledged by the database* or the rotation stops rather than carrying
  on blind (`rotation_progress.checkpoint`, `scheduler.py` — "rotation stopped:
  progress save not acknowledged").
* **A restart re-runs only the segments that did not finish.** Proven by
  reproduction, not read off a comment.
* **A segment is all-or-nothing.** `engine._open` does not save; the pass's only
  save is at the end. Reproduced: two buys and their cash existed in memory
  while the book on disk was still the pre-scan book. So a dead segment's buys
  never reached the book and it genuinely **does start again from its own
  beginning** — the owner's rule, already met.
* **A re-run cannot double-buy**: `already_held` is tested in the gate pass and
  positions are keyed by ticker.
* **A scan never buys on a stale or missing price.** The live quote router
  always returns a batch and `executable()` is False for a non-live price, so
  the pick is refused with `quote_unavailable_or_non_executable`. The old
  "use yesterday's close" path is unreachable for the live router.
* **With the network down at 09:20 the rotation stops before its first buy**,
  because the first checkpoint cannot be saved. Zero buys, nothing lost.

An earlier message in this session told the owner that nothing re-ran an
interrupted scan. **That was wrong and was corrected to them within the
minute.** The record is here so it is not re-derived.

## What was actually missing

**Nothing called the resume on a timer.** It fired at boot + 120 s and on an
Upstox login, and nowhere else. So a rotation that died at 09:25, or a network
drop that healed at 10:00, waited for 14:30, a restart, or the owner logging in.
There was a 60-second loop healing the **database** and a tested resume for the
**scan**, and the two were never connected.

And the "goes offline itself" feeling has a mechanism: when the network drops,
`fetch_daily` falls back to up to `MAX_NETWORK_FALLBACK` = 120 names one at a
time at a 30-second timeout each — **up to about an hour** — with no overall
deadline, while holding the mode lock the 30-minute exit pass needs. The app is
not dead; it is stuck in timeouts. (The intraday pass has a fetch budget; the
daily scan does not. **Not fixed in this change** — named here so it is not
forgotten.)

## What changed

* **`_rotation_resume_tick`**, registered every 5 minutes, Mon-Fri, 09:00-15:55,
  `max_instances=1, coalesce=True`, beside the 60-second save retry. It adds
  **no resume logic** — it calls the existing `catchup_scan_if_missed("resume")`.
  Everything else in it is guards:
  - trading day, market open, and **past 09:20 IST** (before that there is
    nothing to resume and it would pre-empt the real slot; after the close an
    interrupted day still reads incomplete, so without the market test it would
    re-run score-only scans all evening);
  - a rotation must actually be **owed** today;
  - a **10-minute cooldown** and **3 attempts a day**, because a persistent
    outage would otherwise re-enter the hour-long timeout storm every tick;
  - the counters live in `portfolio.rotation_report["resume"]`, which is already
    mirrored to Mongo both ways — a module variable would reset on exactly the
    restart this is meant to survive.
* **`rotation_report["daily_owed"]`** is stamped by the 09:20 job as soon as it
  decides work is due, **before** the degraded-book halt. Without it, a network
  outage stops the rotation before its first checkpoint and leaves no record for
  today at all — so a strict "did it start?" test would miss exactly the case
  the owner asked about.
* **A bounded mode-lock acquire for the resume path only.**
  `_run_category_rotation` took a bare blocking `MODE_LOCK.acquire()`, so a
  resume thread could park there indefinitely behind other book work — a new way
  to hang, not a recovery. The scheduled 09:20 and 14:30 slots still wait as
  long as it takes: they own their slot. The resume waits 30 s and stands down.
* **`/healthz` now carries `rotation`** — date, status, segments done out of
  six, whether today is complete, whether one is owed, and the resume attempts.
  The app previously exposed no scan progress at all, which is why neither the
  owner nor a session could answer "did today's scan finish?" without a login.

**Why the guards and not just a 60-second tick like the save retry:** the save
retry is cheap and idempotent. A rotation is tens of minutes of work that can
sit in network timeouts. Five minutes, a cooldown and a cap keep this to a
handful of honest attempts — and the sequence that delivers the owner's rule is
exactly: the 60-second retry heals the link, then the next 5-minute tick finds a
writable book and resumes.

## On "run from starting"

Taken as: **the segment that broke starts again from its own beginning, and
segments that already finished are left alone.** Re-running finished segments
buys almost nothing (their names are skipped as already-held) and costs session
time the remaining segments need. The owner was told this in writing, and that
re-running all six is a one-line change if they want the literal reading.

**Deliberately not done, and the owner was told why:** checkpointing *inside* a
segment (saving after each buy). It would let a dead segment keep the buys it
had already made, at the cost of a save per buy and of the segment no longer
being all-or-nothing. Their stated rule is satisfied without it, and
all-or-nothing is the safer of the two.

## Money

**Entry side.** More of the day's intended buying actually happens: a rotation
interrupted at 09:25 now resumes within ~5 minutes instead of waiting for 14:30
or a login. Nothing about *which* names are bought changes — same gates, same
sizing, same already-held guard. Exits are untouched, and the bounded lock
acquire makes it less likely that a resume starves the 30-minute stop pass.

## Tests

`web/tests/test_rotation_resume_2026_11_27.py` — 18 tests, **all 18 verified to
fail on the pre-repair code.** It fires on a half-finished rotation and on a
slot that fired but never checkpointed, and never waits on the mode lock; it
does not fire on a finished rotation, when nothing is owed, outside market
hours, on a holiday, before 09:20, on yesterday's record, or with the automatic
track switched off; the cooldown holds, the daily cap is exact, the counters
live in the mirrored record and clear on a new day; and four wiring pins — the
job is registered, the scheduled slots still wait as long as it takes, `/healthz`
carries the rotation block, and the 09:20 stamp is written *before* the
degraded halt.
