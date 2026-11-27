# NEW-01 / NEW-02 — the monthly company figures: saved in batches, progress written after each one

Two halves of one fault in the weekly 16:30 block's fifth step. They are fixed
together because they touch the same twenty lines, and because the next run that
would hit them is **the first trading day of December**.

## What was wrong

**NEW-01 — the save.** `db.save_fundamentals_rows` sent **one `replace_one` per
row**, in a plain loop, with no batching and no bound — and it ran *after* the
fetch's own 600-second budget had already expired, so nothing limited it at all.

Measured, not estimated. Yahoo answers ~60 rows per symbol in ~0.65 s, so a
600 s budget attempts ~929 symbols and returns **55,740 rows** — 55,740 separate
round trips to the cluster, with the weekly universe rebuild (step 7) and the
OHLCV pull queued behind them. The same file already had the right pattern:
`save_macro_rows` does the identical job in **112** unordered `bulk_write` calls
of 500. A **498×** difference in round trips for the same rows.

It also counted every attempt as stored, so a refused write was journalled as a
saved datapoint.

**NEW-02 — the progress ledger.** The done list and the remaining count were
written in **one** `save_settings` call, strictly *after* that unbounded save.
Anything that ended the process during the save — a deploy, a restart, a kill —
wrote neither. The next run read an empty `done`, rebuilt `todo` as the whole
sorted list, and re-fetched the same leading names for another 600 s. The month
could never converge and **companies late in the alphabet were never refreshed
at all**. Worse: `fundamentals_remaining` was in that same lost write, so
`weekly_block_due` never saw a positive balance and PR #164's "resume tomorrow"
path could not fire for an interrupted run — only for one that had finished
anyway.

Consistent with the live store: `fundamentals` held 26,378 documents on 25 Nov,
≈ 440 names at 60 rows each — about what repeated partial saves of the front of
the alphabet would leave.

## What changed

* `web/db.save_fundamentals_rows` now mirrors `save_macro_rows` in the same
  file: `ReplaceOne(upsert)` built per row, unordered `bulk_write` in chunks of
  500, **only what the store acknowledged** counted, a `BulkWriteError` counting
  what its details say landed, and any other failure returning what landed so
  far. Rows missing a symbol, field or as-of are skipped rather than stored
  under a blank id. Keys are unchanged (`symbol|field|as_of`), so the stored
  data is byte-identical — only the number of round trips moves.
* `web/scheduler._monthly_fundamentals_refresh` fetches in batches of
  `FUNDAMENTALS_BATCH = 50` (~32 s each at the measured rate), each with the
  budget that is **left**, and writes the ledger after every batch that produced
  rows. A restart now costs at most one batch of re-fetching.
* `done` still means **attempted**, not succeeded — a symbol Yahoo answers
  nothing for still counts, which is what lets the month finish. The one case
  that must not advance it is a run where Yahoo returned nothing *at all*, so
  attempted names are buffered and flushed into the ledger only by a batch that
  produced rows; a trailing silent batch is flushed at the end, so the month is
  never stamped complete with a short done list.
* A batch that attempts nothing and hands the names back now **stops** the loop
  (`stopped = "no_progress"`) instead of re-offering the same names until the
  budget expired. Found by a test of mine that hung: the loop had no progress
  guarantee.
* The journal line now says `N datapoints stored of M fetched (the store refused
  M-N)` when the two differ, so a refused write is visible instead of being
  reported as stored.

## Money

**Entry side only.** Nothing here reads or writes a position, a stop, a target
or a quote, so the open book is not exposed by it either way. What it costs is
the buy list: this step sits in front of the weekly liquidity rebuild, so a step
that will not finish leaves the five segments choosing from an old list. The
figures themselves feed `/register` and Ocean's survival check, not entry scores.

## Tests

`web/tests/test_fundamentals_batching_2026_11_27.py` — 9 tests. **7 of the 9
verified to fail against the pre-repair files** (the two that pass either way
are guards: that the keys and row contents are unchanged by the batching, and
that a row missing its key is skipped). They cover: a month's worth of figures
costing 112 round trips and no `replace_one` at all; the keys and values
unchanged; a keyless row skipped; only acknowledged writes counted; a partly
refused chunk counting only what landed; names fetched in batches rather than
all at once; the ledger written after every batch and growing each time; **a run
that dies inside a later save still leaving the earlier batches recorded, the
month unstamped and the block due tomorrow**; and a run Yahoo refuses entirely
still recording no progress (unchanged behaviour).

`web/tests/test_fundamentals_budget_2026_10_12.py` — one assertion relaxed with
its reason: the first fetch call now receives the budget that is *left* rather
than exactly 600.0, because the budget is shared across batches.
