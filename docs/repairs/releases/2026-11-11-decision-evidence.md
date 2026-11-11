# SEQ-03 — gate and trade explanations

Owner instruction: proceed from shipped SEQ-02 to the next tracked goal.
Baseline main: `b07af3493d058d25a44dbf482daad15929b0c427`.
Baseline functional release: `a9919f6446690a62b76914fb9178009aaacf55e4`.
Finding scope: INTRA-004, ROOT-ROUTE-09, and the timestamp-loss subclaim of
ROOT-AM07-04. [Programme](../EXECUTION_PLAN.md).

## Reproduction and resulting behavior

Seven regressions failed against the pinned baseline before implementation:
entry evidence across restart, shared identity across partial/final exits,
owner level amendments, blocked-gate values and short-circuit state, missing
history, public-candle timestamps on returned prices, and honest legacy entry
coverage. All seven now pass. Further tests cover actual callers and consumers.

- Each automatic scan candidate gets a versioned decision ID, segment, path,
  observation time, actual score/components/weights, daily-bar coverage and
  benchmark input. Gates retain value, threshold, state and reason. Checks
  not reached remain skipped; unavailable data does not become a passed check.
  Momentum and Ocean have distinct selection records. Existing fail-open
  exceptions are recorded as unavailable with their policy reason.
- Entry checks include score, timing, absolute momentum, regime, optional
  multi-formula filter, breadth, events, benchmark trend, loss/drawdown controls,
  liquidity/correlation, candles, quotes, budgets, cash, size and deadlines.
  The original selection order and risk/strategy thresholds are retained.
- BUY evidence is detached from later settings, position edits and subsequent
  quote requests. New holdings carry one trade ID through restart, amendments
  and each partial/final close. IFR records the actual applied multiplier;
  last-fire date is a separate historical fact rather than proof it was active.
- Exit evidence retains actual fill/slippage, protection, trigger and the
  existing gross/cost/net ledger arithmetic. Standard stop, target, trailing,
  score/time exits, deadline recovery, rotation, danger, drawdown, kill switch
  and owner exits carry the inputs their caller actually used. Danger records
  each verified trigger's returned boolean or unavailable state, then the
  count/action rule. Formula-internal raw series are not duplicated as new data.
- Owner market orders, Buy Desk and resting limits record their own authority
  and allocation checks; they do not claim automatic scan gates were rerun.
  Owner imports explicitly describe reported historical holdings. Malformed
  form submissions remain ordinary validation errors. Legacy entry gates are
  unavailable and never reconstructed from current settings.
- Stop/target amendments retain before/after protection for owner levels,
  retagging, valuation changes, danger tightening, splits/bonus adjustments and
  pyramiding. Each amendment has its own ID and append-only journal event.
- Quote source/time travels with each price batch and cached price. The public
  minute-candle timestamp is retained as bar start; provider time, receipt time
  and observed age are distinct. The LTP endpoint provides no exchange trade
  timestamp, so that field remains unavailable. A concurrent fetch cannot
  replace the source attached to an earlier price. Daily/retained valuation
  marks do not supply automatic fills. Unknown injected legacy adapters stay
  explicitly unknown rather than being labelled live.
- Positions distinguishes Upstox LTP, delayed prices and entry fallback. Blank
  owner SELL accepts a routed LTP; otherwise the owner receives the existing
  request for an explicit price. Typed prices retain owner provenance. Broker
  connection status checks expiry and an old browser token cannot mask a newer
  dated saved login.
- Owner-only Recent Decisions and Trade Explanation pages show saved gate
  tables, timestamps, amendments and partial exits, with JSON downloads and
  links from Scan, Open Positions and Closed Trades. Rendering never reruns a
  gate. The recent list is explicitly capped at 200 rows plus a 500 matching archived rows
  filtered in storage; individual IDs can also resolve from the local spill file. Trade
  detail states its 100-exit-per-store and 200-archived-amendment read limits.

## Storage and verification boundaries

Journal tails are bounded by rows and 2 MiB, with complete overflow acknowledged
by Mongo or fsynced to the local spill before trimming. A failed archive retains
its evidence for retry. Closed-trade evidence beyond 3 MiB moves to a separate
acknowledged archive and leaves a retrieval pointer. This byte limit does not
shorten the numerical trade ledger or change win-rate/risk samples. The existing
5,000-row ledger cap remains. Position amendments retain at most 32 recent events within 8 KiB plus
full events in the journal/archive. JSON and Mongo hydration preserve evidence.
These controls do not close the broader G5 persistence/recovery findings: local
spill on an ephemeral host is not a substitute for a healthy remote archive.

