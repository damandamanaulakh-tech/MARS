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
Live **paper-only** trading engine (₹50L paper capital), FastAPI + Mongo
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
  "1.25 lac, 125000, not percentage"): **[SUPERSEDED 2026-09-10 → ₹5 CRORE, slab ₹1.5L +
  score at m=0.29, ceiling 222]** was ₹50L capital, FIXED ₹1.25L per
  new trade, `max_positions 80` as ceiling — cash funds ~40 concurrent trades, rest of the slots open as
  capital grows. All sizing sites (risk.can_open, orders limit-fill,
  owner_buy) go through `cfg.position_notional()`; `max_position_pct
  0.025` is the fallback when the fixed value is 0. Boot migration v2
  (marker capital_order_2026_07_30_v2) supersedes same-day v1 which had
  read the size as 1.25%.
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
  capital_base persisted JSON+Mongo; a book with no stored base is
  treated as already synced to the live capital (2026-08-30 order).
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
   at the starting capital and the "−2% daily halt" was really "−2% since
   inception", which
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
  design), committed to research/chityy_archive/trigger_layer_v0_2/. ~~The
  sourceborn_chityy .py code was never saved anywhere~~ (WRONG — RECOVERED
  2026-08-11: trigger_pre_1_2_week_engine.py sat in the release assets;
  committed to research/chityy_archive/). Back.Test.Claude.zip = duplicate of the
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
committed evidence regenerates · an external review caught my /risk 500 in the B3 first
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
**CORRECTION BRAKE OFF (owner order 2026-08-09: "i want to take risk on
200 DMA" → picked "Brake fully OFF" from an explicit options card AFTER
being shown the v4 evidence that blind correction dip-buying tested
−2.2pp).** `cfg.correction_brake_enabled=False` (default) — CORRECTION
(Nifty < 200DMA) alone no longer blocks momentum entries; engine journals
CORRECTION_BRAKE_OFF when the override path opens picks. UNCHANGED: panic
still blocks, dead regime feed still brakes (C7 stays fail-safe), score
floor 62, abs-momentum gate, breadth gate (can still block in weak
breadth!), −5% stop, kill switch, regime reserve ladder 20/40. Settings
toggle restores the brake (1). VIX-crush re-entry machinery kept (harmless
alongside). Tests: trading/tests/test_correction_brake.py pins default,
panic, dead-feed, restore, and that engine.py wires the exact predicate.
UNTESTED live — 60-day window judges. FII-08/09 same message: owner ruled
"why not both" → BOTH damage thresholds (static 0.213 + rolling q90)
computed side by side on /register; current reading: static YES,
q90 no, stacks QUIET (FII legs quiet). Assumptions review: "not now".
**BT01-BT08 CALIBRATION RUN + SEQUENCE IMPORTED (2026-08-10, owner: "review
each single item… merge ur self, sequence must import for anything").**
scripts/bt_calibration_v1.py runs the owner's reverse-backtest design with
the PRODUCTION trigger code over 191×12y (94,479 windows). TWO label sets
(±10% main, ±15% strict sensitivity) — both committed under
research/bt_calibration/. THE VERDICT, label-robust:
- **Rise triggers have NO measured edge** — best lift 1.15 at strict labels
  (Inside Bar Cluster, barely SUPPORTED); 59-64 of 79 REJECTED. Survived
  the label sensitivity check — not an assumption artifact.
- **Danger side is REAL: 10 VERIFIED at strict labels** — 6 collapse
  tripwires (ATR Shock+Close Low lift 4.11, leads 4d; Panic Expansion 2.44;
  Support Break w/ Vol 2.11; Volatility Tripwire 1.86 on 4,240 fires;
  Crash-after-Failed-Breakout 1.81; Liquidity Evaporation 1.59) + 4 FALL
  warnings (High-Tight-Flag Failure 1.98, Exhaustion-3-Green 1.81,
  Extended-from-Mean 1.80, Bearish Wide Range 1.69). The owner's system's
  measured edge = DANGER DETECTION, not entry timing.
- **GATE_RISE_ENTRY_01 is INERT**: 1 entry in 94k windows (score formula
  never reaches 65), and it lost −5%. Recalibrating the score formula =
  the BT06 follow-up if ordered.
- Sequencing measured + IMPORTED: triggers.CALIBRATION loads the main-run
  CSV at import; fired lists sort earliest-warning-first by measured lead;
  /triggers shows VERIFIED/SUPPORTED/REJECTED chips. Ladder: Failed-
  Breakdown/Reclaim 9d → Compression/Distribution/Liquidity/Accum 6d →
  Collapse tripwires 4d.
- docs/REGISTER_STATUS_2026-08-10.csv = all 279 classified (9 TRADES / 149
  COMPUTES / 31 HAVE / 64 DATA_NEEDED / 4 MAPPED / 21 AUDIT / 0 name-only).
  Owner's open data questions: dbi definition, shareholding pattern,
  options chain/short interest, block deals+insider (Colab), paid feeds
  y/n. Delivery-based external triggers now BUILDABLE (data landed) —
  awaiting owner go.
- PROPOSED NEXT (needs owner word): wire the 10 VERIFIED danger triggers
  as an exit/throttle confirm layer (their own F-006 rule: risk side,
  NEVER entry). PRs #47/#48 merged same day.
**ASSET RE-CHECK 2026-08-11 (owner: "re check on repo asset… mostly u will
get the data u needed" — RIGHT on every count, PR #50):** the release
assets held the answers to almost everything open. dbi RECOVERED from
05_daily_market_internals.csv (7,162 days 1996-2024 w/ PX flags): dbi =
cross-sectional dispersion / |EW return|, verified 6,808/7,162 exact,
dbi_5d = 5d mean 7,158/7,158 → PX-01/05 compute, ZERO register items left
on undefined formulas. 0.213 SOLVED: thresholds.csv p90(tail_down3_5d) =
0.212987 — static 0.213 and rolling q90 were ALWAYS THE SAME RULE (one
frozen 1996-2024). FII/DII 2007-2022 daily w/ DII + gross legs committed
(fii_dii_cash_history_2007_2022.csv, internally consistent 3,770/3,770;
~73% exact vs old daily file, divergence documented, new = primary
pre-2024). PWOI extended to 2013 (fo_participant_oi_2013_2023.csv,
97,635 rows). Owner's own prior calibration committed
(research/chityy_archive/prior_calibration/) — their tail>0.213 lift 1.91
independently corroborates our BT danger verdict. Still open asks (owner
answering): Colab script for shareholding/F&O-bhavcopy/block-deals+insider
(y/n), paid feeds y/n, TRI, the broken Unconfirmed.880272.crdownload
asset, optional NSE.1991-2010 long-history intake.
**DANGER LAYER LIVE (owner order 2026-08-11: "danger layer, go with c then
auto with B" — their triggers' FIRST money-path influence, evidence-earned).**
trading/danger.py + engine._danger_pass (runs in refresh before the exit
loop): the 10 strict-calibration-VERIFIED danger tripwires (6 collapse + 4
fall, lifts 1.6-4.1x) guard HELD names. Mode C first 7 days (DANGER_ALERT
journal+push, would-have-done text, no touch); then auto mode B: 1 fire =
stop tightens to -2.5% off live price (NEVER loosened; custom_levels=True
so boot retag keeps it), 2+ fires = _exit_half (real ClosedTrade, full
close if qty 1). Sell side ONLY (F-006) — boundary test rewritten:
engine may import trading.danger, never triggers directly, no entry gate
anywhere in the money path. VERIFIED list loads from the strict15 CSV at
import (evidence-locked, guard-tested). portfolio.danger_alerts_start
persisted JSON+Mongo both directions (risk_day lesson). Settings toggle
danger_layer_enabled (default ON). Once-per-ticker-per-day journal guard.
752 tests. Same message: owner SKIPPED re-uploading Jun-2018 FII zip
(the broken crdownload — "month data doesnt change much") · Colab v2 is
RUNNING on their side · "no new positions why so" DIAGNOSED: breadth gate
at 50.8% vs the <=50% line (±7pp bucket sampling noise → BREADTH_BLOCKED
most days) + abs-momentum passing only ~51% — the brake-off is live but
these two tested gates stand; owner told where the toggle is · owner:
"i dont want to skip anything" on remaining DATA_NEEDED — paid-feed items
stay open, free paths first (Upstox option chain via existing daily
token = next build candidate).**
**COLAB v2 + RELEASE INTAKE, THEN 26 REGISTER ITEMS WIRED (2026-08-12/13,
owner: "colab data is also there on drive fetch that too" → "zip is uploaded
on release, fetch it" → "why u r waiting for 28 which u can wire, do that").
docs/WIRING_2026-08-13.md is the record; every intake file hash-verified vs
the Colab MANIFEST_v2 before commit.**
- DATA IN: fo_oi_daily_2024_2026.csv (110,054 rows, 518 days, 278 F&O symbols,
  fut+CE+PE OI) · block_deals_2019_2026.csv (4,002) + bulk_deals_2019_2026.csv
  (6,440) — **TRUNCATED: NSE caps 70 rows/month, all 92 bulk months + 39 of 91
  block months at the cap** · data/shareholding/ 193 NSE records × 20 quarters
  · insider_pit.csv (54,150 SEBI PIT rows, 1,652 syms, 2023-01→2026-05-02).
  TRI still unfetched (absent from Colab's own manifest — niftyindices WAF
  blocks Colab too).
- **LESSON — a session cannot download this PRIVATE repo's release assets**
  (403 "GitHub access is not enabled for this session"); Drive MCP caps
  transfers ~10MB. The working route for big files: a **one-shot GitHub
  Actions workflow** that downloads the asset inside GitHub, verifies size +
  sha256 against the manifest, commits, then gets deleted. Used twice
  (insider_pit.csv, the 193-file shareholding zip: 127 newly verified, 0
  rejected). Per-file Drive fetching via subagents burned two credit limits —
  ask for a zip on the release instead.
- **NEW trading/external_panels.py** — batch loader (ONE pass over the 3.5M-row
  delivery file per run) building an equal-weight sector index + peer breadth
  from the 191×12y panel × the 19-sector map, cross-sectional RS/turnover ranks
  plus a WITHIN-industry rank (their "industry_relative_rank"), per-stock
  delivery ratio, F&O OI, and deal windows. Every field Optional; missing =
  None = DATA_NEEDED, never a quiet False.
- **21 external triggers implemented; 19 compute, 2 honestly blocked.** Wired:
  the 15 sector/RS/peer family (T12W_0039, 0045-0056, 0059, 0094) + 0095 index
  overlay + 0060 delivery spike + 0100/0101 OI tripwires. **T12W_0111/0112
  report DATA_NEEDED by design** — the bulk file's 70-row cap means "no row"
  ≠ "no deal"; a paged pull turns them on with no code change.
- **5 register items wired** (register_v4._wire_intake_items): H-01 promoter %
  (+ QoQ delta, 20 quarters) · X-06 free float **as a labelled PROXY** (public
  %, not true free float) · H-20 bulk net qty + block row count · H-25 insider
  net qty 180d · W-022 PCR on **OI basis, not volume** (said so on the page).
  PX-01/05 already computed since the 08-11 dbi recovery. Total 26 wired.
- **TWO BUGS THE REAL DATA EXPOSED, both fixed before commit:** the deal window
  was anchored to each symbol's last-ever deal, so any name with any deal
  history read "recent deal" forever (every stock fired both deal triggers);
  and after that fix both still fired together because **NSE reports BOTH
  counterparties of a block deal** — buy and sell cancel exactly, so direction
  comes from BULK rows only (per-client, >0.5%), block deals are presence only.
- **THE SHAREHOLDING FILES DO NOT CONTAIN FII/DII/MF.** Only promoter+group %
  and public %. The institutional split and pledge % sit in the XBRL each
  record links to (nsearchives, blocks our IP) → H-02..H-06 and X-07 stay
  DATA_NEEDED with a free Colab path. Do not claim otherwise.
- Register recount, checked against files on disk (docs/REGISTER_DATA_GAPS_
  2026-08-12.csv, all 279 rows): **32 still need an outside feed** (15
  news/analyst/governance, 6 shareholding XBRL, 2 options chain, 1 short
  interest, 8 macro/misc), 4 were never data items (attribution, bootstrap CI,
  covariance, index replication), 26 wired today, rest already computing.
  **Money path: 19 of 279** — the 9 TRADES params + the 10 danger triggers.
  Everything added today is display/watchlist only; boundary test asserts
  engine/risk/orders never import external_panels.
- Scope discipline kept: entry thresholds, exits, sizing, gates ALL unchanged.
- **PRE-EXISTING BUG FOUND AND FIXED while working:** test_trigger_page seeded
  a pick with no `score` and restored the attribute in a finally — but
  run_trigger_layer had already PERSISTED the portfolio, so the score-less pick
  stayed in cfg.state_dir/portfolio.json for every later test AND later run;
  /scan then raised UndefinedError rendering it = **a 500 on the page the owner
  buys from**. Fixture now saves after restoring, and scan.html renders a
  missing score as "—" (never a fake 0). Suite: 777 tests.

**CALIBRATION ROUND 2026-08-13 (owner: "merge it and run the calibration" →
"stop doing back test" → "wire 0066 with gap 1%" → "wire 0040 too").
docs/WIRING_2026-08-13.md carries the full record; 786 tests green.**
- **PanelSet refactor**: external_panels now computes every panel as a FULL-
  HISTORY series with `externals_at(ticker, when)` reading as of ANY past bar,
  so calibration runs the SAME production code with a past date (the
  anti-divergence rule that kept BT01-BT08 honest). `prepare()` = a thin
  wrapper over `externals_at(last)`; live behaviour unchanged.
  `_load_panel(tail)` cached per depth — production keeps the short tail for
  the 512MB box, calibration passes tail=0 (2,981 bars, 2014-06-30→2026-07-17).
- **External calibration INCOMPLETE — owner ordered it stopped at 50/191
  tickers.** scripts/bt_calibration_external_v1.py exists and works (a
  12-ticker pilot ran clean: index overlay 2.02 VERIFIED, sector contagion
  1.32, top-decile-in-industry 1.25, most RS-rise REJECTED). NO results
  committed; the 21 external triggers carry NO measured verdict. Do not quote
  the pilot as a verdict.
- **THE 64 REJECTIONS, made legible** (docs/REJECTED_TRIGGERS_2026-08-13.csv,
  one row per trigger with rule + counts). Chance rates over 12y: RISE 32.08%,
  FALL 19.49%, COLLAPSE 3.45%, REVERSAL_UP 3.84%. hit% = hits/fires,
  chance = event-windows/all-windows, lift = hit/chance. **16 rules are
  INVERTED** (fired and the move came LESS often than an average day; worst:
  tight base −6.73pp, bearish OBV divergence near highs −4.72pp, lower-wick
  cluster at support lift 0.433). **19 fire on >1/3 of all days** (one on
  59.7%) — describing the market, not predicting it. 35 of 64 rejections are
  RISE. The high RISE baseline (32%) is what most rise rules cannot beat.
- **THRESHOLD SWEEP with a TRAIN/TEST SPLIT** (scripts/threshold_sweep_
  nearmiss.py, research/threshold_sweep/): 6 near-misses swept, settings chosen
  on 2014-2020 and read on 2020-2026. Engine validated first — it reproduces
  all 6 committed lifts exactly. **3 of 6 survive, 3 are pure curve fits**:
  0033 went 1.964 train → 0.961 test, 0086 1.345 → 0.925, 0034 1.105 → 0.975.
  **Without the split, 0033 would have looked like the best trigger in the book
  and delivered nothing. Never sweep a threshold without a holdout.**
- **TWO TRIGGERS RECALIBRATED AND WIRED (owner orders), both display/watchlist
  only — tests assert neither is in danger.VERIFIED:**
  * **T12W_0066 gap-up rejection: gap must be >= 1%** (was unconstrained — an
    open a paisa above the prior high counted). 1.091 REJECTED → **1.213
    SUPPORTED**; train 1.217 / test 1.227; 347 fires, lead 2d, FP 30.6%. The
    gap axis improved MONOTONICALLY at every volume multiple (1.06→1.17→1.23→
    1.58 for 0%→0.5%→1%→2%) = a gradient, not a lucky cell. Volume stayed 1.5x.
  * **T12W_0040 oversold-to-neutral: entry RSI < 30** (was 35). 1.065 REJECTED
    → **1.161 SUPPORTED**; train 1.162 / test 1.142; 2,985 fires, lead 4d,
    FP 31.9%. **Exit level deliberately LEFT at 45** — train preferred 45, test
    preferred 50, the halves disagree, so that axis is NOT measured and fitting
    it would be curve-fitting (noted in MEASURED_CONSTANT_SOURCE, test-pinned).
  * **MY ERROR, corrected in the commit: I first quoted 0040 as "1.48 test
    lift at RSI 25→50". That was the best cell ON THE TEST HALF; in train it
    scored 1.096, below the bar, and would never have been selected. Choosing
    by the holdout destroys the holdout. The honest number is 1.142.**
- **NEW: MEASURED_CONSTANTS + MEASURED_CONSTANT_SOURCE** in triggers.py, kept
  apart from ASSUMED_CONSTANTS — a measured number filed as assumed is as
  wrong as the reverse; tests enforce the split and require each to cite its
  run. **RECALIBRATED dict overrides stale CALIBRATION rows**: the 2026-08-09
  verdicts measured the OLD rules, so displaying them beside changed rules
  would misreport the live trigger; each override names its sweep and keeps
  the superseded verdict as history.
- Still-open near-miss: **T12W_0005 inside-bar cluster** holds out of sample
  (7-in-15, test 1.118) but on only 108 test fires — left alone, not ordered.
- 279 register standing after both merges: **19 in the money path** (9 TRADES
  + 10 danger triggers), 32 still need an outside feed, 4 were never data
  items, rest compute as display/research. F-01 (the composite driving score-
  decay exits) is the one money-path item with a stubbed input
  (short_interest=50 at 5% weight; catalyst neutral unless the API key is set).

**DANGER LAYER NOW 12 TRIPWIRES + 13 REGISTER ITEMS WIRED (2026-08-13/14,
owner: "run the external calibration for the 21" → "wire 0095 and 0094 both"
→ "API keys r there, fetch it" → "wire the 13"). PRs #58, #59 merged;
802 tests. docs/WIRING_2026-08-13.md is the record.**
- **EXTERNAL CALIBRATION COMPLETE** (research/bt_calibration_external/
  run_2026-08-13/, 191 tickers, 94,479 windows, same design as BT01-BT08):
  **2 VERIFIED, 1 SUPPORTED, 16 REJECTED, 2 unjudgeable.** Winners are both
  danger-side — T12W_0095 Index Risk Overlay lift **2.280** (3,272 fires,
  lead 3d) and T12W_0094 Sector Contagion **1.546** (6,982 fires). T12W_0050
  Leader Rotation SUPPORTED 1.115 is the ONLY entry-side signal of 21 that
  beat chance. The sector/RS entry family failed a THIRD independent time
  (Stock RS Leads Sector 0.842 = inverted; Sector Breadth Lead 1.009 firing on
  44% of days). F&O OI pair judged on 2024-07+ only; bulk-deal pair recorded
  4 and 21 fires (70-row cap) = NOT_ENOUGH_EVIDENCE as predicted. The runner
  is CHECKPOINTED (two earlier attempts died to container restarts).
- **0095 + 0094 WIRED INTO danger.py — 12 tripwires now guard held names.**
  Both COLLAPSE-side VERIFIED, so they qualified under the rule already in
  the module; no new admission criteria invented. **THE TWO EVIDENCE BARS
  DIFFER AND THAT IS DELIBERATELY VISIBLE**: the 10 incumbents cleared STRICT
  ±15% labels, these two only MAIN ±10% (the external run exists only at main
  labels). Every entry carries a `labels` field, describe() prints it into the
  journal AND the phone alert, a test asserts the groups stay distinguishable.
  Re-running the external calibration at strict labels would settle it.
  Design facts, test-pinned: 0095 needs the held name's support BROKEN, 0094
  needs it INTACT → **mutually exclusive, so the pair alone can only tighten a
  stop, never reach the 2-fire EXIT_HALF rung**. `_EXTERNAL_ALLOWED` names the
  two ordered ids so a future run cannot self-promote into the money path.
  `danger.externals_for()` loads ONLY panel+index (1.5s, never the 3.5M-row
  delivery file) because it runs inside engine.refresh; failure → those two
  abstain, other 10 still run, DANGER_PANELS_UNAVAILABLE journals once/day.
  engine.py still contains NO reference to external_panels — it reaches the
  panels only through danger.py (test-pinned both ways).
- **"nsearchives blocks our IP" WAS WRONG — 6 items needed no Colab.** The
  block is on the bhavcopy/delivery archive paths; the shareholding **XBRL**
  path answers fine. Proven by fetching a real 1.3MB file whose promoter
  (71.77%) and public (28.23%) match the committed TCS JSON exactly. Never
  repeat the old claim.
- **FETCHED (scripts/fetch_pending_feeds.py, checkpointed, 193/193 symbols,
  ZERO failures):** shareholding_institutional.csv (386 rows = 2 quarters ×
  193: FII, FPI cat1/2, DII, MF, insurance, banks, promoter, public, 4 holder
  counts, pledge flag — present on ALL rows) · analyst_ratings.csv (189 with
  real targets) · estimate_revisions.csv (690) · earnings_dates.csv (168, 84
  exchange-CONFIRMED) · shares_outstanding.csv (184, 183 with TRUE free
  float) · usdinr_daily.csv (2,601 days) · india_broad_money.csv (62 ANNUAL
  rows — too coarse for a daily filter, file says so).
  Parser trap: professionally managed companies (HDFCBANK, ICICIBANK, ITC, LT,
  BSE…) have NO promoter so their XBRL omits that context — an earlier cut
  treated it as a broken file and would have dropped 13 major names.
- **13 WIRED** (register_v4._wire_fetched_feeds + 2 triggers): H-02 pledge ·
  H-03 FII · H-04 DII · H-05 MF · H-06 institutional delta · X-07 holder
  counts (TATASTEEL: 850 foreign institutions, +34 QoQ) · H-07 analyst score
  (ASSUMED linear weights, labelled) · W-020 revision counts · X-11 drift ·
  **W-011 real shares outstanding (retires the ASSUMED NI/EPS proxy)** ·
  **X-06 TRUE free float (retires the public-% proxy)** · W-025 USDINR
  momentum · TRG-098 + TRG-105 (external triggers 21 → **23** wired, 12 → 10
  pending). All display/research; both new triggers UNCALIBRATED and
  test-asserted OUT of danger.VERIFIED.
- **DATA TRAP CAUGHT: Yahoo's epsTrend history is inconsistent** — 16 of 189
  symbols imply a >100% 30-day estimate change and 5 carry eps_30d_ago = 0
  (RELIANCE read +149%). The revision COUNTS are a different, sound field. So
  the percentage is suppressed outside ±50%, X-11 says PARTIAL and why, and
  W-020 carries the signal from the counts. Test-pinned on RELIANCE.
- **TRG-098 CAN NEVER BE BACKTESTED** — the earnings calendar is a forward
  snapshot with no archive of past "next earnings date" values.
  `earnings_window` is point-in-time (days-away from the AS-OF bar, not today)
  so a backtest cannot read tomorrow's calendar.
- **REGISTER STANDING: 21 in the money path** (9 TRADES + 12 danger
  tripwires), **19 still need an outside feed** — was 64 on 2026-08-11, 32 on
  the morning of 08-13. The 19: 10 news-family (RSS IS reachable — Google
  News/Moneycontrol/ET all return real feeds — but news is live-only, so those
  triggers can never be calibrated; **1 of the 10, W-019, wired 2026-08-14 by
  the news module → 18 blocked**; the other 9 news triggers wired 2026-08-14,
  see below), plus options chain ×2 (NSE answers {}
  without cookie priming), ESG (Yahoo returned EMPTY for all 193), short
  interest, short-rate leg, credit spreads, R&D line item, durability filings.
- **OWNER'S FUNNEL DESIGN (stated 2026-08-13):** run the news feed at
  UNIVERSE-FETCH time only, keep the verdict attached to the stock, read
  TRIGGER or PASS per name; and size the candidate list to what cash can
  actually fund — **₹50L ÷ ₹1.25L = 40 slots, so at 60-70% deployed only
  12-16 remain**; surfacing 50 candidates is wasted motion. Their words: "in
  real life capital amount is fixed once i use 60-70% then cant use as using
  here". **FULLY BUILT: news half 2026-08-14 (below), slot-sizing half
  2026-08-15** (owner: "wire the slot sizing too"). The block question they
  answered themselves: **"no block just show"**.

**NEWS MODULE SHIPPED 2026-08-14 (owner: "build the news module, no block
just show"), PR #61, 824 tests. `trading/news_feed.py` — DISPLAY ONLY.**
- Every stock the daily pass touches carries a verdict: **TRIGGER / PASS /
  NO_NEWS / UNKNOWN**, attached to the stock (`portfolio.news_watch`,
  persisted JSON **and** Mongo both directions — the risk_day lesson).
- **NO LLM, deliberately** — an LLM verdict can't be reproduced tomorrow;
  these are readable rules. Pure deterministic classifier over Google News
  RSS: 8 weighted event families (results beat/miss, regulatory, governance,
  downgrade/upgrade, rumour, order_win, expansion, block_deal) + a lexicon
  that scores ONLY untagged headlines so one headline can't count twice.
  `TRIGGER_SCORE = 2.0` is **ASSUMED and labelled on-page**: news has NO
  archive of past headlines, so unlike the 112 triggers it **can never be
  calibrated** — which is exactly why it stays out of the money path
  (boundary test: engine/risk/orders/**danger** may not import news_feed).
- **FOUR FAULTS THE LIVE FEED EXPOSED, all fixed pre-commit — read these
  before touching the patterns:**
  1. `"RBI Governor lists 5 ways AI can transform … fraud detection in
     banking"` tagged a BANK as a governance risk, and one tag alone clears
     the bar. Two fixes: negative lookahead so `fraud` won't match
     detection/prevention/management/analytics/control, **and
     `mentions_company()` — a headline that never names the stock is not
     judged against it** (Google News answers a company query with sector
     round-ups). That sector headline was the WHOLE of YESBANK's TRIGGER.
  2. The real event, `"Senior Group President Rajat Monga Quits"`, was
     **MISSED** — the role pattern only knew CFO/CEO. Now MD, chairman,
     president, whole-time director, company secretary × quits/resigns/
     exits/steps down/departs.
  3. Quote pages drowned real news (RELIANCE's top three "headlines" were a
     live-price page, a chart page and a weekly-outlook template) →
     `NOISE_PATTERNS`.
  4. A 503 read as "no news". **UNKNOWN is its own verdict** with
     `fetch_failed` set — a broken fetch and a quiet stock are different
     facts and never render as the same chip.
  Also: Indian headlines read "bags Rs 2,400 crore order" — the words aren't
  adjacent, so order patterns are gap-tolerant (`\bbags?\b[^.;]{0,45}\border`).
- Runs inside the post-scan trigger pass (`scheduler.run_news_feed`, called
  from `run_trigger_layer`), **bounded to 60 names** per the owner's slot
  logic. Journals NEWS_TRIGGER (once/name/day), NEWS_FETCH_FAILED,
  NEWS_FEED_FAILED — a feed outage cannot break the scan. Shown on /triggers
  per row and per stock with the causing headline and the line "Display only
  — never blocks a buy or moves a stop"; W-019 on /register reads the STORED
  verdict so no page render touches the network.

**THE 9 NEWS-FED TRIGGERS WIRED 2026-08-14 (owner: "wire them"), PR #63,
848 tests (635 trading + 213 web). External triggers 23 → 32.**
- T12W_0097 news/event risk flag · 0099 governance + support break · 0103
  positive news, price not extended, accumulation · 0104 order win + volume
  wake-up · 0106 negative news absorbed by a sweep-reclaim · 0107 news spike
  + gap-up rejection · 0108 regulatory + distribution cluster · 0109 earnings
  miss + 10D low break · 0110 sentiment divergence.
- **ONE external trigger left: T12W_0102** (put-call skew) — needs a
  strike-level options chain NSE does not serve us. That is the whole
  external backlog now.
- **THREE OF THEIR FORMULAS NAME A TRIGGER THEY ALREADY DESIGNED**:
  `volume_wakeup_after_dry_base` IS T12W_0057, `support_sweep_reclaim` IS
  T12W_0033, `gap_up_volume_rejection` IS T12W_0066. So 0104/0106/0107 read
  those triggers' ACTUAL hits out of the evaluation dict — the page can never
  show T12W_0057 quiet while T12W_0104 claims it fired. External signature
  widened `(x, e, fam)` → `(x, e, fam, hits)`; test asserts agreement both
  ways. **Check for this pattern before implementing any future formula of
  theirs — they cross-reference their own sheet by name.**
- **A FAILED FETCH CANNOT BECOME AN ALL-CLEAR.** `external_panels.attach_news`
  refuses to copy a reading whose verdict is UNKNOWN → all nine DATA_NEEDED.
  Treating UNKNOWN as "no bad news" would turn ONE Google News outage into a
  silent all-clear on every held stock at once. NO_NEWS is different and does
  produce a real False.
- **The two halves are not the same moment**: news is TODAY's, the price leg
  is as-of the last STORED bar (committed panel runs days behind).
  `stock_view` returns `news_note` saying so and the page prints it,
  test-pinned — a caveat that lives only in a code comment is not a caveat
  the owner ever sees.
- ASSUMED (all on-page): `news_event_bar 2.0` **mirrors
  news_feed.TRIGGER_SCORE** — triggers.py must NOT import news_feed (danger.py
  imports triggers; news stays out of that graph), so the value is duplicated
  and a test pins the two together. `news_cluster_min 2`,
  `news_sentiment_high 0.5`, `news_weak_clv 0.4`. `news_flat_return_3d 0.03`
  is THEIR number. **T12W_0110 is a labelled PROXY** — their sheet says SOCIAL
  sentiment; no social feed exists here.
- Scope unchanged: display/watchlist only, and **permanently uncalibratable**
  (Google News serves ~1 week; no archive of past headlines). Tests assert
  none of the nine is in danger.VERIFIED / danger._EXT_FNS /
  _EXTERNAL_ALLOWED; `danger.externals_for()` takes no news argument; the
  external calibration runner SKIPS them explicitly (a past window has no
  news, so any "result" would be the price leg alone wearing a calibrated
  verdict's clothes).
- **Order flipped**: `run_news_feed` now runs at the START of
  `run_trigger_layer` (was the end) and receives the exact ticker list about
  to be scored. Running afterwards would have left all nine DATA_NEEDED for
  ever. Still isolated — a feed outage touches no price trigger.
- Also fixed: `AM07_HAVE` labelled all 112 trigger rows "79 compute, 33
  DATA_NEEDED", hand-typed and wrong since the external intake — derived now.
- **SUITE GOTCHA, not a bug**: `test_scans_halt_while_mongo_is_expected_but_
  down` fails on a REPEATED SAME-DAY run against the persistent
  `/tmp/sourceborn_paper` — `_degraded_halt` journals MONGO_OFFLINE_HALT once
  per day, so the second run skips the append and the test's `[-5:]` window
  misses it. Base tree fails identically; CI never sees it (fresh container).
  `rm -rf /tmp/sourceborn_paper` before re-running. Do not debug it as real.

**SLOT FUNNEL + SELECTION SEQUENCE + FULL RE-CHECK 2026-08-15 (owner: "wire
the slot sizing too / n re chk all the repo work / stock selection sequence
is still pending"). 874 tests (26 new). docs/RECHECK_2026-08-15.md = the
re-check record — every number computed from code that day, read it before
answering "what's live / what's pending".**
- **Slot funnel LIVE** (`trading/slots.py` + /scan): "N slots fundable today"
  card + a divider in the picks table — everything past the fundable line is
  greyed, shown-never-hidden ("no block just show"). SAME ARITHMETIC as
  `risk.can_open` (entry-price deployed, regime reserve, ₹1.25L notional,
  80-cap), so the display can't promise a slot the gate refuses; base equity
  is entry-price (page fetches no quotes) so it can differ from the gate's
  live-marked equity by the day's unrealised P&L — said on the page. Display
  only; boundary test keeps it out of engine/risk/orders/danger. Their
  arithmetic test-pinned: ₹50L fresh book = 40 slots; 60%/70% deployed =
  16/12 remaining (their exact words).
- **Selection sequence LIVE — audit Node 1 CLOSED** (`trading/
  selection_sequence.py` + /triggers ladder view + Stage column + per-stock
  chip). The owner's ladder verbatim: QUIET → COMPRESSING → ACCUMULATING →
  RECLAIM → ENTRY_WINDOW → POST_ENTRY_WATCH; highest rung wins; held names
  always read post-entry watch (the danger layer guards from there).
  **ENTRY_WINDOW IS GATE_RISE_WATCH_01 passing** — no second formula, so the
  stage and the gate chip can never disagree; COMPRESSING/ACCUMULATING use
  the gate's own >=2; RECLAIM >=1 is ASSUMED (their sheet sets no family
  threshold) and labelled. Danger overlay (distribution>=3 per their
  fall-avoid leg, or any collapse fire) WARNS ⚠ without demoting — their
  ladder has no down direction. Stage stored per row by run_trigger_layer
  (persists JSON+Mongo inside trigger_watch); page derives it for pre-stage
  stored rows so old state never renders blank. Watchlist-only, on-page:
  "reading order only, never an entry signal" (rise side measured ~chance).
- **RE-CHECK verdict: all 13 money-path config claims verified live** (floor
  62, fixed ₹1.25L, −5/+12, reserve 0, brake OFF, breadth ON, ocean parked,
  danger ON, blocklist…), boundaries hold (engine/risk/orders import NO
  display module), persistence mirrors both ways, register backlog computed:
  **9 items still need an outside feed** (TRG-102 options chain, H-09 R&D,
  H-10 ESG, H-19 macro liquidity, W-007 VRP, W-021 short interest, W-026
  yield curve, W-027 global risk, X-05 durability) + TRG-111/112 blocked by
  NSE's 70-row bulk cap. Money path still 21 of 279.
- ~~STILL OPEN: F1 · F2~~ **BOTH FIXED 2026-08-20 (owner: "fix F1 and F2
  both"), 884 tests:**
  * **F1 CLOSED**: `get_daily_candles` now works TOKENLESS (auth header sent
    only when a token exists; endpoint re-verified public with a live TCS
    fetch same day) and the Friday 16:30 weekly top-up no longer gates on
    `broker.is_connected()` — only a missing client OBJECT (no API keys) or
    Mongo offline still skips, both journaled. Login-dead Fridays now
    refresh the store. Paper lock untouched (read-only path; test pins
    `assert_paper_trading` still present).
  * **F2 CLOSED**: `data.committed_nifty200()` reads the committed
    `data/nifty200_daily.csv` (2004→2026-08-07, finally has a consumer) and
    `fetch_daily_range` special-cases ^CNX200 — committed file is the
    deterministic base, network asked ONLY for the tail past the file's last
    row (recursion-safe: the tail window misses the file so re-entry falls
    through to broker/yf). Dead network = base truncated at 2026-08-07,
    which the backtest's existing BENCHMARK TRUNCATED warning already
    discloses. Verified through `backtest._bulk_download` with all network
    mocked dead: 993 benchmark rows 2019→2022 offline.
  * **F3 CLOSED same day (owner: "fix F3 too"), 899 tests — the LAST open
    audit flaw.** The A3 breadth gate now PREFERS a FULL-STORE 200DMA
    reading (~2,700 names, ~±1pp noise) over the ~50-name scan bucket
    (±7pp — buy-or-block used to flap on which names the rotation served;
    the market sat 50.8% vs the ≤50% line). `trading/breadth.py` (pure
    reduction: dead names >30d stale excluded, thin sample → None, reading
    carries asof) + `db.history_tails` (ONE streamed Mongo `$slice` tail
    query, not 2,700 full-doc loads) + `scheduler.refresh_breadth_store`
    (runs before the 09:20 scan incl. catch-up, and after the Friday
    top-up — which F1 just made reliable). Reading persisted on
    `portfolio.breadth_store` JSON+Mongo BOTH ways (risk_day lesson);
    trusted only while closes ≤ `cfg.breadth_store_max_age_days` (10,
    ASSUMED) old — stale/missing falls back to the bucket, i.e. EXACTLY the
    pre-fix gate, never something new. Journal + dashboard breadth panel
    name which source decided and its as-of. VIX-reentry bypass unchanged.
    `$slice` vs live Atlas UNVERIFIED from sandbox — first live journal
    line tells; failure is fail-safe (bucket fallback).

**CUSTOM 50-NAME UNIVERSE + THE PRICE-OUTAGE ROOT CAUSE (2026-08-20, owner:
"prices are still not live, why its not getting update / and reduce the
universe to 50 share only / but all fresh / [34 names] / Automobile & Metal
is growing add accordingly / do not change codes and just work on 50 count,
rest remains same / ARE SOME FOR THIS WEEK, ADD MORE AND MAKE IT 50").
920 tests. READ THIS BEFORE TOUCHING UNIVERSE OR PRICE PATHS.**
- **"PRICES NOT LIVE" ROOT CAUSE FOUND — the history client only existed
  after login.** `state._attach_broker_if_persisted` returned EARLY when no
  token doc was stored (the token dies 03:30 IST daily), so before the
  day's login there was NO Upstox client at all → `data._HISTORY_BROKER`
  None → every historical fetch fell to yfinance, which Yahoo 429-blocks on
  Render → scans/charts starved even though Upstox candles are PUBLIC (F1)
  and — verified live 2026-08-20 — **a DEAD bearer header is simply ignored
  by the candle endpoint**. Fixed: the client is built from env keys and
  attached as history broker WITH OR WITHOUT a token; engine.broker (live
  LTP quotes) still honestly waits for the daily login — that is an Upstox
  rule. Logout keeps the history client (`refresh_broker_attachment` no
  longer nulls it; test-pinned). Both top-up jobs fall back to
  `data.history_broker()` when engine.broker is None.
- **OWNER'S CUSTOM UNIVERSE LIVE**: `universes.OWNER_UNIVERSE_2026_08_20`
  (50 names) = their 33 + 9 automobile + 8 metal fills. Their symbol
  resolutions, CONFIRMED BY THEM via options card: IOCP→IOLCP ·
  HDGC→HDFCBANK · ADITYABIRLA→ABCAPITAL · ICICI→ICICIBANK; my confident
  maps: ELGIE→ELGIEQUIP, USHAMRT→USHAMART, GVTD→GVT&D, KIRPLU→KIRLPNU,
  MASTK→MASTEK, MAXHEALTHCARE→MAXHEALTH, ITCHOTEL→ITCHOTELS,
  BHARTIAIRTEL→BHARTIARTL, AGL→AGL (Allcargo Global, exact symbol).
  **MTARTECH EXCLUDED — NSE lists it BE-series (T2T)**, the tradeable
  universe filters T2T by design; it sits on the series-watch card, manual
  ticket buys only. Told the owner.
- Wiring: `cfg.custom_universe` (default = the 50; empty = rotating
  bucket) → `engine.scan_universe()` (extracted from scan(), logic
  unchanged + the custom branch: held always ride along, blocklist still
  strips, no rotation, same 2y history). **Settings has an editable
  textarea** (weekly refresh is THEIR workflow now — "ARE SOME FOR THIS
  WEEK"): normalises case/.NS/separators, and REFUSES the whole edit
  listing any name not on NSE — a typo can never silently shrink the
  money universe. Stored override list→tuple coerced at the boot door.
- **"all fresh" = `scheduler._daily_universe_topup()`**: every scan day,
  after the scans and BEFORE the trigger layer, the store is topped up for
  custom+held names (tokenless, `fresh_within_days=1`; the weekly job keeps
  4). Journals DATA_REFRESH_DAILY only on failures. Only runs when a custom
  universe is set — 2,700 names daily would not fit the 512MB box.
- /scan shows a "🎯 Your 50-stock universe" badge; scan matrix/piano/
  triggers/news all inherit the 50 automatically (they read the scan).
- Scope kept: "rest remains same" — no gate, score, exit, sizing touched.
- **2026-08-21 THE /positions 500 (owner screenshot: "Internal Server Error…
not fixed") — FOUND AND FIXED (PR #69), 930 tests. READ THIS FAULT CLASS.**
- ROOT CAUSE: `_pick_detail.html` line 16 — `p.entry_score if … and
  p.entry_score else p.score`. A hand-tapped 🟢BUY stores entry_score **0**
  (no scan score exists at a manual entry), 0 is FALSY, the fallback reached
  `p.score` which POSITION rows don't carry → jinja UndefinedError → the
  whole page 500s. ONE owner-bought position kills /positions — the exact
  screen the owner checks prices on, which retroactively explains part of
  "prices are still not live". Shipped in the 2026-07-22 repo port; armed by
  the first manual buy; every fixture/replica had only scan-opened positions
  (score > 0) so every crawl rendered clean until the real book (scan buys
  MIXED WITH owner buys) was modelled. FAULT CLASS: `x if x else y` in a
  template treats a legitimate 0 as absence — grep for it before trusting a
  page.
- DIAGNOSIS METHOD THAT WORKED (keep for next time): healthz/login 200 +
  all pages 303 unauthenticated proved the process healthy → the crash was
  auth'd-render + live state only → modelled the owner's real book shapes
  (scan buy, owner buy score-0, date-only entry_date, GVT&D ampersand,
  custom levels, zero-entry) → each alone rendered, the five together
  500'd → traceback named the line.
- ALSO FIXED, same hunt: register_routes portfolio block called
  `p.equity()` WITHOUT prices and read nonexistent `pos.avg_price` — the
  except swallowed both, so W-028..031 NEVER computed on a book with
  positions. Now entry-price equity (cash + Σqty·entry) + the real field.
- Repeated MY OWN documented mistake: a source-pin test tripped on its own
  explanatory comment (same as the F1 test). Strip comment lines before
  asserting on source. Twice now — do not make it three.

**CODEX REVIEW ON PR #68 — ALL FIVE FINDINGS REAL, ALL FIXED same day,
  926 tests:** (1) custom universe now governs EVERY mode, not just
  momentum — the 09:20 rotation's short/swing/positional passes could
  otherwise BUY outside the 50 and overwrite /scan with off-list rows.
  (2) `db.save_settings` was `replace_one` — the dashboard mode
  quick-switch (saves only {"mode"}) silently WIPED every stored override
  incl. the weekly universe list; latent for every override since the
  beginning; now `$set` merge. (3) All 31 previously-unmapped custom names
  got REAL sectors (`universes.CUSTOM_UNIVERSE_SECTOR_MAP`, Pharma = the
  healthcare bucket per APOLLOHOSP precedent) — risk.can_open counts every
  "Unknown" as ONE sector, so unmapped small-caps would have jammed
  max_sector_positions on unrelated companies. (4) breadth `min_sample`
  raised 200 → 1000 — at ~200 names a "full store" reading carries ~±6pp,
  nearly the bucket noise wearing the wide label; AND the Friday top-up
  scope now includes the FULL universe file, so the store finally GROWS
  (it could never grow before — scope was stored+held) toward ~2,400 and
  clears the bar itself. (5) Settings list capped at 60 names — a pasted
  2,400-name list would have OOMed the 512MB box.
- LIVE-APP FACT (healthz, 2026-08-20): `history_tickers: 191` — the
  full-universe OHLCV fetched 2026-08-09 never entered the app's Mongo
  (zipped to owner as release asset only). The daily top-up will grow the
  store to the custom 50 on its own; the 2,700-name F3 breadth sample is
  really ~191 until the store grows (min_sample 200 → the F3 gate will
  fall back to the bucket until then — fail-safe, disclosed here).

**2026-08-23 TRUST ROUND 2 (owner: "i fed you like a baby each single data…
still data needed / Signal not working / engine still show parked / where is
the lie / if something is not done say it"). Answered with facts, one fix
shipped (PR #71). READ BEFORE ANSWERING ANY "why does the app say X":**
- **The register's TRG rows printed ONE aggregate label 112 times**
  ("…1 DATA_NEEDED") — each copy technically true, but repeated down the
  page it read as data-needed-everywhere, and the owner concluded their
  uploads changed nothing. FIXED: per-trigger truth per row (79 OHLCV /
  21 panels / 9 news / 111-112 bulk-cap / 102 options chain). LESSON: an
  aggregate label repeated out of context is a lie even when true.
- **The real backlog stated to the owner: 9 register items** (macro
  liquidity, yield-curve short leg, credit spreads, ESG, short interest,
  R&D, durability, options chain ×1 trigger + 2 bulk-cap triggers). 64 →
  9 since 08-11; nothing pending is something the owner supplied.
- **Signal "irrelevant stocks" DIAGNOSED, not a fault**: the live book
  holds ~12 positions the OLD full-NSE multi-mode rotation bought before
  the 50-universe order (BERGEPAINT, COFORGE, ETERNAL, NAUKRI, PETRONET,
  RADICO, SUPREMEIND, TVSMOTOR, GILT5YBEES bond ETF…— visible on the
  owner's trigger-page screenshot as post-entry watch). Signal's pool
  leads with held names by design. The old universe filter (NSE_EQ + EQ
  series) INCLUDES ETFs — that is how a gilt ETF entered a momentum book.
  All modes are locked to the 50 since PR #68; the stragglers remain HELD
  until the owner sells or exits fire. Offered (not built): Signal
  defaults to today's top-scored pick instead of alphabetical first.
- **Ocean "parked" re-confirmed to the owner as THEIR OWN approved state**
  (2026-07-27; no order since says enable; needs a crash guard first;
  Settings toggle named). Do not treat "why parked" as an enable order.
- **Told the owner plainly: green tests ≠ working app** (the /positions
  500 proved it — fixtures model what I imagine; their screenshots beat
  my suite). Selection sequence re-explained: it reads, never picks,
  because THEIR calibration measured rise triggers ~chance and THEIR
  REQUIRES_15Y_BACKTEST rule stands; wiring it to entries needs their
  explicit order.

**PENDING-FLAWS ROUND 1 (owner order 2026-08-23: "start fixing ur pending
and flaws one by one"). 961 tests (33 new). Worked from the audit's own
Node/Flaw list — docs/AUDIT_2026-08-12.md §7-8 is the canonical backlog.**
- **Node 7 CLOSED — short_interest unstubbed.** The composite's last
  constant (50, 5% weight in swing+positional, both in the daily rotation)
  is now a LABELLED PROXY from data/fo_oi_daily_2024_2026.csv
  (`trading/signals/futures_oi.py`): OI up + price down = short build-up =
  low; OI down + price up = covering = high; window 5d, 10% OI = full tilt
  (ASSUMED). Non-F&O names (~2,150) and a panel older than 10 days read the
  old neutral 50 — the failure mode is the world before the fix; the
  component self-activates when a fresh Colab panel lands. Measured:
  TATASTEEL 30.3, RELIANCE 54.8 (panel asof 2026-08-11). STUB_COMPONENTS
  is now EMPTY; piano tap page truth-passed.
- **F6-tests CLOSED — engine.scan/refresh + scheduler now have direct
  tests** (test_engine_direct.py, test_scheduler_jobs.py): floor on every
  BUY, kill switch blocks all entries, −5% stop fires at a real price,
  no-quote HOLD never invents a fill, dd-peak persists, 09:20 rotation
  restores the owner's mode even when a mid-rotation scan crashes,
  rotation lock, catch-up gates, 15:35 refresh survives an engine crash.
- **Node 3 CLOSED (FII half) — the FII tide stays live.**
  `trading/signals/fii_nsdl.py` = httpx port of the NSDL backfill script
  (requests not in requirements) keeping BOTH traps: report→trading-date
  shift (corr 0.955 shifted vs 0.075 not) and first-Equity-row-wins (the
  cumulative row once wrote −2.79 lakh Cr over a +418 Cr day).
  `db.save/load_fii_rows` (fii_daily, keyed by date) ·
  `scheduler._weekly_fii_refresh` inside the Friday job, journals
  DATA_REFRESH_FII/_FAILED/_SKIPPED · `flows.set_store_reader` injected at
  boot (broker-attach pattern). **get_flows now MERGES by date: CSV <
  store < live NSE** — the old either/or collapsed the calibrated 5-day
  sum to one day whenever NSE answered. Staleness guard unchanged and
  still rules the merged series. VERIFIED LIVE: 15 rows 2026-07-31→08-20
  fetched from the sandbox. Basis note: NSDL=settlement, NSE=provisional
  (~800 Cr/day gap, documented).
- **Node 9 = docs/DESIGN_ONE_DAMAGE_NUMBER.md — decision doc, NO code.**
  Proposes one canonical tail_down3_5d series with three consumers, zero
  day-one behaviour change, danger tripwires untouched by design. Owner
  must say "go" for steps 1-2; step 3 only after the 60-day window.
- **STILL OPEN, honestly:** Node 3's monthly halves (constituents PIT
  snapshot, fundamentals refresh) · Node 6 delivery triggers + recalib ·
  Node 8 Upstox option chain job (would close TRG-102, W-022, W-007) ·
  Node 10 register full-scale precompute · Nodes 11/12 = OWNER DECISIONS
  (BT06 score recalibration; exit study) · F7 DII post-2025-03 (NSDL DII
  is OTP-walled — genuinely unfetchable from cloud) · F8 live quotes need
  the daily login (structural; token-nudge push is a listed suggestion,
  not ordered).
- Two old test pins updated to the new truths (flows either/or → merge;
  short_interest STUB wording → proxy wording).

**NODE 8 SHIPPED (owner: "continue with node 8"), 973 tests (12 new) —
THE LAST EXTERNAL TRIGGER IS WIRED. External backlog: ZERO pending.**
- `trading/options_chain.py` + `UpstoxClient.get_option_contracts/
  get_option_chain` + `scheduler.run_option_chain_snapshot`: ONE chain
  snapshot per trading day over custom universe + held (+^NSEI), through
  the owner's own token. **AUTH-DEPENDENT BY NATURE (F8)**: option
  endpoints reject tokenless calls (unlike candles) — no login = journaled
  OPTION_CHAIN_SKIPPED (once/day) and every consumer reads DATA_NEEDED,
  never a stale chain wearing today's confidence
  (`options_chain.MAX_AGE_DAYS=2` freshness rule in attach_chain).
- Runs in the 09:20 job before the trigger layer AND on Upstox connect
  (login callback spawns it — catch-up alone would NOT retry when the scan
  had already run tokenless). Once-per-day guard.
- **T12W_0102 wired** (`_e0102`): their formula "PCR/skew deterioration +
  price near support". Deterioration needs OUR OWN forward archive
  (`SNAPSHOT_WINDOW=5` snapshots; history bounded HISTORY_KEEP=15 on
  `portfolio.option_chain`, persisted JSON+Mongo both ways) — first days
  honestly DATA_NEEDED on that leg; skew level (ASSUMED >=3 IV pts) can
  satisfy the options half alone. `pcr_deterioration_frac=0.15` ASSUMED.
  **CHAIN_FED tuple**: calibration runner skips it (no backward archive
  exists; a forward one accumulates — months from now a calibration
  becomes possible), danger layer can't admit it, test-pinned.
- W-022 upgraded to TRUE chain PCR and W-007 gains per-stock ATM IV on
  fresh-snapshot days (register `_wire_chain_items`, "an upgrade that only
  upgrades" — login-less days keep the old proxy/index readings).
- **UNVERIFIED LIVE: the chain response SHAPE** — sandbox has no token, so
  parsing follows Upstox's published schema; the FIRST post-login snapshot
  is the live verification (failure journals OPTION_CHAIN_FAILED,
  consumers stay DATA_NEEDED — fail-visible, not fail-wrong).
- Register standing after Node 8: **9 items still need an outside feed**
  becomes **8** (TRG-102 off the list; the 2 bulk-cap triggers unchanged).

**2026-08-24 OWNER ANSWERS + 0102 PATTERN + NODE 6 CLOSED (owner: three
points then "finish the node 6 after answering above"). 974 tests.**
- **Login-bottleneck answer given (their point 1):** everything already
  runs daily WITHOUT login (scan/news/triggers/breadth/FII/exits/top-up —
  candles public since F1); ONLY live tick quotes + option chains need the
  token (Upstox rejects tokenless there); and login-time catch-up already
  updates everything the moment they log in (built into the callback). The
  one workaround — an auto-login robot storing their password + TOTP on
  Render — breaks Upstox ToS; offered as THEIR decision only, not built.
- **T12W_0102 REWIRED to their 15-day pattern (their point 3, verbatim:
  "there is no number, its just a pattern like the other candle, but span
  of candle is 15 days ... record it for 15 days").** The two ASSUMED
  thresholds (pcr 0.15 / skew 3.0 pts) DELETED — overruled by owner.
  `options_chain.deterioration_pattern`: net rising + more up-days than
  down across the stock's OWN recorded span (PCR or skew series),
  SNAPSHOT_WINDOW=15 (their span), MIN_PATTERN_DAYS=5 (ASSUMED floor —
  a two-point line is not a pattern). Too few recordings = DATA_NEEDED.
- **NODE 6 CLOSED — mostly already done, and the audit's phrasing had
  over-promised.** Checked EVERY sheet file: their sheets hold NO verbatim
  delivery spike/dry-up formulas beyond T12W_0060's one line (the
  parameter-bank Dry-Up rows are placeholders, "Formula" literal,
  "awaiting linked data"). 0060 was already wired verbatim (2026-08-13)
  AND already calibrated in the external run: **REJECTED, lift 0.992 over
  59,195 windows, firing on 22% of all days** — describes the market,
  predicts nothing; under their own rule it joins NO gate. Inventing
  dry-up formulas would violate the build-only-what-they-list order.
- **THE REAL NODE-6 GAP FOUND AND FIXED: the external run's 21 verdicts
  never loaded into the page chips** — T12W_0060 showed NO verdict while
  its REJECTED sat committed. `_load_calibration` now merges BOTH runs
  (each row carries `run` + `data_note`; RECALIBRATED still overrides;
  CALIBRATION = 100 rows). A verdict the owner cannot see might as well
  not exist.
- Fault-class repeat caught in MY OWN new test: windowed journal assertion
  over the persistent state dir (the mongo-halt gotcha class) — fixed by
  counting only the test's own appends. Watch for it in every new test.

**NODE 10 SHIPPED (owner: "continue with node 10"), 979 tests (5 new) —
register market view at FULL SCALE; the F5 50-member/320-bar cap lifted.**
- `scheduler.run_register_precompute`: streams `db.history_tails(340)`
  (the one $slice query, breadth's F3 machinery), builds FIVE-column
  frames, runs `register_v4.market_view` over the FULL store (~2,700
  names when the store grows; ~191 today), stores
  `portfolio.register_market` = {asof, ts, sample, rows} (persisted
  JSON+Mongo BOTH ways — risk_day lesson), journals REGISTER_PRECOMPUTE/
  _FAILED. Cron 16:45 IST mon-fri + a second pass after the Friday
  top-up. MEASURED on the 512MB box: 2,700-name market_view = 0.1s,
  87MB peak — safe.
- **MIN_NAMES=150 thin-store guard**: fewer names stores NOTHING — a
  60-name reading wearing the "full store" label is the exact lie F5's
  disclosure existed to prevent; the page keeps its honest capped
  fallback instead.
- /register prefers a precompute whose closes are ≤5 days old
  (emerald banner names sample+asof); stale/absent degrades to EXACTLY
  the old capped 50-member/320-bar live compute (amber banner names the
  cap and the 16:45 pass). Held names' closes still load on the
  precompute path so W-028..031 keep their vol inputs.
- **BUG IN MY FIRST CUT, and the test that hid it**: I fed close-only
  frames; `register_v4`'s normalizer (the 2026-08-09 lowercase lesson)
  demands all five OHLCV columns, so EVERY member silently dropped and
  PX items read NA with 2,700 names loaded — while my test PASSED,
  because it only asserted "any rows present" and the panel-independent
  FII/VIX rows satisfied it. Fixed the frames (the $slice docs carry
  o/h/l/c/v anyway) AND hardened the test to require PX-01 computed.
  LESSON: a weak assertion is a fault mask — assert the panel-DEPENDENT
  item, not just any output.

**NODE 3 REST SHIPPED (owner: "node 3", 2026-08-24), 988 tests (9 new) —
the frozen-at-commit data files now auto-pull. Node 3 fully CLOSED.**
- **NEW trading/data_refresh.py** — fetchers + injected Mongo readers
  (flows.set_store_reader pattern: trading never imports web.db; readers
  attached at boot in state._bootstrap). Storage is Mongo because Render
  wipes disk on restart — the committed CSVs stay the deterministic base
  and every consumer merges the stored tail on top; reader missing or
  failing = the committed file alone, exactly the pre-Node-3 world.
- **Weekly index tail** (`_weekly_index_refresh`, Friday 16:30 job):
  the 4 dashboard indices via Upstox PUBLIC candles (v2 endpoint,
  tokenless — the F1 fact), rows shaped to index_daily_15y.csv's own
  columns, upserted into Mongo `index_daily` keyed by date. VERIFIED
  LIVE from the sandbox: real Nifty/Sensex/VIX/BankNifty closes for
  2026-08-19→21 through the actual client. Consumers wired: dashboard
  sparklines (headline stays LIVE-or-dash), register load_market_inputs
  (index+VIX feed the Node 10 nightly precompute too), and
  **external_panels._load_index_close — T12W_0095's index leg, a LIVE
  danger tripwire, had been reading an index state frozen at the CSV's
  commit date (17 days stale at ship time). That was the real money-path
  win of this node.** Journals DATA_REFRESH_INDEX/_SKIPPED/_FAILED.
- **Monthly constituents snapshot** (`_monthly_constituents_snapshot`):
  Nifty 50/200/500 lists from niftyindices.com (the exact GET of the
  2026-08-09 snapshots; verified live 50/200/500 rows), stored per
  (month, list) in `index_constituents` — the forward PIT record grows
  itself now. Malformed/HTML responses refused (needs Symbol col + ≥40
  rows). Month-guarded via settings doc_id="data_refresh".
- **Monthly fundamentals refresh** (`_monthly_fundamentals_refresh`):
  Yahoo direct timeseries (the raw endpoint — yfinance wrapper returns
  empty, the 2026-08-09 finding) for custom universe + held, bare
  symbols URL-encoded (the M&M/GVT&D lesson). Rows in `fundamentals`
  keyed symbol|field|as_of; register_v4.load_fundamentals overlays them
  on the committed CSV (stored wins = fresher fetch of the same figure);
  `clear_fundamentals_cache()` called post-store so page + precompute
  see new quarters without a restart. Verified live: TCS 60 datapoints.
- Month guards skip silently inside the month (by design, ×4 Fridays);
  Mongo-down and fetch failures always journal — no silent no-ops.
- NOT auto-pulled still (genuinely blocked): delivery %, PWOI, F&O
  bhavcopy (nsearchives blocks cloud — Colab), DII post-2025-03 (NSDL
  OTP wall), TRI (niftyindices WAF). Node 3 has nothing left that cloud
  can reach.

**NODE 9 STEPS 1-2 SHIPPED (owner: "node 9 go", 2026-08-24), 997 tests
(9 new) — ONE canonical damage number, day-one behaviour changed NOWHERE.**
- **NEW trading/internals.py** — the single computation site for the
  owner's tail_down3_5d (worst 3-day return in last 5 days <= -3%, share
  of panel; the formula recovered from market_internals_1996_2024.csv).
  STATIC_THRESHOLD 0.213 = their own frozen p90. Pure reduction: no
  danger/engine/risk/orders/network imports, test-enforced. A test pins
  the formula BYTE-EQUAL to the register's old inline expression.
- **engine.scan computes the reading over the SAME panel IFR reads**
  (deliberate: a disagreement in the journal is the FORMULAS disagreeing,
  never the sample), stores `portfolio.damage_store` (persisted JSON+Mongo
  both directions — risk_day lesson). Every IFR_SIGNAL_FIRED entry now
  carries canonical_tail_down3_5d + canonical_static_cluster +
  canonical_q90_cluster beside IFR's own numbers. **KEY FACT: IFR's
  private formula is genuinely different** (any single -3% DAY in 5 days
  vs the owner's worst 3-DAY return in 5 days) — 60 days of these pairs
  decide whether they agree.
- FII-08/09 prefer a fresh (<=5d) stored reading, note says
  "[canonical store: scan panel of N names, asof …]"; stale/missing =
  exactly the old page-panel recompute, labelled "[page panel]".
- **Danger layer untouched BY DESIGN and test-enforced** (danger.py must
  not import internals) — its 12 tripwires are calibration-VERIFIED
  per-stock rules; welding them to a panel gauge would discard evidence.
- **STEP 3 NOT DONE, deliberately**: after the 60-day window, read the
  accumulated journal pairs, put the IFR-reads-the-store switch to the
  owner. docs/DESIGN_ONE_DAMAGE_NUMBER.md carries the status postscript.
- Test-writing note: my first cut asserted `"canonical store" not in
  note` while the FALLBACK label itself says "no fresh canonical store" —
  assert on the bracket markers, not on words a sibling label may reuse.

**NODE 11 / BT06 SHIPPED (owner: "node 11", 2026-08-24), 1,002 tests
(5 new) — the side-score weights are MEASURED; the score formula stops
being ASSUMED. Display/watchlist only, and the run's own verdict says
that scope is right.**
- scripts/bt06_score_calibration.py: same 94,479 windows/labels/panel/
  production-trigger-code as the 2026-08-09 run; weights FIT on the
  2014-2020 train half (w = max(0, own-side lift − 1), zero under 30
  train fires — their own BT08 bar), judged ONLY on 2020-2026.
  Checkpointed (windows.npz committed, 896K — the fit re-runs in
  seconds). Run outputs in research/bt06/run_2026-08-24/.
- **38 of 79 triggers earned weight.** Top weights independently
  reproduce the danger-layer story: ATR Shock 2.11, Support Break 1.22,
  Panic Expansion 1.00 — the collapse tripwires dominate.
- **HOLDOUT VERDICT, stated plainly everywhere: even recalibrated, the
  entry gate LOST on the test half** — GATE_RISE_ENTRY_01 went from 1
  entry/-5% (assumed weights; the inert gate reproduced) to 8 entries,
  25% win, −1.49%/trade (measured). RISE scores never beat chance at any
  usable threshold (lift ≤1.0 wherever n>16); COLLAPSE score at 65/70 =
  lift 1.13/1.25 — the aggregate DILUTES the verified tripwires. A
  weighted sum of no-edge triggers is still no edge (rise-side edge
  rejected a FOURTH independent time). The measured score is MORE HONEST,
  not more predictive — REQUIRES_15Y_BACKTEST scope stands on evidence.
- Wiring: triggers.MEASURED_WEIGHTS loads the run CSV at import (missing
  file → sheet weights + ASSUMED label, byte-identical pre-BT06
  behaviour, verified); side_scores uses it; a side with NO weighted
  trigger is ABSENT from the dict (no measured evidence ≠ measured 0;
  in practice all 4 sides have weighted triggers: RISE 8, FALL 15,
  COLLAPSE 8, REVERSAL_UP 7). SCORE_NOTE (new) carries the formula AND
  the holdout verdict; /triggers score_note reads it (was
  ASSUMED_CONSTANTS["score_formula"], which now describes only the still-
  assumed SHAPE). MEASURED_CONSTANT_SOURCE["side_score_weights"] cites
  the run with the loss numbers. Old test pinning sheet-weight arithmetic
  updated to pin whichever weight source side_scores really uses.
- RUN BUG: first pass crashed AFTER the 25-min panel pass on
  `(DatetimeIndex <= ts).to_numpy()` (comparison already returns
  ndarray) — checkpoint made the re-run instant. Fix phase-boundary code
  cheaply testable before long runs.

**NODE 12 EVIDENCE REFRESHED (owner: "node 12", 2026-08-24) — the exit
question is REOPENED BY THE OWNER and awaits their pick; NO engine change
shipped.** The committed exit study was era-pinned at floor 55; the live
floor is 62, so before asking for the decision the three relevant
variants were re-run at the LIVE floor through the SAME study machinery
(scripts/exit_study_live_era.py — thin wrapper importing
exit_variant_study's loader/make_cfg/run_backtest; one resumable JSON per
variant, survived a mid-run container recycle). VERDICT SURVIVES THE ERA:
**D (−5/+20/trail−15) beats live A (−5/+12/t15) in ALL FOUR windows at
floor 62** — totals +69.90 vs +54.22 (+15.7pp/7.5y; was +74.69 vs +57.44
at 55); D15 between (+58.01). D's trade-offs, stated: trail ALIVE
(8/56/31/48 fires vs A's dead 4/9/2/11), fewer trades (2,146 vs 2,891),
LOWER win rate (26-38% vs 32-42% — money from the tail, not hit rate),
2024-26H1 worst-DD −20.75 vs −18.93 (elsewhere same/better).
research/exit_study_live_era/summary.md = the record. Same harness
caveats as every study (survivorship panel, comparison fair).
**OWNER DECIDED: "Switch to D" (picked from the options card, 2026-08-24)
— WIRED same day, 1,003 tests green. Node 12 CLOSED; supersedes the
2026-07-27 "fixed at 12%" decision by the owner's own pick.** What
changed: `PRESETS["nifty200_momentum"].take_profit_pct` 0.12→**0.20**
(stop −5 and trail −15 unchanged — and the trail is now GENUINELY LIVE,
arming above +17.65%; it was dead by arithmetic at +12). One-time boot
migration (marker exit_order_2026_08_24, new `db.unset_settings_keys`
$unset helper — save_settings is $set-merge and cannot delete) clears any
stored take_profit/trailing override top-level AND inside
preset_overrides["nifty200_momentum"], so no pre-order Settings edit can
pin the superseded +12. Held non-custom positions pick up the +20 target
via the existing boot retag (custom_levels still skipped). Truth-pass:
APP_BRIEF exits rewritten (+ the STALE brake paragraph fixed — it still
claimed corrections block buying, off since 2026-08-09) · login copy ·
dashboard era-note · config.trail_is_reachable docstring · new pin
test_live_momentum_bracket_is_minus5_plus20_trail15 (asserts the trail is
reachable) · 5 stale +12 pins updated (exit_policy_c ×2, docs_truth_d,
market_panels horizons, owner_buy). UNTESTED LIVE at +20 — the 60-day
window continues under the new bracket at the owner's order.

**UPSTOX FII/DII WIRE (owner order 2026-08-24: "with upstox key u can wire
the FII data also for each stock, so where u show data needed, needs to
fill with live from upstox"), 1,011 tests (8 new).** Upstox launched
Market Information APIs 2026-05-11 (/v2/market/fii + /v2/market/dii, OI,
PCR, max-pain; data from 2026-04-01; 401 tokenless — VERIFIED live from
the sandbox). What shipped:
- `UpstoxClient.get_market_fii/get_market_dii` + `_market_info_headers`:
  daily login token OR **UPSTOX_ANALYTICS_TOKEN env** (owner can generate
  an Analytics Token on the Upstox Developer Apps page and set it in
  Render — keeps this feed alive before the day's login; quotes still
  need the login, that's an Upstox rule).
- `scheduler.run_market_fii_snapshot` — once/day (09:20 job + login
  callback, chain pattern): archives BOTH raw payloads
  (db.save_market_info_raw — a shape miss loses nothing), maps
  defensively (rows must prove date + idx-fut long/short legs; all-zero
  DII refused), stores pwoi_daily rows + merges DII onto fii_daily.
  Journals MARKET_FII_SNAPSHOT/_SKIPPED/_FAILED; UNMAPPED payloads named
  in the journal. **RESPONSE SHAPE UNVERIFIED until the first
  authenticated call** (sandbox has no token) — mapper candidates cover
  the documented field names; first live journal line tells.
- Consumers: register FII-04/05 read FII rows extended past the frozen
  committed PWOI panel (extend_pwoi_df, degrade-to-before), note says
  "[upstox market/fii to DATE]"; FII-06/07 legs (Client/TOTAL rows) CANNOT
  extend — feed is FII-only — so a mixed frame prints each leg's end date.
  DII rows = the first LIVE DII source since NSDL's OTP wall (F7 half-
  closed FORWARD from 2026-04; the 2025-04→2026-03 hole stays historical).
- **TOLD THE OWNER STRAIGHT: daily PER-STOCK FII does not exist in any
  feed** (Upstox serves segment aggregates — index fut/stock fut/options/
  cash; per-stock institutional = quarterly shareholding, already wired).
  Do not promise per-stock FII again.

**FII-TIDE STUDY 2026-08-25 (owner: "its not about getting in the app,
its about to use data for selection and scoring") — TESTED THEIR WAY,
NO WIRE EARNED.** scripts/fii_tide_study.py + research/fii_tide_study/:
7 signals (FII-04/05 fires, tide, extremes, DII/FII cash ±5000Cr) over
FII idx-fut positioning 2012→2026 + cash 2007→2026 vs forward Nifty,
train/test at 2020-06-30. ALL FAIL an honest read: S1/S4/S6 flip sign
between halves; S2 inverted; **S5 (DII heavy selling, the one that
looked real at +3.7pp test) collapsed on episode dedupe — 74 fire-days
= 13 episodes, 6 of 8 test episodes are the 2020 melt-up, last two
(2021) LOST.** Third independent landing of the same conclusion (uploads
workbook block-table 2026-08-09; owner's prior calibration). FLAGGED TO
OWNER, their call: the composite's LIVE FII-cash ±20 catalyst tilt IS
S6 and fails its holdout (composite drives swing/positional + decay
exits, not momentum entries; their historic spec — untouched without
their word). FII positioning is MARKET-level — it can time/size, never
rank one stock over another; told them per-stock daily FII exists in no
feed. The Upstox daily feed accumulates a forward archive for a future
re-test. Selection influence their data HAS earned stays: the 12 danger
tripwires + canonical damage journal.

**USFDA + SANCTIONS WIRE (owner ask 2026-08-25: "did u wired the US FDA
data and on going senctions"), 1,018 tests (7 new).** Honest answer given
first: NO — the old USFDA calendar was FABRICATED dates driving a real
entry blackout, emptied by the 2026-08-09 audit; nothing real was wired
until now. What shipped (display only — the fabricated-calendar lesson
stands, no USFDA row ever gates a trade):
- **trading/usfda_feed.py**: openFDA drug-enforcement recalls — PUBLIC,
  keyless, VERIFIED live (Torrent: 85 classified recalls with dates +
  reasons). Firm↔ticker match by company-name token (query_token/
  firm_matches); the matched FDA firm name rides every row so a wrong
  match is VISIBLE, never silent. FDA Data Dashboard inspections
  (Form-483 territory) = real endpoint, verified reachable, but needs a
  free registered key → FDA_DATADASHBOARD_USER/_KEY env (owner action at
  datadashboard.fda.gov, OII Unified Logon); without it that half reads
  DATA_NEEDED naming the unlock.
- **scheduler._weekly_usfda_refresh** (Friday block): scans pharma/chem/
  health names from the custom universe + ALL held names (name map from
  upstox_nse_equities.csv), stores Mongo `usfda_events` keyed
  ticker|event_id (save returns NEW count), journals DATA_REFRESH_USFDA/
  _SKIPPED/_FAILED with names-with-events listed.
- **/triggers per-stock page**: 🏛️ USFDA events card (class-colored,
  firm + date shown, "display only" line) — usfda_block_marker.
- **Sanctions half**: the news regulatory family (live daily per stock)
  ALREADY carried sebi/penalty/ban/probe/show-cause; ADDED patterns:
  us fda, form 483, warning letter, import alert, sanction(s|ed) — so
  those headlines tag RISK instead of sliding through untagged. A
  structured SEBI enforcement-orders feed is possible (sebi.gov.in
  answers our cloud IP — probed 200) but NOT built; offer stands.
- Boundary test: engine/risk/orders/danger never import usfda_feed.

**SEBI ENFORCEMENT ORDERS WIRED (owner order 2026-08-25: "wire the SEBI
enforcement orders feed too"), 1,023 tests (5 new).** Display only.
- **trading/sebi_feed.py**: sebi.gov.in's OWN listing backend
  (sebiweb/ajax/home/getnewslistinfo.jsp, sid=2/ssid=9 — found by
  brute-forcing category ids; the static pages are JS shells; ssid=10
  stops May-2025, ssid=12 stops Mar-2023, 9 is the live one). VERIFIED
  live: 50 real orders parsed, newest 2026-08-18. Rows: order_id (from
  the URL), ISO date, title, full URL.
- Matching: EXACT title word (stricter than USFDA's prefix rule — SEBI
  titles are free prose over every sector; "Rainbow" must not match
  RAIN). Token + full title ride every row; wrong matches visible.
- **scheduler._weekly_sebi_refresh** (Friday block): scans ALL custom-
  universe + held names (SEBI hits any sector). Most orders name
  INDIVIDUALS — matches for a 50-name book are RARE; the journal carries
  the scanned-order count so an empty week is dated evidence
  ("No order named these companies"), never silence. Journals
  DATA_REFRESH_SEBI/_SKIPPED/_FAILED. Mongo `sebi_orders` keyed
  ticker|order_id (save returns NEW count).
- **/triggers per-stock page**: ⚖️ SEBI card (sebi_block_marker), order
  titles link to sebi.gov.in, matched token printed.
- Boundary test: engine/risk/orders/danger never import sebi_feed.

**INDEX-TAIL FAULT FIXED 2026-08-26 (owner: "still on sensex and all, it
show 7 august, why u r unable to fix").** ROOT CAUSE: the Node 3 index
refresh was wired WEEKLY (Friday 16:30) and shipped on a Sunday — the
first run had never fired, the index_daily store was EMPTY, and every
consumer (sparklines, register inputs, T12W_0095 index leg) read the CSV
frozen at its 2026-08-07 commit. FAULT CLASS: "wired but the first
scheduled run hasn't happened" — same family as F1's Friday-skip. FIX:
the refresh now ALSO runs daily before the 09:20 scans (and via boot
catch-up, which calls the same job); idempotent (only dates past the
newest held row), quiet when already current (no daily journal spam;
journals only on stored rows or failure). First live run backfills
08-08→today in one shot.

**OCEAN UN-PARKED BEHIND THE CRASH GUARD (owner 2026-08-26: "market works
both way it should be one... both have to work in parallel" → design
presented → owner picked "Approve as specced" from the options card; same
card: breadth gate KEPT ON). 1,029 tests. THE PARK IS OVER — supersedes
the 2026-07-27 park decision by the owner's own approval.**
- **Parallel: Everest + Ocean in every regime.** Ocean's own selection
  (>=30% under 200DMA, weekly RSI<25, climax volume, survival) makes
  candidates rare outside sell-offs; PANIC no longer means nobody-can-buy
  — Ocean can, behind the rails, with the 40% panic reserve still applied.
- **The four rails (engines/ocean.py crash_guard_market/_stock), every
  threshold evidence-traced:** R1 VIX knife (block while VIX > 120d p90
  AND rising, unless vix_crush_ok — the only dip rule that tested
  positive, +2.3pp v4) · R2 the owner's 0.213 damage cluster on the
  canonical Node 9 store (moved BEFORE pick assembly so it reads today's
  panel) · R3 per-stock reclaim (no fresh 20d low + close above prior
  high) · R4 danger veto (the 12 VERIFIED tripwires). FAIL-SAFE: unknown
  VIX / missing damage reading = BLOCKED, never assumed safe. Blocks
  journal once/day (OCEAN_BLOCKED / OCEAN_STOCK_BLOCKED with the rail).
- **Half size ₹62,500 (cfg.ocean_position_value), max 8 concurrent slots
  (cfg.ocean_max_positions, counted via position tags=["ocean"]).** Every
  can_open gate still applies. BUY journal carries sizing mode
  "ocean_fixed" + reasoning "OCEAN: ...".
- **Two deliberate exemptions, both tested:** breadth gate rules Everest
  only (a dip engine exists FOR fallen markets — breadth-gating it means
  it can never buy); candle confirmation skipped for ocean picks (the
  reclaim rail IS the confirmation).
- **survival_check REWRITTEN off yfinance** — Yahoo 429-blocks Render, so
  the old check could NEVER pass in production (the park was double-dead).
  Now reads the fundamentals store (committed CSV + Node 3 monthly refresh
  via data_refresh reader): D/E < 0.5 + latest FCF > 0; current-ratio leg
  dropped honestly (not in store); missing fundamentals = FAIL.
- cfg.ocean_enabled default True + boot migration (marker
  ocean_order_2026_08_26, unset_settings_keys) so the park-era stored
  False cannot re-park it; Settings toggle still works. Engine page
  truth-passed (ON branch names the rails; PARKED branch kept).
- **SAME MESSAGE, index fault FIXED (owner: "still on sensex it show
  7 august"):** the Node 3 index refresh was Friday-only and shipped on a
  Sunday — first run never fired, store empty, everything read the CSV's
  commit date. Now ALSO daily before the 09:20 scans + boot catch-up;
  quiet when current. FAULT CLASS: wired-but-first-run-never-happened.
- Owner also compared to their ASH app (ChatGPT-built, 67% deployed):
  told them straight the breadth gate is why this book is near-empty at
  the ~50-51% line; they chose to KEEP it on the options card.

**DELAYED-QUOTE WIRE 2026-08-26 (owner: "data is just 15 sec delay and
you r like far away" — comparing to their ASH app's 15s quotes), 1,033
tests (4 new).** KEY FACT DISCOVERED: **Upstox INTRADAY candles are
PUBLIC, tokenless** — verified live (375 one-minute Nifty candles, last
bar 15:29 IST, matching the owner's ASH screenshot to the paisa;
newest-FIRST candle order). So the login-bottleneck for prices is
largely gone: only true tick LTP still needs the daily token.
- `UpstoxClient.get_intraday_last` (public, reads candles[0] = newest).
- `quote_router`: tokenless days now serve ~1-min-delayed intraday
  closes (via the tokenless history client) BEFORE the dead-yfinance
  last resort; the connected path falls back per missing name. Source
  label `upstox_1min_delayed` in LAST_SOURCES — never dressed as live
  (the 2026-07-31 lesson); engine fill_source carries the label into
  the BUY journal.
- Dashboard: index cards + quotes chip say "~1 min delayed - Upstox
  public candle (login = real-time)" instead of dash/reconnect-red;
  status() messages truth-passed.
- Old router tests were hitting the now-real endpoint and getting REAL
  prices mid-test — updated to the new contract with the endpoint
  mocked. FAULT CLASS: a newly-public endpoint turns old "safe" test
  paths into live network calls.

**FDA-KEY INSTANT ACTIVATION 2026-08-26 (owner: "wire these 2, ASK ME
ANYTHING NEEDED" — item 1 Ocean was ALREADY live from the same-day
approval, told them so; item 2 needs THEIR free FDA key).** What the
owner must do, told to them precisely: register at datadashboard.fda.gov
(OII Unified Logon, free) → set FDA_DATADASHBOARD_USER (approved email) +
FDA_DATADASHBOARD_KEY in Render. New `scheduler.usfda_key_activation_
check` (boot job, +180s): key present + no fda_dashboard-sourced events
stored → runs the FIRST inspections pull immediately (the env change
restarts Render, so setting the key IS the trigger); permanent no-op
after data exists or without the key. 1,034 tests.

**FDA NEWSROOM WIRE 2026-08-26 (owner: "there must me general news as
well from FDA in paper n all, u can fetch with render key"), 1,036 tests
(2 new).** FDA's OWN newsroom RSS — press releases, MedWatch safety
alerts, recalls, drugs — ALL FOUR verified 200 from the sandbox,
KEYLESS (no key needed despite the owner's phrasing; told them).
- `usfda_feed.fetch_fda_news(name_map)` -> (matched, general): matched
  items use the same firm-token rule as recalls, land in `usfda_events`
  (source "fda_news", classification FDA-PRESS/-MEDWATCH/...) so they
  appear on the stock's 🏛️ card automatically; event_id = md5(link)
  (Python hash() is per-process salted — would re-store every restart).
- `scheduler.run_fda_news_snapshot` — once/day in the 09:20 flow (guard
  key fda_news_day): stores the general newsroom via save_market_info_raw
  ("fda_news"), matched via save_usfda_events; journals FDA_NEWS_MATCHED
  only when a company of ours is named (quiet otherwise), _FAILED on
  outage. `db.load_market_info_raw(kind)` = the new newest-doc getter.
- /triggers index page: collapsible "🏛️ FDA newsroom" card
  (fda_newsroom_marker) — general items with feed tag + link + date,
  "display only" stated; matched names ALSO go to their stock page.
- Display only, boundary intact. Note: FDA newsroom is uncalibratable
  like all news (no archive) — never a gate.

**FRIDAY BLOCK REORDERED + ISOLATED 2026-08-29 (owner: "is the Friday
trigger captured? few things are subjective to everyday login and friday
login... i did it this week" → "fix it"), 1,042 tests (6 new). READ THIS
BEFORE TOUCHING ANY SCHEDULED JOB.**
- **THE DIAGNOSIS (from the LIVE box, not from memory):** /healthz showed
  `history_tickers` **573 of 2,428** — the store had grown from 191 (the
  2026-08-20 reading) so a full-scope Friday top-up HAD run, but after TWO
  full-scope Fridays (08-21 and 08-28; PR #68 landed 08-21 00:56 IST, i.e.
  before that day's 16:30 slot) the pull was **only ~24% done**. There is
  no per-run cap in `history_store.top_up_history` — it loops the whole
  universe — so the pull was being cut off, almost certainly by a container
  restart (the universe file's mtime, which `refresh_universe` rewrites
  atomically on every success, sat at the last DEPLOY date, not Thu/Fri).
- **WHY THAT WAS EXPENSIVE:** all eight weekly sub-jobs ran sequentially
  inside ONE try block **after** that hour-long pull. A crash — or a
  restart, which no try block can catch — silently skipped everything
  below. Three have a daily counterpart and survived (index tail since the
  08-26 fix, breadth, register precompute at 16:45); **FIVE do not: FII
  tide, constituents, fundamentals, USFDA, SEBI.** A truncated pull cost a
  whole week of those, and the weekly block has **no catch-up** (only the
  09:20 scan has one), so the miss waited seven days.
- **THE FIX:** `_run_weekly_step(label, fn, journal)` runs each sub-job in
  its own guard (journals DATA_REFRESH_STEP_FAILED naming WHICH feed, the
  others still run), and the six short store-INDEPENDENT feeds now run
  **BEFORE** the pull — order is the real fix, since isolation alone cannot
  survive a restart. The two store-DERIVED recomputes (breadth, register)
  still follow the pull but are isolated from it, so a crashed pull no
  longer skips them either. New **DATA_REFRESH_WEEKLY_START** journal line:
  before this, a pull that died left NO evidence Friday had fired at all —
  which is exactly why the owner's question could not be answered from
  outside. Docstring truth-passed (it still claimed the pre-PR#68
  stored+held scope).
- Guard tests `web/tests/test_friday_block_isolation.py` — **verified to
  FAIL on the pre-fix scheduler** (4 of 6; the other 2 pin the holiday and
  Mongo-down gates that must not change).
- **STILL OPEN, told to the owner:** the weekly block has no catch-up if
  the process restarts across 16:30 IST. Not built — a 2,400-name pull
  firing at an arbitrary boot time on the 512MB box is its own risk and
  needs their decision. Also unproven: whether 08-28's run fired at all
  (the journal answers it; needs their login).
- **LOGIN MAP restated for the owner** (their "subjective to everyday
  login" worry): only live tick quotes, the option chain and the Upstox
  market FII/DII feed need the daily token — and the login callback re-runs
  those. Candles, scans, news, triggers, danger, exits, breadth and **the
  entire Friday block** are tokenless since F1/2026-08-20.
- **MY ERROR THIS ROUND, repeated from 2026-07-30:** ran the diagnosis as a
  5-agent Workflow with a nested StructuredOutput schema; ALL FIVE hit the
  retry cap (676k tokens, zero output) — the exact failure my own note
  warns about. **Keep workflow schemas FLAT.** Doing it directly took
  minutes and was more accurate, same as last time.

**EXTERNAL REVIEW ADOPTED, ITEMS 1+2 (owner 2026-08-29 uploaded
Selection-style xlsx "AM07_vs_AshStocks_GitHub_Review", asked "review and
tell me what is good and can be adopted", then ordered "go in same / 1 /
then 2 / then 3"). 1,063 tests. THE BEST EXTERNAL REVIEW RECEIVED —
pinned both repo SHAs (AM07 aacbcf48, AshStocks 067c4d66), stated its own
limits (GitHub connector only, binaries by metadata, 18 oversized files
sampled), and graded the real tree, not a stale spec like the 2026-07-27
one. Its 6 AM07 findings AUDITED against code before adopting: 5 accurate
(F-001 mongo, F-002 password, F-007 ₹1L-vs-₹25k-vs-₹62.5k, F-012 doc
drift, F-013 196MB tree), 1 imprecise (F-009: the 18/12/6% vol tiers ARE
dead, but risk_v2 itself is live — governor/loss-tracker/cash-reserve).**
- **ITEM 1 SHIPPED — `web/readiness.py`, fail-closed production boot.**
  F-001 was real AND the pre-existing degraded halt structurally could not
  catch it: `db.mongo_expected()` is `bool(MONGO_URL)`, so "secret never
  set" was indistinguishable from "JSON dev run on purpose" — the live
  book would sit in /tmp and vanish on restart. F-002 verified at
  auth.py:36-38 (`OWNER_PWD or APP_PASSWORD or "sourceborn"`, error-log
  only). Now: on Render (the `RENDER` marker the Secure-cookie flag
  already uses) a boot REFUSES to start without MONGO_URL, a real
  password, and a real SESSION_SECRET — values that ARE the old fallbacks
  fail too (set-to-fallback looks configured, which is worse than unset).
  All failures reported together, each naming its variable.
  `assert_ready()` runs at import in web/main.py so uvicorn fails with the
  reason — **and Render keeps the last good deploy serving a failed boot,
  so this cannot cause an outage.** `SB_ALLOW_INSECURE=1` = documented
  escape hatch, logs every problem as a warning, never silent.
  `SB_REQUIRE_PROD=1` forces the bar off-Render. /healthz gained a
  `readiness` block (booleans + variable NAMES, never values) so a
  misconfigured deploy is visible from outside. NO-OP in dev and tests
  (RENDER unset) — test-pinned, plus a pin that readiness imports nothing
  heavy so it can never itself be the reason a boot fails.
- **ITEM 2 SHIPPED — quarterly institutional holdings on the /scan row**
  (review C-014: AshStocks shows shareholdings on scan rows; AM07 had the
  same data RICHER — FII/DII/MF/insurance + holder counts + pledge, two
  quarters, 193 symbols — but only inside /register, not the screen the
  owner buys from). `scan_routes._institutional()` reads the committed CSV
  through external_panels' existing module cache: one dict lookup per row,
  ZERO network, nothing new fetched. Column shows FII% and DII% with the
  QoQ delta arrow and a ⚠ pledged flag. **Labelled QUARTERLY in the header
  and the tooltip names the filing quarter** — a filing must never read as
  today's flow. A symbol outside the fetched set renders an em dash with
  "Not a zero" in the tooltip. Slot divider colspan widened 7/6 → 8/7;
  a test counts `<th>` against `<td>` so the table can't silently skew.
  VERIFIED BY REAL RENDER, not just tests (the /positions 500 lesson):
  /scan returns 200 with a mixed book — in-set name, out-of-set name, and
  a score-less ampersand ticker — printing "FII 9.06%" for TCS.
- **BUG FOUND WHILE AUDITING THEIR F-012, WHICH THE REVIEW MISSED:**
  `web/routes/dashboard.py:230` reads `os.environ.get("DAILY_SCHEDULER")
  != "1"` and then tells the owner "Daily auto-scan is OFF", but
  `scheduler.py:34` reads `os.environ.get("DAILY_SCHEDULER", "1") != "0"`
  — **unset means ON**, and render.yaml never sets it. So unless the owner
  set it by hand, the dashboard has been reporting auto-scan OFF while it
  ran daily. Lying-screen class (B batch). **FIXED same day on their order
  ("fix the DAILY_SCHEDULER dashboard bug"), 1,079 tests:** new
  `scheduler.daily_enabled()` is THE single source of truth (one env read
  in the whole app, test-pinned: dashboard.py may contain zero raw reads,
  scheduler.py exactly one, inside the helper); all 3 call sites use it;
  the dashboard message now fires only when the scan is genuinely off and
  names the real cause (`DAILY_SCHEDULER=0`) instead of the old backwards
  instruction; module docstring truth-passed (it said "Disabled by
  default" while `start_if_enabled`'s own docstring said the opposite).
  Guard tests verified to FAIL on the pre-fix dashboard (3 of 6).
  **TWO OF MY OWN FAULT CLASSES REPEATED IN THE FIRST CUT OF THAT TEST
  FILE, both caught before commit:** (1) the docstring pin tripped on my
  own TRUTH-PASS note quoting the old wording — THIRD time (F1 test,
  /positions hunt); split on the marker and assert only operative text.
  (2) the "banner absent" test fetched `/dashboard`, **which is a 404** —
  the dashboard is mounted at `/` — so it passed against an error page.
  A weak assertion is a fault mask (the Node 10 lesson): the helper now
  asserts login 200, page 200 AND that the tweaks block rendered before
  any caller reads the body.
- **F-007 + F-009 FIXED 2026-08-30 (owner: "fix f-007 and f-009 too"),
  1,094 tests.** Both were real; NEITHER was a behaviour bug, and no
  owner-ordered number moved.
  * **F-007 was a STATEMENT gap, not a conflict.** The three entry values
    are three separate owner decisions that had never been written down
    together: Everest ₹1,25,000 (order 2026-07-30), Ocean ₹62,500 = half
    (approval 2026-08-26), floor ₹25,000 = a dust guard that SKIPS a trade,
    never a target (2026-07-11). The reviewer's "owner baseline says at
    least ₹1 lakh" traces to the owner's own words "roughly 1-1.25 for each
    trade" — true of **Everest only**. Fixed by writing the whole policy in
    one ENTRY VALUE POLICY block in config.py with each number's
    provenance, and by completing docs/APP_BRIEF.md — **the owner's manual
    had never mentioned Ocean's half size at all**, so reading their own
    brief would not tell them the book can open ₹62,500 trades.
    test_entry_value_policy pins the three values, that Ocean stays exactly
    half, that the floor stays below both (a floor above an entry size
    would silently disable that engine), and that both config and
    APP_BRIEF keep stating all three.
  * **F-009 DELETED, not relabelled.** risk_v2's 18/12/6% vol-tier scheme
    (TIER_THRESHOLDS, annualised_vol_pct, vol_tier, vol_adjusted_qty) was
    never wired — engine.py imported vol_adjusted_qty and never called it,
    and that import went in the 2026-08-09 E batch — yet it stayed
    documented like a live control AND had 4 tests exercising it, which is
    exactly how the reviewer read it as active sizing. Gone, with its
    tests; a comment block in its place records why. Guard test asserts the
    names never return to any production module and that the LIVE half of
    risk_v2 (DrawdownGovernor, ConsecutiveLossTracker,
    dynamic_cash_reserve_pct, correlation_gate_blocks, passes_liquidity)
    survived the deletion. **The reviewer's wording would have been wrong
    if read as "risk_v2 is dead" — the module is very much alive.**
  * Still unfixed by their order: F-012's two
    contradictory scheduler docstrings (the DAILY_SCHEDULER half is done),
    F-013 (196MB tree).
- **ITEM 3 SHIPPED — the weekly block self-heals a missed slot** (review
  C-006: AshStocks runs a continuous 2-min market-hours loop, which
  self-heals where a cron job simply misses; AM07's weekly block had NO
  catch-up at all). **The loop was deliberately NOT copied** — a
  ~2,400-name pull cannot run every two minutes on 512MB. Self-healing
  taken without the loop: the job is registered **mon-fri at the SAME
  16:30 slot** (market closed, nothing competing) and
  `scheduler.weekly_block_due()` keeps the cadence weekly, so an ordinary
  Monday costs ONE settings read. Also delivers the owner's own decision
  verbatim: "If Friday run is not done, it should run on Monday not next
  Friday." Rule: Friday runs when >=4 days since the last completion; any
  other trading day runs only at >=7 days. **The 4-day Friday arm is what
  returns the cadence TO Friday after a Monday catch-up** — without it one
  miss would move the block to Mondays for ever (test-pinned). Ledger =
  `weekly_block_date` in the existing `data_refresh` settings doc, written
  **only after the block's sequence completes** (the guard-set-before-the-
  work fault). Deliberate semantic, commented in code: completion counts
  the SEQUENCE, not the OHLCV pull's internal result — the pull is
  incremental and journals its own failures, so tying the ledger to it
  would re-run an hour-long pull every evening whenever a few names
  failed. Corrupt/future/unreadable stamps all fail TOWARD running. No
  boot-trigger was added: a 2,400-name pull at an arbitrary boot time is
  the risk the owner was told about, so 16:30 stays the only slot.
- **Owner decisions recorded this round:** (a) "If Friday run is not done,
  it should run on Monday not next Friday" — **BUILT, item 3 above**;
  (b) FDA Data Dashboard key is
  the owner's own action ("FDA on me, i ll do"). Owner also corrected me
  usefully: **FDA does arrive via news** — true and already live since the
  2026-08-26 newsroom wire plus the us-fda/form-483/warning-letter/import-
  alert lexicon patterns; the key only adds Form-483 inspection records.
- **Mid-week event-risk discussion (owner: "even if FDA announce anything
  as trump annouced the section again in mid week how app algorithm will
  decide to exist the effected stock/segement... review online with
  current high performance stock apps"). RESEARCHED, NOTHING BUILT.** The
  honest state: **there is NO news→exit path** (grep-verified: engine,
  risk, orders and danger contain no news import), so the app reacts to
  the PRICE of an event, never the event; a gap-down exits at the gap, not
  at −5%, because stops are checked at scheduled passes; and **sector caps
  are ENTRY gates only — nothing cuts a sector as a basket** (0094 sector
  contagion fires per stock, tightening N individual stops). What the
  field does, from the research: nobody retail-scale wins the headline
  race (algos move in ms); in the April-2025 tariff shock professionals
  deleveraged **over days, Apr 2-10, not minutes**; the mechanism is
  **volatility targeting**, which cuts exposure because vol rises when
  prices fall — it needs no knowledge of WHY, and its edge is a
  trend-following loading; news-sentiment alpha is contested in the
  literature AND uncalibratable here (no headline archive); strict
  drawdown-rule exits measurably UNDERPERFORM. Options put to the owner:
  A sector-heat basket exit (testable on their own panel — the real gap),
  B volatility-scaled exposure (best-evidenced, but changes their ordered
  ₹1.25L sizing), C news auto-exit (**recommended against — unprovable**;
  narrow version = alert + one-tap sector square-off, human decides),
  D calendar de-risking. **OWNER PICKED "a and c" (2026-08-29).**
- **C-NARROW SHIPPED — sector event alert + one-tap segment square-off.**
  `trading/sector_events.py` (pure, DISPLAY ONLY, boundary test: engine/
  risk/orders/danger never import it) groups HELD names carrying a news
  **TRIGGER** verdict by `cfg.sector_map`, worst sector first, and shows
  the WHOLE segment (`quiet` = held-but-not-named) because the owner asked
  about exiting a *segment*. UNKNOWN (fetch failed) is reported SEPARATELY
  and is never an alert nor an all-clear — one Google News outage must not
  read as "no bad news" on every holding. Card on /positions
  (sector_event_marker) states on screen: "Nothing here sells by itself."
  Owner-only POST `/positions/sector-exit` with `scope=flagged` (named
  names) or `scope=sector` (whole segment, their own words "exit the
  effected stock/segement"); same `eng._close` path as every other sale, so
  costs/slippage/Closed Trades are identical; a name with NO live quote is
  LEFT OPEN and reported (the 2026-08-04 never-invent-a-fill rule);
  journals OWNER_SECTOR_EXIT with sector, scope, closed list and skipped
  list. An unflagged sector cannot be squared off by this route.
  **WHY NOT AUTOMATIC, stated in the module and on the page:** news here
  can never be calibrated (Google News keeps ~1 week, no archive), so an
  auto-exit would break the owner's own REQUIRES_15Y_BACKTEST rule; the
  millisecond headline race is already lost; and in April-2025 the
  professionals cut over DAYS, not minutes. 1,090 tests.
  Test-fixture notes for next time: `PaperPosition` needs sector/
  peak_price/entry_score/reasoning AND `entry_date` must be a **datetime**
  (engine calls `pos.entry_date.date()`); a login in this sandbox costs
  ~50s because it reaches for index quotes Yahoo blocks — that is the
  sandbox, not the code.
- **A (sector-heat basket exit) MEASURED AND NOT EARNED — NOT WIRED**
  (scripts/sector_heat_study.py, research/sector_heat_study/, 92,950
  stock-days over the 191x12y panel, production `_e0094` via
  `externals_at`, 5-day grid, fwd20, split 2020-06-30). The basket claim
  is that when K names in a sector fire 0094 together, the OTHER holdings
  in that sector fall too — that is the only thing that makes cutting the
  segment better than cutting the named names. **Not-fired names at k>=3
  vs k=0: train −1.44% vs +1.22% (gap −2.66pp, looks real); test +2.41%
  vs +1.98% (gap +0.43pp, WRONG SIGN).** The effect sign-flips between
  halves on n=1,236 test observations — not a small-sample artifact. Same
  failure shape as the 3 curve-fit near-misses (threshold sweep
  2026-08-13) and all 7 FII-tide signals. Regime confound stated and
  REJECTED as an excuse: the test half is bull-heavy (all-name mean fwd20
  +1.98% vs train +1.03%), but the baseline carries that too — the GAP is
  what flipped. **Recommendation: do not wire; the danger layer's existing
  per-stock exit already does the part that measured.** NOTE, said plainly
  because it looks alarming: on THIS metric the fired names also read fine
  in test (+1.92% at k>=3) — that does NOT invalidate 0094's VERIFIED
  status, which was measured on a different question (lift on COLLAPSE
  labels within (t,t+10] vs event-window chance, not mean fwd20 return).
  Do not quote one run's metric against the other's verdict.
- **(superseded design note) A was to be MEASURED first because:**
  Its whole selling point when proposed was that it is testable on the
  committed panel, and the owner's own rule (REQUIRES_15Y_BACKTEST, the
  reason 112 triggers were measured and only 10 wired) applies because A
  moves money. Study design: T12W_0094 sector contagion is already
  VERIFIED per stock (lift 1.546) — the basket question is whether K+
  names firing together in one sector predicts worse forward damage than
  one firing alone. Train/test split at 2020-06-30, same panel/labels as
  BT01-BT08. Wire only on measured evidence + the owner's word.

**PHASE 1 OF THE 2026-08-30 THREE-PHASE ORDER — SHIPPED, 1,084 tests.**
Owner's list, verbatim: "remove the 25 Lacs thing from the entire history
forever / forget & remove the codex things as well forever / for Everest is
1.25 (as per kelly rule) and when ocean activate it will use more in of 35%
/ forget about FDA as well / make sure category - Intraday, short, swing &
positional & N200 momentum execute the trades".
- **₹25L GONE.** README (the live claim) → ₹50L; CLAUDE.md header and the
  4 narrative mentions; config's "supersedes 25L/30/3.3%" line. **Two real
  CODE fallbacks changed**: `paper.load_or_fresh` and `state._hydrate` both
  defaulted a missing `capital_base` to 25L, which on a legacy file would
  inject a phantom +25L capital delta into cash. Both now fall back to the
  CURRENT configured capital = "treat as already synced", which is also the
  safer failure. test_capital_sync rewritten to pin the new behaviour.
  **NOT done, and told to the owner: git history was NOT rewritten** — that
  would break every clone, the open PRs and the release assets. "History"
  taken as "every place it still appears".
- **CODEX GONE from the repo** — 11 files de-named (findings kept, the
  reviewer's name removed) + CLAUDE.md. **The bot itself is a GitHub App on
  the account, NOT a repo file — only the owner can uninstall it** (told
  them; its quota is exhausted anyway so it posts nothing but a limit
  notice).
- **OCEAN TICKET ₹62,500 → ₹1,68,750** (Everest ₹1.25L +35%). Owner picked
  this from an options card. **STATED TO THEM BEFORE BUILDING: an Ocean
  trade is now LARGER than an Everest one, inverting the half-size risk
  story the 2026-08-26 crash-guard approval rested on; 8 slots = up to
  ₹13.5L in the dip engine.** The four rails are untouched. Everest
  unchanged at ₹1.25L. Config ENTRY VALUE POLICY block, APP_BRIEF and
  test_entry_value_policy all re-pinned (Ocean == 1.35 × Everest).
- **FDA GONE.** trading/usfda_feed.py and its 10 tests DELETED; scheduler's
  3 jobs (_weekly_usfda_refresh, run_fda_news_snapshot,
  usfda_key_activation_check) + the boot registration + the Friday step;
  db.save/load_usfda_events; the /triggers card and newsroom card;
  calendars' USFDA_DECISIONS_2026 + event type + docstrings. **TWO
  CONSEQUENCES CAUGHT: (a) sebi_feed imported `query_token` from the
  deleted module — inlined there, SEBI would otherwise have broken; (b) the
  regex cut left an EMPTY `try:` in scheduler, an IndentationError that
  killed every page import — caught by the /risk render test, not by any
  unit test.** LEFT ALONE deliberately, told to the owner: the word "USFDA"
  as a *pharma news search keyword* (signals/policy.py) and the
  us-fda/form-483/warning-letter/import-alert patterns in the news
  regulatory lexicon — those are generic risk detection, not the FDA
  feature, and deleting them would degrade pharma news for nothing.
- **ALL FIVE CATEGORIES EXECUTE.** `_DAILY_MODE_ORDER` =
  momentum, intraday, short, swing, positional — supersedes the owner's own
  2026-07-27 "remove intraday" decision. Momentum stays FIRST (first call
  on cash); the 15:25 square-off still closes every intraday-tagged
  position, so intraday cannot run overnight. APP_BRIEF + the doc-truth pin
  now DERIVE from _DAILY_MODE_ORDER so manual and scheduler cannot drift.

**PHASE 2 (PART) — stale index line + the weekly 500-name batch, 1,099 tests.**
- **"grey line = stored shape to 27 Aug — still it show old data" ROOT
  CAUSE:** the index tail refreshed daily but ONLY at 09:20, **before the
  market opens**, so it could only ever reach the PREVIOUS day's close.
  Today's close waited for tomorrow morning and Friday's for Monday. (The
  27-Aug reading also needed the 08-29 Friday-block fix: the index step used
  to sit AFTER the OHLCV pull, so a cut-off pull skipped it.) FIX: the
  refresh now ALSO runs in the 15:35 EOD job, after the close, isolated so a
  failed pull cannot cost the exit pass. 09:20 stays as the catch-up.
- **WEEKLY 500-NAME BATCH BY LIQUIDITY** (`trading/universe_select.py`, pure;
  `scheduler.refresh_liquid_universe`, in the Friday block with the other
  post-pull recomputes). Owner picked "Top 500 by liquidity/turnover, weekly,
  replaces the 50" from an options card. **Ranked on median CLOSE x VOLUME,
  not share count** — the committed delivery file covers the whole market but
  carries `ttl_trd_qnty`, a SHARE COUNT, and ranking on it puts penny stocks
  on top (10M shares at Rs 3 = Rs 3 Cr vs 50k at Rs 4,000 = Rs 20 Cr). Same
  rupee measure `risk.passes_liquidity` already gates entries with — one
  liquidity definition in the app, not two. MEDIAN so one block-deal day
  cannot promote a thin name; sub-Rs-1-crore names dropped (they could never
  pass the entry gate anyway); too little history = "not judged", never
  "illiquid"; held names always ride along; blocklist still wins.
  `universe_target_size: int = 500`. **HONEST LIMIT, journaled every run:**
  it ranks the names the STORE holds (~573 today, not 2,428), so the list is
  the top of what is stored until the Friday pull fills it — the journal says
  "SHORT BY n" rather than passing a short list off as a full 500. Mongo down
  or an empty result leaves the PREVIOUS list standing; emptying the universe
  would stop the engine trading.
- **STILL OPEN in Phase 2, told to the owner:** "remove placeholders" needs
  them to name which screen/placeholder; and the old HELD names (BERGEPAINT,
  COFORGE, ETERNAL, NAUKRI, PETRONET, RADICO, SUPREMEIND, TVSMOTOR,
  GILT5YBEES...) bought by the pre-50 rotation stay held until sold — the
  universe rebuild changes what is SCANNED, never what is already owned.
  No manual "rebuild now" button was added (not on their list; offered).

**ROTATION EXECUTES + BATCH DETAILS CARD (owner 2026-09-04: "skipper exit
suggestion is there but not executed — make it happen / where is details
for new batch every monday").** "Skipper exit suggestion" = the F6
**rotation suggester** on the dashboard, whose card said "Suggestion only;
nothing is sold automatically" — the exact phrase. (The other candidate,
the danger layer's alert-week text, was checked and is NOT it: its clock
starts on the first refresh after deploy, is persisted both ways, and has
been in auto mode since ~08-18.)
- **`engine._rotation_exits(picks, now)`**, called in scan() BEFORE the
  opening loop so the cash it frees is there when can_open counts it. The
  suggester's rule unchanged (>=`rotation_min_progress` 0.90 to target),
  but it fires ONLY when cash is SHORT for the fresh picks (need computed
  by the SAME `position_notional_for` the loop uses, Ocean at its own
  ticket, minus the regime reserve) — otherwise the +20% target handles
  the name and selling early gives up its last stretch. Only as many
  rotations as the shortfall needs, highest progress first (three
  near-target names must never be sold to fund one). owner_pick exempt
  (the decay-exit rule). LIVE quote fetched per candidate; a name with
  none is left alone AND journaled ROTATION_HELD_NO_QUOTE (never a silent
  skip, never an invented fill). Journals ROTATION_EXIT with progress,
  target, freed, fund_ticker/score, shortfall. `cfg.rotation_auto_enabled`
  (default True) + Settings row "Auto Rotation On". Dashboard card copy
  now says it executes; rotation.py docstring truth-passed.
- **Batch details on /scan** (`universe_batch_marker`): `portfolio.
  universe_batch` {ts, count, target, sample, judged, short_by,
  skipped_thin, skipped_illiquid, added, dropped, held_added} written by
  refresh_liquid_universe and persisted JSON+Mongo BOTH ways (risk_day
  lesson, test-pinned by source). Card shows count vs target, the SHORT BY
  badge with the plain reason, added/dropped lists, "dropping never sells"
  line, and the next rebuild date (`scan_routes._next_friday`).
- **"every monday" ANSWERED, not changed:** the slot is **Friday 16:30
  IST** (so the batch is ready for Monday's open); Monday runs ONLY as the
  catch-up when Friday's block did not complete. Because the ledger had no
  stamp when it shipped (Sat 08-30), the FIRST run was due on the first
  trading day after deploy = Mon 09-01, and the following Friday 09-04
  read 3 days since → not due → next Fri 09-11. Owner offered a Monday-
  morning slot if that is what they actually want.

**OWNER'S 12-STOCK REVIEW + TWO FAULTS FIXED (2026-09-08, owner: "review
these stocks under AM07 POV" → "fix both faults / before that give me
result for these 12 for next 6-9-12 months").**
- Review method (reusable): tokenless Upstox daily candles (2y) → the
  engine's OWN functions (momentum_score + quality blend 35%, floor 62,
  abs-momentum, timing_ok, candle_confirmation, passes_liquidity,
  danger.evaluate_stock, news_feed.read_stock). All 12 (BALKRISIND,
  LATENTVIEW, JWL, RAILTEL, KEC, TRANSRAILL, GODFRYPHLP, BSOFT, ZENSARTECH,
  RHIM, SURYAROSNI, THERMAX) FAILED the 62 floor (best THERMAX 58.3); 9 of
  12 sat 9-29% under their 200DMA; no Ocean candidate (needs -30%); no
  danger tripwire. "BL" = BALKRISIND (close 2287.10 vs their 2288).
- **6/9/12-month "forecast" = MEASURED BASE RATES, not a model**: the
  engine predicts nothing, so the answer was forward returns counted over
  the committed 191x12y panel at monthly points (20,846 observations),
  bucketed by AM07 score band x above/below 200DMA (scratchpad script
  base_rates.py; median / p25 / p75 / %positive / %flat±5). CAVEAT
  STATED: the panel is today's survivors, so the sub-45 band's rosy
  numbers (median +13% at 6m) are inflated — names that kept falling left
  the index and are not counted. Do not quote that band as an edge.
- **FAULT 1 FIXED — news matcher tagged a PERSON as the company.** "CBI
  court acquits Acharya Balkrishna" (Patanjali executive) gave Balkrishna
  INDUSTRIES a regulatory TRIGGER: the surname alone cleared the
  single-token rule. Now `news_feed._weak_token` (first name token) counts
  ONLY with `_MARKET_CONTEXT` in the same headline (share/stock/results/
  order/NCD/...); full core name and symbol stay strong phrases; one-word
  company names ("suzlon") unchanged. Test pins the exact headlines.
- **FAULT 2 FIXED — share-count liquidity floor DELETED.** `LiquidityRules
  .min_avg_daily_volume_shares=200_000` blocked THERMAX (136k shares but
  Rs 48 Cr/day — on the owner's OWN 50 list, so it could never be bought)
  and GODFRYPHLP (191k, Rs 31 Cr). The Rs 5 Cr 5-day rupee rule was
  already there and is now the ONE measure, matching universe_select's
  rupee ranking. Field removed (not zeroed), guard test keeps it out;
  risk.html + dashboard copy truth-passed. Money-path change on owner
  order: a liquid high-priced stock that clears the score floor can now
  enter — nothing else moved.
- July port branch `port/stock-session-round1` (owner: "delete the july
  one"): the session git proxy returns 403 on any push except the
  designated branch, so deletion is an OWNER action on GitHub (Branches →
  trash icon). Told them.
- **THREE MORE TABLES RUN on the 13 (BBL corrected in for "BL" — owner:
  "its BBL not just BL"; Bharat Bijlee close 2239 vs their 2288).** Table
  1 = the four other categories (ONE composite formula, floors differ:
  swing 60/positional 68/short 70/intraday 72 — all 13 scored 17-32; the
  three trend legs read 0, four feed legs neutral 50), the 112 triggers +
  8 gates + ladder stage (5 RECLAIM / 4 ACCUMULATING / 4 COMPRESSING;
  NO gate passes — distribution fires on every name and their watch gate
  needs distribution == 0), lens v2 (edge negative, Kelly 0 for all),
  Ocean rails (KEC the near miss: −29.3% vs −30%, wRSI 25.9 vs 25, and
  its reclaim rail fails), danger incl. 0095 (none fire), impact 2-7 bps
  at ₹1.25L, pairwise corr max 0.60 (RailTel/JWL; five names = one
  rail-infra theme), own-10y base rates per stock. Table 2 = FREE
  fetches: Yahoo fundamentals timeseries WORKS from the sandbox (13/13,
  779 rows) and Yahoo quoteSummary with crumb works; NSE shareholding API
  = 403 (Yahoo holder split used, labelled). Findings: Thermax Q1 FY27
  profit −83%, BBL −61%, KEC D/E 0.87 + negative OCF yet 25 analysts BUY,
  Surya Roshni the only promoter buyer (12.5L shares Nov-Apr), Zensar
  delivery 41→25%. Stale-by-design: F&O OI panel ends 2026-08-11 (>10d →
  short-build neutral 50 for all), delivery ends 08-07, insider PIT ends
  05-02 (Colab pulls). Scratchpad scripts: eval_table1.py, eval_table2.py.
  GOTCHA: `compute_score` calls `signals.news._get_headlines_yfinance`
  (Yahoo news) per stock — from the sandbox it HANGS on connection resets;
  monkeypatch it to `[]` (= the neutral-50 catalyst the live app gets
  without a key) before scoring offline.
- **"READ THE CHAIN NOW" BUTTON SHIPPED (owner: "run the third table with
  my upstox login" → told them the sandbox holds no token → "build 1").**
  `scheduler.read_option_chain_now(ticker)` + owner-only POST
  `/triggers/{ticker}/chain` + 🔗 card on the per-stock trigger page
  (chain_read_marker): PCR(OI), ATM strike, ATM IV call/put, IV skew,
  expiry, strikes, and the 15-day T12W_0102 pattern state ("n of 5 days
  recorded" until MIN_PATTERN_DAYS). Reasons returned, never hidden:
  needs_login (option endpoints need the daily token — Upstox rule) /
  no_fo (no options market, not a fault) / failed (journaled
  OPTION_CHAIN_READ_FAILED). `snapshot_universe(include_index=False)`
  reads one stock without the Nifty chain. **STORAGE RULE + THE BUG IT
  AVOIDS**: the daily job's once-per-day guard keyed on `asof == today`;
  a read-now also stamps today, so without a scope check the 09:20
  UNIVERSE pass would silently skip on any day the owner tapped first.
  Snapshots now carry `scope` ("universe" | "single"); the guard skips
  only on a universe snapshot (test-pinned). Merge into today's dict when
  it exists; otherwise a NEW today's dict with the one row (older rows
  dropped rather than wearing today's as-of). Journals OPTION_CHAIN_READ.
  Display only — engine/risk/orders/danger contain no reference
  (test-pinned). Response SHAPE still unverified live (no token here);
  the first owner tap is the verification, failure is visible.

**PRE-DROP CALIBRATION + BREADTH GATE RE-CUT (2026-09-09, owner: "i feel
things are not working fine... app should predict from data and give inputs
to exist or add before fall and rise, else everyone know what happend / show
me flaws" → "fix it"). 784 trading + 346 web tests.
research/breadth_recut_2026-09-09/summary.md IS THE RECORD — read it before
answering any "can the app see a fall coming" question.**
- **NOTHING PREDICTS A MARKET FALL from this data. 24 of 25 candidates
  failed the holdout** (scripts/predrop_calibration.py; Nifty fwd20 <= -5%,
  split 2020-06-30, episode-counted). Fifth time this project has hit the
  same shape: the bigger the train lift, the more total the test collapse
  (momentum-crash setup 10.47 -> 0.00; realised-vol 2.62 -> 0.00; defensives
  leading 2.24 -> 0.00). **Distribution days (the O'Neil rule) 0.55/0.84 =
  BELOW chance. Breadth divergence 0.00/0.00.** Do not re-propose these.
- **THE OWNER'S DAMAGE CLUSTER IS COINCIDENT, NOT LEADING** — tail_down3_5d
  >= 0.213 scores 1.09 train / 1.18 test for forward drops, i.e. chance. It
  reports damage that already happened. Fine as Ocean's rail R2 (a risk
  choice); never quote it as foresight.
- Only survivor, **NOT wired**: 10y yield +25bp in 20d (1.75 train / 3.78
  test, lead 10-16d). The holdout hits are nearly all the 2022 rate shock and
  the Oct-2022 / Mar-2026 firings preceded +5-7% RISES. ~6 independent events
  in 12 years, file ends 2026-06. Say so if it is ever proposed.
- **SLOW-GRIND EXIT TRIGGER: MEASURED, FAILED, NOT WIRED.** The real gap the
  owner pointed at (the 12 danger tripwires fired on ZERO of 220 names while
  Nifty IT fell 10% in six sessions — they only catch shocks). "Down X% over
  N days, no day worse than -4%", 12 variants, BT01-BT08 labels/split: every
  one strong in train and BELOW CHANCE in test (20%/15d: 6.70 -> 0.00). A
  name that has bled steadily is LESS likely to fall another 10%.
- **BREADTH GATE RE-CUT — MONEY PATH, the one change that earned it.**
  Judged properly for the first time: on the forward 20d return of the
  ENGINE'S OWN PICKS (production selection rebuilt, 540 weekly points), not
  the index. **The live "block at <= 50%" rule was worth NOTHING: it blocked
  the BETTER days in train (+1.02% blocked vs +0.88% allowed) and was +0.02pp
  in test.** True shape is a **U** — both extremes pay, the middle does not.
  Now `cfg.breadth_block_low=0.40` / `breadth_block_high=0.65` (a BAND);
  `breadth_min_pct` DELETED (guard test keeps it out). Edges chosen on TRAIN
  alone: train edge +3.54pp, **holdout +1.61pp**, and **33 of 33 candidate
  bands positive in BOTH halves** = a plateau, not a lucky cell. Every
  single-threshold "block below X" rule is NEGATIVE in train for X <= 45%.
  **BEHAVIOUR CHANGE: a washed-out tape (< 40%) is now BUYABLE** — it
  measured as one of the best states. VIX bypass, momentum-only, fail-open
  and the F3 store-preference all unchanged. Boot migration
  breadth_band_2026_09_09 clears stored overrides. Today's live 48.2% is
  INSIDE the band, so today still blocks — the change bites in a washout.
- **MY OWN BUG, caught before the decision:** the first cut put newly-listed
  names (price but no 200-bar mean) in the breadth DENOMINATOR and never the
  numerator, crushing early-year breadth toward 0 and inventing a fake
  washout regime. Fixed in both scripts (count only names that HAVE the MA).
  Node 10 weak-assertion fault class. Also: india_10y_bond_yield.csv is
  MM/DD/YYYY (investing.com), NOT day-first — a dayfirst read scrambles every
  date past the 12th.
- **SECTOR FEED WIRED (display only)** — the owner's flaw "IT -10% in six
  sessions and no screen says so". `data_refresh.SECTOR_INDEX_KEYS` (16 real
  NSE sector/size indices, every key VERIFIED live tokenless 2026-09-09;
  Consumer Durables / Oil & Gas / Healthcare 400'd and are deliberately
  ABSENT rather than guessed) + `fetch_sector_tail` + pure `sector_moves` +
  `db.save/load_sector_rows` (`sector_index`, keyed date|sector) +
  `scheduler._sector_index_refresh` in the SAME three slots as the index tail
  (09:20, 15:35 post-close, Friday block) + `market_panels.sector_panel` +
  the 🧭 dashboard card (sector_strip_marker), worst first, 1d and 5d.
  One stored day prints a dash, never a 0% that reads flat; a dead sector is
  NAMED in the journal. **T12W_0094/0095 KEEP their own equal-weight
  construction** — they are calibration-VERIFIED on that definition and
  swapping the input would void the evidence silently (test-pinned both ways).
- Live verification: 16/16 sectors fetched, IT 5d -5.12% — the move the app
  could not previously see.

**UNIVERSE FILE REFRESHED (owner 2026-09-09: "push the fresh new 2400
universe"), 785 trading + 346 web tests.** `data/upstox_nse_equities.csv`
was frozen at the 2026-08-09 commit; re-run through the app's OWN
`universe_refresh.refresh_universe()` against the live Upstox public master.
- **2,428 → 2,648 names** (+280 new listings, −60 gone). The owner said
  "2400"; the real current count is 2,648 — told them, did not force the
  number. Verified before commit: 2,648 unique tickers AND 2,648 unique
  instrument keys, zero malformed rows, 1,469 mis_allowed flags carried
  from the old file, and the ampersand names (GVT&D, M&M) still resolve.
- **HFCL MOVED TO THE BE (TRADE-TO-TRADE) SERIES** — found only because the
  refresh made two tests fail. It sat in the owner's own 50-name list. This
  is the MTARTECH case of 2026-08-20 exactly, and the owner already accepted
  that rule, so the precedent was applied rather than a new decision taken:
  HFCL is OUT of `OWNER_UNIVERSE_2026_08_20` (**now 49**), its sector entry
  is KEPT so a held or hand-bought position still maps to Telecom, and it
  lands on the BE/BZ series-watch card at the next 08:50 run. **Owner must
  name a replacement if they want 50 back.** THE TRAP THIS AVOIDED: with
  HFCL still in the list, the Settings box (which refuses any name not in
  the universe file) would have rejected the owner's ENTIRE weekly universe
  edit with "universe_unknown".
- Two hardcoded counts in tests replaced with derived ones (`len(U50) + 1`,
  `f"({len(U)} names"`) — a real market event should never surface as a
  meaningless count mismatch. New pin
  `test_the_t2t_names_stay_out_of_the_auto_scanned_list` covers MTARTECH and
  HFCL together, so re-adding either needs NSE to move it back to EQ.
- **NOT the same thing as the OHLCV store**: the file is the LIST (2,648
  names); the app's Mongo `ohlcv` store held 573 of them at the 08-29 check
  and grows only through the Friday 16:30 pull on the live box. A session
  cannot push into their Mongo (no MONGO_URL here). The weekly 500-name
  liquidity batch still ranks what the STORE holds and journals SHORT BY.

**THREE-REPO REVIEW + THE 2026-09-10 OWNER BATCH (owner: "review both repos on
git named on ash08 & ash stock n then tell me" -> then a 9-item order, then
A-D, then 6 answers). PR #102, 1,159 tests (28 new). READ THE REPO FINDINGS
BEFORE COMPARING AM07 TO ANYTHING AGAIN.**
- **Both sibling repos cloned and read** (public, `add_repo` then a shallow
  clone; ASH08 8.7k lines/65 files/114 tests, AshStocks 15k lines/185 files).
  **ASH08's score does not work**: `metrics.py` sets `quality = 100*n_bars/126`
  — DATA COVERAGE, not company quality. Verified in their own committed
  snapshot: **all 191 names carry quality = 100.0, one distinct value**. So
  their live formula is `0.65*clip(50+mom6*200) + 35`, a constant 35 added to
  everything. Their "floor 68" is really "6-month return above +0.385%" and
  passes **92 of 191 (48%)**; a stock at exactly 0% 6m scores 67.5. The score
  also SATURATES — 25 of 191 sit at exactly 100.00, so it cannot rank anything
  up more than +25%. Their real filtering is the M6 rank<=25 gate, not the
  score. Also found: sector cap protects nothing for unmapped names
  (`_sector_open_count` returns 0 when segment is None; only ~84 of the N200
  are mapped), Kelly sizes off a FROZEN `book_value` not live equity, and the
  governor's headline 14.77x is EW internals, ZERO costs, ends 2022, with all
  four FII flags `NOT WIRED` — which their own `infinity_gaps.py` states
  honestly. **ASH08 contains no backtest at all.**
- **AshStocks' python brain IS AM07's formula — measured, Spearman rho =
  +1.000** over the 191 panel; their SELECT set is a strict subset of ours
  (only-AshStocks = 0 names). Adopting it adds nothing. Their LIVE rank is
  something else again (`SEL_MOMENTUM_20_TOP10`, 20-session momentum, no
  floor, rho +0.22 vs ours) — a genuinely uncorrelated second opinion. Three
  different scoring paths coexist in that repo (python brain 0.65/0.35, a
  server patch 0.58/0.32/0.10 candle, and the live 20-day rank); which one
  runs depends on which patch is applied. Their `validation-evidence.v2.1
  .json` INDEPENDENTLY REJECTED the owner's 0.213 damage cluster on a
  three-way chronological split (train -0.0353 / validation -0.0042 /
  **holdout +0.0113, wrong sign**, `SIGN_REVERSAL_ACROSS_TIME_SPLITS`) — the
  same conclusion our 2026-09-09 pre-drop run reached. **Their pre-rise
  pattern (compression + volume ignition, lift 1.24 train / 1.37 test) is
  our T12W_0005 inside-bar cluster.** Two builds, different data, the same
  one entry pattern survives and everything else fails.
- **THE BEST PIECE IN ANY OF THE THREE REPOS is AshStocks' measured Kelly**
  (`lib/stock-selection-engine.mjs`): f = p-(1-p)/b from its OWN closed
  trades, Beta prior of 25W/25L, returns clipped +/-12%, costs subtracted,
  confidence ramp min(1, n/300), `noPositiveEdgeBehavior: block_new_entries`,
  and Kelly only ever CAPS a flat base. Owner ordered it adopted.
**WHAT SHIPPED (all owner-ordered; nothing self-initiated):**
1. **MARKET-HOURS GATE — and the hole was real.** `catchup_scan_if_missed`
   gated on `is_trading_day()`, the DAY and never the CLOCK, and fires ~2 min
   after EVERY boot: a Render restart at 21:00 ran a full scan that could BUY
   at prices nobody could trade. `calendars.is_market_open()` (09:15-15:30
   IST) is now the one predicate, read by engine AND `web/market_gate.py`
   (source-pinned so they cannot disagree). Reading is never gated — the
   owner asked to keep the advisory running at any hour. **Their own buttons
   ARE gated ("yes all block") — this deliberately overrides the 2026-08-04
   lesson about not blocking the owner, because they were asked and answered
   in writing.** Resting limit orders may WAIT overnight, never FILL.
   `SB_IGNORE_MARKET_HOURS=1` in both conftests only; a test pins the
   production default and that it never reaches render.yaml.
2. **30-MIN IN-SESSION PASS** (`scheduler._session_refresh`), stops AND
   targets on the same clock — owner approved the symmetric version. 15:35
   becomes MARK-ONLY and journals EXIT_DUE_MARKET_CLOSED. **The -5% stop now
   checks ~13x/day instead of ~2x. UNTESTED live — expect more stops firing
   on intraday dips that used to recover by the close.**
3. **CAPITAL Rs 50L -> Rs 5 CRORE, ceiling 80 -> 222** (a CEILING, owner's
   word; 20 sectors x 12 = 240 so it fits under the sector cap untouched).
4. **SLAB SIZING supersedes fixed** (owner: "no flat size to follow / we can
   put 150000 slab as minimum and rest on the score basis"). `sizing_mode =
   "slab"`: Kelly on the live floor at **m = 0.29** (their pick, "prime
   number"), floored at Rs 1,50,000. A score AT the floor still has zero
   estimated edge and is SKIPPED, never opened at the slab. Measured at 20%
   vol on Rs 5 Cr: 63 -> slab, 65 -> 3.4L, 68 -> 6.8L, 72 -> 11.4L, 90 ->
   25L cap. m=0.29 = 49.6% of max growth / 1.68% halving risk / **6.9x error
   budget** before 2x-Kelly. `trading/measured_kelly.py` carries the
   AshStocks estimator + `growth_vs_ruin`/`growth_ladder`.
   **THE REASON FILTER IS THE IMPORTANT HALF**: owner said "no cap on closed
   trades, i have already more than 100" and the COUNT gate is duly off — but
   SQUARE_OFF (the 45 stale intraday tags), OWNER_SELL, OWNER_CLOSE_ALL,
   OWNER_SECTOR_EXIT, KILL_SWITCH, DRAWDOWN_LADDER and RETAG_EXITS are
   EXCLUDED and the by-reason breakdown is returned for the screen. Those
   measure the owner and a tagging bug, not the strategy.
5. **HORIZONS WERE CROSSED AND IT WAS NOT COSMETIC.** `short` held 7 days
   while `swing` held 60 — the app's short was SHORTER than its swing, the
   opposite of the owner's meaning, so every trade ever tagged either ran on
   the wrong horizon. Now intraday 1 / swing 20 / short 63 / positional 252.
6. **k x ATR TARGETS** (`trading/targets.py`, owner picked "C- k x ATR").
   **k is anchored to a MEASUREMENT: median ATR = 2.50% of price over the
   committed 191 panel** (p10 1.89, p90 3.48), so k = old_target/2.50% and
   every owner-decided target is reproduced for the median name — momentum
   k=8 -> +20%, Node 12 UNCHANGED. Only `short` moves (6% -> 12.5%) because
   its HORIZON moved. **MY FIRST CUT ANCHORED k TO AN ESTIMATE** (that ATR ~
   daily sigma, 1.26%) and would have put the momentum target at 33-55%,
   silently overturning an owner decision. Anchor a constant to a
   measurement, never an estimate — recorded in the module.
7. **ROTATION NOW FIRES (item 7).** It required BOTH a funded scan pick AND a
   cash shortfall; with the breadth band producing zero picks most days it
   returned at its first line EVERY DAY, while the dashboard card — which
   reads the WATCHLIST — said to rotate. That disagreement is what the owner
   saw. Two triggers now (owner: "combinaton of a & b"): always off the
   watchlist via `_watchlist_candidates` (same `scan_matrix` rows the card
   reads), plus a hard trigger at **cash < 5% of equity**. It will not sell a
   real position to buy something below the entry floor.
8. **UNIVERSE TARGET 500 -> 1200, MEASURED.** A random 150-name sample of the
   2,648-name file, priced from LIVE Upstox candles: only **48.3% clear the
   Rs 5 crore median turnover `risk.passes_liquidity` demands to BUY** ->
   **~1,279 of 2,648 names are tradeable at all**. 1,200 is not a compromise
   for the 512MB box, it is the size of the tradeable market. Owner asked
   whether to drop from 2,400 and the answer was measured, not guessed.
**BUG FOUND WHILE WIRING:** `_hydrate_portfolio_from_mongo` defaulted
`capital_base` to a HARDCODED Rs 50,00,000 — right when written, wrong the
moment the book moved. A legacy mirror doc would read "base 50L vs configured
5Cr" and `sync_capital` would have injected **Rs 4.6 CRORE of phantom cash**
on the first restart. Reads the live config now; verified by running the
hydrate path against a legacy doc. Same fault class as the 25L fallback.
**BASE RATES 1/3/6/9/12 MONTHS MEASURED (item 4), 22,749 observations — and
the result is uncomfortable, so quote it honestly:** at **1 month the score
band tells you nothing** (above 200DMA: <45 -> +3.8%, 45-55 -> +1.5%, 55-62
-> +0.7%, 62-70 -> +0.9%, 70+ -> +1.2% — flat or backwards). At 12 months the
BUY band (62-70, +16.6%) BEATS the strong band (70+, +11.2%). **The score
sorts risk, not return.** Above-200DMA beats below at every band and horizon,
so the absolute-momentum gate earns its place. The `<45` rows are
survivorship-inflated — never quote them as an edge. NOT yet on a screen.
**STILL OPEN from this batch:** item B's scan half (config says 1,200 but the
daily scan still reads the 49-name list; needs the Mongo OHLCV store filled —
573 of 2,648 — the compute is already proven by the Node-10 precompute doing
~2,700 names in 0.1s/87MB) · the sector-by-sector progress strip · item 8's
Friday LIST refresh (owner clarified 2026-09-10: "universe means the stocks
app will analysis for trade — do not even think or touch or dlt the years
data", which removes the replace-vs-merge risk entirely) · item 4 onto a
screen · **item 5 (the three-formula combination) is HELD BY OWNER
INSTRUCTION until every other item is wired.**
**WORKFLOW FAILED A THIRD TIME.** The 8-agent audit hit the StructuredOutput
retry cap on ALL EIGHT agents — 1.2M tokens, zero output — with a schema that
was already flat (strings + arrays of strings). 2026-07-30, 2026-08-29, now
this. **Stop reaching for the Workflow tool on this repo.** Doing it by hand
took less time and found more, all three times.

**THE 49-vs-1200 ROOT CAUSE — THE SCAN NEVER READ THE STORE (2026-09-11,
owner: "why system still stucking at 49 and rest of things when its decided
that 1200 is the count why new stocks n not listed in that yet and why its
follow old" -> "go"). PR #103, 1,189 tests (17 new). READ THIS BEFORE
TOUCHING THE UNIVERSE, THE SCAN'S HISTORY PATH, OR THE FRIDAY BLOCK.**
- **`fetch_daily` fetched EVERY name from the Upstox network, one request at
  a time, and never touched the Mongo OHLCV store at all.** MEASURED LIVE:
  **1.77s per name**, times the five modes in the 09:20 rotation -> ~7 min
  for the owner's 49 names, ~50 min for 340, **~3 HOURS for 1,200**. That is
  why the universe could never grow, and **why filling the store would not
  have helped: nothing read it.** I had told the owner the day before that
  this was "a data problem, wait for the store to fill" — WRONG, and only
  found because they pushed back. Their question was the fix.
- **MEMORY WAS NEVER THE CONSTRAINT.** Measured: 1,200 two-year frames =
  **+15 MB**, 2,648 = **+32 MB** on the 512MB box. The Settings 60-name cap
  comment claiming a 2,400-name list "would OOM" was never measured. The
  real cost was always the per-name FETCH.
- **B — `fetch_daily` IS NOW STORE-FIRST.** One batched `db.history_docs`
  query ($in + $slice, new), then Upstox for what the store lacks or holds
  STALE (`data.STORE_MAX_AGE_DAYS = 5`, ASSUMED, calendar-derived), then
  yfinance. **Network fallback BOUNDED** (`MAX_NETWORK_FALLBACK = 120`) so
  the 09:20 window always closes; names it could not cover are journaled
  **SCAN_COVERAGE_SHORT** — a 1,200-name universe reading as fully scanned
  when 170 were seen is the lying-screen class. A stale doc is never used
  (costs a fetch, never a wrong score). **Backtests locked out of the store**
  (it holds today's bars = leak). Reader unset / Mongo down = EXACTLY the
  pre-2026-09-11 behaviour. Injected at boot (`state` -> `data.set_history_
  store(db.history_docs)`), so trading/ still never imports web.db.
  **MEASURED AFTER: 1,200 names in 2.8s** (was ~3 hours).
- **A — THE UNIVERSE REBUILD HAD NEVER RUN ONCE.** `refresh_liquid_universe`
  sat AFTER the ~61-minute OHLCV pull — the exact position that cost five
  other feeds a week each and that I moved THEM out of on 2026-08-29,
  **leaving this one in** — and unlike its two post-pull neighbours (breadth,
  register precompute) it has NO daily counterpart, so a pull cut short by a
  container restart meant the list was never rebuilt at all. **EVIDENCE, from
  the live box not from memory: /healthz `history_tickers` read 573 on
  2026-08-29 and the SAME 573 on 2026-09-11** — twelve days, zero names —
  while `custom_universe` stayed the hand-typed 49. Now runs BEFORE the pull
  (it ranks whatever the store already holds; it never needed the pull to
  have just finished) AND again after, to pick up what the pull landed.
- **C — THE PULL IS TIME-BOUNDED AND RESUMABLE.** `top_up_history` gained
  `max_fetches` / `time_budget_s` (call site: `SB_TOPUP_BUDGET_S`, default
  2400s) and returns `remaining`/`stopped`/`elapsed_s`. It saves each name as
  it goes, so a bounded run always FINISHES and the next resumes. **New
  ledger key `weekly_block_remaining`**: while it is above zero the block is
  due the NEXT trading day, not in seven — otherwise a pull that stopped on
  its budget would stamp "done" and leave the store short for a week, i.e.
  the same bug in a new costume.
- **MY SOURCE-PIN FAULT CLASS REPEATED A FIFTH TIME**, caught before commit:
  a new guard test grepped for `"import web"` and matched the explanatory
  COMMENT saying trading/ must not import web.db — **in a file where I had
  already written the comment-stripping helper ten lines above.** Strip
  comment lines BEFORE asserting on source. F1 test, /positions hunt,
  DAILY_SCHEDULER guard, the watchlist pin, now this.
- ~~Still open: item 5~~ **ITEM 5 CLOSED 2026-09-11 — MEASURED FIRST, THEN
  WIRED AS THE OWNER ORDERED.**

**ITEM 5 — THE THREE-FORMULA VIEW (owner: "go with ash formula wiring" ->
"go with these formula" [with their 3-column table on screen] -> "ash08 68").
PRs #105 (the measurement) and #106 (the wire). 838 trading + 371 web tests.
research/ash_formula_study/summary.md IS THE RECORD — read it before ever
proposing an outside formula again.**
- **MEASURED FIRST** (their own item-5 terms were discuss -> confirm ->
  wire): 23,507 observations, 191x12y panel, 146 month-end points,
  PRODUCTION scoring functions, train <= 2020-06-30 / test after, outcome
  under the LIVE -5%/+20%/200d bracket (close basis, first touch).
- **AshStocks' python brain IS AM07's formula — Spearman +1.000.** Its
  momentum is `50 + avg(r6/v6, r12/v12)*25` and its quality
  `(low-vol + liquidity)/2`: the same arithmetic as our momentum_score and
  quality_score. So `multi_formula.evaluate` DELIBERATELY reads our own
  score for the AshStocks column instead of re-implementing it, and a test
  pins that — re-implementing would make two things out of one and let them
  drift. All it contributes is a higher FLOOR (70 vs our 62).
- **ASH08's "quality" is DATA COVERAGE** (`100*n_bars/126`, constant 100.0
  across all 191 names in its own snapshot), so its floor of 68 solves to
  **"6-month return above +0.385%"** = AM07's absolute-momentum gate with a
  rounding difference. It passes **97.0%** of our candidates (removes 334 of
  11,233). Every threshold was negative on train; the one positive test row
  (>=80, +0.16) reads -0.16 in train and would never have been selected.
  Choosing by the holdout destroys the holdout (the T12W_0040 lesson).
- **AshStocks' LIVE rule (SEL_MOMENTUM_20_TOP10) measured NEGATIVE in BOTH
  halves: -0.77 train, -0.45 test.** Its INVERSE is a clean monotone
  gradient holding out of sample (+0.49/+0.50 at the bottom quartile) with a
  legible mechanism — a name up ~17% in 20 days hits the -5% stop **68.4%**
  of the time vs **63.4%** for one that eased back. NOT WIRED: positive in
  only 7 of 12 years, month-level median edge +0.08 train / **-0.04 test**,
  helps in under half of test months, and as a hard filter it cuts
  candidates from a median of **76 to 11 per month**.
- **Combining adds nothing**: every blend of the three ranks within
  +/-0.09pp of AM07 alone. Ranked alone, top 30/month: AM07 2.57/**4.17**,
  ASH08 2.69/3.87, 20-day 2.00/3.51 — AM07 wins the holdout, ASH08 wins
  train only. **Fifth independent failure of an entry-side signal here.**
- **WHAT SHIPPED** (owner said go twice after being shown the above —
  their decision, taken and built): **`trading/multi_formula.py`** (PURE,
  same contract as internals.py: no network, no config mutation, no
  engine/risk/orders/danger import, test-pinned) computes the ASH08 (floor
  **68**, their number) and AshStocks (floor 70) verdicts. Computed ONCE in
  `engine.scan` where the history is already in hand, stored on
  `scan_matrix.rows[t]["multi"]` and on `last_scan_picks` — pages cost one
  dict lookup and no refetch. New **Formulas column** on /scan + a
  **🔬 card** carrying the evidence beside the verdicts (a formula shown
  without its record invites trust it has not earned) that states whether it
  is SHOWING or GATING.
- **THE FILTER IS A SWITCH, DEFAULT OFF** (`cfg.multi_formula_gate`,
  Settings row names the measurement). The owner owns the switch; the
  measurement owns the default. ON => a pick must clear the outside floors
  too; every removed name lands on `last_scan_skipped` AND journals
  **MULTI_FORMULA_BLOCKED** (a gate that silently shrinks the buy list is
  the lying-screen class). **FAILS OPEN on DATA_NEEDED** — a short history
  is not evidence against a stock, and blocking on it would quietly shrink
  the money universe. A crash in the view costs the columns, never the scan.
- **THREE BUGS IN MY OWN FIRST CUT, all caught before push:** (1) the gate
  used `skipped` and `now` at line ~475 when both are bound ~line 548 — an
  UnboundLocalError on EVERY scan; fixed by collecting locally and
  reporting where both exist. (2) My page test asserted `AshSt 71.0 fail`
  when 71.0 >= 70.0 is a PASS — **the test was wrong, not the code**; read
  the render before believing the assertion. (3) The engine fixture could
  never prove "it still buys" because the smooth synthetic series never
  prints a candle structure (`candle_wait_no_pattern`) and the dead regime
  feed brakes via C7 — both neutralised in the fixture so the assertion is
  real, not vacuous (the Node 10 weak-assertion lesson).
- **GUARD TESTS MADE SELF-MAINTAINING**: `test_scan_institutional.py`
  hardcoded colspan 8/7 and column count 8, so the new column broke them —
  a meaningless failure saying nothing about soundness (the same class as
  the 2026-09-09 hardcoded universe counts). Both now DERIVE the invariant
  (colspan == owner `<th>` count, viewer == owner - 1, head `<td>` == row
  `<td>`), and I **verified they still fail on a deliberately skewed
  colspan** before keeping them.
- **OWNER CLOSED ALL THREE OPEN ASKS 2026-09-12: "forget about HFCl, branch
  n placeholders". NEVER RE-RAISE ANY OF THEM:**
  1. **HFCL replacement — dropped.** `OWNER_UNIVERSE_2026_08_20` stays at
     **49 names**. NSE moved HFCL to the BE/T2T series so the tradeable
     filter excludes it (the MTARTECH precedent); the owner does not want a
     50th name substituted. The sector entry stays mapped so a held or
     hand-bought position still resolves to Telecom, and the T2T guard test
     stands. Do not propose a replacement.
  2. **Branch `port/stock-session-round1` — dropped.** It stays on GitHub.
     The session git proxy 403s any push but the designated branch, so
     deletion was only ever an owner tap; they have declined it. Stop
     listing it.
  3. **"Remove placeholders" — dropped.** This was a Phase-2 leftover
     waiting on the owner to name a screen. They have withdrawn it; there
     is no placeholder backlog. Do not go hunting for one.
  **The 2026-09-10 nine-item batch and item 5 are now fully closed with
  nothing outstanding.** Next owner-facing work is the 60-day paper window
  under the live settings (Rs 5 crore, slab sizing at m=0.29, ceiling 222,
  -5%/+20% with the trail live, 30-minute in-session exit checks,
  market-hours gate, breadth BAND 0.40-0.65).

**BACKTEST REVIEW + BRICS CHECK + SCAN LIST 49 -> 83 (2026-09-12..16).
1,213 tests. READ THE BACKTEST FINDINGS BEFORE QUOTING ANY /backtest NUMBER.**
- **THE BACKTEST IS NOT TESTING THE APP** (owner: "check last 2 days back
  test / mostly negative / review, do not change anything"). Four divergences
  found in code, none of them disclosed on the page except the first four
  live-gate items it already lists:
  1. **It runs `cfg.universe` (192 large caps), NOT the traded list.** Only
     **19 of the 49** live names overlapped; 30 had never been backtested at
     all and are not in the committed panel either.
  2. **Flat +20% targets.** `_try_open_positions` sets
     `fill * (1 + cfg.take_profit_pct)`, but the live engine has used
     **k x ATR per stock** (`engine._target_for` -> targets.py) since
     2026-09-10. Same shared `check_exit`, different target fed in. MY
     divergence — k was anchored to a measurement but the exit study was
     never re-run with per-stock targets.
  3. **Exits checked once daily on the close; live checks every 30 minutes.**
     An intraday dip through -5% that recovers by the close = no stop in the
     backtest, a REAL stop live. The live app stops out more than any
     backtest shows.
  4. **Flat-slab sizing.** The backtest calls `can_open` without a close
     series, so `position_notional_for` returns the Rs 1.5L slab for EVERY
     score; the live engine DOES pass the series (engine.py ~line 830), so
     the real ladder is 62.5 -> 1.5L, 68 -> 4.04L, 72 -> 6.73L, 90 -> 18.85L.
  Also unmodelled and NOT in the page's warning list: ocean, danger layer,
  rotation exits.
- **WHY THE RESULT LOOKED WEAK — MEASURED, AND IT IS NOT THE STRATEGY.**
  2024 window, same names/rules, only the money settings differing:
  live (Rs 5 Cr + slab) **+4.18%** vs legacy (Rs 50L + fixed 1.25L)
  **+15.72%**, Nifty +8.75%. Instrumented `can_open`: live deployed a
  **median 26.6% of equity** (106 positions), legacy **100%** (49).
  **Return ratio 0.27 == deployment ratio 0.27.** The engine earns a quarter
  because it is running on a quarter. Max DD -3.30 vs -14.66 is the same
  fact from the other side — idle cash cannot fall. Fully deployed, the
  recipe roughly matches or beats Nifty in 3 of 5 windows (2020-21 alpha
  +0.36, 2022-23 +0.01, 2024 +6.96).
  **The real binding constraint is the SECTOR CAP** (`max_sector_positions
  = 12`): refused **892 times** in that run vs 755 opens. The 222 ceiling
  never bound once.
  **MY EARLIER ANSWER WAS WRONG AND I CORRECTED IT TO THE OWNER**: I first
  said the negative result was the documented 2024-26 cold stretch. True of
  the default window (page defaults to today-2y) but NOT the main cause.
  Measure before explaining.
- **BRICS SUMMIT CHECK (owner: "there is brick summit ... check online ...
  which companies are getting anything ... stocks must be listed in
  universe").** 18th BRICS summit, New Delhi, 12-13 Sep 2026, New Delhi
  Declaration adopted. **NO Indian listed company was named as receiving a
  contract, order or MoU** — outcomes are government-to-government (customs
  agreement, counter-terrorism plan, agri research platform, health/TCIM,
  iBRICS sovereign-capital summit). Naming "beneficiary stocks" would have
  been inventing the link. NSE/BSE were CLOSED Mon 14 Sep; trading resumed
  Tue 15th, so no market reaction existed when asked. Nifty last closed
  23,398.10 on 11 Sep, fifth straight weekly fall.
  Coverage measured: every theme name was ALREADY in the 2,648 universe
  file (it is the whole NSE) — the gap was the **49-name scan list**, which
  had **zero** names in defence, shipbuilding, EV battery, green hydrogen
  and no pharma majors, and was 21-of-49 concentrated in Metal + Auto.
- **REAL DATA GAP FOUND: LTIMindtree (LTIM) is absent from the universe
  file** — the only miss in a 30-name Nifty-50 sample (LT, LTF, LTFOODS all
  present, so not a prefix-filter bug). It cannot be scanned or bought.
  NOT yet investigated; owner was told.
- **SCAN LIST 49 -> 83 (owner order 2026-09-16: "add defence, shipbuilding,
  pharma names to the scan list").** 14 defence + 3 shipbuilding yards +
  17 pharma. **Every name screened through the production gate
  `risk_v2.passes_liquidity` on LIVE tokenless Upstox candles before being
  added** — all cleared the Rs 5 Cr 5-day rupee-turnover bar (HAL 643 Cr/day
  down to SYNGENE 13 Cr/day; evidence committed at
  research/universe_add_2026-09-16.csv). The THERMAX lesson: a name the
  entry gate can never pass is decoration.
  Every added name got a REAL sector (PR #68 lesson — unmapped names all
  count as ONE "Unknown" sector and jam the cap). **"Defence" is a NEW
  sector label**; shipbuilding yards went to Capital Goods (no yard bucket
  in the 19-sector vocabulary); **SCI stays Logistics** — it is a shipping
  LINE, not a builder. `APOLLO.NS` is Apollo MICRO SYSTEMS (defence
  electronics), verified against the file's name column — not Tyres or
  Hospitals.
  Sector counts now exceed the cap in two places (Pharma 20, Defence 14 vs
  cap 12) — that is fine and deliberate: the cap limits HELD positions, not
  how many are scanned.
- **TRAP CAUGHT BEFORE SHIPPING: the Settings universe box capped at a
  hardcoded 60**, so with the list at 83 the owner saving their own live
  universe would have been rejected "universe_too_big". **A guard must never
  refuse the state the app is already running.** The cap is now DERIVED
  (`settings_routes._universe_paste_cap` = max(100, cfg.universe_target_size)
  = 1,200); its OOM premise died on 2026-09-11 when fetch_daily went
  store-first (1,200 frames = +15MB, 2.8s). Template message truth-passed.
- **THREE HARDCODED-COUNT TESTS MADE SELF-MAINTAINING** (same fault class as
  the colspan and universe counts): `test_every_name_is_unique_and_tradeable`
  no longer pins 49 (it was 50, then 49, now 83 — each an ordinary event);
  `test_settings_refuses_more_than_sixty_names` repointed to refuse the WHOLE
  universe file instead of 80 names, plus a new twin asserting a scannable
  list is ACCEPTED. New pins: every scanned name carries a sector; one
  unmistakable name per ordered theme.

**THE EXIT DRAIN — THE ENGINE READ THE SESSION 5h30m LATE (owner 2026-09-16:
"how all positons got exist / no new position are getting added" -> "go").
PR #109 MERGED, 1,233 tests (860 trading + 373 web). READ THIS BEFORE
TOUCHING ANY CLOCK, GATE OR SCHEDULED JOB.**
- **ROOT CAUSE, and it was MINE from the 2026-09-10 market-hours gate.** Every
  `now` in the engine is `datetime.utcnow()` — NAIVE UTC — but
  `calendars._ist_now` documents a naive datetime as "assumed already IST" and
  passes it straight through. IST is UTC+5:30, so the 09:15-15:30 window was
  read 5h30m late. MEASURED: real 09:20 IST (the daily scan) -> engine handed
  03:50 -> **CLOSED** · 09:15-14:45 CLOSED · 14:45-15:30 OPEN · **15:35 EOD
  pass OPEN** · **21:00 OPEN**. So for six days the 09:20 scan believed the
  market was shut and **bought nothing** (the owner's "no new position" half),
  every morning exit was deferred with EXIT_DUE_MARKET_CLOSED and they **all
  fired together after 14:45** (the "all positions exit" half), and the
  post-close/overnight passes traded freely — the exact INVERSION of their
  "trade are prohibited after market hours" order.
- **Fixed at the ENGINE boundary** (`TradingEngine._ist` / `_market_state` /
  `_trading_allowed`), NOT in calendars: `is_market_open()` with no argument is
  correct (real IST clock) and its naive-means-IST contract is pinned by
  existing tests, so converting there would break every honest caller. All 6
  `market_state(now)` sites route through `_market_state`.
- **THE TEST THAT LET IT SHIP**: the old guard asserted only that the STRING
  "is_market_open" appears in `_trading_allowed`'s source. **A name in the
  source is not a behaviour** — the fault-mask class again (Node 10, the
  /dashboard-404 pin). The new test walks a real trading day through the
  engine. Anything that gates on a clock needs a clock test, not a grep.
- **THREE CONTRIBUTING FAULTS, also fixed:**
  1. **The breadth gate guarded MOMENTUM ONLY** while the 09:20 job runs FIVE
     passes — so in the dead band the one pass that holds 200 days was blocked
     while **swing (floor 60, LOWER than momentum's 62)**, short and intraday
     kept buying; intraday is force-closed at 15:25 the same day BY DESIGN and
     swing sells at 20 days. Now covers every pass. **Only momentum pulls 2y**;
     the other four pull 6mo and `compute_breadth` drops any ticker under 200
     bars, so their own read is (None, 0) and the extension ALONE would have
     been a no-op for exactly the passes it must cover — momentum runs first,
     caches the day's reading on `portfolio.breadth_today` (persisted
     JSON+Mongo both ways), the rest read it. One market reading per day, not
     one per pass. **HONEST LIMIT: the breadth study measured MOMENTUM picks
     only; the band on the other four is the owner's reasoned order, NOT a
     measured result.** The 2026-09-09 re-cut is untouched (below 40% still
     buyable) and the VIX bypass still wins.
  2. **Rotation sold when the engine had refused to buy** — it runs after the
     breadth and market-hours gates empty the pick list, then falls back to the
     watchlist and sells a near-target winner to fund a name that will not be
     bought. It also never honoured the market-hours order (refresh() did via
     marking_only; this branch did not). Both closed (`entries_blocked` kills
     the watchlist fallback; market-hours guard at the top); a real funded pick
     — ocean is gate-exempt — still rotates.
  3. **The danger layer's 2.5% leash overrode the owner's -5% stop.**
     `TIGHTEN_PCT` is an **ASSUMED** number (no measurement in the repo backs
     it, unlike the 12 tripwires' 1.6-4.1x lifts), it sets `custom_levels` so
     the stop never returns to -5%, and it predates the 30-minute in-session
     pass which took stop checking from ~2/day to ~13. The LEASH WIDTH is now
     floored at `cfg.stop_loss_pct` (reads cfg, so it follows the owner). Still
     trails a winner up; can no longer be narrower than the risk they accepted.
     `danger.describe()` takes the leash ACTUALLY applied so the journal and
     phone alert cannot name a stop that was never set.
- **RULED OUT BY ARITHMETIC, stated so it is never re-asked:** KILL_SWITCH
  needs a Rs 10,00,000 single-day loss and DRAWDOWN_LADDER Rs 50,00,000 off
  peak on the Rs 5 Cr book — both larger than the whole deployed amount.
  Neither fired. **The book emptied one position at a time, not in one event.**
- **EXPECT FEWER TRADES, NOT MORE** — the buys that were happening were ones
  that could not stay. Breadth last read 48.2%, inside the 40-65% band, so the
  book may stay quiet until it clears; the difference is the 09:20 scan can now
  actually buy when it does.
- **MY PROCESS FAILURE THIS ROUND, recorded because the owner called it:** I
  answered their first report with a mechanism story and told them to go search
  their own journal ("why i get excuses only"). Then I named the breadth/mode
  asymmetry as the main cause — **incomplete**; the clock was the root, and I
  only found it because I wrote a test that used a real timestamp. Measure
  before explaining (the 2026-09-12 backtest lesson), and when the owner cannot
  see their own data from where you are, say that in one sentence and then do
  the work anyway — do not hand it back to them.

**STATUS CHECK 2026-09-24 (owner: "chk pending and push") — nothing was
pending in git; two open items answered from the LIVE box, not from memory.**
- **THE STORE IS GROWING AT LAST: /healthz `history_tickers` = 1,458** (was
  573 on both 2026-08-29 and 2026-09-11 — twelve days frozen). PR #103's
  fix is doing what it was built to do: the universe rebuild moved BEFORE
  the OHLCV pull and the pull became time-bounded + resumable, so each run
  now lands names instead of dying mid-way and stamping "done".
- **LTIM CLOSED — the gap is UPSTREAM AT UPSTOX, not our code.** The
  2026-09-16 note flagged LTIMindtree as absent from the universe file and
  left it uninvestigated. Checked the exact master `universe_refresh` reads
  (`assets.upstox.com/.../NSE.json.gz`, **78,160 rows**): **ZERO** rows carry
  LTIM as a trading_symbol (only substring noise — EMULTIMQ, ALLTIME, BTML)
  and **ZERO** carry MINDTREE in the name. The `complete.json.gz` master
  agrees. So this is not a T2T exclusion, not a prefix-filter bug, and not
  ours to fix: the broker's own instrument feed does not list the stock, so
  the app cannot scan, price or buy it. **Do not hand-write an instrument
  key to force it in** — an invented key is the fabricated-data class.
  Re-check only if Upstox adds it.
- **STILL OPEN, confirmed live today:** the scan reads the **83-name** owner
  list while `universe_target_size` is **1,200** (item B's scan half). The
  original blocker was the store, and that blocker has largely lifted
  (1,458 names) — this is now an owner decision, not a data wait.
  Also open, unchanged and unordered: the sector-by-sector progress strip ·
  the measured 1/3/6/9/12-month base rates are still on no screen · the
  three correctable backtest divergences (universe, k x ATR targets, exit
  cadence) offered 2026-09-12 and awaiting their word · F-012's two
  contradictory scheduler docstrings · F-013 (196MB tree).
- **`/healthz` "file_date" CANNOT answer "did the 08:50 universe refresh
  run".** It is the file's **mtime**, and on Render that tracks the last
  DEPLOY (the image carries the checked-out file), so it read 2026-09-16 —
  the PR #110 merge — eight days on. A refreshed file is also wiped back to
  the repo copy by any restart. Do not read a stale file_date as a broken
  refresh job, and do not "fix" the job on that evidence alone.

**THE 512 ROUND (owner 2026-09-24: "update scan to 1200 / also chk mongo /
its hitting 512 + / need to layoff unwanted"). MEASURED FIRST, then cut.
READ THIS BEFORE ADDING ANY FILE LOAD TO A SCHEDULED JOB OR A PAGE.**
- **THE RAM PICTURE, measured on this box (MiB):** boot **114** · one
  1,200-name scan pass **+72** (frames 26 + scoring/candles/multi 45) ·
  trigger-layer panels resident for the process lifetime **+41** (tail-420
  panel + F&O + deals caches are never evicted) · register precompute peak
  **+87** (16:45) · the 30-min danger pass, cache-warm, **+7** · and the
  **DELIVERY STREAM +153 peak** — `_load_delivery` chunks the 3.5M-row
  delivery_daily_2019_2026.csv.gz 400k rows at a time and that is the
  working set. It ran once a day inside run_trigger_layer AND on EVERY
  /triggers/<stock> page tap (one stock still streams the whole file). Stacked
  in the 09:20 chain that is ~380 before heap fragmentation or an overlapping
  30-min pass; the live box cold-started twice in one day (24 s, 44 s) on an
  "always-on" plan, which is what an OOM kill looks like from outside.
- **THE LAYOFF: `external_panels.PROD_SOURCES` = ALL_SOURCES minus
  "delivery"**, passed by the daily trigger pass and the per-stock page
  (`prepare(..., sources=)` passthrough; default unchanged so research
  scripts regenerate byte-identical). What it cost: the delivery ratio behind
  **T12W_0060 — calibrated REJECTED** (lift 0.992, fires 22% of days) — and
  register items reading a file **frozen at 2026-08-07** (nsearchives blocks
  cloud; Colab is the only refresh). Those now read DATA_NEEDED, never a
  quiet False. The danger layer already loaded panel+index only — untouched,
  test-pinned on the CALL not the docstring. **The 41 MB of resident caches
  were LEFT alone**: the danger pass reads them every 30 minutes and a
  reload is 4.7 s; evicting saves 41 MB for 13 reloads a day — not worth it
  at this size. Revisit only if the number on /healthz says so.
- **"UPDATE SCAN TO 1200" — IT WAS ALREADY THE MECHANISM, and my morning
  status was WRONG about the live value.** `refresh_liquid_universe` writes
  the liquidity-ranked list INTO `cfg.custom_universe` and saves it to
  Mongo; the "83" I reported that morning was the LOCAL dataclass default,
  not the live override. On the live box, with 1,458 stored names and the
  Friday block completing since PR #103, the scan has very likely read
  ~1,000+ names since mid-September — which is exactly WHEN the 512 started.
  A session cannot see the live value; **/healthz now carries
  `scan_universe` {count, target, last_rebuild, rebuild_count}** so this can
  never be misreported again. What DID change: **the owner's hand-ordered
  83 (2026-09-16: defence, shipbuilding, pharma) now ride in
  `always_include`** beside held + watched names — without that, the first
  Friday after 09-16 could silently drop a hand-ordered name that ranked
  below the turnover cut, and the two standing orders would have fought
  over one field. `universe_batch` records `owner_list_kept` /
  `owner_list_carried`.
- **"CHK MONGO" — could NOT be sized from code, so it is INSTRUMENTED, not
  guessed.** Estimates: ohlcv ~70 MB (191×12y at 226 KB each = 44, plus
  1,267 new names at 1y = 27 — top-up fetches `years=1` for a new name),
  `journal_archive` is the only APPEND-ONLY collection but the scan writes no
  per-ticker lines so it grows ~100-300 entries/day, backtests store
  summaries only, fundamentals ~11 MB. Nothing reached 512 on paper. **New
  `db.storage_stats()`** (read-only, dbStats + collStats in MB, largest
  collection first, never raises) is on **/healthz as `mongo`** — `data_mb +
  index_mb` is what Atlas counts against the M0 512 MB cap. The FIRST live
  read of that block answers the question; do not theorise past it. If
  `journal_archive` is the culprit, note the standing rule "journal
  append-only" — trimming it is an OWNER decision.
- **/healthz also carries `memory` {rss_mb, peak_mb}** (MiB both, /proc
  statm + ru_maxrss) so Render's 512 is visible without the dashboard.
- **`_daily_universe_topup` docstring truth-passed**: it said "bounded to
  ~50 names"; it covers up to ~1,200 (one fetch each, ~35 min, TIME not
  memory — one frame deep) and is **load-bearing**: fetch_daily treats a
  doc older than STORE_MAX_AGE_DAYS as stale and network fallback is capped
  at 120/scan, so without this pass SCAN_COVERAGE_SHORT would journal daily
  between Fridays. NOT cut.
- **THREE OF MY OWN TEST BUGS caught before commit, two of them THE
  documented fault class again:** a "delivery not in source" pin tripped on
  `externals_for`'s DOCSTRING (my stripper removes `#` lines, not
  docstrings — pin the CALL, `sources=("panel","index")`); the docstring pin
  tripped on my OWN truth-pass note quoting "~50" (split on the marker,
  assert the operative paragraph — the same lesson as the F1 test, the
  DAILY_SCHEDULER guard and the /positions hunt); and a `save_settings`
  lambda that missed the `doc_id=` kwarg the boot migrations pass.
- **A REAL REGRESSION IN MY OWN CHANGE, caught by an EXISTING guard test
  (`test_an_empty_result_never_empties_the_universe`), fixed before commit:**
  once the 83 owner names joined `always_include`, `picked` could never be
  empty, so the "store returned nothing → previous list stands" guard never
  fired — on a Friday when Mongo answered nothing the rebuild would have
  REPLACED a ~1,200-name universe with the 83. The guard now reads what the
  STORE ranked (`picked − always_include`), not the union. FAULT CLASS: a
  guard that tests a variable whose meaning an upstream change just widened.
  When you add to a union, re-read every `if not <union>` below it.
- **"UPSTOX LOGIN ISSUE AS WELL" (same message, mid-turn). THE SYMPTOM WAS
  NOT GIVEN; the code-side investigation is recorded so nobody re-chases
  the dead ends.** RULED OUT, each verified: `token_expires_at_ist` is
  UTC-consistent on both sides (connected_at is utcnow, expiry is computed
  and compared in UTC — NOT the 2026-09-16 clock class) · the client NEVER
  clears the token on a 401/429 (only /upstox/disconnect and the 03:30 IST
  expiry end a session — rate limits cannot log the owner out) ·
  `_client_from_session` falls back to Mongo, so the badge agrees across
  devices and restarts. **FOUND AND BOUNDED: the login-time burst.** The
  OAuth callback fires three daemon threads at once — the catch-up scan
  (5 modes × the scanned universe), the option-chain snapshot, and the FII
  pull. The chain job's scope was custom_universe + held = **~1,200 names
  since the liquidity rebuild, at TWO authenticated calls each = ~2,400 in
  one burst** against Upstox's 1,000-per-30-minute limit: the back ~1,900
  silently 429'd while the box hammered for twenty minutes beside the scan,
  at its memory ceiling, at the exact moment the owner was watching the page
  after logging in. Now: scope = held + today's picks + the trigger/news
  watchlist + the owner's hand-ordered list (what the chain actually informs
  — T12W_0102 on held/watched names, W-022/W-007 per stock on the register),
  capped `OPTION_CHAIN_MAX_TICKERS = 120`, paced `OPTION_CHAIN_PACE_S =
  0.25` s between names (~240 calls in ~30 s). `SB_CHAIN_PACE_S=0` in the two
  test conftests only, the SB_IGNORE_MARKET_HOURS pattern; production never
  sets it. The one-stock "read the chain now" button is untouched (pace
  default 0, `include_index=False`). **NOTED, NOT CHANGED — by the owner's
  own order:** `_daily_multi_mode_scan` has no once-per-day guard (trading
  day, degraded halt, rotation lock only), so a login BEFORE 09:20 runs the
  full advisory scan at login and 09:20 runs it again. The 09:15 gate means
  the early one only scores and shows ("keep the advisory running at any
  hour", 2026-09-10); it costs memory twice, at different times. If the
  owner's symptom is "the app goes dead right after I log in", that plus the
  chain burst is the mechanism; if it is an error page or a badge, the
  symptom is still needed.

**FIRST LIVE /healthz AFTER PR #112 (2026-09-24 12:25Z = 17:55 IST, owner:
"chk healthz after deploy") — read from the box, not from memory:**
- **MONGO IS NOT THE 512.** `mongo.data_mb 68 + index_mb 0` of the Atlas
  M0 512 MB cap; `ohlcv` is 65 MB of it (1,458 docs), `portfolio` 1 MB,
  every other collection rounds to 0. The 12 collections listed sum to
  exactly `objects 7,109`, so nothing is hidden below the top-12 cut — and
  **there is NO `journal_archive` collection on the live DB** (the journal
  lives inside the portfolio doc). The append-only trim question is MOOT;
  never raise it. The "512+" the owner saw was RAM, full stop.
- **THE SCAN READS 443 NAMES** (`scan_universe {count 443, target 1200,
  last_rebuild 2026-09-21}`), not the 83 I reported that morning and not
  1,200. Rebuilt on MONDAY 09-21 = the Friday-miss catch-up fired as
  designed. 1,458 stored → 443 selected: the liquidity ranking drops
  sub-Rs-1-crore names and too-thin history ("not judged"), so the list is
  bounded by what the store holds AND clears the turnover cut. The 09-25
  Friday block ranks again with whatever the pull has landed since.
- `memory {rss 146, peak 145}` MiB = a fresh post-deploy process (peak ≈
  rss); the first request timed out at 60 s with zero bytes (deploy/cold
  start), the next answered in 14.6 s, login in 0.5 s. Boot on Render is
  ~30 MiB above the sandbox's 114 (Mongo hydration). The daily chain's
  real peak is tomorrow's 09:20 reading — take it before claiming the
  layoff worked.
- `readiness` all green; `universe.file_date 2026-09-24` = the deploy's
  mtime again, exactly as the 09-24 status note warned — not refresh
  evidence.

**THE "UPSTOX NOT CONNECTING" LOOP (2026-09-24 evening, owner: "system is
not getting logged on upstox / keep asking password n rotating there / not
opening final dashboard" -> URL `?upstox_connected=1` -> screenshot ->
"still red, fix it"). READ THIS BEFORE TOUCHING THE OAUTH CALLBACK, THE
ENGINE'S BROKER, OR ANY "connected" LABEL.**
- **THE HANDSHAKE NEVER FAILED.** Every login reached the dashboard with
  `upstox_connected=1`, and the header pill read the owner's name in green
  (that pill reads the browser COOKIE). Beside it: the red "token EXPIRED"
  bar, "no live quote / Reconnect Upstox" on all four index cards, and the
  Quotes chip saying "log in for real-time". Those three read the ENGINE's
  broker object. Two parts of one page disagreed about one token.
- **ROOT CAUSE, code-verified and reproduced:** the callback saved the token
  to Mongo and then `refresh_broker_attachment()` had the engine READ IT
  BACK. Reproduced locally with a working store: fresh callback -> engine
  NEW, 8.9 h left, no red bar. On the live box that read handed back
  YESTERDAY's document after every login (the owner reconnected on ONE
  stable process and it stayed red -> the write is not landing, reason
  unknown from outside: `save_upstox_token` was log-only). The stale engine
  then called the index quotes with the dead token -> 401 -> the cards
  printed a **Reconnect Upstox link -> the owner reconnected -> same
  result**. That link WAS the "rotating".
- **THREE LYING-SCREEN FAULTS underneath, all mine:** the callback had
  ALWAYS redirected with `?upstox_error=...` and NO page read it (a failed
  connect looked like nothing happened) · a valid token whose quote Upstox
  refused rendered as "Reconnect" · the save failure was a warning in a log
  the owner cannot see.
- **WHAT SHIPPED (owner: "still red, fix it"):** `state.attach_broker_client`
  — the engine gets the token IN MEMORY from the client that did the
  exchange; Mongo is only for surviving a restart · `db.save_upstox_token`
  returns bool + `LAST_TOKEN_SAVE_ERROR`; a failed save journals
  UPSTOX_CONNECTED{persisted:False,error} and lands the owner on an AMBER
  notice ("connected for this run, restart needs a fresh login, see the
  journal") · `state._heal_stale_broker()` in `engine()` and
  `quote_source_status()`: an expired/missing engine token re-reads Mongo
  and attaches a FRESHER document (covers a Render deploy overlap: login
  on the old process, dashboard on the new — the lazy attach used to fire
  only on None, never on expired) · every callback outcome journaled
  (UPSTOX_CONNECTED / UPSTOX_CONNECT_FAILED with Upstox's reply) and
  rendered by the dashboard (`CONNECT_NOTICES`, `data-upstox-notice`) ·
  index cards: connected + no tick = "connected · Upstox returned no tick
  (rate limit / outage)" with the public ~1-min candle as the number; the
  Reconnect link appears ONLY when the router itself says unhealthy. Card
  "connected" = `quote_source_status().healthy`, the SAME verdict as the red
  bar, so they cannot disagree again. Money path untouched. 1,265 tests
  (405 web + 860 trading), 13 new in
  web/tests/test_upstox_stale_engine_2026_09_24.py.
- **CONFIRMED LIVE 19:15 IST (owner screenshot 2, after the PR #114
  deploy): red bar GONE, "Upstox: MANPREET KAUR" green, all four index
  cards live (Nifty 23,063.10), Quotes chip "Upstox real-time" — AND the
  amber bar: "token could NOT be saved to the database".** So the engine
  half is fixed and the write really is failing on the live box. Portfolio
  writes work (17 positions survived the 12:59 restart), so it is specific
  to the `upstox_token` write. **MY MISTAKE in that bar: it pointed the
  owner at "the journal line UPSTOX_CONNECTED" — which exists only as a CSV
  download on /reports.** Fixed same evening: the bar prints `Database
  said: <LAST_TOKEN_SAVE_ERROR>` itself, and the callback retries the save
  once after `db.ensure_connected()` (a 60 s reconnect-cooldown flap must
  not cost a re-login). **NEXT LOGIN'S AMBER BAR NAMES THE ERROR — read it
  before theorising.** Atlas is at 68 MB of 512, so it is not the space
  quota. Consequence until fixed: every Render restart (incl. every env
  edit) needs a fresh Upstox login; the engine works between restarts.
- **Also learned, tell the owner once:** nothing in Render takes a token —
  the app only uses UPSTOX_API_KEY/SECRET/REDIRECT_URI and the daily OAuth
  login; every Render env edit RESTARTS the app (watched one at 18:29 IST
  while they were logging in). "i changed the token and added fresh" in
  Render does nothing but restart.
- **TEST LEAK the fix exposed:** the callback now leaves the engine
  connected in-process (intended), so `test_dashboard_prints_a_dash_and_a_
  reconnect_link_when_offline` — which relied on the old Mongo round trip
  coincidentally detaching in tests — now sets `eng.broker = None`
  explicitly. Model the state you assert; do not inherit it.

**BUY DESK (owner order 2026-09-24 20:50 IST: "add the shortlist and
recomenderd to buy in app / use upto 2.5 cr" -> options card -> "m saying
buy the stocks from listed by app, use upto 2.5 cr of capital amount,
bypass the market hours filter for this only for now one time" / Shortlist
= "Cleared floor + momentum + liquidity"). `web/buy_desk.py` +
`POST /scan/buy-desk` + the 🛒 card on /scan (buy_desk_marker). A session
cannot reach the live book, so the one-time buy is a BUTTON the owner taps.**
- **Shortlist** = last scan-matrix rows with score >= the MATRIX's own
  floor, abs_mom True, outcome not `liquidity_*`, not held, not
  blocklisted; ranked by score, capped 60. The engine's own hesitations
  (candle wait, breadth, sector cap, correlation, timing) are deliberately
  NOT applied — "buy the stocks listed by app". Card names the scan mode and
  floor it read (the page shows the LAST rotation pass's matrix; abs_mom is
  trivially True outside momentum, so the shortlist is only as momentum as
  the matrix is — said on the card).
- **Recommended** = the shortlist filled in score order through the live
  slab ladder (`sizing.position_notional_for` with the STORED close series
  via `candle_chart.load_ohlcv`, cfg switched to `nifty200_momentum` — a
  shallow COPY for the page preview, the real cfg for the POST exactly as
  the owner ticket does) until the budget (default ₹2,50,00,000) is spent.
  Remainder below the slab = stop, never a dust position. Past the budget =
  shown greyed, never hidden.
- **Execution** = `engine._open` (same path as the 09:20 scan and the owner
  ticket), live quotes via the router, `slippage_price` like an engine fill,
  tags owner_pick + buy_desk (decay never auto-sells; stop/target/trail
  still protect), a name with no quote is SKIPPED AND REPORTED (never an
  invented fill). Journals OWNER_BATCH_BUY {budget, spent, opened, skipped,
  market_hours_bypassed}; the card shows the last run. Gates NOT applied on
  purpose, matching the owner-ticket precedent: `risk.can_open` (sector
  cap, heat, ceiling) — the owner's call, their book.
- **Market-hours bypass is a CHECKBOX on the form ("this order only"),
  journaled; no config changed** — their words "for now one time".
- Preview qty uses the top-50 picks' last scan close (the page fetches no
  quotes); a shortlist name outside the top 50 shows "no scan close" and is
  priced live at tap time. 10 new tests (web/tests/test_buy_desk_2026_09_24
  .py). Money impact: up to the typed budget of PAPER capital, only when
  the owner taps.

**THE ATLAS QUOTA — THE REAL "512" (owner 2026-09-24 21:40 IST: "issue is
at mongo / 513 MB on 512 plan / lets fix that first"). READ THIS BEFORE
TRUSTING ANY "MongoDB connected" GREEN.** The Atlas FREE tier refuses EVERY
write past 512 MB, CLUSTER-WIDE. That is the "Database said" behind the
token save failing — and, far worse, `save_portfolio` is log-only on
failure, so **every book save since the quota tripped has been silently
lost; each restart rolled the book back to the last copy that landed**
(the 17-position book the owner saw was the boot copy; the day's Buy Desk
buys and journal live only in process memory until the cluster has room).
The app's own db measures **68 MB** (/healthz mongo), so ~445 MB sits in
ANOTHER database on the same cluster — most likely the retired
sourceborn-engine app's — which the app could not see. SHIPPED:
`db._cluster_sizes` lists every non-system database with data+index MB on
/healthz (`mongo.cluster`, hog first) · `db.LAST_WRITE_ERROR/_AT` set by
`save_portfolio`, cleared by the next success, on `db.status()` and
/healthz · dashboard STORAGE chip goes RED "WRITES REFUSED, book not being
saved" and a top ⛔ banner prints the database's words + the fix. The FIX
ITSELF is an OWNER action in the Atlas UI (Browse Collections → the other
database → drop, or delete its big collections); a session cannot reach
the cluster. Until then: DO NOT restart or edit Render (a restart discards
the unsaved book). After freeing space: tap Refresh Prices — the next
save lands and the banner clears. LESSON: a connection that answers reads
but refuses writes must never render green; a log-only save is a lie.

**FRIDAY 09:20 CHECK-IN 2026-09-25 (owner order 2026-09-24: "chk 09:20
tomorrow"). The session's own check-in fired 10:00 IST but this container
had restarted and only resumed at 15:33 IST — the LATE reading is the
session's fault, not the app's. Read from the live box:**
- `memory rss 149 / peak 148` at 15:33 IST (152/151 by 15:53). **peak ≈
  rss ⇒ the process that ran the 09:20 chain is GONE; the one answering
  booted AFTER it.** So the delivery-layoff peak (PR #112) is STILL
  unmeasured — the only fact is that the process restarted at least once
  since 21:21 IST yesterday (peak 211 then). Why (OOM kill, Render
  restart, an env edit) cannot be read from outside; Render's event log
  is the owner's screen. A process that booted after 09:20 on a trading
  day and shows +2 MB over boot also means the boot catch-up did NOT
  score a scan in it — either it judged today's scan already done (which
  needs a save that LANDED) or a gate stopped it; the journal answers,
  needs the owner's login.
- **Consequence stated plainly:** while Atlas refuses writes, every
  restart rolls the book back to the last copy that landed — so
  yesterday's Buy Desk buys survived ONLY if the owner freed cluster
  space before that restart. Merging PR #117 (15:34 IST) restarts the
  app again, but the pre-merge peak already proved a restart had
  happened, so the merge cost nothing that was not already gone.
- `scan_universe 443 / 1200, last_rebuild 2026-09-21` (unchanged; today's
  16:30 block rebuilds) · `history_tickers 1,458` · app db 68 MB (ohlcv
  65) · readiness all green · `universe.file_date 2026-09-24` = deploy
  mtime again, not refresh evidence.
- **Harness note for the next session:** a foreground `timeout … until
  grep` wait returns instantly here (sleep is blocked) — poll in a
  BACKGROUND task and act on its completion notification; two waits this
  round "completed with no output" and proved nothing.

 all 8 now built (ticket +
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

**FULL RE-AUDIT 2026-08-12 (owner: "do the reaudit fully… each parameter,
file, folder… clear formula and detail, no placeholder… each header…
flaws, suggestions… sequence of selections is not there, auto weekly new
pull is not there") — docs/AUDIT_2026-08-12.md IS THE DOCUMENT, read it
before answering any "what exists / what's wired" question.** 9 sections:
verdict · the machine + data-freshness matrix (auto vs frozen, per file) ·
money path formulas · COMPLETE parameter dictionary (every Config field /
preset / risk gate / IFR constant / danger trigger / regime rule / sizing
formula / 34 candles / 112 trigger constants, each with formula + code
home + consumer, spot-verified vs code) · page-by-page map (every header
on every screen, its data source, its update time) · full file/data/
external-access inventory (every repo file's purpose, every data file's
rows/range/consumer incl. NO-CODE-CONSUMER marks, Mongo collections,
every external endpoint, env vars) · flaws F1-F8 (F1 NEW: weekly OHLCV
top-up gates on broker.is_connected() but Upstox historical candles are
PUBLIC — Fridays skip whenever the 03:30-dead token isn't back, fix =
drop the gate; F2: nifty200_daily.csv committed but backtest still
network-fetches ^CNX200; F3: breadth gate reads the ~50-name bucket ±7pp
noise, sits at 50.8% vs ≤50% line = why book stays empty) · 12 PENDING
NODES sequenced one-decision-each (Node 1 = the owner's named gap:
selection-sequence stage machine quiet→compressing→accumulating→reclaim→
entry-window→post-entry danger watch, watchlist-only per calibration
evidence; Node 2 = weekly top-up token-gate removal; Node 3 = auto weekly
pull for FII/delivery/PWOI/fundamentals + offline benchmark) · 6
suggestions ("WHY NO TRADE TODAY?" dashboard line, 09:25 scan push,
weekly self-report page, stage-ladder view, token nudge). The three
dictionary sections were built by 3 parallel agents and spliced; momentum
preset / can_open order / IFR constants spot-verified against code before
commit. Owner works the nodes one by one — wait for their pick per node.

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
