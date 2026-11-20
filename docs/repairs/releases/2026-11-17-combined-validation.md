# SEQ-11 combined validation — deployed, forward acceptance open

Owner authorization: 17 November 2026, test the actual strategy on unseen data and forward paper trades; version data/code/settings, include costs/slippage, report net expectancy, drawdown and coverage, update the repo/workflow, no skips.
Baseline main: `8900be4c07fa85dc4ffa04812a6757eb9c3db4b8`. Frozen strategy/protocol/settings commit: `89a8e9baa02241efe4ee48f5bb3efc1449864c69`.

**Historical result: negative net expectancy in every declared slippage scenario. No settings promotion. Forward acceptance remains open.** Deployment is held because the existing production process is refusing storage writes; restarting it before preserving RAM/spills could discard unsaved state. Repository/CI completion is separate from being live.

## Historical measurements

The predeclared 20-name panel is disjoint from the 30 Goal08–10 names. It covers 1 September–16 November 2026: 51 regular sessions, 510 five-category scans per scenario, a fresh INR 5 crore shared book, and the actual `TradingEngine.scan`, `refresh` and intraday close/recovery functions. No substituted final selection scores or forced entries manufacture trades; unavailable historical providers follow the stated missing-input assumptions.

| Slippage per side | Fully closed economic trades | Net expectancy INR | Fully closed net P&L INR | Daily marked drawdown % | Scheduled drawdown % | Open holdings |
|---:|---:|---:|---:|---:|---:|---:|
| 5 bps | 171 | -4,295.05 | -734,453.57 | 1.6620 | 1.6811 | 14 |
| 15 bps | 174 | -5,436.72 | -945,989.43 | 1.9379 | 1.9549 | 14 |
| 30 bps | 176 | -6,975.63 | -1,227,710.66 | 2.3380 | 2.3502 | 14 |

At 5 bps, Momentum closed 11 trades (net INR -572,848.53), Intraday 152 (-118,660.95), Short 5 (-4,719.81), Swing 3 (-38,224.27), and Positional zero (expectancy unknown). Intraday gross P&L after adverse fills was -81,263.95 before a further 37,397.00 in charges. Removing fees alone would not create positive expectancy. These are simulated outcomes on this panel, not production trades.

All 106 historical requests succeeded. Accepted inputs: 12,056 daily rows and 89,775 five-minute rows across 57 regular sessions including warm-up; 294 unsupported special-session rows are excluded. At 5 bps all 22,936 daily, 4,308 intraday and 14,755 execution-quote requests were supplied. These are repeated requests, not independent samples. The test covers 20/1,205 configured names; historical news, sector, valuation, FII/publication-time, spread and other context remains explicit missing coverage.

Only prior-date daily bars and completed intraday bars reach signals. A contemporaneous boundary open plus adverse slippage supplies a simulated fill; missing quotes cannot fill orders. The original ledger and live strategy thresholds are unchanged. Basic tariff use is the approved paper assumption, with the Goal05 dated fee model.

Fourteen holdings remain open. Marked equity includes their value and fees already paid; separate hypothetical liquidation estimates include another adverse sell and fees without adding artificial closing trades. Daily and scheduled drawdowns can miss unobserved intrabar troughs. Current-survivor selection, a reduced cross-section, instantaneous scans, fresh-book initial conditions and missing historical context prevent a full-universe or future-profit claim.

## Versioning and reproduction corrections

- [Protocol and acquisition marker](../../../research/combined_2026_11_17/protocol.json) were fixed before the first request. Data/code/runtime/settings hashes, raw response text and original outcome bytes are retained. Consumed data is never relabelled unseen.
- The first isolated reproduction exposed missing non-Python assets in the source ZIP. `runtime-assets.json` verifies 459 assets / 203,326,589 bytes against the original pre-acquisition Git blob IDs and SHA256 hashes. It restores original trigger parameters, measured weights, IFR thresholds and other committed inputs; no new market data or settings are introduced. The original ZIP, seal and results remain unchanged.
- Before inspecting the original numerical results, review found that segment reporting mixed open budget categories with closed stored modes. The separate reporting amendment groups both by stored mode. Its replay must prove every decision, fill, fee, trade count, expectancy and shared-book result unchanged; the original results remain available.
- Independent slippage scenarios run in fresh processes. The input acceleration removes only rows older than the original completed-bar validator would discard. All accepted bars and the entire original numerical result must match the initial sequential, unaccelerated run exactly. No strategy threshold, candidate or slippage scenario is selected after seeing the outcome.