ROOT-AM07-04 remains **partially open**: provider timestamps/ages are retained,
but no new candle-age cutoff is silently introduced by this evidence release.
Completed intraday bars, session alignment and age policy belong to SEQ-04.
The daily scoring path is labelled `1d` and completion not validated. Same-window
stock/sector/benchmark attribution remains SEQ-06. Candles retain their current
policy until the already-approved SEQ-07 change. Cost arithmetic remains the
existing model until SEQ-05. Historical losses and missing historical evidence
are not rewritten.

Primary provider contracts checked on 11 November:
[Upstox LTP v2 response](https://upstox.com/developer/api-documentation/ltp/)
lists price and instrument identity without a trade timestamp;
[Upstox intraday candles](https://upstox.com/developer/api-documentation/get-intra-day-candle-data/)
describes the timestamp as candle start. Both referenced v2 APIs are marked
deprecated; endpoint migration remains separately scoped. These documents
establish field meaning, not the freshness of any production response.

## Validation

Review also reproduced an oversized open-position amendment tail: repeated
large danger observations exceeded the intended portfolio storage budget.
A byte-cap regression failed before the fix. It now proves the bounded recent
tail and all complete events retained in the append-only journal.

60 new regression cases cover evidence identity/detachment, settings changes,
JSON/Mongo restart, all five standard exit triggers, missing quote behavior,
daily-close execution refusal before rotation, Ocean rules, danger unavailable
panels, concurrent capture isolation, source/price cache binding, typed and
blank owner SELL, token expiry/selection, partial exits, owner-only read pages,
archive retrieval, byte caps and failed/unacknowledged archival writes.
135 focused checks pass with no failures or skips, including SEQ-02 recovery.

The initial full suite exposed a take-profit boundary difference during
instrumentation; the original return comparison was restored and its existing
assertion passes. Price-provenance fixtures now supply the same labelled batch
as production, without removing their price/cache assertions. The cold-start
login fixture now uses a current token; expired-token behavior has separate
negative tests. The owner deadline regression still asserts unchanged cash,
positions and trades and now requires a saved rejection explanation.

Runs across midnight exposed six failures in existing date fixtures: two
valuation fixtures changed day after collection, and three trading fixture
groups mixed local session dates with the engine UTC clock. The observation
day is now fixed where required and synthetic sessions use the engine clock.
All original stale-data, retained-price, breadth and recovery assertions remain.

An overlapping local run reused the default temporary portfolio directory and
reported six failures involving foreign fixture holdings and repeated correction
journals. That test state was preserved separately. The final run used a fresh
temporary book and ran sequentially, without concurrent suites or changed assertions.

Final local validation on 12 November IST: **2,994 passed, zero failures,
errors or skips**, Python 3.12, 195.998 seconds. Repair validation retains
170 backlog records, two separate incidents and all 165 original IDs; diff checks
pass. Publication, CI and deployment evidence follows below.
No production trade, scan or outage was induced. Normal post-release
scan/entry/amendment/exit evidence must be observed separately from health.


## Publication and deployment — 12 November IST

- Functional PR: [#201](https://github.com/damandamanaulakh-tech/MARS/pull/201); reviewed head
  `7cfb526d6de11dc931999040b4202b6c99e6df9c`.
- All 47 published files were byte-compared with the locally tested files.
  Functional tree: `2925ff6a09ee9baa02d8b3893f6573de31c4afcd`.
- [PR CI](https://github.com/damandamanaulakh-tech/MARS/actions/runs/43082305814): **2,994 passed**, zero skipped, Python 3.11,
  336.29 seconds. Test job `130737697120`, no-skip guard and
  repair-record job all passed. Merge used the verified expected head.
- Merge/deployment revision: `5bee374a6c9de71b2bb15203434f90fa0a7eb7c1`. Render deployment
  `dep-dbqbn5rncjis73fa489g` is live, completed `2026-11-11T19:03:29.902915Z`
  (12 November 2026 00:33:29 IST). No duplicate deployment was triggered.
- `/healthz` observed `2026-11-11T19:04:03Z` (12 November 2026 00:34:03 IST):
  ok/ready/database-connected true, database-degraded false, write-error null.
  Startup completion was observed and the filtered post-start error-log query
  returned no error rows. Health alone does not verify a trading lifecycle.
- The Recent Decisions route redirects unauthenticated requests to login;
  authenticated rendering, exports and archived retrieval were tested with
  isolated portfolios. No production trade or scan was induced.

Read-only production observation at `2026-11-11T19:04:03Z`: 49
open positions, 193 closed trades,
0 open positions with a saved new entry-decision ID and
0 `SCAN_DECISION` rows. Last saved portfolio:
`2026-11-11T10:06:25.991854` UTC. Normal scan/entry/amendment/exit evidence remains
pending. Missing historical entry gates stay unavailable.

Engineering status: **shipped and health verified**. ROOT-AM07-04 remains
partial until SEQ-04's data/age work; INTRA-004 retains its SEQ-06 market-context
portion. Broader persistence findings remain open. **Next queued: SEQ-04.**
