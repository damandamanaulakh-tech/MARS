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
- Book: `max_positions 30`, `max_position_pct 3.3%` (research favored ~25).
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
  held names exempt for exits. Exits per-mode (momentum −12/+12 — **user
  set +12 on 2026-07-21**, was +30, partial 0.07, UNTESTED vs +30, 60-day
  window judges; trail −15, decay, 200d max-hold); intraday ±2.5/1.5
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
**STILL OPEN, owner action needed: set `OWNER_PWD` in the Render dashboard.**
The fallback is `"sourceborn"` — which is also the brand name, so it is
guessable and un-greppable. Until it is set, the app on a public URL has a
one-word password.
**2026-07-27 external-review adoptions (owner: "yes push all 3"), 449 tests:**
1. Full NSE 2026 holiday list (16 dates incl. Holi/Diwali/election day) in
   `calendars.NSE_FIXED_HOLIDAYS` — `is_trading_day()` was already wired into
   every scheduler job, only the list was thin. 2027 = 4 fixed dates only,
   refresh from NSE circular ~Dec 2026; missing dates fail safe.
2. Nifty 200 (^CNX200) secondary backtest benchmark — the fair ruler for a
   N200-momentum strategy. `BacktestResult.benchmark2*`, walk_forward rows,
   /backtest page. Unfetchable ⇒ visible DATA_NEEDED warning, alpha vs N50
   unchanged. NOTE: ^CNX200 fetch from Render UNVERIFIED (sandbox has no
   yfinance network) — first live run tells.
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
- F6 FII/DII: ON HOLD per owner — they will push fresh data to git; when the
  file lands, commit it and never ask again.
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
  research/chityy_archive/ (parameter registry 22k params, signal studies,
  trigger tables, master_30000_stock_list) · data/chityy/
  master_extended_chityy.csv REBUILT clean from master_extended.xlsx (was
  corrupt, zero line breaks) · data/fii_dii_daily_2014_2023.csv (2,339
  daily rows for FII-tide research). NOT FOUND in any asset: the
  sourceborn_chityy .py code — probably inside PRE RISE_FALL_TRIGGER_
  LAYER_CSV_CODE_v0_2.zip (browser-only link); owner may asset-attach it
  or the code stays release-only. Back.Test.Claude.zip = duplicate of the
  repo's 191-ticker panel (release-only).
- Original archive plan (superseded by the above; kept for context):
  do NOT put it in the git tree (100MB/file limit, Render deploys clone the
  repo). Owner instructed to publish a GitHub RELEASE on am07, tag
  `chityy-archive`, with the 8 original zips as assets. WHEN THE RELEASE
  APPEARS: index it in data/README.md + here; pull the small code zip and
  commit sourceborn_chityy code + clean master_extended_chityy.csv (repo
  copy is CORRUPT, zero line breaks) + picks CSVs into
  research/chityy_archive/. Check for the release with the GitHub tools.
**Audit findings NOT yet fixed (owner deferred, still true):** −15% trail is
unreachable under −5%/+12% (peak must clear +11.76% but +12% closes first —
the −5% stop decision silently killed the "workhorse exit") · `partial 0.07`
is documented but has NO consumer · entry scores on the momentum blend and
exits on the AM07 composite (two different rulers) · owner custom stops
wiped on boot by `retag_positions_per_mode` · `apply_mode_preset` overwrites
8 fields you save in Settings · drawdown governor resets to 0 every restart ·
`broker.bid_ask()` does not exist (spread check never ran) · FII/DII CSV is
22 months stale, no guard · owner password `sourceborn` is printed in
login.html · `/scan/parameter/{key}` is unauthenticated ·
`engine.scan/refresh` and all of `web/scheduler.py` have ZERO tests ·
orphans: auto/autoswap/intraday/macro/providers (910 lines), all of
research/tunnel_studies (dead `/home/user/sourceborn-engine/` paths),
`data/chityy/master_extended_chityy.csv` is CORRUPT (zero line breaks) ·
docs/WORK_LIST.md still marks A2/A4/A5 PENDING though `a185d94` shipped them.
User's 8-step plan validated this session: all 8 now built (ticket +
parameter pages closed the last two). COMMUNICATION (user demanded,
binding): DISCUSS FIRST — no code until explicit yes; before any build,
one line on what it changes for their money; user reads git — never
dismiss their code reads, locate what they saw (ocean 5-line call-site
vs 207-line engines/ocean.py). User frustration peaked 2026-07-21; the
Upstox/Zerodha framing that landed: app = brain (decides), broker =
hands; go-live = 60-day proof + their word, then real orders small +
kill switch.

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
