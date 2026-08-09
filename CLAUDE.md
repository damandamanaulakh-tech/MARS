# CLAUDE.md — Sourceborn AM07 (durable session brain)

Read this first. Each session starts blank; this file is the memory.
Repo `damandamanaulakh-tech/am07` — the app's clean STOCK-ONLY home
since 2026-07-22 (user's standing wish: one repo = one work, shareable
if the app ever launches). **Branch `main` = live**; Render service
`sourceborn-stocks` deploys it → https://sourceborn-stocks.onrender.com.
Ship pattern: commit → push main (standing OK for direct push; use a
session branch first only for risky changes). LEGACY: old repo
`sourceborn-engine` (mixed history + chatbot branches) is retired —
user archives/deletes it whole after the Render re-point; code + all
59 data files verified byte-identical at the 2026-07-22 port, nothing
lives only there. App state (book/journal/history) lives in Mongo
Atlas, not in any repo — repo moves never touch money or memory.

## What it is
Live **paper-only** trading engine (₹25L paper capital), FastAPI + Mongo
Atlas on Render starter (512MB always-on). Hard PAPER_ONLY lock in
`trading/brokers/upstox.py:assert_paper_trading` — no live orders, ever,
without explicit user approval. User is non-technical: simple language,
tables, direct, no hedging, no fake data (say DATA_NEEDED), mercy on
tokens. **Verify against code — never trust a narrative** (incl. other AIs'
uploads: audit them; several contained fabricated numbers/padded catalogs).

## THE FINAL SET (evidence-earned; user approved; LIVE as of 2026-07-21)
- Signal: **6m+12m vol-adjusted momentum, rank-blended**
  (`strategies/nifty200_momentum.py`) + quality blend 35%.
- Book (owner order 2026-07-30 "change the capital to 50 lack, 80 stocks,
  roughly 1-1.25 for each trade — change it now"; corrected same day
  "1.25 lac, 125000, not percentage"): **₹50L capital, FIXED ₹1.25L per
  new trade (`cfg.position_size_value=125000`), `max_positions 80` as
  ceiling** — cash funds ~40 concurrent trades, rest of the slots open as
  capital grows. All sizing sites (risk.can_open, orders limit-fill,
  owner_buy) go through `cfg.position_notional()`; `max_position_pct
  0.025` is the fallback when the fixed value is 0. Boot migration v2
  (marker capital_order_2026_07_30_v2) supersedes same-day v1 which had
  read the size as 1.25%. Supersedes 25L/30/3.3%.
- **[SUPERSEDED 2026-08-04 → sizing_mode="fixed"; kept as history] SIZING
  SUPERSEDED SAME DAY — ½-Kelly was live 4 days.** Owner picked "Switch to
  ½-Kelly sizing" after reading docs/ALGO_V1_V2.md (I had asked because "ok
  with kelly" was ambiguous). `cfg.sizing_mode="half_kelly"`,
  `kelly_fraction=0.5`, `kelly_ic=0.05` (ASSUMED), `kelly_max_pct=0.05`
  (₹2.5L hard ceiling/name). Size = `0.5·µ/σ²·equity` where
  `µ = IC·σ·z`, `z=(score−62)/30` — so **low-vol stocks get bigger bets** and
  a score AT the floor gets ZERO (the "above 62" order is enforced twice).
  **Maths lives in NEW `trading/sizing.py`** (`position_notional_for` is the
  one entry point) because engine may not import lens_v2; lens_v2 now
  re-exports from sizing so lens and book can never diverge. `risk.can_open`
  gained `notional_override=` — every gate still applies (cash, reserve,
  sector count+capital, heat, min_position_value, kill switch).
  **Removed a double-count I found**: the old strength ladder (0.5/0.7/1.0×
  by score) is skipped under Kelly since µ is already score-derived; the IFR
  throttle stays. Sizing inputs journaled per BUY under `"sizing"`.
  Settings exposes sizing_mode/kelly_fraction/kelly_ic/kelly_max_pct with
  validation (bad mode → half_kelly, fraction >1 → clamped to 1).
  MEASURED ladder at σ=20%: score 63 → ₹20.8k (below the ₹25k min, skipped) ·
  65 → ₹62.5k · 68.8 → ₹1.42L · 72 → ₹2.08L · 80+ → ₹2.5L capped. Real
  stocks: TATASTEEL 68.8 → ₹1,36,711 · SUNPHARMA 72.9 → ₹2,07,099 ·
  RELIANCE/TCS/BHARTIARTL/MARUTI all skip (score < 62). **Expect fewer but
  larger, conviction-weighted trades.** UNTESTED live — 60-day window judges.
  Applied via one-time boot migration over stale overrides (marker in
  settings doc_id="migrations") + `Portfolio.sync_capital`: Starting-
  Capital changes now MOVE CASH by the delta (journaled CAPITAL_ADJUST,
  P&L history kept; CAPITAL_ADJUST_BLOCKED if cash would go negative).
  capital_base persisted JSON+Mongo; legacy books default base 25L.
- **Absolute-momentum gate ON** (own 6m return > 0) — earned 4/4 in v3.
- **Regime brake ON**: correction (Nifty < 200DMA) blocks new buys…
- **…until VIX-crush re-entry** (v4): India VIX ≤ 75% of 120d spike peak →
  momentum buying re-enables in correction (`regime.vix_crush_ok`,
  `cfg.correction_vix_reentry=True`, journaled CORRECTION_VIX_REENTRY).
- Universe: **full NSE via rotating bucket ~50/day** — and the master list
  **auto-refreshes daily** from Upstox's public instrument file (2,393 at
  2026-07-21; was a stale fixed 2,376 — user caught it). 08:50 IST job +
  boot catch-up, `trading/universe_refresh.py`, journals UNIVERSE_REFRESH/
  _FAILED, /healthz shows count+file_date. mis_allowed carried; new
  listings default 0.
- Candle gate: daily ENTRY-TIMING only (no monthly alpha — v3). Volume-
  expansion: not a membership rule. Ocean engine: **PARKED — and since
  2026-07-27 the park is REAL** (`cfg.ocean_enabled=False`, Settings toggle,
  `engine._ocean_picks`). Audit found the word "parked" was doc-only: the
  branch ran on `regime.ocean_active` alone, True in correction/panic/
  neutral, and in a correction Everest is off — so the parked engine was the
  ONLY thing able to buy, with picks that skip the score floor and the
  absolute-momentum gate. Blocks now journal OCEAN_BLOCKED. Needs a crash
  guard before the flag goes on (knife-catching −41%DD in 2020).
- **Blocklist** `cfg.ticker_blocklist=("YESBANK.NS",)` — never scanned/bought;
  held names exempt for exits. Exits per-mode (momentum **−5/+12** — stop
  −5 owner order 2026-07-26, +12 set 2026-07-21; partial 0.07 has NO
  consumer; trail −15 unreachable at this bracket; decay — never on
  owner_pick positions; 200d max-hold); intraday ±2.5/1.5
  squared off 15:25 ALWAYS by tag.

