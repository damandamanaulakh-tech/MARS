# SOURCEBORN AM07 — FULL APP REVIEW (2026-07-12)

Honest end-to-end audit. Verified against code, not narrative. 311 tests pass,
~13,800 lines Python, 29 engine modules, 14 web routes, 15 pages.

## 1. Verdict in one line
A **well-engineered, honestly-tested, defensively-strong paper engine** with a
**clear UI and full audit trail** — but with **no proven money-making edge yet**
and **three real data gaps** that only you can close (15Y history, events feed,
sector map for the full universe).

## 2. Scorecard (1–5, 5 = best)
| Dimension | Score | Why |
|---|---|---|
| Code quality / structure | 4 | Clean modules, pure functions, 311 tests. Minus: 44 bare `except` (silent failures), some 900-line files |
| Test coverage | 4 | Engine + pages tested, no-network mocks. Minus: no true integration test against live Upstox |
| Risk management | 5 | Best part. Ladder, stops, caps, correlation, kill-switch, PAPER_ONLY lock — all wired + tested |
| Data integrity / honesty | 5 | No fake data; stubs labelled; /proof ledger; survivorship bias disclosed |
| Proven edge (alpha) | 1 | 7yr −8.5% alpha; 10 event/general tests: defense great, offense negative. **Not proven** |
| UI clarity | 4 | Rebuilt this session: P&L, picks, watchlist, triggers, candles. Minus: dense in places |
| Live-readiness | 3 | Paper works; live path built + locked. Depends on daily Upstox login (fragile) |
| Data completeness | 2 | Universe + 16-stock 10Y in repo; **15Y full, events feed, full sector map missing** |

## 3. What you ASKED → what was DONE (status)
| Ask | Status |
|---|---|
| Positions not opening / MIN throttle | ✅ fixed + tested |
| Category/Target/StopLoss/LTP/Value/P&L/potential cols | ✅ live |
| 50 positions / 100% deploy / buy-and-hold | ✅ live |
| Dead tickers, 30-day login, remove button | ✅ live |
| Working backtest + 10 event/general tests on REAL data | ✅ done (docs/BACKTEST_*.md) |
| Candle engine + your pattern pics (8 patterns) + chart | ✅ live (F17 chart, ✓pattern per pick) |
| Intraday square-off (overnight bug) | ✅ fixed (double-bug) |
| Which-parameters-hit visibility (⚡ card, F10 counts) | ✅ live |
| Why-not-bought (F5), decay warning (F4) | ✅ live |
| Rotation suggester (F6), Scan 2.0 + shadow book + CSV (F8) | ✅ live |
| Festival calendar (F2), min position value (F7) | ✅ live |
| % display (F13), capital columns (F9) | ✅ live |
| Lead-lag regime (F3), pyramiding (F15 opt-in) | ✅ live |
| File inventory + keep/delete + Drive map | ✅ docs |
| 200-DMA fix, OpenAI news key | ✅ live |
| Commodities tradable (F16) | ⏸ your hold |
| Exit tweaks T1-T4 | ❌ your reject (crash protection is expected) |

## 4. PLACEHOLDERS / STUBS still in the engine (honest list)
| Item | State | Unblock |
|---|---|---|
| `short_interest` factor | hard-coded 50 (neutral) | No free NSE PCR source — Upstox F&O OI could proxy it |
| `analyst_revision` factor | hard-coded 50 | yfinance recs flaky for NSE — needs a fundamentals feed |
| `catalyst` (news) | 50 unless API key set | **Now live if OPENAI_API_KEY works** — verify next scan |
| Politician/CEO/pre-bid news | not built | needs an events/news feed (DATA_NEEDED) |
| 30-day corporate planner (F11) | not built | same events feed |
| FII/DII in regime | sample CSV present, not wired | propose thresholds → wire |
| Full 2,376 rotating universe live | machinery built, not switched on | needs sector map for all 2,376 |
| Commodities/currency tradable | signals only | your F16 go-ahead |

