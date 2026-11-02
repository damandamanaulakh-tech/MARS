# Daily category rotation recovery

Batch 2, 2 November 2026. Baseline: `bdc82ec7039ccad151f45674ae253c6db6672751`.

## Failure

`last_scan_live` is written by any in-hours `TradingEngine.scan()`, including a manual Intraday pass. Catch-up used that timestamp as proof the full daily rotation had run. A restart after an Intraday pass could therefore leave Momentum and other categories unfinished with no retry. Rotation outcomes were only stored after the entire loop. Preparation also ran outside the rotation lock's release-finally.

## Completion and storage contract

`rotation_report.daily` stores the morning rotation's IST date, run ID, live/look-only phase, category order, per-category result, status and checkpoint identity. `active` drives the dashboard; bounded `rotations` retains summaries; `momentum_completed` retains the last successful live Momentum pass's timestamp and result.

- A manual scan does not set daily completion.
- An exception-free category result is completed; risk-gate refusals can be a valid completed evaluation with zero buys.
- Live completion requires the pass to begin and end while the market is open. Look-only passes do not satisfy live completion.
- Successful, checkpointed categories are skipped during same-day recovery; failed/missing categories are attempted again. A new session date or switch from look-only to live starts a new run.
- Progress is stored with cash, holdings and journal in the existing portfolio snapshot, before scanning and after every category. Final status is complete only when every required pass is complete.
- `Portfolio.save()` now returns an explicit boolean. Local-only operation acknowledges a successful JSON write; configured Mongo requires the actual acknowledged Mongo replacement. Unavailable/refused/unacknowledged Mongo is False. Existing callers can continue ignoring the return; the rotation runner requires True.
- A refused checkpoint stops the rotation and its subsequent pending-order/trigger work. The screen says the save was not acknowledged. This is not yet a global entry gate across every application path; XR-071's broader work remains open.

The morning lock covers preparation and category work with an unconditional release. Catch-up has a separate in-process admission lock, preventing overlapping startup/login preparation. A queued caller rechecks completion after acquiring the rotation lock. Preparation failures are logged and journaled. The existing market-hours controls remain in the engine.

## Retry and migration limits

Checkpoints are category boundaries, not per-order transaction IDs. A process can stop after an engine mutation but before the category checkpoint. On retry, the existing engine checks already-held tickers before opening them. This repair does not establish exactly-once execution across crashes or competing processes; the portfolio-command/idempotency backlog remains separate. Closed-and-reopened holdings, unacknowledged portfolio mutations, and concurrent writers require those broader contracts.

Legacy snapshots still load because the existing rotation_report dictionary is reused. Legacy reports lack explicit phase/completion evidence, so they cannot certify a complete daily rotation; the first post-deployment catch-up may run a fresh rotation through existing gates. The 14:30 rotation remains a separate fresh opportunity and does not replace morning completion.

No hosting plan changes are included. An in-process scheduler cannot execute while its host is stopped. Durable category progress improves recovery when the process returns; operational verification still requires observing a real scheduled run and a restart after deployment.

## Tests

`web/tests/test_rotation_recovery_2026_11_02.py` covers missing/partial/failed/completed rotations, duplicate triggers, lock cleanup, refused initial/intermediate/final checkpoints, market close during a pass, IST midnight, rendered status, and the retained Momentum timestamp.

`web/tests/test_rotation_checkpoint_storage_2026_11_02.py` covers Mongo acknowledgement/refusal, configured-Mongo disconnection versus local-only persistence, and JSON/Mongo round trips. Existing catch-up fixtures now test the corrected completion requirement; fake connected stores acknowledge their writes explicitly.

Validation logs and deployment status are recorded in the adjacent workspace's `work-2026-11-02/BATCH_02.md`. Tests use fake storage and scans; no live trading or deployment is needed to run them.
