> Historical audit snapshot, 30 October 2026. Findings and line numbers describe that baseline. See [current repair status](../README.md) for subsequent fixes.

# MARS web, persistence and deployment review — 2026-10-30

Review target: `historical isolated review checkout`, main commit `bdc82ec7039ccad151f45674ae253c6db6672751`. Compared against fetched `origin/claude/repo-audit-overview-epyrvc`. This report covers `web/`, `.github`, `render.yaml`, `install.sh`, and `requirements.txt`. All repository work was read-only. No secrets, production database, broker authentication, orders, notifications, jobs, deployments or Git mutations were performed. Parent auditor owns overall trading/math/data/tests findings; findings below concern actual inspected code, not historical work-log assertions.

## Scope and evidence strength

Inventory: 42 production Python files under web (including package initializers), 35 templates, 72 route decorators (33 GET / 39 POST), plus explicit HEAD `/` and GET/HEAD `/healthz`. There are 17 route modules in addition to main. All files were enumerated before tracing behavior.

Fully read as original source or executable AST-unparsed source: `main`, `auth`, `readiness`, `clock`, `market_gate`, `templating`, `db`, `state`, `scheduler`; every route module; `buy_desk`, `candle_chart`, `health_stats`, `market_panels`, `memory_relief`, `push_ntfy`, `watchlist_capture`, `why_no_buy`, `valuation_exit_feed`, `valuation_view`, `valuation_market_view`. AST compaction removes comments/docstrings, not executable statements; critical findings were verified against original line numbers.

Deeply sampled, not an honest line-by-line completeness claim: `valuation_chain_view` (all persistence/input validation, price/peer retrieval, macro/feed adapters, row evaluation, provenance and known-fault paths read; final row-cache/render budget machinery inventoried and sampled); `trade_audit` (audit API, ID/cache orchestration, price/corporate-action checks, correction application and route locking traced, repetitive candle matching helpers sampled); `den_walk` (tree assembly, mapping and user-branch save/remove/read paths read, repeated numeric node-format builders sampled). All templates structurally inventoried by headings/forms/conditional permissions; shared base template and complete PWA/service-worker/install/manifest assets read; repetitive page/table layouts sampled, not every line. Test inventory and targeted relevant test source inspected; full suite was run by the parent auditor, not independently here. No rendered-browser claim is made.

Three isolated functional reproductions ran in `python`, dependencies pinned as the repository, with network/database operations mocked: negative manual quantity increased cash and stored a negative position; OAuth callback with owner session but no state reached the token-exchange construction path; FII refresh deleted DII fields in a patched in-memory collection. The remaining defects below follow directly from traced executable code; they were not exercised against live services.

## What the product currently contains

This is a single-process FastAPI/Jinja trading control panel, still internally titled `Sourceborn AM07` version `2.0.0-dev`. It integrates trading calculations and a paper portfolio, scheduled market/history ingestion, a Mongo persistence adapter with local JSON fallback, owner controls, limited viewer access, and a substantial research/valuation workspace. Render starts one Uvicorn process. Process-global Config, Portfolio, engine instances, locks and caches are central assumptions; this design is not automatically safe under multiple workers or overlapping old/new deployment processes.

The dashboard shows portfolio/cash/deployment data, index and market panels, scan outcomes, news/trigger context, and explanations for no buys. `/positions` shows per-position entry/current prices, entry versus latest score, stop/target/exit-rule markers, notes and owner actions. `/trades` combines current/archived trades with a candle-backed audit and explicit correction control. `/scan` provides candidate score tables, parameters, charts, CSV, market/limit owner buys, Buy Desk batching and order cancellation. `/engine`, `/strategy`, `/risk`, `/lens` explain market regime, weights, entry/exit constraints, category allocation and comparison formulas. `/triggers` is a bounded research/watch layer; `/register` maps the large item register to available evidence. `/reports` exports reports and watchlist snapshots, creates a backup and exposes permitted downloadable names. `/backtest` starts a background calculation and reads its result, using stored/committed history. `/settings` handles app and execution settings, owner universe and push subscription. `/den` is the extensive algorithm/valuation investigation page.

