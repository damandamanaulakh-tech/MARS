# SEQ-06 — Verified sector and matching-period market context

Owner authorization: 13 November 2026, “verify sector identity and compare stock,
sector and market over the same timeframe with timestamps. keep missing data
unknown. test and update repo and workflow, no skipping.”

Baseline: `ade19e8fa1ee6476485c09f147580d2e47ae257f` (current main fetched before
work). Prior functional deployment: `29ca4aa338f3836b6f5c41335e2b06868823ce71`,
Render `dep-dbralp142hec73bd2o1g`, still live when this batch began.

Status: LOCAL_TESTED. Implementation and full local validation complete. PR, CI,
merge, deployment, health and natural runtime observations are not yet claimed.

## Reproduced problem

The actual live caller in `engine._danger_pass` invoked `danger.externals_for`
without the held stock's date. On pinned main, TCS returned peer and index dates
**2026-07-17**, with two peers breaking support and 60 index closes. The helper
read the bundled research panel. This is XR-061's live-path defect, reproduced
again on 13 November. [Baseline evidence](../evidence/2026-11-13-context-baseline.txt).

The saved decision schema also had no verified company/ISIN/industry membership
or same-window stock/sector/market return record. Four new acceptance regressions
failed before implementation. Existing composite scores use configured labels,
neutral fallbacks and policy/rotation bonuses: those are not evidence of a
measured sector-index return.

## Resulting behavior

| Requirement | Implementation and proof |
|---|---|
| Verified identity | Official NSE constituent CSVs supply company, symbol, ISIN and industry. Matching sector-index membership selects the comparison basket; configured labels alone cannot verify it. Conflicting identities, instrument ISIN conflicts, future retrieval dates and observations older than seven days stay unknown. |
| Explicit basket choice | Bank takes precedence over Financial Services; Pharma over Healthcare. Other multiple memberships are ambiguous. No verified sector membership means unknown, never a Nifty fallback called a sector. Current membership is an observed comparison basket, not historical constituent proof. |
| Same timeframe | Stock, sector and configured market benchmark use the identical full window, including intermediate bars. The existing momentum lookback count is labelled in daily sessions or completed five-minute bars. Missing dates cannot shorten the comparison or be forward-filled. |
| Completed data | Daily observations require completed verified NSE sessions; daily timestamps are derived session closes, not provider tick timestamps. Five-minute inputs reuse SEQ-04's timezone, session, interval, completion, freshness and continuity checks. Future, unfinished, invalid, duplicate-conflicting or unverified-calendar input is rejected with reasons. |
| Honest prices and sources | Each leg records source, receipt time when available, last available close, bar counts, price basis and rejection counts. Missing provider time/adjustment history stays unknown. Known Yahoo auto-adjusted returns are not subtracted from price-index returns. These are price comparisons, not total-return, attribution or causal models. |
| Actual decision wiring | Scans, accepted Ocean candidates and normal refresh exits save immutable context. Direct/owner/deadline paths without a comparison say it was not captured. No additional history fetch delays deadline recovery solely to manufacture context. Existing entries and financial ledger remain unchanged. |
| Existing danger rules | Live peer/index checks read the current price store and stored Nifty tail, require aligned completed dates and a complete fixed calibrated peer basket with verified matching official industries. Their 20-session formulas and 30% threshold remain unchanged. Missing peers/index/identity abstain. The target support test uses the same completed target history. Other OHLCV danger checks and valid-quote protective stops continue independently. |
| Owner interface | `/market-context` shows retained comparisons and source freshness; saved decision/trade pages show the same table and JSON. Up to 200 latest retained ticker/segment/path observations are explicit. Reads do not scan, fetch prices, reconstruct old decisions or place trades. |
| Failure and restart | Snapshot refresh is independent of portfolio locks and trades, with bounded parallel requests, source-by-source failures, retained prior timestamps and acknowledged Mongo writes. Unreadable storage is not replaced. Decision context survives existing JSON, Mongo and trade-evidence archival paths. |

