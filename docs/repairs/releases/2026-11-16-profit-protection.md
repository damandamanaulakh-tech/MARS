# SEQ-08 — Profit protection and reachable targets

Status: RESEARCH_COMPLETE_NO_PROMOTION. [PR216](https://github.com/damandamanaulakh-tech/MARS/pull/216)
is merged. All 3359 canonical local/CI tests pass with zero failures/errors/skips;
CI also reproduces the complete frozen study and rendered report. The selected
candidate failed the research criteria, so production settings stay unchanged.
This research-only batch requires no application deployment.

Baseline: `b0615be779f75d0ab06dc7ab8738efaf03dc2d61` (Goal07 evidence).
Finding: INTRA-003; compound re-entry/sizing/full-strategy evidence remains open.
Owner instruction: compare trailing exits and reachable targets with costs and
slippage, test unseen periods before changing settings, update repo/workflow,
and skip no checks.

## Scope and result

[Full study and comparison](../../../research/profit_protection_2026_11_16/README.md)
uses five-minute Upstox historical bars for all 15 names in the pre-existing
15-stock panel. The five arms are current price rules, net-cost trailing
activation, ATR profit lock, a target estimated from 20 prior sessions, and a
combined candidate. Entry clocks are fixed at 10:00 and 13:00 IST to isolate
exit differences; these are not actual MARS selection signals or portfolio
returns. Each observation uses at most INR 100,000 including buy fees.

The combined arm was selected on training, retained after failed validation,
and evaluated on an untouched chronological holdout. It **failed** the declared
positive-expectancy and robust-improvement criteria. No alternative is selected
after seeing holdout results. Existing settings, historical ledger, open-position
protection, sizing and re-entry remain unchanged.

At the 30-minute observation cadence and 5 bps adverse slippage per side:

| Holdout policy | Mean net bps | Mean net INR | Win % | Target-hit % |
|---|---:|---:|---:|---:|
| Current price rules | -13.503 | -133.00 | 43.04 | 11.74 |
| Net-cost activation | -13.481 | -132.92 | 43.48 | 11.88 |
| ATR profit lock | -13.510 | -133.18 | 43.77 | 11.88 |
| Prior-session target | -15.194 | -150.12 | 47.83 | 33.33 |
| Combined, selected before holdout | -15.495 | -153.19 | 47.97 | 33.33 |

The locked candidate's paired difference is -1.993 bps per entry, with a
95% day-cluster interval of [-4.474, +0.637] bps. A higher win/target-hit rate
did not compensate for smaller winning payoffs. All nine execution/slippage
paired intervals include zero. Neither point estimates nor uncertainty establish
a robust gain. These modeled sample losses are not booked MARS P&L.

## Before-data protocol and before-holdout seal

1. `dd6b5b2aa89c2d9e7f57d8f5efbd1a6250d4f35a` froze the initial protocol before
   retrieval: universe, date splits, candidate formulas, fees/slippage, three
   execution scenarios, sample requirements and selection/rejection rules.
2. `23d35cee3c6dbd8c2496f2f80ac19941ff70576f` records a factual source correction
   before any return evaluation. Goal04 attaches completed five-minute history
   to `pick.history`; `_target_for` reads it. The baseline is therefore five-minute
   ATR with the existing 1–6% target band, not the historical daily-ATR targets.
   The original protocol is retained, and the amendment states that data had
   been acquired but only coverage was inspected.
3. `8ea0f8ae38cac69df413bbf2b441e30d48c0fa72` sealed the runner, data/source hashes,
   train/validation outcomes and selected arm before holdout evaluation.
   `holdout_opened.json` records access before evaluation so interruption does
   not make the period unseen again. Resume requires identical sealed inputs.

## Data, execution and costs

- 90/90 requests succeeded, retaining raw UTF-8 response bytes in a compressed
  archive and request URLs, timestamps and independent SHA-256 hashes.
- 118,335 source rows: 118,125 accepted bars in 1,575 complete regular
  stock-sessions; 210 special-session rows explicitly excluded. All 15 names
  have complete eligible regular sessions; no shortening or forward-fill.
- Training: 3 August–11 September, 30 sessions / 900 matched entries.
  Validation: 14 September–9 October, 18 sessions / 540 entries.
  Holdout: 12 October–13 November, 23 sessions / 690 entries.
- Five arms × three execution paths × three slippage assumptions produces
  95,850 simulated outcomes. The independent-information count is not multiplied
  by scenarios; uncertainty resamples 23 whole holdout days.
- Net returns use the existing Goal05 Basic fee model with dated exchange
  schedules and adverse 5/15/30 bps per-side fills. Historical Basic is a
  counterfactual tariff assumption. No old ledger values are recalculated.
- Both OHLC orderings and delayed 30-minute observations are retained. No fill
  at an old stop through a gap; no target fill from a high missed between checks;
  no current/future-bar feature leakage; final exit uses the 15:25 open only.
- The report distinguishes defined daily-mean realized drawdown from portfolio
  or mark-to-market drawdown, reports intrabar exit-time uncertainty and includes
  net expectancy, payoff, fees, slippage, target hit rate, excursions, giveback
  and per-name/time concentration.

Official [Upstox candle contract](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/),
[published charges](https://upstox.com/brokerage-charges/) and
[NSE FA73061](https://nsearchives.nseindia.com/content/circulars/FA73061.pdf)
were rechecked 16 November. The archived requests provide actual provider evidence.

## Validation and shipment

- 48 new regression cases; 110 expanded research/cost/exit checks pass with
  zero skips. The actual current exit function reproduces the losing-trail
  mechanism; the baseline target is compared with the production target helper.
- Cases cover net break-even, gap losses despite profit locks, intra-bar
  ordering, missed target touches, independent hard stops, fee arithmetic,
  empty/all-loss samples, invalid/missing/future/unfinished session bars,
  source tampering, no future feature leakage, paired day-cluster uncertainty,
  train-only selection and interrupted/changed holdout protection.
- CI now reproduces every rounded individual outcome, all three period
  summaries, the frozen selection, source hashes and the rendered report
  offline. The full application suite, no-skip guard and repair-record
  validation remain mandatory.
- Final local/CI and merge proof are recorded below. Research-only commits use
  `[skip render]`; matching application deployment is not an acceptance claim
  for this isolated study.

## Remaining work

This holdout is consumed. Any retuning needs a new untouched period. A future
candidate needs actual point-in-time entry signals, score/danger/sector exits,
execution cadence, spread/impact and portfolio constraints, then forward paper
validation and owner review. Current-name selection and provider adjustment
uncertainty restrict generalization; other segments are not tested here.

INTRA-003 remains compound and open for SEQ-09 re-entry, SEQ-10 sizing and
SEQ-11 combined validation. Earlier naturally occurring runtime observations
remain pending. No production scan or trade was induced for this study.

## Final local evidence

The canonical full suite passed **3,359 tests, zero failures/errors/skips**, in
260.20 seconds on Python 3.12.16. The complete frozen study replay and report
comparison also pass, with no network or production calls. The no-skip guard
and repair-record validator pass. All 170 records, 165 original IDs and four
incidents remain. [Machine-readable checks and hashes](../evidence/2026-11-16-profit-protection.json).

## Merged research and CI proof

- [PR216](https://github.com/damandamanaulakh-tech/MARS/pull/216) merged at **2026-11-16T08:59:59Z**
  (16 November 2026, **14:29:59 IST**).
- Reviewed head: `031562195d3de516cefaecdaebe58f6a87dcbc34`.
- Merge: `0f91a235a47c55d9291f5c5882cdd0953c977d85`.
- Reviewed/merged tree: `d5dc9c34b14510540ef4bd36762c8b5aa2a6decd`; exact equality
  verified after fetching main. Published blobs matched local Git hashes.
- [CI 43807192211](https://github.com/damandamanaulakh-tech/MARS/actions/runs/43807192211): **3,359 passed**, zero failures/errors/skips,
  268.35 seconds. Repair-record validation, explicit no-skip guard, full
  individual-outcome/summary/selection reproduction and report comparison all
  passed. Replay/report completed at 2026-11-16T05:03:13Z.
- Goal08 research is complete with **no promotion**. SEQ-09 remains queued.
  INTRA-003's separate re-entry/sizing/full-strategy claims remain open.

Read-only Render check at **2026-11-16T09:01:00Z** still lists Goal07 deployment
`dep-dbt893vf3r2c73f6rclg`, revision
`deb07a316007e40bf5765462d2194b169d70409f`, as live. Goal08 is an isolated
research shipment; this is not a new application deployment or trade observation.
