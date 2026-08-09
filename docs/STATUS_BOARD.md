# STATUS BOARD — everything asked/suggested, wired or pending (2026-07-11)

> **FOSSIL (2026-08-09 audit):** dated 2026-07-11 and heavily superseded — cap is 80 not 50, full exit stack is live (not buy-and-hold), the remove button was replaced by SELL, floor is 62 not 55, stop is −5% not −12%. Kept as history only.


Legend: ✅ wired+live · ⏳ pending (reason given) · ❌ closed by your decision
Asked-by: U = you asked · C = Claude suggested, you approved

## ✅ DONE — wired and live on Render
| # | Item | Asked | Proof |
|---|---|---|---|
| 1 | Regime correction trigger −7%→−12% | C | config live |
| 2 | Position cap 12→30→50 | U | config live |
| 3 | 100% deployment (0 reserve bull) + buy-and-hold (no exits) | U | config live; decay+crash-brake stay by design |
| 4 | Category/Target/Stop per position | U | positions page |
| 5 | 12 dead tickers fixed (ZOMATO→ETERNAL etc.) | C | universe |
| 6 | 30-day login | U | auth |
| 7 | Remove-position button | U | positions page |
| 8 | Backtest repaired (was all-trades-day-1) | U | /backtest |
| 9 | walk_forward() validator | C | trading/backtest.py |
| 10 | F&O-expiry halt removed (engine benched 4d/mo) | C | calendars |
| 11 | P0-P2 audit batch: abs-momentum gate · score floor 55 · momentum in 09:20 cron · drawdown crash-brake wired · dynamic cash reserve · sector capital cap 25% · stale-data guard · NSE holiday guard · PAPER_ONLY hard lock · config snapshot per trade | C (you approved all) | engine/risk |
| 12 | Chityy ports C1-C4: quality co-scorer (35% blend) · /proof honesty ledger · universe-from-CSV loader | C | live |
| 13 | 512MB fixes: journal RAM caps + Mongo archive · YF thread-pool cap · equity-history cap · gc between modes | U ("hitting 512") | shipped |
| 14 | **Upstox-first data routing** (Yahoo 429-blocks cloud = root cause of dead scans/backtests) | C | data.py; needs your morning Upstox login |
| 15 | Universe file (2,376 NSE stocks) + FII/DII CSV committed | C (you approved) | data/ |
| 16 | 10 real-data backtests: COVID/war/election + 2017/2018/2019/2021/2023/2024/2025-26 | U ("5+5 tests") | docs/BACKTEST_*.md |
| 17 | Real 10y OHLCV dataset cleaned+committed (16 stocks, corp-actions adjusted) | C | data/history_15stock/ |
| 18 | Positions page v2: Current ₹ · Value ₹ · P&L ₹/% · To-Target · To-Stop-Loss · TOTAL row; jargon → DETAILS | U | live |
| 19 | Dashboard: UNREALIZED P&L (real marks) · 🎯Today's Picks · 👀Watchlist · ⚡Engine-Triggers card | U ("which parameters hit daily") | live |
| 20 | Intraday square-off DOUBLE-bug fixed (job never registered on Render + wrong gating) — closes 15:25 IST by position tag | U ("why kept overnight") | scheduler/engine |
| 21 | Master file inventory: 81 files, dups removed, 10 groups | U ("big table of files") | docs/FILE_INVENTORY.md |
| 22 | **Batch C candle engine**: breakout / URR upthrust-retest / engulfing as BUY-CONFIRMATION gate + Upstox intraday candles API | U | trading/candles.py; skips show as candle_wait_* |
| 23 | 200-DMA regime fix (was silently dead → "insufficient data") | U (item 17) | regime.py 14mo fetch |
| 24 | News catalyst via YOUR OpenAI key (env OPENAI_API_KEY) | U ("render added for openai") | signals/news.py |
| 25 | Label clarity: equity = cash+holdings; Deployed = market value | U (TOTAL mismatch) | dashboard |
| 26 | Durable memory: CLAUDE.md protocol + carryforward + connector advice + other-AI extraction prompt | U | repo + chat |

