> Historical audit snapshot, 30 October 2026. Findings and line numbers describe that baseline. See [current repair status](../README.md) for subsequent fixes.

# MARS trading review — main bdc82ec7039ccad151f45674ae253c6db6672751

Review date: 30 October 2026 (Asia/Kolkata). Read-only source review, isolated pure-function reproductions. No broker calls or live service changes. Scope: all 69 production Python modules under `trading/` outside `valuation/`, `valuation_context.py`, `valuation_exit.py` were inventoried and their AST/function structures inspected (23,474 lines). Deep source tracing concentrated on the money path and risk, scoring, data routing, breadth, regime, Ocean and backtest functions; this is NOT a claim that every line in the 1,586-line register and 1,280-line external panel library was manually audited. All tests are being run separately by the parent reviewer.

## Actual product and money path

This is an NSE cash-equity **paper portfolio and research/operations application**. The automatic primary selector (Everest) has five category passes: momentum, intraday, short, swing, positional. Ocean is a separate rare deep-reversion selector with its own four rails, fixed ticket and slot ceiling; it can run alongside Everest. The Python order model modifies paper cash and positions. There is no live broker order-placement endpoint/method in the reviewed broker client. Upstox is used for authorization, account/fund readings, instruments, quotes, candles, market-flow and option data.

The actual scan sequence is:

1. Choose custom universe (when nonempty), else full-universe rotating bucket/Nifty fallback; retain held names for exit/correlation history; exclude blocked tickers and known fund units.
2. Read OHLCV from injected Mongo store first (five-calendar-day maximum age), then bounded Upstox fallback, then Yahoo. Missing coverage is recorded; a full scan universe is not proof every member was scored.
3. Read regime and FII, mark held positions, evaluate daily loss halt, apply regime reserve.
4. Score/rank. Momentum uses the momentum+quality blend; other categories use their own ten-component weights. Evaluate timing, absolute momentum and optional three-formula filter.
5. Compute market damage, assemble Ocean candidates, apply breadth gate, calendar/earnings restrictions and optional sector/ADX controls.
6. Apply drawdown/loss controls, stale-data/liquidity/correlation/candle gates. Market closed means advisory results only.
7. Rotate eligible near-target holdings to fund cleared candidates when cash/category room is short.
8. Compute requested notional from slab/score/volatility, optionally cap with measured Kelly, apply cash/reserve/category/sector/heat/minimum-value gates, throttle by IFR/drawdown, and book paper BUY with reasoning, price source, target/stop and config snapshot.
9. Save scan views, journals and state.

Exit refresh reads quotes, adjusts held split/bonus events before marks, runs danger protection, evaluates per-position stop/target/trail/score/time and valuation target changes, runs the daily kill switch and drawdown cuts, records equity and saves. Missing individual quotes hold exits for those names; “no quote” is not fabricated into a sale.

## Live defaults verified in code (not evidence of current deployed settings)

- Starting paper capital: ₹5 crore, `Config.starting_capital`.
- Normal cash reserve: 0%; correction reserve 20%; panic reserve 40%.
- Requested universe target: 1,200, weekly liquidity ranking; bootstrap custom list exists, so actual live list needs persisted settings/store inspection.
- Maximum 222 held positions is a ceiling, not a target.
- Slab floor ₹150,000; requested size above floor depends on score and realized volatility; requested per-name Kelly cap 5% of equity (₹25 lakh at ₹5 crore). Cash/category/sector gates may reduce the fill. Legacy fixed mode remains ₹125,000.
- Category shares in **main**: momentum40%, intraday/short/swing/positional15% each, all as shares of deployable equity. Owner/Ocean sit outside these shares but consume cash/deployment. The draft branch changes the base to subtract owner/Ocean holdings first.
- Daily portfolio loss halt −2%; category losing-strategy-exit count halt `max(2, ceil(10% × (open-in-category + today's losing exits)))`.
- Sector cap 12 names and25% capital for mapped sectors; Unknown sectors are exempt. This makes mapping coverage a real risk-control limit.
- Portfolio heat default25% of equity at stops, configurable.
- Entry price slippage5bps each side, modeled trading charges recognized on close. One delivery-style cost model is shared across categories; it is an approximation.
- Automatic entries/exits constrained to09:15–15:30 IST, except mark/advisory work; quote/exit refresh intended every30min.
- Momentum correction brake default OFF; panic still blocks absent a qualifying reentry override. VIX reentry, breadth and other guards remain separate.
- Breadth blocks at `0.40 <= share_above_200DMA < 0.65`, all five categories, Ocean exempt. Preferred population is the191-name study panel (150 names minimum), then documented fallbacks. Applying a momentum-calibrated band to all horizons is a decision, not separately demonstrated evidence.
- Candle gate ON but skipped for momentum. Multi-formula gate OFF (three columns still displayed). Pyramiding OFF.
- Ocean ON, ₹168,750 per entry, at most8 Ocean slots (₹13.5 lakh tickets total before effects of exits). Survival actually means D/E<0.5 and latest FCF>0 from stored fundamentals; the old header’s current ratio/three-year FCF/staged buying are not implemented in the current path.