The Den has sequence, engine, weight, trigger/danger, timing, capacity/sizing, register, evidence, algorithm settings, sandbox, valuation context, valuation chain, and formula/assumption sections. The sequence is an explanatory registry, not proof that every stage executes in a persistent workflow. The code explicitly lists missing or partial work: per-stock checkpoint; thesis/falsifier; URR verification; durable decision+evidence record; mandate/raw input validation; rotation reconciliation and durable progress; snapshot coverage and condition capture. Owner-added tree branches are displayed notes/formulas, not dynamically executed logic. Sandbox drafts are stored separately and do not replace the live engine.

Valuation integrations are more developed than a static UI: user targets and dated context; bounded stored/zip price retrieval; sector benchmarks; bond/rate, FII, breadth, damage and news context; statements/peer tables; owner NAV/EV/VNB/segment/peer/FX inputs; assumed or overridden inputs; same-day and selection-date handling; known-fault labels for conglomerates and unchecked lender book values; explicit DATA_NEEDED / NOT_COMPUTED records; current-versus-tested exit valuations; scheduled precompute. This is still constrained by data coverage and research assumptions; a rendered valuation is not automatically an executable trade recommendation or verified economic forecast.

## Highest-confidence defects and priorities

Priority is relative to protecting portfolio state and making the existing application reliable. P1 means fix before relying on unattended portfolio actions; P2 is a correctness/security issue to schedule promptly. These are proposals only; no patch was applied.

### 1. P1 — Mongo recovery overwrites the durable book after claiming it did not

`web/state.py:238-257`. When boot occurred while Mongo was down and the local portfolio has activity, `heal_after_reconnect` loads the persisted book and appends `MONGO_RECOVERED_CONFLICT` saying “NOTHING was overwritten” and that Mongo is untouched until the next save. The same function then immediately executes `p.save()` at257. Portfolio save is monkey-patched to mirror the local book into Mongo, so the recovery conflict can overwrite the real durable portfolio with the locally constructed one. An owner action during degraded operation is enough to make the local book non-virgin.

Fix direction: preserve both versions, make authority explicit, and restore the durable book or stop reconciliation before any default overwrite. The audit branch already adds conflict parking in JSON/Mongo, read-back verification and Mongo-book restoration. That is a candidate fix to review, not proof it is deployed.

### 2. P1 — Write-refused Mongo still permits scheduled entries

`web/db.py:228-240`, `web/state.py:204-206`, `web/scheduler.py:3245-3267`. `save_portfolio` catches `replace_one` errors, exposes a last-write-error, and leaves the connection marked connected. `mongo_degraded` only checks whether Mongo is configured and connected. The scheduled entry halt therefore catches unreachable Mongo but not quota, permissions, validation or other write refusal while reads/connectivity still work. New paper entries and mutations can continue while durable state is no longer landing. A health response can show the error without stopping entries.

Fix direction: distinguish reachable/readable/writable; probe a save and halt entry creation until durable writes are confirmed. Audit branch implements `writes_landing()` and a dedicated write-refused halt. Existing exits and manual controls need their own explicit persistence policy.

### 3. P1 — Manual entry can create negative positions and manufacture cash

`web/routes/positions.py:873-909`. `/positions/new` parses typed integer quantity and float entry price but does not require them to be positive finite values or enforce cash/position invariants. It calculates notional, subtracts it from cash, constructs `PaperPosition`, and saves. This is intended to record holdings; the need for importing a past holding does not justify invalid numeric state.

Isolated reproduction: starting cash `10000.0`, `qty=-5`, `entry_price=100.0`, ticker `TEST`, valid date, mocked owner/config/portfolio produced HTTP303, ending cash `10500.0`, and stored quantity `-5`. No live state was involved. Inputs such as zero/negative price also deserve explicit rejection. No fix in the compared branch.

Fix direction: validate positive integer quantity and positive finite price; provide explicit handling for external/imported holdings and capital adjustment rather than silently misusing book cash; guard mutation and save with the same portfolio lock policy.

### 4. P1 — Manual buy precondition is outside the mutation lock

`web/routes/scan_routes.py:802-803,822-857`; engine `_open` behavior independently confirmed by engine reviewer at `trading/engine.py:1463,1532`. The already-held test happens before quote fetch and before acquiring MODE_LOCK. Two requests can both pass the check, then acquire the lock sequentially and both call `_open`; `_open` debits cash and assigns `positions[ticker]` with no duplicate guard. The second entry replaces the first position after both cash debits. Manual sell/close-all/sector-exit routes also lack the common mode/portfolio lock used by scans, while engine `_close` pops the position directly.