## Bounded engineering repairs

- **XR-078:** daily `/backtest` now uses score-driven sizing, the live measured-Kelly cap and category share, with actual initial stop risk. The terminal equity curve includes final liquidation fees; initial capital participates in drawdown. Two new tests fail against baseline code and pass after repair. The daily route retains its explicit live-rule gaps and is not the combined study.
- **XR-103:** five research entry points reject unversioned or mismatched caches before loading them. Identity covers code, requirements, installed numerical runtime, defaults, parameters, raw inputs and measured calibration assets. Tuple-based parameters are normalized to stored JSON form, so unchanged valid caches can be reused. Originals are preserved; mismatched output requires a new path.
- **Forward evidence:** normal executed entries retain code/runtime/settings identity and compressed original own-symbol bars. Manifests are stored once. Economic-trade IDs and immutable exit IDs survive partial closes, annotations and restarts. Portfolio acknowledgement precedes append-only export; failures remain queued and duplicate delivery is idempotent.
- **Honest metrics:** empty samples, missing costs/identities and incomplete quantities remain unknown. Zero/negative equity is a real loss. Owner entries and inherited positions do not establish automatic-strategy edge; mixed exit versions and owner exits are visible. Shared-book drawdown is separated from cohort expectancy and adjusts recorded capital transfers.
- Authenticated `/validation` and `/api/validation` only read saved evidence. Public `/healthz` adds capture counts/timestamps/errors without financial data or database requests. The page does not run scans or create trades.

## Test and release evidence

Final focused suite: 46 new tests, zero failures/skips. The final full canonical suite passed **3,515 tests, zero failures/errors/skips**, in 323.883 seconds. [Sanitized validation evidence](../evidence/2026-11-17-combined-validation.json). An existing candle-advisory fixture now explicitly supplies an eligible fixed-size ticket because a flat price series cannot support volatility-based sizing; its buy assertion is unchanged.