| Category | Actual entry floor | Fixed stop before ATR override | ATR target k / target band | Trail | Decay floor | Max hold actual calendar days |
|---|---:|---:|---|---:|---:|---:|
| Momentum |62 (slab needs strictly above62)|−5%; wider of5% and2×ATR14/price, capped15%|8 /10–60%|−15%|40|200|
| Intraday |72|−1.5%|1 /1–6%|−1%|40|1 +15:25 square-off|
| Short |70|−3%|5 /5–35%|−4%|45|63|
| Swing |60|−5%|4 /3–20%|−8%|45|20|
| Positional |68|−10%|10 /10–60%|−12%|45|252|

Qualifying valuation records can replace the ATR target (parent reviewer handles that subsystem). Targets/stops are snapshotted per position; changing the shared scan mode does not intentionally retag a held trade's exit ruler.

## Formulas verified

Momentum raw score is `clip(50 +25 × mean(r6/vol6, r12/vol12),0,100)`, using6m/12m returns and annualized realized volatilities. Actual entry rank is65% momentum +35% quality. Quality is the average of low-volatility score and share-volume tier score in the live path; it does not receive a fundamental snapshot there. This is not an exact replica of the Nifty200 Momentum30 index portfolio: daily scans, broader/custom universe, quality blend, risk gates and different sizing materially change it. Index CAGR text is not MARS performance.

Composite component formulas in `scoring.py` include 1m return mapped around50, rising-day volume expansion, quarterly YoY earnings, news sentiment,3m relative strength,1m return/annual volatility, futures-OI proxy, analyst consensus, proximity to20-day high and peer-sector return. Missing fundamental/news/proxy feeds frequently fall back to neutral50. The component exists in code does not establish available current inputs. Main earnings/analyst legs still query Yahoo; draft adds stored-first readers. Intraday and positional have different weights, but the actual scan reads **daily bars for every category**: preset `bar_interval='5m'/'1wk'` is not consumed by `scan`. Non-momentum history defaults6mo.

Requested slab size, when score is above floor and volatility is usable:

`max(150000, equity × min(0.05, 0.29 ×0.05 × (score−floor)/(30×annual_sigma)))`.

The0.05 information coefficient is an assumption, not a measured forecast of this book. A score at/below floor produces zero estimated edge, even though the selection comparison is `>=floor`. Measured Kelly separately estimates `p − (1−p)/b` from filtered net closed returns, smooths win probability, scales by0.29 and confidence `min(1,n/300)`. It only gates momentum by default. Main negative estimate blocks even a thin mix of wins/losses; all-wins or all-losses return NO_SAMPLE and no cap. Draft changes a negative reading below30 usable trades into slab-only entries.

## Confirmed findings in main

1. **P1 — Five real danger fires discarded in a crashing-frame reproduction.** `trading/danger.py:144` uses `fn(x) is True`, but8/10 OHLCV tripwire functions return NumPy boolean scalars. Main's existing crashing fixture shape (300 bars, final−18% high-volume breakdown) makes0068,0072,0089,0091,0092 and0096 true; only0096 is counted. Thus a six-trigger condition becomes a single-trigger action, often tighten instead of half-exit; Ocean's danger veto also uses this evaluator. Existing broad test only requires at least1 fire and passes. Audit branch fixes boolean conversion and minimum bars.

2. **P1 — Dead regime feed can remove the intended protection.** `trading/regime.py:84–87,267–279` converts fetch failures/empty data into active NEUTRAL. `engine.py:211–255` intends to preserve known correction/panic on exceptions, but normal failures do not escape the fetcher. Empty-data repro returns `regime='neutral',everest_active=True`; the scan can change last regime and reserve to0. Audit branch raises an explicit feed-dead error instead.

