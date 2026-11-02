> Historical audit snapshot, 30 October 2026. Findings and line numbers describe that baseline. See [current repair status](../README.md) for subsequent fixes.

# MARS documentation, research and data-tooling review

Reviewed main `bdc82ec7039ccad151f45674ae253c6db6672751`; comparison draft186 `d79f6a2d9c8f9a92f8f5b57a70f36f14b5283262`; 2026-10-30 IST. Read-only review. No source edits, live transactions, deployments or data fetch jobs were run.

## Review scope and evidence limits

Read the current owner brief, live-set index, V1.3 recipe, 47-row work-status record, valuation context/exit records, one-damage design, upload review, all research `summary.md` files, valuation README, and current Den sequence table. Inventoried every docs Markdown document and section and every one of31 top-level scripts via AST, inspected the critical script call sites, study split/cache logic, intake code, feed checkpoints and latest implementation evidence. Old August audits and the5640-line CLAUDE chronology were read selectively for relevant claims, not independently re-proven line by line. Historic CSV/NPZ/JSON result contents were structurally inventoried or sampled, not all historical experiments rerun. See root's separate data inventory for full dataset validation. The240 release assets were inventoried only; their1.71GB of content was not downloaded or read.

Fresh executable checks in this subreview: three intake-verifier failure cases and two consecutive cleaner runs in disposable temp folders. GitHub metadata and one public `/healthz` GET were also verified. All quoted historic financial results below are committed study reports, not a claim of current returns or newly reproduced trading performance.

## Main conclusion

The repo is an evolved paper-trading application with substantial research and provenance records, but its documentation mixes three different states: historical strategy research, actual current main code, and future proposals. The most important reconciliation is that October21 introduced valuation-based EXIT targets into main, while ENTRY selection is still momentum/composite. The draft October22 entry-path/scheduling corrections are NOT merged. An old “display-only valuation” label is no longer a sufficient description of the full indirect data flow.

The refreshed47-point checklist is `docs/repairs/evidence/47_POINT_CHECKLIST.md` and should be included alongside the overall report. It does not confuse presence in code with verified operation in the hosted account.

## Confirmed findings

