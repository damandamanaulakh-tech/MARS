# Repository repair workflow

## Canonical records

Update `docs/repairs/backlog.json`, its CSV and readable table, the relevant
goal, and a batch/release record in the same PR as each repair. Additional
incidents go in `incidents.json`. Preserve baseline IDs in `baseline-ids.json`;
append new work with provenance rather than renumbering or deleting old work.
Desktop exports are not a second source of truth and receive no ongoing updates.

## Repair sequence

1. Fetch current main and record its SHA. Read the finding, acceptance criteria,
   actual callers and relevant historical evidence. Keep unproven claims explicit.
2. Reproduce the failure using isolated inputs. Record the failing behavior and
   the tests that distinguish it from the correct behavior.
3. Make one bounded repair. Record any policy decision separately; a bug fix
   does not authorize changing risk thresholds or placing real orders.
4. Run focused regression and applicable broader checks. Resolve every failure
   and skipped test. Record the actual test totals, not merely that a command ran.
5. Update the tracker, goals and implementation/batch record. Open a PR with
   the problem, resulting behavior, test evidence and remaining limitations.
6. Require successful CI, including `repair-records` and the no-skipped-tests
   step. Verify the reviewed head before merge. Record the resulting merge SHA.
7. If deployed, record the deployment ID, deployed SHA, terminal status and
   post-deploy health evidence. Observe automatic deployments rather than
   triggering duplicates. A merged change alone is not proof it is live.
8. Verify the affected runtime behavior when safe and observable. If a scenario
   cannot be induced safely, retain its isolated regression proof and record the
   production observation as pending. Do not create trades just to close a card.

## Status meanings

| State | Required evidence |
|---|---|
| NOT_STARTED | Finding retained with acceptance criteria; no repair claimed |
| REPRODUCED / IN_PROGRESS | Reproduction and pinned baseline; implementation underway |
| LOCAL_TESTED | Bounded implementation and actual passing checks |
| PUBLISHED / CI_PENDING | Reviewable branch/PR exists; CI result not yet complete |
| MERGED | Merge SHA and PR verified |
| DEPLOYED_HEALTH_VERIFIED | Matching deployed SHA is live and health/readiness checked |
| RUNTIME_VERIFIED | The affected workflow's acceptance evidence has been observed |
| PARTIAL_* | Only a stated subclaim meets the corresponding state |

Use a clear pending suffix for an unresolved runtime check. Rejected, obsolete
or duplicate findings remain in the tracker with their disposition and evidence.
Do not equate health verification with an observed Momentum pass or liquidation.

## Keeping records current after merge

Deployment evidence is only known after merge. Add that evidence in a follow-up
documentation PR with the exact observed revision and timestamp. A subsequent
docs-only revision does not erase the earlier functional release evidence.
At the start of new work, refresh live state when needed rather than treating
an old release note as a real-time status page.

## CI and local commands

```sh
python scripts/check_repair_records.py --sync  # after editing backlog.json
python scripts/check_repair_records.py         # read-only CI check
pip install -r requirements.txt pytest openpyxl
python -m pytest trading/tests/ web/tests/ -q -ra --junitxml=pytest-results.xml
```

The GitHub workflow rejects any skipped test in that report. Do not bypass
this check, suppress a failure, delete a test, or mark an unresolved record
complete to move to the next batch. Keep production credentials out of tests.
Run full suites sequentially when they share the default temporary portfolio
directory. Preserve failed-run state separately and use a fresh isolated test
book for release validation; never reuse production state.

This document records the workflow; it does not grant new permissions beyond
the user's instructions for the current task.

## Online source batches

- Trace provider → fetch/parse → acknowledged storage → actual page or formula.
  A source link or an existing adapter alone is not a completed connection.
- Record publisher URL, observation/publication date, attempt time and scope.
  Keep unavailable, empty, partial, stale, login-required and insufficient-history
  outcomes distinct. Never substitute zero or a successful verdict for missing data.
- Recheck source validity at decision time against the same frozen batch snapshot.
  Do not silently assign a timezone to an unproved stored receipt time. Test the
  real adapters and comparison on scan/exit paths, including save/reload; mocked
  comparison results establish wiring only.
- Use bounded requests and retained good observations. An unreadable prior cache
  must not be replaced with an empty snapshot. Completion markers require mapped
  data and acknowledged writes; independently failing sources retry independently.
- Test parsers, source/consumer wiring, data retention, invalid inputs and failed
  writes offline. Observe normal deployed ingestion separately: development-host
  access does not prove Render access, and health alone does not prove a feed landed.