## ⏳ PENDING — queued, with the honest reason
| # | Item | Why pending | Unblocks when |
|---|---|---|---|
| ~~F17~~ | ✅ DONE 2026-07-12: NIFTY candle chart card + per-pick confirming-pattern label | — | — |
| F1 | Sweep remaining Drive folders (Full App/FII/Claude Master 07/GPT, "100s more") into inventory | bulk read+index work | session time |
| ~~F9~~ | ✅ DONE 2026-07-12: Capital-Used + %-of-Capital cols + gross P/L split cards | — | — |
| ~~F13~~ | ✅ DONE 2026-07-12: pct fields shown/entered as % with % suffix | — | — |
| ~~F4~~ | ✅ DONE 2026-07-12: ⚠DECAY amber badge on positions near the score-decay floor | — | — |
| ~~F5~~ | ✅ DONE 2026-07-12: watchlist "Why not bought" column (cash/slots/sector/candle) | — | — |
| E | GURU vs Engine head-to-head on real 10y data | needs one full session | session time |
| ~~F10~~ | ✅ DONE 2026-07-12: top-driver hit counts + sector + exit-reason count tables | — | — |
| F12 | Reports: date-editable trade export + backtest gross-profit vs gross-loss split | medium | session time |
| ~~F7~~ | ✅ DONE 2026-07-11: ₹25,000 min position value live (can_open + post-throttle) | — | — |
| ~~F6~~ | ✅ DONE 2026-07-12: dashboard rotation card (≥90% to target → fund top watchlist pick; suggestion only) | — | — |
| ~F8~ | ✅ CORE DONE 2026-07-12: rotating_bucket() + full universe loader + virtual shadow-book (unbought YES picks marked to mkt) + sector chips + /scan.csv. NEXT: wire rotating buckets as the live 09:20 scan universe (needs sector map for 2,376) | partial | wire buckets into cron |
| ~F3~ | ✅ DONE 2026-07-12: lead-lag risk-off (VIX-spike + Nifty 1-day cascade) escalates regime early. True US/SGX feed still DATA_NEEDED | signal layer done | global feed source |
| F14 | Mode buttons → watchlist filters | UX change | session time |
| F15 | ✅ BUILT 2026-07-12 (opt-in, default OFF): add-to-winner tranches, Settings-toggleable. Turn on in Settings when you want it live | built, off | your toggle |
| F2 | ✅ v1.1 DONE: 16-festival calendar + wedding seasons + sector hints, journaled + 🪔 on dashboard. Politician/CEO/pre-bid news = still needs events feed (DATA_NEEDED) | flag layer done | events feed for news part |
| F11 | 30-day corporate planner per stock (watchlist + manual) | same events-feed dependency as F2 | same |
| F16 | ⏸ ON HOLD (your call 2026-07-11) | held | you un-hold |
| — | FII/DII wired into regime | thresholds need proposing first | next session proposal |
| — | Upstox history DEPTH test (10y+ full universe → kills G001?) | needs your Upstox connected during a session | morning login + session |
| — | G001 full proof: 15y whole-universe walk-forward + point-in-time Nifty-200 membership | DATA_NEEDED (your hunt / Upstox depth) | data lands |

## ❌ CLOSED by your decision
| Item | Your call |
|---|---|
| T1 remove +30% target · T2 vol-scaled trailing · T3 faster re-entry · T4 remove max-hold | **Rejected** — "crash protection expected, tests were to check engine only" |
| T5 keep stop-loss −12% | Kept (no change) |

## Your side (only you can do these)
| When | What |
|---|---|
| Every morning | Upstox login (token dies 03:30 IST — feeds scans, quotes, backtests, regime) |
| Daily 1 min | Glance ⚡Engine Triggers + Today's Picks |
| When found | 15Y data / events feed / any API keys → G001, F2, F11 unlock |