| Priority | Finding | Evidence | Practical implication |
|---|---|---|---|
| High for research intake | The supposedly fail-closed verifier passes missing/invalid/conflicting data | `research/tools/fetch_kit/verify_intake.py:47-60,98-142`: empty files are skipped, no finite/positive OHLC check, cross-check output never changes `hard_fail`. Fresh probes: zero files exit0; all-NaN OHLC row exit0;50% provider close disagreement on100% of overlap exit0. All print `Hard gates PASSED`. | A green intake result cannot establish usable or provider-consistent data. No claim this script is automatically invoked by the production engine. |
| High for source provenance | Cleaner resolves disputed rows using the following day and loses the rejection evidence on rerun | `research/tools/fetch_kit/clean_intake.py:5-7` promises conflicting rows left alone; actual lines58-84 select close nearest the next available single-row day,93-97 rewrite the source,107 opens `_rejected.csv` with `w`. Fresh probe selected200 over100 because the next day was200; second run erased the prior rejection row. | Preserve immutable raw inputs, make disputed-row handling explicit, never replace the sole rejection ledger on rerun. This is a research/tooling defect, not demonstrated live price mutation. |
| High for claimed reproducibility | Live valuation-target evidence is not reproducible from the tracked tree alone | `docs/VALUATION_EXIT_2026-10-21.md:18-54,58,275-277` cites `bt_item19.py` and `STUDY_item19_valuation_exits.md`; neither is tracked or present. The tracked `research/valuation` workbook is the Tata formula fixture, not the trading-study harness. | Unit tests and a narrative table do not reproduce the study used to justify the money-path change. Commit the exact harness, input versions, settings and raw outputs before treating it as independently reproducible. |
| Medium | Study rerun caches have no code/config/data identity | `scripts/exit_variant_study.py:107-118,143-166` pins only the entry floor and resumes by(variant,window). `exit_study_live_era.py:53-76` and `stop_cooldown_study.py:169-181` skip by window labels. BT06 windows cache is also not tied to trigger definition hashes. | Changes to defaults, ATR stops, candle gates or source data can produce different studies under the same name or silently reuse old rows. A clean rerun plus frozen complete configuration and content hashes is needed. |
| Medium | Failed feed requests are marked complete | `scripts/fetch_pending_feeds.py:174-180,296-300` checkpoints XBRL/Yahoo items after both success and exception. | A transient failure is permanently skipped on normal resume, leaving coverage gaps. Success and retryable failure need different states. |
| Medium | Research time windows have known boundary weaknesses | v3 computes next-month return at signal-month index (`param_search_v3.py:99`) and selects optimization/validation with signal-date slicing190/196. Some trigger-label studies split on signal date even when outcomes extend beyond the split. | Calendar labels can be shifted one month; training outcomes can touch the following period. The repo's prior audit reports small overlap, but this subreview did not rerun every published estimate. Purging/embargo and outcome-date labels would improve evidence. |
| Medium | Claimed alpha interval is not a statistical bound | `docs/PARAM_SEARCH_V3.md:3-15` describes+8.9pp survivor-panel alpha and−2.2pp partial-PIT alpha as the interval in which true alpha lies. Both are biased/incomplete samples; no confidence interval or bounding proof is supplied. | Report these as two sensitivity results, not a proven range for true profitability. |
| Medium | Current product docs misstate current behavior | README₹50L vs config₹5Cr; APP_BRIEF has correction brake OFF but later says empty correction book is correct; old repo name am07;47-row status says valuation exits NOT BUILT though main now includes them. FINAL_SET carries stale first-live-day/512MB readings. | Consolidate a generated/current status reference with dated historical notes clearly separated; tests pin a subset of values and do not make every prose claim current. |
| Medium, UI completeness | International-data dashboard message is stale | `trading/macro_feeds.py:651-659` defines FRED S&P500/Nasdaq/Dow series; dashboard still always shows `INTERNATIONAL_NOTE = "International: DATA_NEEDED — no reachable free feed yet"` (`web/market_panels.py:40-45`, dashboard224-228). | The required international-market display remains incomplete even though feed support now exists for valuation context. Display data should reflect actual availability/freshness rather than an October12 finding. |
| Low / repository governance | GitHub metadata lacks license/description; all3 branches report unprotected | Fresh repository/branches REST response. This is not a full rulesets/account-security audit. | Clarify repository purpose/license and review branch policy before inviting external reuse/contribution. |

## What the research actually supports in its own reports

| Research area | Committed result | Current interpretation |
|---|---|---|
| Parameter searchv1,10 mega-caps |0/4 unseen windows | Narrow-universe momentum evidence failed; archived record is retained. |
| Broad close-onlyv2/v2.1 | Reported4/5 wins,+9.3pp/year average | Breadth appears useful, but close-only panel, inferred repair, absent liquidity and survivorship constrain interpretation. |
| Real OHLCVv3,191 names |3/4 windows,+8.89pp reported average; latest2024–mid2026−4.64pp | This is monthly vectorized selection, not an exact multi-category live-system replay. |
| Partial point-in-timev3.1 |2/4 windows,−2.2pp reported average | Missing delisted names and sparse membership snapshots prevent a complete unbiased PIT test. |
| Correctionv4 | VIX crush reentry selected; generic dip buying rejected | Limited correction episodes and different live settings; keep result tied to the exact tested rule. |
| OHLCV trigger calibration |79 triggers over94479 windows; strict run10 VERIFIED,9 SUPPORTED,59 REJECTED,1 insufficient | “Verified” is a specific in-sample lift/count rule; it does not prove the full trading system or every runtime caller works. |
| External-trigger calibration |21 triggers;2 VERIFIED,1 SUPPORTED,16 REJECTED,2 insufficient | Sector contagion/index-risk results strongest; delivery/OI/flow entry claims generally not supported. |
| BT06 measured side scores |8 holdout entries,25% win rate,−1.49% expectancy in reported bracket | Reweighting did not establish an entry edge; display/watchlist usage is appropriate to the recorded evidence. |
| Threshold sweep | Generally holdout failures; individual threshold findings retained | Do not promote post-hoc best cells or weak aggregate lift without a new validation period. |
| Breadth re-cut |40–65% band selected on train; reported+1.61pp holdout benefit for momentum picks | Population matters. Current main intentionally uses the191-name reference panel after the October16 correction. Other categories were an owner extension, not separately measured. |
| FII/DII tide | None of7 signals earned entry-selection adoption | Positive DII result concentrated in2020; historical composite FII tilt still warrants decision/evidence reconciliation. |
| Sector-wide exits | Harm to unfired neighbors disappeared/reversed in holdout | Full-sector liquidation not earned by this study; separate per-stock danger signal from group exit policy. |
| Three-formula ASH study | AM07+ASH08/20-day combinations added almost nothing; top10%20-day rule worse both halves | More formulas are not automatically more independent information. Display columns differ from approved entry filters. |
| Exit bracket study |+20% target beat old+12% across4 windows; slightly worse DD in one | Historic result drove an approved change, but current record now also includes ATR and valuation targets. |
| ATR stop/cooldown |2×ATR capped15% beat baseline in train/test; cooldown lost in test | ATR stop adopted for momentum, cooldown not. Study omits breadth/shares/danger/rotation/Ocean and keeps regime brakeON unlike liveOFF. |
| Valuation targetA3 | TEST+5.68% versusC0+5.46%, DD−4.18 vs−4.11;33% target coverage | Only~3 years of usable valuation snapshots; small0.22pp book gain; survivor panel; fixed slab; non-momentum adoption not separately measured. Harness absent. |
| Pre-fall framework | A retrospective2026 crash analysis proposing PF parameters | A design/research document, not a validated predictor or production entry/exit system. Scratch generators are explicitly uncommitted. |