## Sources and scope

[Raw constituent files and retrieval/hash manifest](../evidence/2026-11-13-sector-sources/manifest.json)
are retained beside the normalized `data/sector_identity_snapshot.json`.
Primary documentation checked during this batch:

- [NSE sectoral indices](https://www.nseindia.com/static/products-services/indices-sectoral)
  and [NSE Indices sectoral directory](https://www.niftyindices.com/indices/equity/sectoral-indices).
- [Nifty Total Market](https://www.niftyindices.com/indices/equity/broad-based-indices/nifty-total-market)
  and its linked constituent CSV; the operationally reachable official archive
  is `https://archives.nseindia.com/content/indices/ind_niftytotalmarket_list.csv`.
- [Nifty IT](https://www.niftyindices.com/indices/equity/sectoral-indices/nifty-it),
  [Chemicals](https://www.niftyindices.com/indices/equity/sectoral-indices/nifty-chemicals)
  and [Cement](https://www.niftyindices.com/indices/equity/sectoral-indices/nifty-cement)
  verify source filenames (Chemicals and Cement are case-sensitive).
- [Upstox instrument documentation](https://upstox.com/developer/api-documentation/instruments/)
  identifies instrument keys as unique identifiers. The app's existing stored
  instrument master supplies ISIN cross-checks when present; absent cross-checks
  are explicitly labelled and do not become a claimed broker match.

Initial verified observations: **14 of 15 sources**, **751 distinct identities**,
**177 names with verified sector-index membership**, before per-decision
instrument-conflict checks. Total Market's 754 source rows contain 749 supported
identities plus five explicitly excluded placeholders/REIT units; six BE-series
names retain their official identity and series. Exclusions and raw bytes are
preserved. Membership CSVs have no proved publication/effective timestamp;
retrieval time is recorded separately and never substituted for either.

The Cement official primary URL is verified; its archive mirror returned 404
and the primary website timed out from the development host. It remains unknown
and retries independently. Development-host access is not proof of Render
access. A seven-day identity freshness limit is an explicit operational
assumption, not an exchange rule. The verified regular-session calendar remains
2026-only; later years/special sessions require an updated verified calendar.

## Policy boundary and retained work

This batch adds explanatory context and repairs stale live inputs to **existing**
danger checks. It does not add entry gates, tune score weights, change targets,
rewrite trades or claim improved profitability. The configured peer-score and
policy/rotation bonuses are labelled **unverified legacy component**; measured
coverage remains unknown. XR-009's broader earnings/catalyst/short-interest
coverage and score-policy decision remain open. Existing valuation assumptions
and historical research panels remain separate; this context is not silently
promoted into a valuation or backtest formula. Historical membership, verified
corporate-action adjustment history and realized causal loss attribution are
not manufactured from today's snapshot.

## Validation and shipment

- Four acceptance failures plus a direct reproduction of July-dated live panels.
- 58 new meaningful regressions; 116 focused current decision/context regressions
  passed with zero skips. Earlier 118 integration checks also passed.
- Final full local suite: **3186 passed, zero failed/errors/skips**, Python 3.12,
  196.49 seconds. The canonical CI count is expected to be 3185: local script
  discovery includes the unrelated untracked `scripts/check_repair_records 2.py`
  syntax-check parameter. No test is skipped or removed. The canonical baseline
  is 3127 plus 58 new regressions.
- Initial full run: 3182 passed and two old one-argument mocks failed after the
  danger helper acquired explicit history/time inputs. Those mocks now accept
  the supplied context; their stop/leash assertions are unchanged.
- Final focused pass: **98 passed**, zero skips. CI is pending; its no-skip guard
  remains enabled.
- Original 74-trade audit and prior financial ledger: unchanged.
- Matching deployment/health: pending.
- Normal post-deploy scan, retained source-refresh and comparison/exit observations:
  pending. No production trade, scan or outage will be induced to close a card.