Goal08, Goal09 and Goal10 frozen replay and report checks all pass unchanged. Goal11 exactly reproduced all three original scenarios; the separate attribution replay verified every decision/fill/cost/shared-book outcome unchanged. The generated README and all five CSV tables pass their consistency check. Seven final populated-page regressions pass after the display-only labels/currency/IST changes. [PR222](https://github.com/damandamanaulakh-tech/MARS/pull/222) is merged as `0f141a8ea062b42d18bc60c018e9b7cff4457bcf`, from reviewed head `0c2e0040f3d1a815c477c6f247994cc2c6ffbaa4`. [CI](https://github.com/damandamanaulakh-tech/MARS/actions/runs/44071003513) passed all **3,515 tests with zero failures/errors/skips** in 385.59 seconds and all six jobs. Both complete combined reproductions and reports passed on GitHub, alongside the unchanged earlier studies. [Release proof](../evidence/2026-11-17-combined-release.json). The CI no-skip guard remains enabled; original reproduction and attribution verification run as two mandatory matrix jobs, alongside all existing application/research checks.

[Full research report](../../../research/combined_2026_11_17/README.md), [every simulated trade](../../../research/combined_2026_11_17/trades.csv), [all segment/scenario metrics](../../../research/combined_2026_11_17/metrics.csv), [exit reasons and costs](../../../research/combined_2026_11_17/exit_reasons.csv), [gate results](../../../research/combined_2026_11_17/gates.csv), and [input coverage](../../../research/combined_2026_11_17/coverage.csv) are committed separately from the original production ledger.

## Production hold: recurring quota refusal

At 09:57 UTC / 15:27 IST on 17 November, `/healthz` returned HTTP 200 and ready=true, but actual writes were refused at **528 MB against 512 MB**. The rounded statistics estimate is 523 MiB; actual writer refusal governs. [Sanitized proof](../evidence/2026-11-17-combined-storage-hold.json). Atlas counts logical data plus indexes, not compressed sizeOnDisk ([MongoDB documentation](https://www.mongodb.com/docs/atlas/reference/faq/storage/)).

The saved Mongo book has 39 positions, 228 closed legs and last_save **16 November 10:04:52 UTC**. It is not proof that newer in-memory trades are saved. The deployed revision remains Goal10 `65a58509018fcce86a299328497f1ffe3ee1a142`, Render `dep-dbtkffe417fc73e1cvd0`. Sector-source writes are also unacknowledged.

No deployment or restart command, production scan/trade request, deletion, paid upgrade or settings change was issued. A later ordinary health read woke the free instance, as recorded below. The previous private native export connection is absent; the Mongo connector cannot export raw BSON, and Safari currently denies JavaScript from Apple Events. Browser access was requested while independent repository work continued. Existing 14 November backups are retained but cannot stand in for current RAM or new stored records.

The release merge used `[skip render]`; Render still lists the existing Goal10 revision and no Goal11 deployment. This avoids a release restart while repository CI still runs. This holds deployment, not tests ([Render documentation](https://render.com/docs/deploys)). Before deployment: preserve/reconcile current RAM and spill history, prepare verified private native backup/restore and exact bounded free-tier recovery, then prove an ordinary portfolio save was acknowledged and read back. No arbitrary cleanup is authorized by this hold.

## Later live observation: free-instance cold start

At **11:13 UTC /16:43 IST**, an ordinary health GET woke the existing free instance. Render logs record startup beginning at 11:13:12 UTC and completing at 11:13:39 UTC on the same Goal10 revision. No deployment/restart command was issued. The fresh process initially reported a null write-error field; that reset did not establish persistence recovery.

At **11:16:02 UTC**, writes again failed at 528 MB /512 MB, with a quota error timestamp of 11:15:54 UTC and unacknowledged sector-source storage. A read-only Mongo comparison retained exactly the same saved financial projection: 39 positions, 228 closed legs and last_save 16 November 10:04:52 UTC. This proves the saved snapshot stayed unchanged; it does **not** prove earlier RAM/spill state was preserved. Reconcile that uncertainty before claiming lossless recovery. The owner said they would restore Render access; “Render ready” confirmation and a usable native export connection remain pending.

## Forward acceptance still open

No new Goal11 forward cohort is claimed. The recorder is prepared but has not been deployed or durably observed. Future acceptance requires at least 30 genuinely new regular sessions and 30 fully closed economic trades in every claimed segment, with cost/identity/coverage evidence; these coverage floors are not statistical proof. Own-symbol entry capture does not reconstruct the full peer/provider snapshot. Keep original code/settings/reset cohorts separate and preserve any gaps. No automatic promotion.

The original 165 finding IDs, all 170 backlog rows and four incident records remain. The renewed storage problem is recorded as a recurrence of RUN-20261114-01, with OPS-05 and XR-071 still open. Broader original audit findings are not declared complete by this batch.

## 20 November continuation: live baseline and storage recovery

The owner resumed Goal11 and storage work. The 17 November hold above is a
historical record, superseded by the verified current-state recovery in the
[20 November storage release](2026-11-20-storage-and-history.md). Earlier
process-only RAM/spill gaps remain unknown; they were not silently filled.

[PR224](https://github.com/damandamanaulakh-tech/MARS/pull/224) merged the storage
hotfix with Goal11 as `7337aa29510b3775de44ac0f3df2998cdc7ad85d`, matching the
reviewed tree `0703c5c382caaf2ecfa374ccfc607e5ab7a593b0`. All **3,535 CI tests**,
zero skips, and all six jobs passed, including both complete combined replays.
The normal owner scan was allowed to finish. A fresh native checkpoint at
08:37 UTC preserved 33 open positions and 80 closed rows; 3,939 newly appended
journal records were independently re-read and restored in addition to the
original 40,333-record backup. No production test trade or scan was induced.

Render `dep-dc00e86i0phs73bblqrg` made that exact revision live at
**08:40:32 UTC /14:10:32 IST**. Health/readiness, database writes and owner pages
passed. An ordinary save was independently read back and restored.
[Native forward proof](../evidence/2026-11-20-combined-live.json) verifies all
**168 expected source/input hashes**, the settings digest and immutable run
identity. One manifest, one inherited baseline and one book event are durable;
the API reports zero pending exports and no storage error. The persisted outbox
still includes already-exported IDs until the next book save; that is a retry
checkpoint, not missing data. Restart verification remains for the follow-up
release.

The baseline contains **33 inherited positions and 80 historical closed rows**.
There are **zero new forward entries or completed trades** in this observation.
Forward net expectancy and drawdown are **unknown**, and all three bounded
historical scenarios remain negative. Thirty new regular sessions and thirty
fully closed trades per claimed segment remain the declared coverage floor;
future observations cannot be fabricated or replaced by historical closures.
No strategy settings were promoted. All 165 original IDs, 170 backlog rows and
five incidents remain.