Fix direction: recheck all mutable preconditions inside a single transaction/lock and apply that policy consistently to every portfolio writer; add an idempotency/request token if repeat submission is a realistic interface path. The compared branch leaves these routes unchanged. This is a concurrency interleaving proof, not a live race test.

### 5. P2 — OAuth callback accepts missing CSRF state

`web/routes/upstox.py:185-194`. It only rejects when both the supplied and stored state exist and differ. Either missing state skips the comparison and attempts code exchange. Owner authentication remains required, but that does not provide the missing OAuth request binding.

Isolated reproduction: owner session with no `upstox_oauth_state`, supplied `code='fixture-only'`, `state=None`, and mocked `UpstoxClient.from_env` reached the exchange path sentinel. No broker call ran. Audit branch rejects missing state and then compares values. Require both, consume once, and keep an explicit user-facing reconnect notice.

### 6. P2 — Buy Desk can exceed the owner's typed budget

`web/buy_desk.py:174-185,216-221`. Planning quantities/costs use unslipped market price, while execution adds buy slippage and only checks available portfolio cash. The typed batch budget is not checked against accumulated fill cost. An exactly allocated Rs2.5crore budget at 5bps can cost approximately Rs12,500 more than the preview. The arithmetic is conditional on prices/quantities dividing the budget and enough cash existing; it is not a claim about a real past fill.

Fix direction: size against the actual expected fill, track cumulative spend during execution, and recheck the budget. Audit branch does both.

### 7. P2 — Weekly FII updates delete DII data from the same documents

`web/db.py:423-429` versus `web/db.py:695-702`; callers `web/scheduler.py:1489-1576,1579-1622`. DII writes use `$set` on date-keyed `fii_daily` documents. `save_fii_rows` uses `replace_one` containing only FII/date/source fields, deleting `dii_buy`, `dii_sell`, `dii_net`, and `dii_source` for every overlapping date. Weekly NSDL refresh covers the recent two months, so this can erase stored DII history. The daily market snapshot is date-stamped and normally runs before the weekly 16:30 block, preventing immediate same-day repair.

Fix direction: use field-wise merge for FII or separate collections, with a regression check that writing one source preserves the other. Same code in audit branch. Isolated adapter reproduction preserved no DII keys: before `['_id', 'date', 'dii_buy', 'dii_net', 'dii_sell', 'dii_source']`; after `['_id', 'date', 'fii_buy', 'fii_net', 'fii_sell', 'source']`; `dii_net_preserved=False`.

### 8. P2 — Capital/settings state changes even when the settings form is refused

`web/routes/settings_routes.py:381-384` with subsequent universe validation. It writes the new mode into live Config before verifying custom-universe size/symbols. A rejected universe returns early, leaving a new mode identifier with previous preset values and without the intended saved settings. A later scan/mode restoration reads that inconsistent object.

Fix direction: validate a full prospective settings object before mutating shared Config; apply once. Audit branch defers mode assignment until universe checks pass.

### 9. P2 — A noncanonical limit-order string can bypass after-hours refusal

`web/routes/scan_routes.py:787-789,814-857`. The market-hours gate uses stripped/lowercased order type, but the actual branch tests the original string. `LIMIT` or ` limit ` passes the gate's limit exemption but fails the raw equality and executes the market-order path. Normal HTML choices are lowercase, so this affects malformed/direct owner requests; it is not unauthenticated order access.

Fix direction: normalize once, validate an enum, and use that canonical value through gating and execution. No branch fix.

### 10. P2 — Corporate-action history refetch records success even when write failed

`web/scheduler.py:620-638`; `web/db.py:save_history_doc` catches its own database exceptions. The scheduler verifies connectivity, fetches replacement adjusted candles, calls the silent writer, immediately journals `HISTORY_REFETCHED`, and discards the pending name. A refused write is therefore treated as completed. This can leave adjusted positions paired with unreplaced raw history and suppress the next retry.

Fix direction: return/write-acknowledge success or read back revision/end/hash before journaling completion and clearing pending work. The branch diff does not change this function.

### 11. P2 — Partial daily history failures suppress the advertised same-day retry

`web/scheduler.py:3408-3422,3430-3453`. Daily top-up journals `summ['failed']` but returns True anyway; `_topup_once_today` writes the daily completion date. The 09:20 caller then skips retrying failed names from 08:30. This is at odds with the explicit promise to retry incomplete top-up before scanning. Failures can leave partial coverage and increase limited network fallback.