## Daily machine (all automatic; failures visible)
03:30 Upstox token dies (user must log in once daily — the ONLY manual act;
login triggers catch-up scan) · 08:50 universe refresh · 09:20 multi-mode
scan, momentum FIRST (order since PR#9: momentum, short, swing, positional —
intraday REMOVED from auto-rotation 2026-07-27, owner order; preset stays in
Settings for manual use) · 14:30 top-up · 15:25 square-off intraday-tagged +
expire owner limit orders · 15:35 refresh/exits · Friday 16:30 weekly OHLCV
top-up (merge, never replace) · SCAN_FAILED + SCAN_CATCHUP journaled;
catch-up (refresh+scan) fires 2 min after every boot
(`scheduler.catchup_scan_if_missed`; SB_DISABLE_CATCHUP=1 in tests).
Stale-data guard: refresh with no live quotes = DATA_STALE + HOLD.
Owner desk (all owner-only, journaled, paper): 🟢BUY buttons on every
listed stock (scan top-50, watchlist, today's picks) · **order ticket** on
/scan — qty/market/limit/stop%/target%, blank=engine decides; limit orders
rest on `portfolio.pending_orders`, day validity, filled at any engine
price pass (`trading/orders.py`), cancel button · **🎹 tap-a-parameter**:
every piano note links /scan/parameter/{key} — plain definition, pass
rule, code knob, per-stock pass/fail lists (user's exact piano intent) ·
/backtest Download-History + Export-Stored-ZIP · /healthz shows
history_tickers + universe count/date.

## Evidence ledger (all committed in docs/ + scripts/, reproducible)
- v1 (10 mega-caps): 0/4 OOS windows — momentum has NO edge narrow. The
  in-sample-great/OOS-loss gap explains every "great backtest, flat live".
- v2.1 (1,417-symbol panel, glitch-repaired): **4/5 OOS, +9.3pp avg** —
  the edge lives at breadth. (4,476 corrupt prints surgically repaired.)
- v3 (191 real Upstox tickers, liquidity-screened): 3/4 OOS, +8.9 avg;
  v3.1 PIT-constrained: 2/4, −2.2 — **honest alpha bounds −2..+9/yr**
  (PIT over-corrects: current-member prices only). Cold stretch 2024-26
  confirmed 3× = the user's lived flat year.
- v4 (10 correction episodes, preregistered): dip-buying −2.2 ✗, half-mom
  −2.5 ✗, Ocean +0.4 but unsafe ✗parked, **VIX-crush +2.3 ✓ wired**
  (+24% COVID rebound; +2.0% current episode).
- Old-vs-new demo: 2015-26, NEW +794% vs OLD +616% (deeper DD −27.5 vs
  −15.9). `scripts/demo_old_vs_new_engine.py`.

## Data in repo (feed NOTHING twice)
`data/history_upstox_export_2026-07-19.zip` 191×12y OHLCV (user's Upstox
store; also in app Mongo `ohlcv`) · `index_daily_15y.csv` Nifty/Sensex/
VIX/BankNifty 2011-2026 · `nse_all_stock_data.csv.zip` close panel 1,417
sym 1991-2024 (687 ≥15y) · `history_15stock_raw/` (30 raw NSE files) +
`history_15stock/` (16 clean, rebuilt by `scripts/build_clean_15stock.py`,
10 adjustments manifest) · `universe_snapshots/` 5 PIT lists 2017-2023
(Wayback) · `upstox_nse_equities.csv` universe (auto-refreshed daily; repo
copy = last committed snapshot) · parameter workbook.
DATA_NEEDED remaining: none blocking. (Optional later: full-universe OHLCV
download beyond N200; PIT lists 2020-22 don't exist in archives.)

## Current state (2026-07-21 late — post "brief questions" round)
Book: 0 positions, ~₹24.79L cash (45 sold — mostly SQUARE_OFF on stale
intraday tags; root cause fixed: policy-C retag + tags can't recur).
Regime: CORRECTION (episode since 2026-02-27, real). VIX re-entry armed.
**User's 5 pending BUY calls: TORNTPHARM, CONCOR, PNB, DIVISLAB,
FEDERALBNK** — one tap each on any list row. MODE to park on N200
MOMENTUM. **60-day live-paper verdict starts at next login.**
docs/APP_BRIEF.md = owner's manual (updated with ticket + piano pages).
**2026-07-26 owner decisions wired: stop −5% (was −12, UNTESTED number,
owner order) · target stays +12% · N7 up-day-only volume · N1 BE/T2T
series-watch card (08:50, SERIES_WATCH_*) · flags bundle (cascade/
sector-rout/F1-confirm chips, display-only) · filings feed v1 (09:05
BSE announcements for held+watch, FILINGS_*; BSE returns empty to cloud
IP so far — fail-visible, tune from live). Entry thresholds UNCHANGED —
full-bucket test proved current rules win at breadth (research/).**
**Target question CLOSED: user chose +12% "for now" (2026-07-21) — wired;
"actual" (order-flow etc. params) = next phase of app, their words.**
**2026-07-27 full-repo audit (12 agents, owner asked "each corner check").
Owner approved 2 fixes; BOTH SHIPPED, 433 tests green:**
1. **Ocean park is now real** — see THE FINAL SET above. In the current
   CORRECTION the engine now opens NOTHING until the owner flips
   `ocean_enabled` or the VIX-crush re-entry turns Everest back on. That is
   the intended behaviour, not a fault: expect an empty book and
   OCEAN_BLOCKED journal lines.
2. **Daily kill switch is now actually daily** — `RiskState.roll_day()` was
   only ever called from `backtest.py`, so `day_start_equity` stayed pinned
   at ₹25L and the "−2% daily halt" was really "−2% since inception", which
   force-closed the WHOLE BOOK on an ordinary drift. `engine._roll_risk_day`
   re-baselines once per day to yesterday's last mark, journals
   RISK_DAY_ROLL, and persists (`portfolio.risk_day` /
   `risk_day_equity`) so a mid-day Render restart can't re-baseline.
   Bonus bug found while wiring the Settings toggle: **bool fields saved as
   the STRING "0" (truthy) — entering 0 to switch a flag OFF switched it
   ON.** Live for `pyramid_enabled`. Fixed in `settings_routes`.
**2026-07-27 audit fixes 3 & 5 (owner: "there is owner only none else"),
442 tests green:** login page's "Quick switch" Owner/Viewer buttons DELETED —
they carried working passwords in unauthenticated markup · **viewer account
removed entirely**, owner is the only seeded user · `sync-to-hf.yml` DELETED
(checked all 16 runs first: every one failed on empty HF_TOKEN/HF_SPACE
before the push line, so **nothing ever leaked**) · guard tests in
`web/tests/test_login_no_secrets.py`, verified to fail if the old page returns.
**CLOSED 2026-08-09 (owner: "APP_PASSWORD its there"): the password env IS
set in Render — as `APP_PASSWORD`, which `web/auth.py` honors (OWNER_PWD
wins if both exist; verified in code, lines 35-37). The `"sourceborn"`
fallback is out of play on the live app. Never re-raise.**
**2026-07-27 external-review adoptions (owner: "yes push all 3"), 449 tests:**
1. Full NSE 2026 holiday list (16 dates incl. Holi/Diwali/election day) in
   `calendars.NSE_FIXED_HOLIDAYS` — `is_trading_day()` was already wired into
   every scheduler job, only the list was thin. 2027 = 4 fixed dates only,
   refresh from NSE circular ~Dec 2026; missing dates fail safe.
2. Nifty 200 (^CNX200) secondary backtest benchmark — the fair ruler for a
   N200-momentum strategy. `BacktestResult.benchmark2*`, walk_forward rows,
   /backtest page. Unfetchable ⇒ visible DATA_NEEDED warning, alpha vs N50
   unchanged. NOTE: ^CNX200 fetch from Render UNVERIFIED (sandbox has no
   yfinance network) — first live run tells. **Backtest button itself
   CONFIRMED WORKING by owner 2026-07-29 post PR#18 memory fix**; still
   awaiting owner word on whether the N200 line rendered or showed
   DATA_NEEDED.
3. Truth pass on marketing copy: login page (ATR-stops/live-mode/500+ claims
   were false → now "fixed stops, paper only, 2,300+") + dashboard "can do"
   panel (correlation 0.7→0.85, reserve ladder 20/25/35/50→real 0/0/20/40,
   fictional sector blacklist removed). Guarded by test_truthful_copy.py.
**Owner decision (2026-07-27, CLOSED): cash reserve stays 0% — "go for full,
no use to hold". Base = fully deployed. The regime-dynamic reserve
(risk_v2: 20% correction / 40% panic, forced in by engine.scan) predates this
and STAYS as crash protection — owner was told; strip only on their word.**
The external review tables themselves: ~60% graded a stale spec, not the code
(exits/caps/universe/PIT all wrong); adopted the 3 valid items above.
**2026-07-27 late-day owner decisions (their words):**
- **Exits CLOSED by owner: "fixed at 12% or when i do manually"** — decided
  BEFORE the exit study finished. The completed study (7 variants x 4 OOS
  windows, docs/exit_variant_study_results.json) then showed D (+20% target,
  trail -15 kept) beat the live +12% in ALL FOUR windows: totals +74.7 vs
  +57.4, best-or-2nd in each, fewer trades, same worst-DD, and it revives
  the dead trail (fires 6-41x/window vs 3-5). Owner was shown this and asked
  once; their +12 stands unless they say change. DO NOT re-open unasked.
- Kite/live orders: "leave it for now" (still paper-only).
- **Phone alerts SHIPPED (owner: "ok u can make the phone alerts"):** ntfy.sh
  (pywebpush won't build - would risk Render deploy). `web/push_ntfy.py` +
  Settings card + scheduler wiring (09:20/14:30/15:25/15:35). Pushes
  BUY/SELL/SCAN_FAILED/DATA_STALE/CORRECTION_VIX_REENTRY, batched, cursor in
  Mongo settings doc_id="push", private random topic. Owner setup: install
  ntfy app -> Settings -> Enable -> subscribe to shown topic.
- Tunnel-studies gap CLOSED (2026-07-27). Owner's Drive zip = byte-identical
  to repo (nothing was lost); the 2 missing generators were never saved
  anywhere. RECONSTRUCTED from the surviving 3 and VERIFIED by regeneration:
  icici 776/780 rows exact, cochin 1024/1028 — the only diffs are mom_rank on
  the last 4 days (07-18..23), because the original 07-24 runs used a cohort
  export fresher than the committed zip (data ends 07-17); that export lived
  only in the retired repo. Proof it's data-not-code: committed TCS readings
  vary after 07-17 though its surviving script would truncate there. All 8
  research scripts now run repo-relative (dead sourceborn-engine paths
  repaired). Committed readings remain the canonical record.
**Old-review reconciliation (2026-07-27, owner asked "how much exist"):
the early-July system review (260-tests/192-ticker/exits-off era) is a
FOSSIL — 15 of its ~20 items since built (code comments cite its labels:
P0/A2, P0/B3, P1/F3), 1 reversed, 4 still open: breadth gate (>50% of
universe above 200DMA before full deployment — the one idea never built
anywhere), score-band calibration vs forward returns, FII/DII staleness,
flat 5-bps slippage. Its "7-yr alpha −8.5%" number is superseded by the
v1-v4 program — do not quote it.**
**2026-07-27 closures round (owner walked the 4 survivors + evidence Q):**
- F2 CLOSED: pending was only (a) short_interest stubbed 50 (5% weight, no
  free NSE source) and (b) catalyst needs OPENAI_API_KEY/ANTHROPIC_API_KEY
  set in Render (fallback neutral 50, fail-visible). Recommendation accepted
  path: LEAVE the stub — the AM07 composite doesn't pick momentum entries,
  and the weights are the owner's historic spec. Owner action if wanted:
  check the API key exists in Render.
- Weekly data renewal SHIPPED (owner: "must be renewed every week"): the
  universe LIST already refreshes DAILY (better than weekly); what was
  frozen was the OHLCV store. New: `history_store.top_up_history` (MERGE,
  never replace — naive weekly re-save would truncate 12y to 1y) + scheduler
  job Friday 16:30 IST, journals DATA_REFRESH_WEEKLY/_SKIPPED/_FAILED.
  Committed research zip = refresh per session when >7d old (session duty).
- A3 breadth gate: TESTED (preregistered, scripts/breadth_gate_study.py,
  docs/breadth_gate_results.json). Verdict: a reshuffler, not an edge —
  totals +58.3 vs baseline +57.4 over the 4 windows (+0.9pp/7.5y), better
  in chop (2022-23 +4.3pp, 2024-26 +1.9pp w/ DD -18.8→-15.6), worse in
  trend (2020-21 -3.8pp, 2019 -1.6pp). Recommendation was no-wire; owner
  ORDERED WIRE ("A3 — breadth gate: wire it") — their right, evidence
  shown. WIRED: `cfg.breadth_gate_enabled=True` (+min_pct 0.50, min_sample
  30), `engine._breadth_gate_blocks` — momentum-mode NEW entries blocked
  when <=50% of the scan panel is above its own 200DMA; journals
  BREADTH_BLOCKED; fail-open on thin samples; exits never touched; Settings
  toggle. NOTE: live breadth = the ~50-name rotating-bucket sample, study
  used the full 191 panel — sampling noise ~±7pp near the threshold.
  Catalyst key CONFIRMED in Render by owner — F2 fully closed.
  Item 17 CLOSED (owner: "single trade section never works, u can remove
  intraday but but in 3 sections"): intraday REMOVED from the 09:20
  rotation — _DAILY_MODE_ORDER = momentum, short, swing, positional; the
  intraday preset stays in Settings for manual use; 15:25 square-off job
  unchanged. Items 18 (flat slippage) & 20 (old blame-split) closed per
  recommendation — owner left them as questions; one word reopens.
- A4 score calibration CLOSED (wontfix, owner asked discuss-and-close): the
  AM07 composite doesn't drive momentum entries (momentum_score does, and
  it's v2.1/v3-tested); calibrating a score that isn't picking stocks is
  work without money impact. Revisit only if swing/positional become primary.
- D4 flat 5-bps slippage CLOSED (accepted approximation): liquidity floor
  (Rs 1Cr+ median turnover) keeps fills in names where 5bps is sane; no
  order-book data exists to do better honestly; changing it re-scores every
  backtest without changing any decision.
- Evidence standard DEFINED (owner asked "what is required, ask and close"):
  the missing evidence is LIVE, not more backtests. The 60-day paper window
  (started at next login after 2026-07-21) is the test. Pass = paper return
  within/above the walk-forward expectation band vs Nifty 200 over the same
  window, with scans verifiably running daily (healthz + journal). Weekly
  read-only check-ins; verdict at day 60. No engine changes mid-window
  without owner ask.
- Old Table-3 blame-split CLOSED: describes a config that no longer exists;
  app needs nothing from it.
- F6 FII/DII: REFRESHED 2026-07-29 (owner confirmed the monthly release
  zips ARE FII/DII: "all zip excel which name Mar 2025, april 2023 kind of
  are FII DII Only"). data/fii_dii_cash.csv extended 2024-09-06 →
  2025-03-29 (85 rows) from the SEBI FII disclosure xlsx in Dec_2024/
  Jan_2025/Feb_2025/Mar_2025.zip via NEW
  scripts/build_fii_cash_from_disclosures.py (TR_TYPE 01=buy 04=sell,
  equity only, verified: Jan-2025 −76k Cr = the record FPI exodus,
  Mar-2025 sell-early/buy-late = published story). DII does NOT exist in
  those files — new rows carry EMPTY dii (flows.py reads blank as 0.0;
  no engine path consumes DII). Gaps: 2024-09-09→2024-11-29 (no
  Sep–Nov 2024 assets) and after 2025-03-29 (archive ends; SEBI publishes
  months late — a LIVE current feed still doesn't exist; staleness guard
  remains owner-deferred). Old hand-fed rows untouched, guard test in
  test_flows.py. Never ask again.
- Proof ledger truth-passed (PR #10): G001/G002 CLOSED (data + PIT exist),
  G004 IN_PROGRESS (60-day live window is the remaining test), G003 LOCKED.
- Chityy archive ARRIVED (published 2026-07-27 13:35Z, tag `chityy-archive`).
  74 ASSETS (1.32 GB): monthly TRANSACTION-LEVEL FII disclosure zips
  2009->Mar 2025 (per-trade: fund/scrip/date/qty/value) — the raw material
  for Chityy FII sell-cluster research; fetchable from sessions via the
  repo releases/download URL. 31 more files sit as DESCRIPTION links
  (user-attachments) which the session proxy CANNOT fetch — includes the
  chityy code packs, FII DII Nifty.zip, nse xlsx 1991-2026, Back Test
  Claude.zip. Full index in data/README.md. OWNER ASK (pending): re-attach
  the small key zips (FII DII Nifty, Back Test Claude, aqu, + whichever
  holds sourceborn_chityy code / clean master_extended_chityy.csv) in the
  ASSETS box so a session can rescue code into research/chityy_archive/.
  F6 note: archive newest month = Mar 2025 -> fii_dii_cash.csv staleness
  for the LIVE regime is NOT fixed by this; still needs a current feed.
- RESCUE COMPLETE (owner re-attached 5 key zips as assets; "am07 is the
  chityy" — their words: this app IS the Chityy lineage). In git now:
  research/chityy_archive/ (parameter registry (1,500 JSON + 1,200 CSV params committed), signal studies,
  trigger tables, master_30000_stock_list) · data/chityy/
  master_extended_chityy.csv REBUILT clean from master_extended.xlsx (was
  corrupt, zero line breaks) · data/fii_dii_daily_2014_2023.csv (2,339
  daily rows for FII-tide research). Code question CLOSED
  (2026-07-27 evening): owner asset-attached everything (106 assets now) —
  PRE RISE_FALL_TRIGGER_LAYER_CSV_CODE_v0_2 holds NO .py, only 8 CSV design
  maps (trigger params/score gates/clusters/pyramid map/reverse-backtest
  design), committed to research/chityy_archive/trigger_layer_v0_2/. The
  sourceborn_chityy .py code was never saved anywhere — design maps are the
  surviving record. Back.Test.Claude.zip = duplicate of the
  repo's 191-ticker panel (release-only).
- Original archive plan (superseded by the above; kept for context):
  do NOT put it in the git tree (100MB/file limit, Render deploys clone the
  repo). Owner instructed to publish a GitHub RELEASE on am07, tag
  `chityy-archive`, with the 8 original zips as assets. WHEN THE RELEASE
  APPEARS: index it in data/README.md + here; pull the small code zip and
  commit sourceborn_chityy code + clean master_extended_chityy.csv (repo
  copy is CORRUPT, zero line breaks) + picks CSVs into
  research/chityy_archive/. Check for the release with the GitHub tools.
**2026-07-27 night RECHECK (owner: "review all now once") — 2 fresh-eyes
agents over the day's full diff + self-review; 4 real bugs found IN THE
DAY'S OWN FIXES, all fixed same night, 477 tests:**
1. Kill switch LATCHED FOREVER once tripped: liquidation empties the book,
   empty-book refresh() early-returned before the day-roll, so the switch
   never re-armed. Fixed: roll happens at scan() start AND in the
   empty-book refresh branch.
2. "Daily" halt measured only 14:30->15:35 (roll first ran at 15:35, after
   today's marks were recorded). Fixed by the scan-start roll: baseline =
   yesterday's close, as intended.
3. risk_day fields were NOT in the Mongo mirror/hydrate (web/state.py) —
   the whole persistence died on every Render restart (JSON in /tmp is
   wiped). Fixed both directions + risk_day_killed flag persisted so a
   mid-day restart can't forget a halt.
4. Bool-override poisoning was self-perpetuating: state._bootstrap applied
   stored string "0" raw on every boot and settings-save couldn't heal it.
   Fixed: _coerce_override at the bootstrap door.
Also: OCEAN_BLOCKED now journals once/day and only when Everest is off
(was ~5 spam lines/day incl. neutral) · push enable() seeds the cursor
(first enable no longer blasts up to 300 historical events at the phone) ·
"22k params" doc claim corrected (committed registry = 1,500 JSON + 1,200
CSV; 22k counted cells) · .gitignore negation added for data/chityy/ ·
docs/WORK_LIST.md A2-A5 finally marked DONE (shipped in a185d94).
**2026-07-28 candle library 18→34 (owner: "i knw u have few but i want
more" + 3 reference-chart screenshots):** 16 new structures in
`trading/candles.py` — bullish: three_inside_up, three_outside_up,
three_line_strike, abandoned_baby_bull, kicker_bull, dragonfly_doji,
morning_doji_star · bearish (warn-only, never buy): three_inside_down,
three_outside_down, abandoned_baby_bear, kicker_bear, gravestone_doji,
hanging_man, evening_doji_star, bearish_harami · neutral: spinning_top.
The 6 strong multi-bar bullish reversals joined `cfg.candle_patterns`
(entry-confirmation gate — a scored pick can execute sooner; scores/
stops/targets untouched); single-bar needs-follow-up shapes
(dragonfly_doji, inverted_hammer, bullish_harami) stay lanes-only.
Piano styling now DERIVES from the registries (`scan_routes._BEARISH_NAMES
= set(BEARISH_PATTERNS)`, `_CANDLE_HIT` from `_PATTERN_FUNCS`) so new
structures can never be mis-styled; all 34 have plain-language
`_STRUCT_DEFS` tap pages. 491 tests.
**2026-07-29 (owner: "a & c" then "go"):** guessed 2029 election date
DELETED from calendars.py (PR#20) · **visual candle chart SHIPPED (PR#21)**:
`/chart/{ticker}` — server-side SVG (zero deps, 512MB-safe), last 90 daily
candles + volume, ▲/▼ markers on the exact bars each of the 34 structures
fired (`web/candle_chart.py per_bar_structures`), entry/stop/target dashed
lines for held positions, hover tooltips, structure chips link to piano tap
pages, DATA_NEEDED when no stored history (never fake). 📈 links on scan
picks, dashboard picks/watchlist/positions, positions page. BONUS BUG FIXED:
dashboard share-candle chips hardcoded the OLD 6 bearish names — the 8 new
bearish structures showed GREEN; now derives from BEARISH_PATTERNS
(`dashboard._bearish_names`), guard test. 500 tests. Also 2026-07-29:
financialdata.net marketing email assessed on owner ask — REJECTED (India
data paywalled at $29/mo Standard, duplicates free Upstox; US-only depth;
none of our real gaps: FII/DII, NSE short interest, Indian news). Owner
approved skip.
**2026-07-29 signal↔universe link (owner: "signal window need to link with
universe, it must pick stock from there not the fixed"):** /signal now has
an any-stock search box over the FULL auto-refreshed universe (datalist,
2,393 names) next to the scored-today dropdown. Component priority: BUY
journal/positions → last picks → **whole scan_matrix bucket (~50/day, was
missing)** → **on-demand compute** (`signal_routes._live_signal`: stored
history via `candle_chart.load_ohlcv(tail=420)`, exact live-mode
momentum_score+quality_score+blend, labeled "computed now from stored
history (to DATE) — not a stored scan result" in amber) → DATA_NEEDED page
(<1y history or none; strings never faked). Zero network; scan/entries
untouched — viewer only. 505 tests.
**2026-07-30 LENS V1/V2 + dashboard panels (owner: "in Universe too u should
focus on same · ship it · make some changes in the dashboard, adding few
option u can adopt some of each kind" + 5 reference-mockup screenshots):**
- **`trading/lens_v2.py` — the growth-first lens, READ-ONLY.** v1 (live
  engine) maximises return subject to not blowing up; v2 maximises E[log W]
  (compound growth). Pure functions: `edge_estimate` (Grinold α=IC·σ·z, turns
  a RANK into a µ — the step v1 never needs because its size is fixed) ·
  `kelly_fraction` (runs at cfg 0.5 capped 5% since 2026-07-30; the ¼/25% were the launch defaults) · `convexity` (ATR room-to-high,
  payoff R, breakeven hit-rate, skew) · `impact_bps`/`capacity_notional`
  (square-root law, Y=0.75 — 4× size = 2× impact, the wall that stops
  scaling) · `momentum_halflife` · `v2_view`/`rank_v2` (ranks by expected
  log-growth, not score). **Assumptions on screen, not buried**: IC=0.05 is
  an ASSUMPTION; convexity+impact are PROXIES from daily bars (no options
  chain / order book / live short interest exist here); <60 bars = None =
  DATA_NEEDED, never a guess.
- **DEFENSIVE `manipulation_risk_flags`** (owner asked for the illegal side;
  refused the playbook, built the victim-side detector instead): reads the
  outside signature of an engineered move from public daily OHLCV —
  parabolic_without_volume · thin_book_ramp (<₹1Cr/day) ·
  volume_spike_price_stall · repeated_limit_up · no_down_days. 2+ flags =
  `is_trap` = v2 refuses the name. Rationale: v2's small-cap/illiquidity
  hunting ground is exactly where pumps fish for exit liquidity.
- **HARD BOUNDARY, tested**: `engine.py`/`risk.py`/`orders.py` must never
  import lens_v2, and lens_v2 must contain no order/config-mutation/network
  call (`test_lens_v2.py` asserts both by source inspection).
- **`/lens` page** (`web/routes/lens_routes.py` + `lens.html`, sidebar 🔭):
  v1 vs v2 books side by side over the scanned universe + any-stock search;
  per-row µ/σ/¼-Kelly/v2-size/payoff-R/breakeven/growth/flags; ⚠ marks where
  LIQUIDITY not edge capped the size; ⛔ TRAP rows. Bounded to 40 rows/request.
- **Dashboard `web/market_panels.py`** — 6 panels adopted from the mockups,
  all real data: index strip w/ SVG sparklines (committed index_daily_15y.csv,
  labelled "stored to DATE", VIX inverted so up=red) · Market Breadth (the
  engine's OWN scan panel — the same number the A3 gate uses; score bands,
  pass/abs-mom/timing %, sector strength chips) · What's Coming (curated
  calendars, next 45d) · Walk-Forward Evidence (docs/param_search_v3_results
  .json passed through UNTOUCHED — the −4.64 losing window must show, guard
  test) · Trade Horizons (real per-mode presets, live one marked) · ATR vs
  your fixed −5% stop (display only). `_panel()` wrapper: a broken panel
  hides, never takes the page down (tested). 513 → 545 tests.
**2026-07-30 `docs/ALGO_V1_V2.md` — the full v1-vs-v2 algorithm** (owner: "i
want the full logics behind agent say / build a proper algo"). Every figure
COMPUTED from committed code+data on 2026-07-30 and re-verified by script; no
illustrative-invented numbers. Contents: both objective functions (v1 = return
s.t. survival; v2 = max E[log W]) · v1's 14 stages + pseudocode · v2's 10 stages
+ pseudocode · parameter table · worked example (TATASTEEL, real) · the scaling
wall · the ruin law · the trap detector · honest limits · where the illegal line
is and why it fails on arithmetic first.
**THREE REAL FINDINGS worth remembering:**
1. **Kelly independently recommends ₹1,38,825 on a ₹50L book at IC=0.05; owner
   chose ₹1,25,000 by instinct — 11% apart.** v1's fixed size is not a
   compromise, it is near the growth-optimal number for an honest edge.
2. **Capacity wall MEASURED**: 7 large caps absorb ~₹182 Cr at 25bps; a 25-name
   book ≈ **₹650 Cr is where this strategy stops scaling**. Impact on TATASTEEL:
   0.5bps at ₹1.25L → 42.5bps at ₹100Cr → 134bps at ₹1,000Cr (more than the
   whole estimated annual edge). The "scale to data centres" plan dies here.
3. **Growth-vs-ruin, corrected**: ¼-Kelly = 43.8% of max growth for 0.78%
   halving risk · ½-Kelly = **75% for 12.5%** (the honest sweet spot) · full
   Kelly = 100% for 50% · 1.5× = same 75% growth as ½ but 6× the risk · 2× = 0%
   growth, certain ruin. (An earlier draft of mine wrongly claimed ¼-Kelly keeps
   97% of max growth — the correct figure is 43.8%; g(m) ∝ m − m²/2.)
**DOC-vs-CODE ERROR CAUGHT while writing it: the live score floor was 55, not
70.** `Config.min_score_to_enter=70.0` is the dataclass default but
`apply_mode_preset()` overwrites it from `modes.PRESETS` at every boot — so the
dataclass line is DEAD for every real mode. Change the PRESET, never the default.
**Owner then ordered the floor RAISED: "it will buy above 62" (2026-07-30) —
`PRESETS["nifty200_momentum"].min_score_to_enter = 62.0`, shipped.** Live
per-mode floors now: momentum **62**, intraday 72, short 70, swing 60,
positional 68. UNTESTED at 62 (was 55 from P0/B3) — the 60-day window judges.
Guard tests in `test_capital_sync.py` pin the preset value AND the
default-is-overwritten behaviour so this can never silently drift again.
`lens_v2.v2_view(floor=...)` now takes the LIVE floor from
`cfg.min_score_to_enter` (was hardcoded 55) so v2's zero-edge anchor tracks the
engine. Owner also confirmed on the same message: **Kelly finding accepted
("ok with kelly")** — READ AS ACKNOWLEDGEMENT of the ₹1.25L-vs-₹1,38,825
finding, NOT as an order to switch sizing to Kelly; asked to confirm, fixed
₹1.25L stays until owner says otherwise. **½-Kelly-not-full guidance
acknowledged ("ok")** — no change made.
**Workflow lesson: the 13-agent design run FAILED** (all 6 design agents hit the
StructuredOutput retry cap, ~889k tokens, zero output) because the schema was
over-nested. Doc was then written directly from the shipped code, which was both
cheaper and more accurate — the algorithm already existed in `trading/lens_v2.py`.
Keep workflow schemas FLAT.
**Breadth-vs-VIX conflict CLOSED (owner: "yes — we cant wait for recovery,
that is movement of entries"):** vix_reentry now BYPASSES the breadth gate
(`engine._breadth_gate_blocks(bypass=vix_reentry)`) — fear-break entries go
through even while breadth is still <=50%; breadth guards all normal days.
Tested (test_vix_reentry_bypasses_the_breadth_gate). 478 tests.
**2026-07-31 "do check and fix all" — the deferred-flaw list WORKED THROUGH.
627 tests green from a clean state dir. Seven fixed:**
1. **`broker.bid_ask()` never existed.** `engine.scan` had called it for months
   inside a try/except, so it raised AttributeError every time, was swallowed,
   `spread_pct` stayed None and **the spread half of the liquidity gate never
   ran once**. Implemented on `UpstoxClient` (reads `depth.buy[0].price` /
   `depth.sell[0].price`, accepts a ticker or a raw instrument key, returns
   `(None, None)` when depth is absent so the caller skips the test instead of
   believing a fabricated spread).
2. **FII/DII parser was reading keys that do not exist.** The live NSE feed
   WORKS; the parser looked for `fiibyVal`/`diibyVal` while the endpoint
   returns ONE ROW PER CATEGORY — `{"category":"FII/FPI"|"DII","date":
   "03-Aug-2026","buyValue":"12621.89","sellValue":"11699.63"}`. Every lookup
   missed, every value defaulted to 0, and the function returned **all-zero
   readings dated TODAY** — fresh-looking, so they passed every check and
   scored a fabricated 40 (mildly bearish) every single day. Rewritten: groups
   by date, splits FII/FPI vs DII, strips comma separators, **rejects all-zero
   rows** (a parse miss is not a flat market). Real value now: +₹922 Cr → 73.8.
3. **Flow staleness guard.** `MAX_FLOW_AGE_DAYS = 45`; `get_flows()` returns
   None past that (callers already treat None as neutral 50), `max_age_days=0`
   opts research out. Also `fii_flow_score(net, days=)` — the live endpoint
   returns ONE day and the thresholds are calibrated on a 5-day sum, so a
   record buy day used to read as an ordinary week; engine passes the real day
   count.
4. **Drawdown governor no longer forgets.** `Portfolio.dd_peak_equity`
   persisted in JSON **and** the Mongo mirror (both directions — Render wipes
   /tmp, so JSON alone was useless); `engine.refresh` restores the peak before
   `update()` and saves it after. Before this, every restart measured 0%
   drawdown and disabled every throttle the governor controls.
5. **Owner custom stops survive boot.** `PaperPosition.custom_levels` set by
   `orders.apply_custom_levels`; `retag_positions_per_mode` skips those
   positions. A hand-set stop used to be overwritten by the mode preset.
6. **`/scan/parameter/{key}` auth-gated** — it was reachable with no login and
   exposed the whole scan (every ticker, score, gate outcome).
7. **Settings now tells the truth twice.** `Config.trail_is_reachable()` +
   an amber banner: at −5%/+12% a −15% trail needs the peak above **+11.76%**
   and +12% closes first — a 0.24pp window, dead. **Exits NOT changed** (owner's
   closed decision) — the page just stops implying a protection that cannot
   fire. And `PRESET_OWNED_FIELDS` names the 8 fields `apply_mode_preset()`
   re-applies on every restart, with a test asserting the list can never drift
   from what that method really writes.
Stale entries removed from the old list while checking: the owner password is
NOT printed in login.html any more (fixed 2026-07-27).
**Audit findings still NOT fixed (owner deferred / low value):** `partial 0.07`
documented with NO consumer · entry scores on the momentum blend, exits on the
AM07 composite (two different rulers) · `engine.scan/refresh` and all of
`web/scheduler.py` have ZERO direct tests · orphans:
auto/autoswap/intraday/macro/providers (910 lines) ·
~~chityy master CSV corrupt~~ (WRONG — verified CLEAN 31×59, 2026-08-09) ·
docs/WORK_LIST.md still marks A2/A4/A5 PENDING though `a185d94` shipped them.
**2026-08-04 — owner reported 5 things in one angry message; ALL FIVE WERE REAL
CODE FAULTS, all fixed, 638 tests green. Read this before touching prices.**
1. **THE PRICE OUTAGE, ROOT CAUSE FOUND — and it was never the API.**
   `upstox_instruments._build_lookup` filtered `instrument_type not in
   {"EQ",""}`. Upstox labels cash equities **`EQUITY`**, so **all 9,467 NSE_EQ
   rows were discarded and the lookup came out with ZERO entries** (measured).
   With no instrument keys, `get_ltp()` was handed an empty list, so EVERY
   stock quote failed — and an empty lookup is indistinguishable from "ticker
   not listed", so nothing ever complained. The only live numbers on the whole
   app were the 4 **indices**, which come from the hardcoded `INDEX_KEYS`
   constants and never touch this table — exactly the owner's "hope not only 3
   are updated". Verified after the fix: 9,462 tickers resolve, incl. the two
   they named (VSSL, ULTRACEMCO); before, **zero** did. PR#31's
   `symbol=`→`instrument_key=` fix was necessary but the keys never arrived, so
   it changed nothing visible. Also added: `committed_lookup()` fallback to the
   repo's own `data/upstox_nse_equities.csv` (2,393 real keys, no network) —
   an empty lookup is **never** accepted as an answer now, it logs ERROR and
   `status()["lookup_source"]` says `committed_csv`. LESSON: a filter that
   silently yields 0 rows is the same bug class as a stale price served as live.
2. **✕ on the positions page DELETED trades.** `/positions/{t}/remove` popped
   the position and refunded ENTRY cash — no ClosedTrade, no exit price, no
   P&L. DIVISLAB vanished from the owner's record this way. **✕ is gone**;
   every row now has **SELL + a price box** → `eng._close(..., "OWNER_SELL")`
   → real Closed Trade. Blank box = live price; no live quote = the owner types
   their own fill (an exit is NEVER blocked for want of a quote). The old
   `/remove` route is a no-op redirect so a stale bookmark can't destroy a
   trade. `price_source` journaled (`live_quote` / `owner_typed`).
3. **The universe really was "alphbet wise".** `rotating_bucket` took a
   CONTIGUOUS slice of an alphabetically-sorted list, so each day's ~50-name
   scan was one letter-block (Z names waited ~48 days; ADANI*/BAJAJ*/TATA*
   all landed the same day). Now a **stride** `tickers[b::n_buckets]` — same
   determinism, same full coverage per cycle, but 20 distinct first letters per
   day instead of 2.
4. **"still u r making 30000 positions" = ½-Kelly, and they were right.**
   Measured at 20% vol: score 63 → **₹20,719** · 65 → ₹62,158 · 68 → ₹1.24L.
   Just above the 62 floor Kelly opens ₹20–60k trades while the owner's written
   order was **₹1.25 lakh each**. Kelly came from a question *I* asked; their
   number wins. **`sizing_mode` reverted to `"fixed"`** + boot migration
   (marker `fixed_size_order_2026_08_04`) to clear the stored half_kelly
   override. Kelly is still selectable in Settings and `trading/sizing.py` +
   `/lens` keep the maths. DO NOT switch sizing again without a written order.
5. **"u cant put ur fucking safty all the time"** — heard. No new gate was
   added in this batch. Where a number is genuinely missing the app asks the
   owner for it instead of refusing (see the SELL price box). Guard rails that
   block the owner from acting on their own book are the wrong default.
**Standing lesson from this round: I shipped a "prices are live now" fix (PR#31)
without ever verifying a single stock resolved to an instrument key. One
`to_instrument_keys(['TCS.NS'])` call would have shown `{}`. Verify the whole
chain end-to-end before telling the owner it works.**
**2026-08-04 late — THE TRUST TALK + THE OWNER'S OWN SYSTEM FINALLY WIRED.**
Owner: "how many filtration i was looking for but u are not even close…
i built wrong thing… u are able to execute only 30%". Counted instead of
arguing: their trigger_layer_v0_2 design (112 triggers / 8 gates / 8 clusters
/ pyramid map, in research/chityy_archive/ since the rescue) had **0 wired**.
The live engine is MY momentum design; THEIR system sat in a folder. Also
called out: Kelly (my idea) shipped same-day while their sheets waited —
asymmetry conceded. Billing complaint → pointed to claude.ai Settings→Usage +
support.claude.com (I can't see or fix billing). Then owner ordered **"wire
it"** → SHIPPED same session, 666 tests green:
- **`trading/triggers.py`** — registry PARSED FROM their CSV at import (code
  can never drift from the sheet; guard test re-reads the CSV and cross-checks
  all 112 ids/families/sides/weights). **79 OHLCV-ready triggers implemented**
  (compression 16, accumulation 16, reclaim 11 of 12, liquidity 10 of 12,
  distribution 20 of 20, collapse 6 of 14); **33 external-data triggers =
  DATA_NEEDED** (sector/RS panels, delivery %, F&O OI, news/governance flags)
  — listed with what unlocks them, never guessed. Their 8 gates run with THEIR
  exact logic strings (watch = compression>=2 & accumulation>=2 &
  distribution==0; entry = rise>=65 & fall<35 & dq>=80; fall-avoid =
  dist>=3 | fall>=55; collapse-block = score>=60 | T12W_0089; sector-confirm
  and event-overlay = DATA_NEEDED per their own sheet). Side scores =
  weighted-%-fired, **ASSUMED formula, labelled on-page** (their sheets define
  gates, not the score formula — calibration = their BT06). All unnumbered
  constants collected in `ASSUMED_CONSTANTS`. Insufficient lookback → None,
  lowers data_quality_score — thin data visible, never silently bearish.
- **Scope = DISPLAY + WATCHLIST ONLY, their own REQUIRES_15Y_BACKTEST rule.**
  GATE_RISE_ENTRY_01's verdict is SHOWN, never traded. Boundary test:
  engine/risk/orders may not import triggers. `scheduler.run_trigger_layer`
  runs post-09:20-scan (+ owner button), over scanned+held+picked (cap 80),
  stores `portfolio.trigger_watch` (persisted JSON+Mongo both directions),
  journals TRIGGER_WATCHLIST (once/name) and TRIGGER_LAYER_FAILED — a crash
  can't break the scan.
- **`/triggers` page** (⚡ sidebar): today's table (family counts, side
  scores, DQ%, gates-passed chips, WATCH highlight), any-stock search over
  the full universe, per-stock page = all 112 with formulas + fired/quiet/
  n-a/DATA_NEEDED per trigger and all 8 gate verdicts.
- **NEXT STEP WHEN ORDERED: the 15Y calibration backtest** — their
  reverse_backtest_design_v0_2.csv (BT01-BT08: event labels → hit matrix →
  lift vs baseline → false-positive audit → cluster scores → paper replay →
  proof ladder) against the repo's 191×12y panel. That run earns/denies each
  trigger a place in the money path and fixes the ASSUMED score formula.
**STANDING ORDER FROM THE TRUST TALK: no more self-initiated features. Build
only what the owner lists, their items first, exactly as specified.**
**2026-08-09 FULL-CORNER AUDIT (owner: "re run everything at each corner…
each file on git must be read without skipping a word"). Ten readers, 100%
line coverage of all non-test code/templates/docs/scripts, data verified
structurally, every HIGH finding re-verified by hand. FULL REPORT:
docs/AUDIT_2026-08-09.md — read it before touching anything. ONE fix shipped
(it broke the owner's ordered feature): the trigger layer had NEVER run on
real data — production columns are Open/High/Low/Close, the layer read
lowercase → KeyError on the first stock, TRIGGER_LAYER_FAILED daily, tests
passed on lowercase fixtures. Normalized at the boundary + production-shaped
fixtures + a real-loader end-to-end test. 669 tests. **MONEY-PATH FIXES A1-A5 SHIPPED same day (owner: "fix all 5 money path
first"), 681 tests:** ladder deleted (₹1.25L means ₹1.25L; IFR kept) ·
no-quote positions HOLD with EXIT_HELD_NO_QUOTE (kill-switch/drawdown/
square-off all skip unpriced names — no invented fills, ever) · scan()
evaluates the -2% halt on live held marks (KILL_SWITCH_SCAN) · owner
BUY/limit positions tagged owner_pick = decay never auto-sells them
(stop/target/trail/max-hold still protect; limit fills carry the real scan
score) · fabricated USFDA calendar EMPTIED + guessed future earnings dates
removed (DATA_NEEDED; blackout fires only on real fed dates). **A6-A11 ALSO FIXED same day (owner: "fix A6 to A11 too"), 691 tests:**
typo→fixed not kelly · preset-owned Settings edits STICK per-mode
(cfg.preset_overrides, re-applied in apply_mode_preset, RESET clears) ·
backtest mode thread-local (web backtest can't poison the 09:20 scan) ·
deploy/sector caps on CURRENT EQUITY not frozen 50L · scan entries fill at
LIVE quotes when broker answers (fill_source journaled; prev_close fallback)
· owner BUY/limit auto-size through sizing.position_notional_for (manual =
fixed ₹1.25L under every mode). **B1-B12 (lying screens) FIXED same day (owner: "fix B batch too"), 711
tests:** header regime label · Engine page PARKED truth + real ladder
0/0/20/40 + as-of stamp · Risk page truth pass + read-only governor ·
positions.csv live marks + price_source · kill-switch pushes to phone,
email honesty · daily-close-as-quote branch DELETED · IFR badge 22d window
· CAGR claim removed + dynamic ticket placeholders · zero-flow=50,
NaN=neutral, honest Top-drivers · expiry Tuesday (SEBI 2025) · benchmark
truncation disclosed · index-key map unified, N200 benchmark now possible
(first live backtest verifies). **C1-C12 + D1-D5 FIXED same day (owner: "fix C and D too"), 749 tests —
full detail in docs/AUDIT_2026-08-09.md status notes. Highlights: Mongo
retries + degraded-boot entry halt + reconnect heal · journal overflow
spills/replays (append-only survives outages) · rotation lock + MODE_LOCK ·
requirements PINNED + feedparser shipped (policy boost live first time) ·
regime feed failure keeps the brake (sticky, not fail-open) · corrupt state
quarantined · loss-halt persisted · APP_BRIEF/ALGO/boards truth-passed with
guard tests pinning docs to config · studies era-pinned (floor 55) so
committed evidence regenerates · Codex caught my /risk 500 in the B3 first
cut → fixed + render test; crash-brake SELLs named on phone; holiday-aware
expiries; N200 benchmark window-clipped; per-ticker quote provenance in the
export. A PARALLEL SESSION (01EuCaxUby…) pushed flow_status()/engine-page FII
display to main mid-day — merged in, kept both. FORMERLY-LISTED, NOW FIXED: Mongo
connects once, never retries (boot during blip = phantom fresh book, day's
trades vanish) · scan mutates shared cfg.mode while requests run · ^CNX200
benchmark structurally can't load (duplicate _INDEX_KEYS in data.py) ·
positions.csv exports entry price as current (P&L always 0) · engine page
says OCEAN ACTIVE while parked · risk page never truth-passed (corr 70/85,
fictional Metal blacklist, dead vol-tiers) · dashboard header prints regime
as "exits due" · index_daily_15y.csv 65d stale feeds backtest benchmark ·
feedparser missing = policy boost never ran · requirements unpinned ·
exit/breadth study results not regenerable at floor 62 · APP_BRIEF has 6
wrong money facts · ALGO_V1_V2 ¼-Kelly numbers irreproducible (lens runs ½)
· chityy master CSV is actually CLEAN (old "corrupt" note wrong).**
**E batch FIXED same day (owner: "fix E too"), 704 tests green (204 web +
500 trading):** orphans deleted (auto/autoswap/intraday/providers/macro/
stubs/supertrend/tranche_plan/partial_profit_pct/vol_adjusted_qty import)
· dd-governor 75% tier actually throttles (min(ifr,dd)) · brokerage knob
charges (×2 per round trip) · OCEAN_ERROR journaled (was except:pass) ·
backtest enforces the ticker blocklist + honest LIVE-GATE-GAP warning +
survivorship warning now conditional on PIT · trigger clusters honor
per-member _MIN_BARS; GATE_EXIT_WARN names its missing weak_rs leg ·
login throttled (1s/try, 5s at 10 fails; SB_LOGIN_DELAY_SEC=0 in tests) ·
session cookie Secure via env (RENDER/SESSION_SECURE) · boot SECURITY log
while OWNER_PWD unset · marks() lock-guarded · scheduler misfire grace
300s + coalesce · weekly top-up checks is_trading_day · SELL box 1-paise
step · M&M URL-encoded · scan bolds at real floor. E broke the suite
twice, both MY errors caught before push: hardcoded https_only=True
killed every TestClient login (85 failures — Secure cookie never returns
over http), and the 4×→5× copy sed also hit "text-4xl" CSS (phone-layout
guard caught it). Lesson: after sweeping edits, full suite from a clean
state dir BEFORE claiming done. Honest leftovers: page-level "live" badge
still means "any ticker live" (per-row price_source is the truth source);
ntfy topic still an open-publish credential; password env CLOSED same day
(owner set APP_PASSWORD in Render — accepted by auth.py, fallback dead).
**Uploads review 2026-08-09 (owner: "review and get Data extraction", 10
files/2 batches) — docs/UPLOADS_REVIEW_2026-08-09.md = verdict table.
EXTRACTED: Chityy REVERSE_PATTERN_PARAMETER_ENGINE v0.4 complete (14 CSVs
→ research/chityy_archive/reverse_pattern_v0_4/; sample data VERIFIED
exact vs committed panel — TCS 2241.7/2446.9 on 2026-06-03 real) · NSE
MWPL June-2026, 211 syms → data/fo_mwpl_2026-06.csv (REAL) · top-25 F&O
OI snapshot → data/fo_oi_top_contracts_2026-06-08.csv · ASHSTOCKS flaw
register (external build's honest self-audit) → research/external/.
REJECTED: FPI category positions SYNTHETIC (arithmetic-proved 30/20/10%
of MWPL — matches their own flaw F-003) · uploaded VIX csv real but a
subset of index_daily_15y.csv (verified ±0.01, fed nothing twice) · 3
backtest workbooks = narrative not evidence: IFR+FII alpha lives ENTIRELY
in the 2007-10 block (negative 2 of last 3 blocks — a DD-reduction
overlay, not a return edge), the VIX-ladder run has no trade ledger, and
the "DII cushion" conclusion CONTRADICTS its own table (+3.27% when DII
NOT buying vs +1.23% buying). NOTHING wired — data + records only.
Next-batch asks: FII derivatives-OI daily panel, full F&O bhavcopy,
VIX-run trade ledger.**
**DATA FETCH ROUND same day (owner: "fix all 5 and commit the kit / Fetch
the data as well, no placeholder / Why u cant fetch from API of Upstox"):
docs/DATA_FETCH_2026-08-09.md = the record. KEY FACT LEARNED: Upstox
HISTORICAL candles are PUBLIC — no token (verified live from the sandbox);
only live quotes need the daily login. V3 caps ~10y of daily candles per
request (16y+ = UDAPI1148) — the uploaded kit would have failed on EVERY
symbol; that was defect #6. Kit committed FIXED at research/tools/
fetch_kit/ (chunking, F&O UDiFF-vs-old naming fallback incl. the Apr-Jun
2024 election window, REAL Kite cross-check, CA yearly windows, TRI fetch,
genuinely fail-closed auditor + clean_intake quarantine step). FETCHED
same session, all verified: full-universe OHLCV 2,715 instruments
2000->2026-08-07 (0 fail; hard gates PASSED after cleaning: 1,822 echo
dups dropped, 721 conflict dates resolved by continuity rule, 743 rows
quarantined+committed as REJECTED_ROWS; ~103MB gz — NOT in git, zipped to
owner for release-asset upload) · nifty200_daily.csv 2004->now (benchmark
offline at last) · index_daily_15y.csv extended to 2026-08-07 (80 overlap
values checked, 0 disagree) · corporate_actions_2010_2026.csv 34,792 rows
· dated N50/200/500 constituents (forward PIT starts 2026-08-09) ·
fii_dii_net_recent.csv 30d (niftytrader, verified vs NSE; the Colab
script's "full history" claim was WRONG — 30d rolling window only) ·
fundamentals_n200 11,377 datapoints 200/200 (DIRECT Yahoo timeseries —
yfinance 1.5.2 statement wrapper returns EMPTY where the raw endpoint has
data; REL/TCS figures verified vs published). NOT fetchable from cloud
(tested, not assumed): nsearchives (delivery/F&O/PWOI — timeouts) and
niftyindices TRI POST (WAF serves HTML shell) — owner's home machine via
the kit is the path. FII/DII 2025-04->2026-06 hole still open (NSE monthly
reports, manual). Owner's Colab script reviewed, not committed — its two
good ideas absorbed into the kit. Nothing wired into the engine.**
**COLAB INTAKE same day (owner ran the script from Colab — its network
reaches nsearchives, ours can't): PWOI COMMITTED
(data/fo_participant_oi_2024_2026.csv, 641 days 2024-01→2026-08-07,
verified: NSE's own ±1-contract totals; 2024-06-04 FII election short
matches record — the REAL version of the synthetic FPI upload) ·
fundamentals statements zip committed (superset; RELIANCE Q1 identical to
our direct fetch — two paths agree) · corporate actions duplicate skipped ·
fii_dii csv was 1 day not history (niftytrader failed in Colab too; row 2
mislabeled DII) · DELIVERY % COMMITTED
(data/delivery_daily_2019_2026.csv.gz — 1,698 days 2019-06→2026-08-07,
3.48M EQ/BE/BZ rows, 52MB gz, biggest file in the tree; built from the
owner's release-asset zip, all 1,778 files hash-verified, keyed by
INTERNAL date which dropped 80 holiday echoes AND recovered 6 mislabeled
sessions incl. 3 Muhurats; raw zip stays release-only) · TRI failed in Colab too (absent from its own manifest) —
home machine via kit remains the path. Verdicts in
docs/DATA_FETCH_2026-08-09.md.**
**FII/DII HOLE CLOSED same evening — NSDL FPI archive answers our cloud IP,
no login (nseindia blocks us, fpi.nsdl.co.in doesn't).
scripts/fetch_fpi_nsdl_archive.py; fii_dii_cash.csv extended 2025-04-01 →
2026-08-06 (326 rows, FII only — NSDL DII archive is OTP-walled, DII
unconsumed anyway). TWO TRAPS in the source, fixed+documented: NSDL dates
are REPORT dates (previous trading day's activity — unshifted corr vs NSE
0.075, shifted 0.955) and a cumulative table repeats each date (last-match
parse wrote -2.79 lakh Cr YTD over a +418 Cr day; first-match-wins).
NSDL=settlement basis, ~800 Cr/day median gap vs NSE provisional — known,
documented, live engine still reads NSE API first. Remaining holes:
2024-09-09→2024-11-29 (SEBI era) and DII after 2025-03. TRI = the ONLY
still-unfetched item anywhere (niftyindices WAF; home machine or Colab
retry with hardened session).**
**SELECTION REGISTER v4 WIRED same day (owner uploaded
Selection_Register_v4_279_OwnBuild.xlsx — an ASH08-lineage register of 279
items — then ordered "focus rest from 279 … discuss and wire the rest").
732 tests green. What shipped:**
- Register committed: research/external/ (xlsx + extracted CSV). All 112
  TRG formulas verified VERBATIM vs our trigger_params CSV (zero drift).
  ~151 of 279 already lived in AM07 (mapped in register_v4.AM07_HAVE);
  the "numbers replaced formulas" worry checked out clean — only 4 loose
  rows, listed on-page.
- **trading/register_v4.py — DISPLAY/RESEARCH ONLY** (same boundary as
  triggers: engine/risk/orders never import it, test-enforced; registry
  parsed from the committed CSV at import so code can't drift from sheet).
  Computes ~90 of the 128 missing items from committed data: E-tags,
  B-07/H/W/X per-stock analytics (fundamentals via curated CSV +
  statements zip, shares-outstanding ASSUMED NI/EPS where needed,
  labelled), PX panic signatures (panel breadth/dispersion/tails;
  **dbi never defined in their files → PX-01/05 DATA_NEEDED**, tail cut
  -3% ASSUMED on-page), FII-01..07 stack LIVE off the new fii_dii_cash +
  PWOI panels (FII-04 index-fut net, FII-05 short-share q80, FII-07
  PCR-OI shock), VIX-02 spike freeze flag, S-12/13/14 coverage/effective-
  score telemetry. ~38 stay DATA_NEEDED with what unlocks each
  (shareholding, analyst, news, USDINR, options chain, etc.).
  **FII-08/09 wait on an OWNER PICK: their own sheet records TWO damage
  thresholds (static 0.213 vs rolling q90) — "pick one and hash it".**
- /register page (📇 sidebar): market view + any-stock search, U/S rows
  shown as their-build-vs-AM07 verdicts, every assumption on-page.
- BONUS BUG: /triggers search datalist held the CHARACTERS of
  "20MICRONS.NS" (load_full_universe()[0]) — fixed to the real 2,428
  names.
- TCS sanity: ROE 45.9%, Altman 10.5, Piotroski 5/9, 3Y CAGR -11.8%,
  RS 6M -27pp — all match the known story.
User's 8-step plan validated this session: all 8 now built (ticket +
parameter pages closed the last two). COMMUNICATION (user demanded,
binding): DISCUSS FIRST — no code until explicit yes; before any build,
one line on what it changes for their money; user reads git — never
dismiss their code reads, locate what they saw (ocean 5-line call-site
vs 207-line engines/ocean.py). User frustration peaked 2026-07-21; the
Upstox/Zerodha framing that landed: app = brain (decides), broker =
hands; go-live = 60-day proof + their word, then real orders small +
kill switch.

**2026-07-27 evening data decisions (owner):** full-universe OHLCV
CLOSED — "we dont need all... its there and enough for me" (current 191-
ticker panel + full_universe_2392.zip suffice for tests/param verification;
never re-raise) · F6 fresh CURRENT FII/DII data incoming via new release
ASSETS uploads — when they land: refresh data/fii_dii_cash.csv and never
ask again · ^CNX200 fetch verification is on Claude at the first live
backtest after deploy · monthly FII archive = 60+ months, gaps accepted ·
data/india_10y_bond_yield.csv committed (2016->2026-06, macro research,
not wired) · owner is moving more browser-only files into release ASSETS
(only assets are session-readable).

## Standing user rules
0. Merge authorization (owner, 2026-07-27, this session): Claude MAY merge
   its own PRs once CI is green — "u r also authorize for doing the merge".
1. Paper-only until explicit go-live. 2. No fake data — DATA_NEEDED.
3. Journal append-only. 4. Session end: update CLAUDE.md, commit, push
BOTH branches. 5. Propose → approve → adopt; review-only = no code.
6. Fetch extra data from Upstox API directly rather than asking. 7. Every
change traceable to a user ask; disclose deviations pre-commit; never
claim done without repo/render proof (audit 2026-07-16 is binding).

## Next steps
1. 60-day observation: weekly = read journal/closed-trades, verify scans
   ran (healthz + last_scan), report vs expectation band. NO engine
   changes during the window without user ask.
2. Parked: Ocean crash-guard design; full-universe OHLCV; AlgoVerse =
   possible live-execution rail if go-live ever comes (F&O-only today).
3. Lineage: session 01Nv6gRydiFC8aj8exkhyDNx built the app; this session
   (01EuCaxUby4fJoChwFQ5XEtV) = searches v1-v4, final set, fixes; full
   history in git log + docs/. Drive folders inventoried in
   docs/DRIVE_VERIFICATION_2026-07-18.md.
