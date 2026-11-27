# NEW-03 — the database calls that run while the engine holds a lock now give up

**Owner decision, 2026-11-27, from an options card: bound the book-save and
scan paths, not the whole client.** The alternative offered was one
`socketTimeoutMS` on the client, which also bounds the `/healthz` statistics
pass and the journal CSV export and could pause buying on a merely slow day.

## What was wrong

`MongoClient` is built with `serverSelectionTimeoutMS=3000` and
`connectTimeoutMS=3000` only. Those bound *finding* a server and *opening* a
socket. Verified against pymongo 4.17.0, the computed options are
`socket_timeout: None` and `timeout (CSOT): None` — **nothing bounded a read or
a write.** A server that accepted a request and then stopped answering was
waited on for ever. The five existing `maxTimeMS` guards do not help: that is a
server-side abort whose error still has to travel back over the same dead socket.

That is not a theoretical state. It is the state the cluster was in on 26
November when its plan changed (RUN-20261126-02: `connected: true`, `mongo:
null`).

The damage is where those calls run. The 30-minute exit pass takes the mode lock
and then the book lock, and saves the book inside both — four unbounded calls on
that path (`closed_history_policy`, `archive_journal`, `archive_trades`,
`save_portfolio`). The scan reads `history_docs` ($in over up to 1,205 ids)
under the same mode lock. So on a 56-position book, **one stalled request leaves
every stop and every target unchecked until the process restarts**, every later
30-minute slot waits 60 s and stands down, and the 09:20 / 14:30 rotations and
the 15:35 marks wait for ever. `engine._open`, `_exit_half` and `_close` are all
`@_book_lock`, so **the owner's own SELL button, the sector square-off and the
Buy Desk block too** — they cannot sell by hand to get out.

And it was silent: the scheduler logged *"rotation running — its own exit pass
covers this slot"*, which in this case is false on both halves.

Reproduced with no network: a fake portfolio collection blocking on an Event, a
56-position book, the real save wrapper and the real mode lock — the next exit
pass stands down after 1 s, the rotation waits for ever, the book lock cannot be
acquired, and only a restart clears it.

## What changed

* `web/db.bounded(label)` — a context manager applying `pymongo.timeout(
  LOCKED_PATH_TIMEOUT_S)`, with `LOCKED_PATH_TIMEOUT_S = 20.0`. Exceptions are
  not swallowed: every call site already has a handler that records the failure.
* Applied to the six calls that run while a lock is held:
  `closed_history_policy` (find_one), `save_portfolio` (replace_one),
  `archive_portfolio_snapshot` (replace_one, added by XR-070 the same day),
  `archive_trades` (bulk_write), `archive_journal` (append) and `history_docs`
  (find — now materialised inside the bound so the cursor cannot outlive it).
* **Not** applied to the unlocked heavy reads — `storage_stats`,
  `_cluster_sizes`, the journal export — per the owner's choice. They hold no
  lock, so a slow one cannot freeze an exit pass.
* `web/scheduler._session_refresh`'s stand-down log now checks who holds the
  lock: a real rotation still reports the slot as covered (it runs its own exit
  pass before each category), and anything else logs a **warning** naming the
  slot as skipped with stops and targets not checked.

**Why 20 s is wide on purpose:** the slowest single call measured against the
live cluster is on the order of half a second. The 13.04 s `/healthz` figure is
~25-30 round trips in one pass, not one call, and that pass is deliberately not
bounded. A `NetworkTimeout` **is** a `ConnectionFailure`, so a breach lands in
`_is_link_error` → `note_link_failure`, which drops the client; `is_connected()`
rebuilds it within 60 s and the heal pass saves the book. The OPS-09 recovery
machinery already existed — until now there was no failure for it to act on,
only a wait.

## Money

**Exit side, and the worst of the twelve in effect.** Before: one stalled
request and nothing checks a stop until someone restarts the app. After: the
call ends, the lock is released, the next 30-minute pass runs, and a refused
save is visible on the dashboard. It is not proven to have happened live; it is
proven that nothing bounded it.

## Tests

`web/tests/test_db_bounded_calls_2026_11_27.py` — 10 tests, **7 of the 10
verified to fail against the pre-repair files.** They pin: the client is
deliberately left unbounded (a future `socketTimeoutMS` there is option A and
needs the owner's word); **every** named locked-path call is inside
`bounded(...)`, read from the syntax tree, so a new database call on those paths
cannot slip in unbounded; the bound is at least 10 s; the driver really carries
a deadline during a book save, read from pymongo's own `_csot` state, and the
bound does not leak past the call; the same for `history_docs`,
`archive_journal` and `archive_trades`; the unlocked heavy reads are **not**
bounded; and both halves of the stand-down log.

**Honest limit, stated in the test's own docstring:** the driver enforces this
deadline inside its network layer, which no fake collection can exercise. What
is pinned is that the deadline is set while the call runs — absent before this
repair. Proving it fires needs a real stalled server.

## Not done

Whether the live `MONGO_URL` already carries `socketTimeoutMS` as a URI option
cannot be read from a session, and `/healthz` does not expose it. A URI option
would win over the absence in code. One line in the statistics snapshot printing
`_CLIENT.options.pool_options.socket_timeout` would settle it — not added here.