## 5. FLAWS found (ranked) + FIX
| # | Flaw | Impact | Fix |
|---|---|---|---|
| 1 | **No proven edge** | The whole point | 15Y walk-forward proof + Guru head-to-head (needs data) |
| 2 | **Daily Upstox login required** | If you forget, scans starve on blocked Yahoo | Add a dashboard RED banner + push/email reminder when token dies |
| 3 | 44 bare `except Exception` | Silent failures hide bugs | Narrow them, log each; a scan can fail quietly today |
| 4 | Survivorship bias in all backtests | Alpha looks better than reality | Point-in-time index membership (DATA_NEEDED) |
| 5 | Self-basket benchmark in 10-stock tests | Not a true Nifty compare | Use Nifty TRI when 15Y lands |
| 6 | No brokerage/STT/slippage in event tests | Real returns lower | Add a cost model (~0.1–0.3% round-trip) |
| 7 | Sector map covers ~200 of 2,376 | Sector caps misfire on full universe | Build sector map from Upstox/NSE (one-time) |
| 8 | Single-user, single-portfolio | Fine now, not multi-account | Per-user keying later if needed |
| 9 | Secrets in Render env only | OK, but no rotation policy | Document + rotate keys periodically |
| 10 | No automated live-vs-paper reconciliation | Can't yet prove paper == real fills | Fill-model calibration when live |

## 6. GOOD (keep / strengths)
- Risk stack is genuinely strong and fully tested — the engine's real asset.
- Ruthless honesty: /proof ledger, DATA_NEEDED discipline, no invented numbers.
- Every decision journaled (append-only) → full audit trail, nothing hidden.
- Root-cause fix (Yahoo 429 → Upstox routing) unblocked months of dead scans.
- Memory-safe on 512 MB (RAM caps + Mongo archive) — the recurring OOM is gone.
- Candle engine + shadow book = real "verification + opportunity-cost" system.
- Clean ship discipline: work branch → cherry-pick → live, 21 commits/session.

## 7. BAD (watch)
- Alpha negative in calm/up years (defense premium). Momentum + exits-off in a
  momentum-crash year = the all-red book you saw. This is the engine's CHARACTER,
  not a bug — but it means it earns in falls, pays in calm. Know that.
- Feature surface is now large; keep testing every page render (past banner bug).
- Complexity risk: 29 modules, many gates. More gates ≠ more alpha; simplicity
  may beat additions from here.

## 8. TOP 5 IMPROVEMENTS (my ranked recommendation)
1. **Prove or disprove edge** — get 15Y data → walk-forward per year → decide
   with evidence whether to keep momentum, switch to the value/quality tilt that
   held up, or stop. Everything else is secondary to this.
2. **Guru head-to-head** on the real 10Y data (Batch E) — cheap, high signal.
3. **Login-fragility fix** — loud reminder + graceful "data stale" state so a
   missed login never silently starves a day.
4. **Wire FII/DII into regime** — you have the data; propose thresholds, test.
5. **Cost model in backtests** — so every future number is net, not gross.

## 9. The one honest truth
The APP is done and solid. The STRATEGY is unproven. More features won't create
edge — **tested rules on real long data will**. The next phase is data + proof,
not code.


## 10. FIXES APPLIED (2026-07-12, post-review — 5 fixes shipped)
| Flaw (from §5) | Fix shipped | Verified |
|---|---|---|
| #2 Daily-login fragility (silent starve) | Loud RED dashboard banner when Upstox down/expired + amber "token <3h" warning; `quote_router.status()` now returns healthy flag + hours-left | test + page render |
| #2b Stale-data safety | `refresh()` stale-data guard: no live quotes → journal DATA_STALE + HOLD (no false exits on entry-price marks) | test |
| improvement #4 FII/DII → regime | `flows.get_flows()` reads committed `data/fii_dii_cash.csv` when NSE 403s; `engine.scan` now feeds real 5-day FII net into `fetch_regime()` (was ALWAYS None) — classify flags FII selling < −1000 Cr | 3 tests |
| #3 Silent `except` | Narrowed the scan hot-path excepts (regime/FII/festival/virtual/pyramid) to logged; **surfaced a real latent bug: festival block referenced `now` before it was defined → festival journaling had NEVER fired. Fixed.** | test |
| #6 Cost transparency | Backtest was ALREADY net of costs (STT 0.1%/side + 5bps slippage, applied in `_close_position`). Made it explicit in the report warnings so returns read as net, benchmark as gross | verified in code |

Note: improvements #1 (prove edge via 15Y data) and #2 (Guru head-to-head) were
NOT done — #1 needs real 15-year OHLCV you don't yet have; #2 you dropped.
316 tests pass.
