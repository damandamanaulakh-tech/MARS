# XR-070 — a restart that finds two books keeps the newer one and files both

Owner decision, 2026-11-27, chosen from an options card: **"Keep the newer one,
file the other."** The two alternatives offered and declined were *stop and ask
me* (what the code already did) and *always trust the database* (which would
have been the wrong answer during the 26 November outage).

## What was actually wrong — the tracker row was out of date

The row said the app "writes a note saying 'NOTHING was overwritten' and then on
the very next line overwrites the real saved book with the outage book." **That
is not what the code does, and has not been for some time.** `web/state.py`
journalled the note and then returned *before* any save, so nothing was ever
overwritten. The note was true.

The real fault is the other way round, and it is the one the owner lived through:

* A conflict made the healer `return` without calling `db.claim_recovery()`, so
  `db.recovered_pending()` stayed true. Every later heal attempt hit the same
  branch and refused again — **for the life of the process.**
* `db.portfolio_write_failed(...)` then set `entry_block_reason =
  portfolio_save_failed`, so **no new position could open** the whole time.
* The book the session was really trading on therefore never reached the
  database. Because the local JSON copy lives in the container's temporary
  storage, **the next restart threw it away.**

So: nothing was overwritten, and everything after the conflict was lost anyway.

## Reproduced first, against what is live

`heal_after_reconnect` run on one durable book (2 positions, last action 6 hours
ago) and one outage book (1 position bought just now). Same script, two trees:

| | live `main` (dc50354) | this repair |
|---|---|---|
| book the session holds | `OUTAGE.NS` | `OUTAGE.NS` |
| book reaching the database | **NOTHING WAS SAVED** | `OUTAGE.NS` |
| copies filed away | **0** | **2** |
| recovery still pending | **true** (for ever) | false |
| new buys blocked by | **`portfolio_save_failed`** | nothing |
| journal | `MONGO_RECOVERED_CONFLICT` | `MONGO_RECOVERED_CONFLICT_RESOLVED` |

## What changed

* `web/db.archive_portfolio_snapshot(doc, label)` — files a whole book away
  **verbatim** into a `portfolio_archive` collection and returns the id. It is
  deliberately **not** routed through `save_portfolio(doc, doc_id=...)`: that
  reads the closed-history policy first (which can raise and abort the archive)
  and re-applies the trade retention, so the filed copy could come out trimmed.
  An archive that edits what it archives is not an archive. Returns `None` on
  any failure and never raises.
* `trading.paper.Portfolio.snapshot()` — the whole book as one plain dict,
  extracted verbatim from `save()`, which now calls it. A book can be filed away
  without being saved as the live one. Verified key-for-key against the file
  `save()` writes (51 keys, identical set). It does not trim; `save()` still
  does that first.
* `web/state._book_activity_ts(journal)` — the newest timestamp a book's own
  journal carries. Every action this app takes journals a `ts`, so the newest
  one is when that book last did something. Unreadable stamps are skipped, never
  guessed at.
* `web/state._resolve_book_conflict(p, persisted)` — files **both** books away,
  then keeps whichever acted last and continues on it. One whole book is kept:
  the two are **never merged**, because merging would have to invent a cash
  figure that both books spent, and would resurrect a sell as an open position.
  When the session book is the one kept, `_durable_book_loaded` is set so the
  save guard lets it through — without that line the session book would be
  refused for ever, which is the fault itself.
* The old refuse-and-journal path still runs when either book's age cannot be
  read (nothing to choose on) or **either archive write fails** — a book that
  could not be filed away is never discarded. A tie keeps the durable copy.
* `db.status()` carries `book_conflict` / `book_conflict_at`, and the dashboard
  prints an amber **"Your book was reconciled after a restart"** panel naming
  both position counts and both archive ids. A journal line the owner never
  downloads is not a notice.

## Money

Entry and exit side, and it only fires on a restart that meets two different
books. Before: buying blocked until the process restarted, and the live book
lost at that restart. After: one whole book continues, both copies are kept, and
buying resumes with no owner action. Nothing in the normal (non-conflict) paths
changed — the virgin-hydrate case and the phantom/refused-read cases are
untouched and still refuse.

## Tests

`web/tests/test_book_conflict_2026_11_27.py` — 8 tests: the outage book kept
when it acted last and reaching the database; the durable book kept when it did;
both books filed verbatim before either is chosen; the journal row and the
dashboard panel; a failing archive never discarding a book; books with no
readable age not guessed between; the virgin path untouched; and the dashboard
panel absent when there is no conflict.

`web/tests/test_portfolio_save_guard_2026_11_20.py` — the 2026-11-20 pin
`test_recovery_retry_cannot_acknowledge_a_phantom_or_conflicting_book` asserted
the behaviour the owner has now changed. Its `missing` and `refused` cases keep
refusing and stay; its `conflict` case is replaced by
`test_a_conflicting_book_is_now_reconciled_rather_than_frozen`. The fixture's
fake database gained the archive collection, because without it the conflict
path fails to file and falls back to refusing — the test would then have passed
for the wrong reason.