3. **P1 — Partial quote response can trip a false daily liquidation halt.** `engine.py:317` passes partial quotes straight to `Portfolio.equity`, whose `paper.py:248` fallback is entry price. Other mark/baseline paths use latest stored close. Repro: unchanged ₹52L book (winner A ₹120 vs entry₹100, unchanged B₹100), quote only B => scan equity₹50L =>−3.85% false daily loss and persistent halt. Later refresh can close the quoted book. Draft uses `_freshest_marks` here.

4. **P1 — Partial drawdown liquidation is marked complete and never resumed.** `engine.py:3199–3216`: any successful close records current level as done, even when other required closes have no quote. Repro at full_halt: first pass quotes A only and closes A; second pass quotes B, closes nothing; B remains, `dd_cut_level='full_halt'`. Applies to cut-half/mostly-cash too and survives persistence. This code is unchanged in the draft branch. Completion must track the remaining obligation while avoiding repeated full cuts.

5. **P2 — A new ATR-width stop is omitted from the heat calculation.** `risk.py:154–184`, `engine.py:963–972` assume the category stop (momentum5%) before `_open` sets the actual up-to15% stop. Repro with configured2% heat cap: existing risk₹9L + approved new estimated₹1L, but actual new risk₹3L =>₹12L actual risk vs₹10L cap. The default25% cap often will not bind under normal unlevered15%-maximum stops; the defect matters for tighter configured limits or other/custom held risk. Draft passes the planned stop width.

6. **P2 — Same-scan correlation gate does not compare newly accepted peers.** `engine.py:686–693,863–873,890`: open_returns is built once from the pre-scan book; clearing candidate A never adds A returns before testing B. Two perfectly correlated candidates with an initially empty book both clear. Draft adds each cleared candidate to the comparison set in rank order. Pure repro: candidate against{} => no blocker; same candidate against first accepted series =>corr1.0 block.

7. **P2 — Missing numeric values can pass risk checks.** `risk_v2.py:210–214`: all-NaN5-day volume yields NaN, and `nan <5` is false, so liquidity returns `(True,'')`. `engines/ocean.py:147–157`: NaN debt/equity/FCF returns `(True,['D/E nan ok','FCF positive (...)'])`. Draft rejects nonfinite data and aligns debt/equity report dates. These are reproduced pure functions; they do not prove currently stored production inputs contain NaNs. In the ordinary Everest path, an all-NaN recent-volume tail can already fail timing, so the isolated liquidity failure should not be overstated as proof that this exact malformed row necessarily buys; Ocean has independent NaN acceptance paths.

8. **P2 — Backtest sizes a different strategy.** `backtest.py:550–555` calls can_open without `notional_override`; main's replay uses legacy₹125k instead of live slab scoring. Repro at ₹5Cr equity,score80 and sample20.38% volatility: live requested₹21.34L versus replay₹1.25L. Return/deployment comparison is materially altered. Draft adds `_entry_notional`. Even after that, replay still omits valuation targets, category portfolio rotation, IFR/breadth/VIX/measured-Kelly details and uses daily closes and flat targets. It cannot certify the full deployed system.

9. **P2 — IFR can forget an already-active defensive window when data is thin.** `ifr.py:214–231` returns inactive1.0 sizing before checking saved last-fire dates. Repro empty panel, todayOct22,last fireOct21 =>inactive although22-day window remains. Unchanged in draft. Preserve last known active defense and report inability to refresh separately if that is the desired policy.

10. **P2 — Headline/policy semantics and timeouts can skew composite scoring.** `signals/policy.py:94–102` uses substring matching, so “Capital development deadline announced” matches IT/Auto/Pharma via API/EV/DLI; real policy entries can grant sector bonuses for unrelated text. Draft adds boundaries. `signals/news.py:95–101` fetches Yahoo news without the timeout/breaker used for earnings and analyst info; one hung call can stall a category rotation holding the mode lock. Draft bounds it.

11. **P2 — Maximum holding period labels disagree with implementation.** Presets describe20/63/252 trading sessions, but `exits.py:159–162` compares calendar days.252 calendar days is around8.3months, not12months of sessions. Bar-interval descriptions also overstate different time-frame data: current scan uses daily OHLCV for all categories. Unchanged in draft.

