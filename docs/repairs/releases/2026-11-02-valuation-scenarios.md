# Valuation scenario wiring — 2 November 2026

User authorization: "ok, get it wire", following the five-stock skeleton.
Implementation and review: [PR #191](https://github.com/damandamanaulakh-tech/MARS/pull/191).

## Result

An owner opens MARS Den section 12 and follows "Explore market, sector and
foreign/domestic fund-flow scenarios" to `/den/valuation/scenarios`.
The default five stocks match the reviewed prototype; up to 20 NSE symbols
can be selected. Falling, unchanged and rising presets, market move, individual
sector moves, FPI/DII assumptions, sector coefficient and discount assumptions
are editable. Results show saved base/date/beta, market and sector rupee impact,
adjusted target, change, entry discount and illustrative entry ceiling.

Saved valuations are read through valuation_exit_feed.current_doc(); no rebuild
or write is triggered. Missing/invalid valuation dates, missing ranges and
insufficient beta observations refuse a row; older dates remain explicit.
Market fallback does not add a second sector effect. Direction reversals show a
calibration warning. Invalid/nonfinite inputs are rejected; formulas remain
owner-only under both the page check and the existing viewer middleware.

The view is exploratory. Hypothetical flows are not presented as live FPI/DII
data. Scenario URLs preserve inputs, not trading settings. No scenario output
enters position sizing, stops, automated valuation records or exit rules.

## Validation evidence

- 59 focused tests pass: all 15 reviewed results, independent hand calculation,
  flow offsets, finite/range checks, data gaps, stale/future dates, market
  fallback, warning behavior, authentication, viewer refusal and no writes.
- The first full local run passed 2,707 tests, zero skipped. The explicit
  owner-only route check was then added and the 59 focused checks rerun.
  Final full-suite/CI status is recorded in the linked PR's checks; it must
  pass the repository's zero-skip guard before merge.
- Browser interaction verified on an isolated local server with the five dated
  fixtures: changing DII from 8,000 to 12,000 crore reduced the margin from 12%
  to 10%, preserving the adjusted target. No production credentials or orders
  were used in this verification.
- Repair-record retention check passed: all 165 original findings remain,
  plus the existing two separate incidents. This feature does not close them.

## Release boundary

This record describes implementation and pre-merge checks. A merge is not a
deployment claim. Render's existing main-branch auto-deployment must report
the matching revision live; health and the new authenticated route must then
be checked. Exact post-merge observations belong in a follow-up release entry.

## Still open

Joint calibration of market/sector coefficients; net-flow discount calibration;
independent telecom mapping for the tested-rule fallback; richer fundamental
shocks; baseline accounting audit; unseen-period evaluation. The prototype's
Dr Reddy's reversal is deliberately exposed, not silently corrected or traded.