- Keep source-derived news/context separate from changes to trading thresholds or
  valuation assumptions. Filing-specific financial facts require their own period,
  issuer, units and document evidence; general headlines cannot stand in for them.

## Decision-evidence batches

- Capture each gate at the branch that evaluates it. Record path, segment,
  value, threshold, state and reason; a check not reached is skipped and a
  missing input is unavailable. Do not rerun current settings to explain an
  old decision. An owner instruction and an automatic selection are different
  paths, linked by reference when appropriate.
- Keep provider time, receipt time and bar interval distinct. Preserve the
  per-price source through caches and concurrent requests. LTP without an
  exchange timestamp must say that it is unavailable. A source label or age
  observation alone does not authorize a new trading threshold.
- Verify entry, amendment and each partial/final exit identity across restarts,
  storage failures and owner read pages. Archive bulky snapshots separately
  from financial samples; require acknowledgement before discarding a copy.
- Keep historical coverage and view limits explicit. After deploy, observe a
  normal scan and trade lifecycle when available; never induce a production
  trade solely to turn a pending evidence check into completed status.

## Intraday data batches

- Prove the provider request's interval, timestamp meaning, timezone and regular
  session calendar. Reject future, unfinished, misaligned and invalid candles
  before scoring. Require continuous completed bars over each used lookback;
  missing input remains unavailable, with per-stock reasons and coverage counts.
- Keep signal bars separate from daily risk/market context. Record rolling-window
  units and component fallbacks. A daily backtest is not evidence of intraday
  performance. Changes to strategy thresholds still need their own evidence.
- Recheck candle/quote validity at use, after slow work and through caches or
  restarts. Missing score must not manufacture score decay or trail tightening;
  valid-quote protective exits and pending deadline recovery remain independent.
- When changing the signal timeframe, compare saved entry scores with current
  scores only when both have a proved matching interval. Retain the original
  entry, stop and target; show unavailable legacy comparisons explicitly.
- Bound provider work and expose capped, unmapped, timed-out and failed names.
  Persist the next fetch offset so a cap does not permanently starve later names.
  Page reads show saved observations without running scans or changing holdings.
- Maintain the verified annual NSE calendar before year-end. The SEQ-04 validator
  deliberately marks unverified years unavailable; special sessions need an
  explicit verified schedule. Do not bypass this check with test market overrides.
- Require failure-path tests, actual scan/refresh wiring, owner-page and JSON/Mongo
  reload checks, full zero-skip CI, exact deployed revision and health evidence.
  Natural runtime observations and performance validation remain separate.

## Trading cost batches

- Save the selected broker plan, source observation date and actual effective
  dates separately. A current published tariff is not proof of an old account's
  plan. Version paper assumptions and each executed order's component breakdown.
- Debit buy fees once, allocate them across partial exits and pyramid orders,
  and persist per-scrip/IST-day DP state in JSON and Mongo. Test split adjustments,
  same-day delivery allocation/refunds, restarts, fee-inclusive cash and failures.
- Keep the original closed ledger and stored backtests. Historical tariff studies
  are separate read-only tables/exports with stored and modeled amounts, explicit
  order/asset/coverage uncertainty and no apply-to-ledger operation. Price audit
  corrections retain recorded versioned fees.
- Reject unsupported new fee schedules explicitly. Missing fee data must not
  disable a valid-quote protective sale of an existing holding: record any
  fallback as an unverified estimate, never a verified zero-cost sale.
- Recheck exchange circulars and broker changes before promoting a new tariff.
  Never infer broker penalties, conversions, negotiated discounts or fund taxes
  from a segment label. A cheaper fee comparison is not profitability evidence.
- Retain no-skip CI and distinguish local accounting proof, merged source,
  matching deployment health and a naturally occurring fee-bearing lifecycle.

## Market and sector context batches

- Keep official company/ISIN/industry and index membership evidence distinct
  from a configured strategy label. Record source hashes, retrieval time and
  publication/effective time separately; undated files do not prove history.
- Compare all legs over identical completed timestamps and full lookbacks.
  Reject future, unfinished, conflicting, misaligned or stale inputs; never
  fill absent prices, shorten a requested window, or call a market fallback
  a verified sector. Expose provider/receipt times and adjustment uncertainty.
- Preserve existing policy thresholds. Explanatory returns do not authorize
  new entry gates or valuation rules. Existing panel-based protection requires
  complete, aligned verified inputs; missing inputs abstain while valid-quote
  protective stops remain independent.
- Source refresh must retain prior good observations, retry failed sources,
  require acknowledged persistence and remain independent of portfolio locks.
  Read pages show saved evidence without fetching, trading or rewriting history.