12. **Design limitation — owner/Ocean budget base is overallocated in main.** Five shares sum to100% of deployable equity while owner/Ocean holdings also consume cash/deployment but no category share. Later categories can be cash/deployment blocked while their displayed share still has room. Draft defines shares from deployable capital less owner/Ocean entry costs. This is a budget policy change as well as a consistency improvement, not automatically adopted by this review.

13. **Optional-path concern — pyramiding bypasses normal gate stack.** OFF by default. `pyramid.py:13–40` checks eligibility/cash at raw quote only; `engine.py:1411–1432` debits slipped fill without can_open/category/heat/reserve checks, and a cash-tight tranche can go negative by slippage. Do not treat enabling this existing toggle as equivalent to validated scan-entry safety.

14. **Documentation/safety claim — PAPER_ONLY helper is not the active defense in today's implementation.** `brokers/upstox.py:48` defines a helper that raises unless PAPER_ONLY=0, but repository search finds no production call. Current paper-only status is supported by absence of real order code and paper mutation paths. A future live-order connector would have to call the helper and implement explicit authorization; the helper alone does not establish a wired runtime firewall.

## Architecture strengths and limits

- Strong reusable pure modules for sizing, targets, category budgets, corporate actions, market internals and scoring; conservative missing-data handling in several exit paths.
- Reasons and data sources are often preserved in the journal rather than hidden. Missing scan coverage is explicitly counted. Position-specific stops/targets protect against shared-mode drift.
- Historical inference is isolated from snapshot feeders with a thread-local backtest flag. Current fundamental/news snapshots are neutralized during replay to avoid one major lookahead route.
- Read-only research/display separation is explicit for `lens_v2`, selection sequence, structural flags, register279 and most triggers/news/sector/SEBI context. An item visible on a page should not be counted as an automatic entry control. Only calibrated danger subset reaches the sell path, subject to the bug above.
- Trigger calibration distinguishes strict±15%OHLCV and main±10%external evidence. This is a useful provenance practice, but a VERIFIED label remains specific to its sample and test definition.
- There are many defensive fallbacks to50/None/no action and broad exception catches. Availability improves, but it makes gate availability and “neutral” source diagnostics essential.
- Large orchestration files (engine3296 lines; register1586; triggers1575; external panels1280) and years of comments make prose stale. Code should be the current truth; old module headers repeatedly name superseded sizes, regimes or timings.
- Unknown-sector exemption and sparse point-in-time universe snapshots limit portfolio diversification claims and backtest generalizability.
- The return figures embedded in comments/research are historical outputs, not verification of current real-world profitability or current production performance.

## Draft branch differences that must not be described as main

`origin/claude/repo-audit-overview-epyrvc` adds stored_feeds.py and entry-path tests; changes additional web/docs files outside this scoped review. Within this scoped trading subtree there are22 changed files,2,092 insertions/150 deletions. It fixes danger booleans, regime-feed fallback, partial quote halt, planned heat, same-scan correlation, NaN liquidity/survival, FII window dates, policy word matching, news timeout; adds stored earnings/analyst source readings; changes budget base, thin negative Kelly policy, removes spread gate; fixes replay sizing/history merge. It does not change the partial drawdown-cut completion defect, IFR early-return state loss, interval/session semantics, or disabled-by-default pyramid path noted here.

## Suggested order, no changes performed

1. Independently validate draft fixes and resolve remaining safety-state issues (partial liquidation, IFR continuity, web money mutation paths) before treating main as reliable.
2. Build one execution-contract regression suite: partial data, nonfinite inputs, restart persistence, mixed categories, same-scan interactions, quote provenance, portfolio cash/heat invariants. Avoid tests that merely find a string or accept one surviving trigger.
3. Unify current rule/config documentation and clearly label research/display-only items. Retain raw historical evidence, but put superseded prose behind a dated history section.
4. Make the backtest share production money-path components and explicitly enumerate remaining simulated-vs-live differences. Re-run evidence for the actual rule version and point-in-time inputs before making performance claims.
5. Verify live settings, deployment SHA, database backups, fresh quote/feed coverage, scheduled jobs and journals separately; source review and local passing tests cannot confirm these.

Test result supplied by parent (not run by this sub-review): main initial run 2,581 passed,2 failed,3 skipped; failures involve UTC versus local-midnight fixtures and a TZ=UTC confirmation run is in progress. Do not report either failed assertion as a money-path defect until the confirmation is considered.