Fix direction: persist a remainder per name and mark complete only after all required updates succeed, while allowing fresh successes to remain. No branch fix in this function.

### 12. P2 — Several valuation saves report success without proving persistence

`web/db.py:944-953`; `web/valuation_view.py:101-110,159-174`; `web/valuation_chain_view.py:494-507,590-617`. Database settings writes return silently when disconnected or rejected. Target save, assumption save and owner-input save treat “no exception” as success and maintain only process-memory copies. Den routes can redirect as saved although a restart will lose the new inputs. The newer sector-FX implementation in `valuation_market_view` already demonstrates the better pattern: timestamp read-back and explicit “kept in memory / NOT stored” text.

Fix direction: make persistence outcomes structured across the adapter, and distinguish temporary in-process edits from durable saves. Audit branch leaves these modules unchanged.

### 13. P2 — Backtest stored-history presence suppresses a deeper archive

`web/routes/backtest_routes.py:91-123`. A ticker present in Mongo is removed from the missing list even if its stored frame does not cover the requested start/warm-up range. The committed long history zip is then not consulted for older dates. A short store can silently shrink a long replay.

Fix direction: check time-window coverage and merge missing historical dates with store winning overlaps. Audit branch implements `store_covers` and `merge_store_and_zip`; trading implementation owned by parent/engine auditor.

## Security and access controls that are present

Production readiness recognizes Render or `SB_REQUIRE_PROD` and requires credentials/session secret/Mongo configuration, rejecting known insecure defaults unless explicitly overridden. Session cookies are signed, HttpOnly via middleware, SameSite=Lax, secure on Render or SESSION_SECURE, and last30days. Owner and optional viewer identities are supported. A central viewer guard only permits dashboard, positions, trades, login/logout, health and static/service-worker GET/HEAD; other pages and mutation methods are blocked server-side. Owner mutation routes additionally check owner in relevant paths. A signed session is not encrypted, but it holds identity metadata rather than broker token.

Login failure sleeps add friction but are process-global rather than a strong shared/per-client rate-limit policy. Most forms have no explicit synchronizer CSRF token; SameSite=Lax helps normal cross-site POST cases, but OAuth required its separate fix above. Existing server controls deserve testing under deployment-specific proxy/cookie settings before assuming security from labels. No credential values were inspected. Public `/healthz` exposes operational counts/status, which appears intentional and is not described as a credential leak.

`/healthz` always returns `ok=True` and HTTP200 when its handler runs, alongside diagnostic/readiness fields. Render uses it as liveness. It does not assert that schedules are started, broker auth is usable, writes land, history is current, a rotation finished, or every portfolio invariant is sound. Operators need explicit separate operational signals for those conditions.

## Persistence, job and runtime design

Mongo stores portfolio/settings, trade and journal overflow, backtest results, OHLCV, FII/DII, index/sector/macro data, index constituents, market raw payloads, PWOI, SEBI orders, corporate actions, fundamentals, tokens and watchlist snapshots. Settings use `$set`, preserving other writers' keys. Portfolio save also writes local state. Reconnect logic attempts recovery, archive overflow can spill locally, and the UI exposes connection/write diagnostics. Many methods swallow exceptions and return empty/None; callers cannot reliably distinguish genuine absence, unavailable storage and refused updates. This is the common root of several concrete bugs above.

Locks exist for category rotations, mode switching, top-up, valuation renders, caches and some corporate-action/correction paths. They are not database transactions and do not cover all manual writers. Local JSON is on Render's ephemeral filesystem unless an external persistent disk is separately configured. No such disk is in the blueprint; Mongo is consequently the essential durable store.

Verified scheduled structure (IST): 08:30 custom-universe+held+study-panel history top-up; 08:50 instrument/universe and BE/T2T/corporate-action work; 09:05 filings/watch work; 09:20 five-category rotation; 14:30 deployment top-up rotation; 15:25 intraday square-off and day-order expiry; 15:35 EOD/index/sector/macro feeds; weekday16:30 weekly due/remainder block; 16:45 register precompute; 16:50 valuation precompute. Exit checks run every30minutes in the configured trading-hour span. An optional5-minute intraday scheduler is off in render.yaml. Startup catches up after a delay; test environment can suppress scheduler startup entirely. Schedule registration is not proof that any live run succeeded.