## Strengths worth preserving

- Most studies retain negative results and reject attractive-looking entry signals after holdout/episode checks.
- Parameter assumptions are frequently named, source-linked and distinguished from measurements; DATA_NEEDED is used meaningfully in the valuation chain.
- Production trigger functions are reused in calibration instead of reimplemented approximations, and current tests attempt to protect display/money boundaries.
- Raw historical docs, uploaded review verdicts and superseded strategy records are retained rather than deleted.
- A source workbook is now tracked as a164-cell formula reproduction fixture; latest valuation notes explicitly disclose deviations between study and runtime.
- The GitHub main and draft checks are green, but passing tests do not invalidate the directly reproduced intake failures or missing study provenance.

## Current47-item task book — important status changes

The separate47-row table covers every original item. The large changes sinceOctober14 are: valuation EXIT targets now in main (targets only; stops remain fixed/ATR); sector routing has a Nifty500 fallback; macro feeds expanded to RBI/Upstox/FRED/NSE; owner bucket and WHY NO BUY are implemented; formula/Tata fixes reached main. The danger/entry/recovery/time-budget/cooldown fixes remain in draft186. Parent/group graph, thesis persistence, versioned event loop, URR/decision evidence, complete statements coverage, Google Drive backup, unified mix runner and real broker execution are still absent/partial. International feed support and dashboard display are at different completion stages.

## Metadata / service snapshot

See `docs/repairs/evidence/GITHUB_STATE.md`. One public healthGET returned200 and service-self-reported ready/connected, write_error null, history2233 names, scan1205/target1200, last_rebuild2026-09-30, master2678/file_date2026-10-22, RSS202/peak319MiB. It exposed no commit SHA. This does not prove main/draft deployment, accurate live positions, successful scheduled rotation, or current Render plan. The stale rebuild ledger is a real observed fact requiring investigation; the root found additional data-quality defects separately.

## Recommended order of work after review approval

1. Resolve the main-versus-draft186 deployment/merge status and independently validate its money-path fixes before promoting them.
2. Reconcile live data freshness and scheduler completion with journal evidence; investigate why the universe ledger remainsSeptember30.
3. Repair intake fail-closed checks and preserve raw/conflict/rejection provenance before new ingestion or research rebuilds.
4. Commit the exact item19 study harness, data manifest, configurations and outputs; version every study cache and distinguish historical results from reproducible current runs.
5. Update current product/task docs and feed-dependent UI from actual code/data state, preserving historical records.
6. Then address remaining functional proposals in the47-point checklist with explicit scope and evidence, especially statements coverage, thesis/event/version records and complete portfolio replay.
