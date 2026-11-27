# OPS-03 — a weekly feed that hangs can no longer hold the whole 16:30 block

This one was **live and measured on the day it was fixed**, not inferred from
October's logs.

## Proof it was still biting, read from the running app on 27 November

* `scan_universe.last_rebuild` = **2026-09-30** — the stamp written by the
  *last* pre-pull step of the block. **58 days** with no completion.
* `history_tickers` = **2233** against a universe file of **2678** — so
  **445 of the owner's own names had never had a price fetched.**

Both readings point the same way, and the block is brought back **every
weekday** when it does not finish (`weekly_block_due`), so it was retrying and
hanging again every single evening.

## What was wrong

The 2026-08-29 isolation protects the block from a feed that **crashes**:
`_run_weekly_step` wraps each sub-job in its own `try`. It did nothing about a
feed that simply **never answers**. `fn()` was called and waited on with no
bound, so one unresponsive host held the block and every step queued behind it
never ran — including the liquidity-universe rebuild, which is the last
pre-pull step and the one whose stamp is 58 days old.

Three of the seven pre-pull feeds had an internal bound (fundamentals 600 s,
market feeds, and the pull's own 2,400 s). **Four had none at all** — the FII
NSDL pull, the index tail, the sector indices and the SEBI listing — and each
of those is a loop of many HTTP requests. And the OHLCV pull was not even
inside `_run_weekly_step`: it sat in a plain `try`, so nothing but its own
between-names budget bounded it, and a single unresponsive request held the
three steps after it.

## What changed

* `WEEKLY_STEP_TIMEOUT_S = 600.0` and `weekly_step_timeout_s(label)`, with
  `SB_WEEKLY_STEP_TIMEOUT_S` as an override and **0 meaning unbounded** (the
  pre-repair behaviour, kept reachable).
* `_run_weekly_step` runs the step in a daemon thread and `join(cap)`. On
  overrun it journals **`DATA_REFRESH_STEP_TIMEOUT`** naming the step and
  returns False, and the block carries on with the next one.
* **A step with its own budget gets that budget plus 300 s of slack**, so the
  cap can never fire before the step's own bound does — it exists to catch a
  hang, never to cut short honest work. Measured: fundamentals 900 s, the pull
  2,700 s, a plain feed 600 s.
* The OHLCV pull is extracted into `_do_weekly_pull` and now runs through the
  same cap as everything else, so **no step in the block is unbounded**.
* **An abandoned pull keeps the block due tomorrow.** The block reaches its
  completion stamp either way, so without this the store would be recorded as
  finished and wait a week — losing exactly the resume behaviour PR #103 built.
  A remainder of 1 is *not* a measured count; it is "unfinished", the only
  honest thing known, and it brings the block back the next trading day. A pull
  that reports its own real remainder is believed, unchanged.

**Honest limit, stated plainly:** a Python thread cannot be killed. A step that
overruns is **abandoned, not stopped** — the block moves on and the orphan
either finishes late or dies with the process. Feed writes are idempotent
upserts keyed by date or id, so a late finish is harmless. What this buys is
that one hung feed costs one feed, not the whole evening.

**What this does NOT do:** it does not say *which* feed hangs. That is one
journal line — `DATA_REFRESH_WEEKLY_START` followed by whichever
`DATA_REFRESH_*` line comes next — and it needs the owner's login to read. The
difference is that from the next block onward, a hang names itself in
`DATA_REFRESH_STEP_TIMEOUT` instead of leaving no trace at all.

## Money

**Entry side.** Nothing in the block touches a held position's stop, target or
mark. What it costs is the buy list: the five segments have been choosing from a
list ranked on **30 September** turnover, so names that became liquid since
cannot be bought and names that dried up are still offered, and 445 names have
no price history at all. One indirect exit-side thread, named and then
dismissed: the valuation precompute is a post-pull step and valuation records
*are* on the money path since 2026-10-21, but that precompute also has its own
daily 16:50 cron, so the weekly block is not a single point of failure for it.

## Tests

`web/tests/test_weekly_step_timeout_2026_11_27.py` — 13 tests, **10 verified to
fail on the pre-repair scheduler.** A hanging step is abandoned inside the cap
and the step after it still runs; the timeout is journalled naming the step; a
step that finishes is unaffected and journals nothing; a crash still reports as
a crash, not a timeout; `0` means unbounded and a nonsense env value falls back
to the default; a step with its own budget gets budget + slack, and a *tiny*
override cannot cut the pull short while a plain feed does honour it; the
default is at least 300 s; `render.yaml` does not silence the cap; a
syntax-tree pin that the pull now goes through `_run_weekly_step` and that
`top_up_history` is called only inside the extracted helper; an abandoned pull
leaves the block due tomorrow; and a pull that reports a real remainder is
believed.

**A regression my own extraction introduced, caught by an existing test:**
moving the pull into `_do_weekly_pull` lost two function-local imports (`db`
and `date`), so the pull raised `NameError` immediately and journalled
`DATA_REFRESH_FAILED` — it would have been completely dead on the live box.
`web/tests/test_friday_block_isolation.py` caught it on the first run. Extract a
function that relies on local imports and you must move them with it.
