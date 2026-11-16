# SEQ-07 — Candles as assistance

Status: LOCAL_TESTED. All 3311 canonical local tests pass with zero skips;
canonical CI, merge and deployment verification are pending. Normal runtime observation is separate.

Baseline: `eac216c6680130989beaff3871b89c7562544de9` (PR213).
Related finding: XR-081; original GATE-01/GATE-05 review and owner policy retained.
Owner instruction: candle absence must not block otherwise eligible buys;
protective exits remain independent; test and update repo/workflow, no skipping.

## Reproduction and resulting behavior

The old engine skipped `candle_wait_no_pattern` in Intraday, Short, Swing and
Positional. Momentum and Ocean were exempt. The live backtest also applied the
veto. An 18-case baseline regression had **15 failures and 3 passes**: entry
policy/evidence, backtest parity, and invalid breakout data were reproduced.
Missing High/Low passed NaN comparisons; dropping missing Volume could silently
borrow a previous high-volume row.

All five segments now use candle observations as assistance. The observation
records pattern/support, absent pattern, unavailable input or disabled setting;
it has an explicit advisory role and cannot veto an entry or exit. Pattern
evidence does not change score, ranking or position size. Actual liquidity,
quotes, timing, risk, capacity, Ocean reclaim and execution-data rules retain
their separate checks and thresholds. Historical research scripts and stored
backtests/ledgers are not rewritten or presented as results of the new policy.

Pattern readers validate finite, positive, coherent OHLC over their exact used
window. Breakout requires actual last-row volume and a complete 20-bar reference;
no dropping rows or using older volume. Invalid or insufficient pattern inputs
are unavailable, rather than a measured failed pattern. Valid pattern behavior
is retained. Structure displays distinguish unavailable from false.

Each evaluated entry stores the enabled switch, configured patterns, lookback,
volume multiplier, legacy skip list, bar interval and timestamp with the result.
The old skip list remains readable but cannot restore a gate. Settings exposes
the advisory on/off switch; the Den and parameter pages explain the policy.
Read pages use saved evidence, retaining historical gate roles and outcomes.
Intraday still uses the completed 5-minute signal bars from SEQ-04.

## Validation

- Baseline: 15 failed, 3 passed; zero skipped.
- Initial corrected candle checks: 41 passed; zero skipped.
- Expanded focused suite: **158 passed, zero skipped**. Tests exercise actual
  scans in all five segments, real no-pattern data, backtest parity, malformed
  OHLCV and window ordering, supporting/disabled evidence, JSON and Mongo
  hydration, owner setting persistence, saved explanation pages and no refetch.
- Real refresh tests execute stop, target and trailing exits with the candle
  evaluator forbidden, both enabled and disabled. Separate tests show pattern
  support cannot override missing executable quotes or position capacity.
- Earlier Momentum-only gate assertions are updated to the owner's explicitly
  superseding policy; their replacement still proves actual Swing entry and
  saved absent-pattern evidence. SEQ-04 still proves the candle consumer sees
  5-minute data; its spy follows the renamed advisory consumer.
- The full application suite and mandatory no-skip/repair-record CI guards must
  pass before merge. Final totals, head and deployment proof will be appended.

## Upstox and current live state

At 16 November 03:56:25 and 03:56:52 UTC (09:26 IST), Render logged Upstox
LTP **HTTP 401** for batches of 53 and 3 instruments. Mongo metadata still
showed a connection timestamp of 15 November 05:46:21 UTC. The owner says they
will reconnect today; current accepted quote access is not verified. No token,
account identity or instrument list is published. RUN-20261114-02 remains open.

The existing release returned HTTP 200 and ready at 03:56:30 UTC, connected to
Mongo with no reported write error. The current process had no sector refresh
attempt yet and reported durability unknown; that does not erase the separately
dated Goal06 acknowledged-save proof. No production scan or trade was requested.

Goal08 remains queued for research; this batch makes no profitability claim or
new target/trailing/position-size change.

## Broker reconnection verified

At **2026-11-16T04:08:19.444911Z (09:38:19 IST)** the authenticated read-only
`/upstox/price-check` returned positive Upstox LTP values for **6/6 symbols**:
TCS, INFY, RELIANCE, HDFCBANK, MARUTI and Nifty 50. No missing values and
`error: null`. Mongo had recorded the new connection at 03:59:33.852400 UTC.
[Sanitized response](../evidence/2026-11-16-upstox-quote-check.json) omits account
identity, credentials and instrument keys. The response contains no exchange
trade timestamp; accepted quote access is verified, freshness is not inferred.
No scan, order or portfolio mutation was invoked by this diagnostic.
RUN-20261114-02 keeps its separate rejected-authorization status-label subclaim
open; the quote-acceptance subclaim is now verified.

The first broad local run collected unrelated, untracked duplicate test files:
3,761 passed and one old duplicate failed because it still spies on the retired
candle consumer. Those user files were not edited or removed. Final validation
uses an isolated copy of every tracked file plus this batch's new files; the
canonical CI suite and no-skip guard remain the release authority.

## Final local validation

The exact final application/test files passed **3,311 tests, zero failures, errors or skips**, in 170.47 seconds, using Python 3.12.16 and the pinned requirements. [Checks and source hashes](../evidence/2026-11-16-candle-assistance.json) identify the tested source. Malformed saved pattern controls also remain unavailable without raising an entry exception. Backtest entry evidence now survives closure. The no-skip guard and repair-record validator both pass locally. Canonical CI, merge and deployment remain pending.