Memory constraints are explicit: Render512MiB is assumed by several busy banners; allocator arena limiting and garbage collection/release helpers; limited view rows; zip/chunk/bounded history retrieval; quote/chart/peer/feed caches; max25 valuation rows; max40 manual input symbols, max20 peers, max12 segments; a380MiB computation threshold and20-second Den budget by default; bounded120-name valuation precompute with chunk25/120-second budget; watchlist120snapshots,300rows each; trigger/news caps. This is thoughtful resource engineering, but timers typically bound entry between operations rather than forcibly interrupt a blocking remote request. No production memory benchmark was run.

The push channel is opt-in ntfy, with generated high-entropy topic and stored cursor; it sends bounded journal summaries. Notifications were not sent during audit. PWA assets use network-first behavior and a generic offline page; they do not prove the engine continues running when the service itself fails. Shared UI pulls Tailwind/Alpine/Chart/fonts externally; Alpine and Tailwind are not fully immutable local assets. Direct Python requirements are exact-pinned; transitive resolution is not fully locked and CI installs unpinned pytest.

## Exact branch comparison within this scope

`git diff main..origin/claude/repo-audit-overview-epyrvc -- web .github render.yaml install.sh requirements.txt`: 29 changed files,2487 insertions,221 deletions. No changes to CI,render,install,requirements in this comparison. Production changes are only:

- `web/buy_desk.py`: slippage-aware plan budget and cumulative executed-budget check.
- `web/db.py`: write-health helper.
- `web/market_panels.py`: share base aligned with deployable less owner/Ocean holdings.
- `web/routes/backtest_routes.py`: merge stored tail and older committed history according to coverage.
- `web/routes/risk_routes.py`: thin-record Kelly explanation/slab and category share-base diagnostics.
- `web/routes/settings_routes.py`: postpone mode mutation until universe validation passes; danger-description update.
- `web/routes/upstox.py`: reject missing OAuth state with a dedicated notice.
- `web/scheduler.py`: durable per-category rotation progress/resumption; slot catch-up; completed/unfinished state; total weekly-step budgets and tracking abandoned still-running workers; retry ledger for timed-out pulls; earlier universe rebuild; write-refusal entry halt; separate delayed trigger-layer cooldown with RSS guard/retries.
- `web/state.py`: serialize/park conflicting local book, restore Mongo book on reconnect, and carry local journal evidence.
- `web/why_no_buy.py`: partial/interrupted/current rotation status, upsert rather than duplicate rotation records, corrected category-budget words.
- `web/templates/_den_engines.html`,dashboard,risk: matching explanations for danger/Kelly/share-base changes.

The remainder are test changes: new `test_entryfix_scheduler_2026_10_22.py`598lines,`test_entryfix_web_2026_10_22.py`478lines,`test_entryfix_seams_web_2026_10_22.py`79lines, plus modified existing tests. This is a substantive repair branch, not only an overview document. It still leaves the manual order/position routes, shared FII/DII writer, valuation persistence claims and daily/refetch success paths above unchanged. A merge should be reviewed and tested as code changes; branch existence does not imply deployment or correctness.

The branch's weekly time cap uses a daemon worker and abandons waiting after the cap; it explicitly permits the old worker to continue writing while the block advances. A still-running same-labelled step is not restarted. This is a useful bounded-wait measure, but it is not worker cancellation or atomic job rollback; review overlap/resource consequences before calling it a hard execution deadline.

## Practical follow-up order

1. Protect durable state: recovery conflict, write health, portfolio lock/idempotency and numeric invariants. These have higher operational impact than UI polish.
2. Review the existing branch's verified fixes, run the full suite on the intended CI/deploy Python, then add targeted invariant tests for issues it leaves behind.
3. Make every persistence adapter return a meaningful outcome; use those results for job retries, success journals and UI saved states.
4. Separate liveness from readiness/operational completion: last-success per task, failed/remainder counts, writable store, latest source date and source fallback status.
5. Keep the Den's implemented/missing/assumed statuses visible; require source/effective-date evidence for outputs used by automated exits, rather than assuming a research UI card is a tested execution contract.
6. Fix installer drift: `install.sh:21` acceptsPython3.9+ while its error says3.11 required; final instruction runs8000 but line44 says default7860. CI3.11 and Render3.11.9 should be tested against exact current pins; parent environment was3.12.14, which is not equivalent deployment verification.

This report supports a comprehensive repo summary but does not establish financial performance, successful production deployment, live Mongo contents, valid daily broker token, production memory stability, or rendered-browser/accessibility compliance.