- Prove missing-data and failure paths, real scan/refresh consumers, JSON/Mongo
  reloads, full zero-skip CI and exact deployed revision/health. Keep ordinary
  runtime observation and performance claims separate.

## Candle assistance batches

- Record the owner-approved advisory policy separately from pattern detection.
  Pattern absence, missing data and an owner-disabled check cannot become entry
  or exit vetoes. Keep quote, data, risk and protective-exit checks independent.
- Preserve pattern settings, actual bar interval/time, result and advisory role
  with each evaluated decision. Do not reconstruct old roles from new settings.
- Validate the exact OHLC/volume window; never drop a bad row or substitute an
  older volume. Unavailable is distinct from a measured absent pattern.
- Exercise actual entry paths for every segment, live/backtest policy parity,
  protective exits, settings persistence, JSON/Mongo reload and owner pages.
  Preserve historical ledgers and mark normal deployed observations separately.
- Require full zero-skip CI, repair-record checks and exact deployed revision
  and readiness evidence; never create production trades for verification.

## Profit-protection and target research

- Pin the actual current signal interval, exit call paths and settings before
  choosing a baseline. Preserve a dated protocol and any source-driven amendment;
  record whether prices or outcomes were already inspected when it changed.
- Declare the universe, chronological training/validation/holdout periods,
  candidates, costs, slippage, execution assumptions and rejection criteria
  before computing results. Keep sample exclusions and provider bytes/hashes.
- Use matched entries to isolate exits, and label fixed-clock studies separately
  from full-strategy or portfolio replay. Daily bars cannot prove intraday
  improvement. Validate session continuity and use only pre-entry features.
- Model both costs and adverse fills, gaps, uncertain within-bar order and actual
  observation cadence. A touched target need not fill; a net break-even stop can
  still lose through a gap. Report expectancy, payoff, drawdown definition,
  coverage, concentration and uncertainty alongside win rate.
- Select on training only and seal data/code/config/result hashes before the
  holdout. Do not switch winners after validation. Record holdout access before
  evaluation so an interrupted run cannot make an observed period unseen again.
  Retuning requires a new genuinely untouched period; retain failed candidates.
- Research tools have no apply-to-settings or apply-to-ledger operation. A
  statistical pass nominates a candidate for full-strategy/forward paper proof
  and owner review; it is not permission to change production thresholds.
- CI must reproduce the committed frozen study offline, in addition to the full
  application suite, repair-record checks and the existing zero-skip guard.
  Research-only commits use `[skip render]`; record merge/CI separately from an
  application deployment, which this isolated batch does not require.

## Live write failures during shipment

A 200 health response or connected database is not an acknowledged write. If
post-deploy verification finds quota/permission/refused writes, record the
exact deployed revision, safe error code, last persisted save and separate
incident. Keep the affected goal active with `DEPLOYED_PERSISTENCE_BLOCKED`;
do not advance the sequence or delete records to obtain a green status.
Process-local refresh diagnostics must say they reset to unknown on restart.
Coordinate a reviewed capacity recovery before further restarts that could
lose unsaved state. Paid shared-cluster changes require owner authorization
and maintenance of all affected writers; the repair task does not authorize
a purchase or deletion of another application's data.

## Archive preparation and cleanup

Verify repository visibility before publishing any backup artifact. Public Git
contains source and sanitized validation only; database exports, credentials
and private manifests remain in private owner-controlled storage. File-byte
verification, source completeness, database restore, independent durable copies
and a consistent live-state snapshot are separate claims. Never label a
connector-normalized sample as native BSON or a full backup.

Use a new private output directory for each preparation. Preserve original
bytes and index metadata, independently re-read every record, reject damaged
formats explicitly, and verify the complete affected reader/writer chain before
activating compression. Measure exact cleanup IDs and bytes after preserving
recoverable data; a collection name or stale date alone does not establish that
its contents are disposable. Keep unrelated apps and financial history intact.

## Temporary cold archives

An owner-requested temporary cleanup is distinct from a live compression migration.
Record the exact manifest hash, dates, IDs/bytes in private storage, reference
exclusions, independent native-BSON source recheck, restore proof, conditional
canary, acknowledged batch checkpoints and full untargeted-record comparison.
Publish only sanitized counts/hashes. Explain that cold-only explanations are
not available through the online reader until compatible restoration is wired.

After recovery, prove actual application saves with a durable read-back and
source timestamps; a cleared process-local error after startup alone is not
proof. Record free-instance cold starts and any earlier RAM/spill uncertainty.
Two formats on one computer are not two independent backups. Keep permanent
retention, write-failure entry guards and controlled restart verification open
until their own acceptance evidence exists.
