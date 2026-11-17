# SEQ-11 combined validation — in progress

Owner authorization: 17 November 2026, test the actual strategy on unseen data
and forward paper trades; version data/code/settings, include costs/slippage,
report net expectancy, drawdown and coverage, update the repo/workflow, no skips.
Baseline main: `8900be4c07fa85dc4ffa04812a6757eb9c3db4b8`.

## Bounded repairs and evidence

- XR-078: `/backtest` omitted score-driven sizing, measured-Kelly cap and
  category share. Its terminal equity curve excluded final liquidation costs.
  New regression tests fail on baseline backtest code and pass after the repair.
  This daily approximation retains its explicit live-rule gaps; it is not the
  combined-validation harness.
- XR-103: five research entry points loaded caches by variant/window name.
  They now require matching source, defaults, parameters, input-file hashes and
  runtime identity before reuse. Unversioned old results are preserved and
  refused, requiring a new output path.
- Actual-engine replay calls `TradingEngine.scan`, `refresh` and intraday
  recovery with the real score, timing, sizing, risk, rotation, exit and fee
  code. Only I/O, clock, alert transport and isolated-book persistence are
  adapted. Boundary opens and completed bars are causal; no future OHLCV or
  retained closing mark can fill an order. It uses a predeclared 20-symbol
  subset of the saved 1,205-name universe and a fresh INR 5 crore book.
- Passive forward capture retains code/settings identity and raw own-symbol
  entry inputs with full limitations. An outbox checkpoints with the existing
  portfolio before acknowledged append-only Mongo export. Failed exports retry
  after restart; duplicate delivery is idempotent. Owner/unknown entry paths,
  pre-release trades, partial exits and mixed versions are explicit.
- Authenticated `/validation` and `/api/validation` read saved observations.
  They do not trigger scans, orders, price lookups or portfolio writes.

## Protocol and completion boundary

The [frozen protocol](../../../research/combined_2026_11_17/protocol.json) declares
the names, window, schedules, 5/15/30 bps adverse slippage, cost model, initial
conditions and unavailable historical inputs before acquisition. No tuning is
permitted on the holdout. Previous Goals08–10 research results remain intact;
their rejected candidates are not silently combined or promoted.

Forward performance cannot be completed on deployment day. Keep acceptance
pending until at least 30 new regular sessions and 30 fully closed economic
trades per claimed segment, with costs/identity/coverage proved; these floors
are not statistical proof. Each code/settings/portfolio-reset period stays
identifiable. Missing historical context and whole-universe coverage are
limitations, never a fabricated pass.

Local focused validation currently passes 30 tests with no skips. Full canonical
suite, immutable holdout results, CI, merge, deployment and natural forward
observations are still pending at this pre-acquisition checkpoint.
