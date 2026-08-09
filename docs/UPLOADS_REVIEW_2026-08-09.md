# Uploads review — 2026-08-09 (10 files, two batches)

Owner order: "review and get Data extraction". Every file was opened and
checked against committed code and data before anything was extracted.
Standing rule applied: verify against code/data, never trust a narrative —
several earlier uploads carried fabricated numbers.

**Money-path impact of this whole batch: NONE. No engine, sizing, or exit
change. Data and records only.**

## Verdict table

| # | File | Verdict | Extracted to |
|---|------|---------|--------------|
| 1 | India_VIX_historical.csv | **REAL — verified, redundant** | nothing (already committed) |
| 2 | ASHSTOCKS_Flaw_Register.csv | Honest register for a DIFFERENT system | research/external/ |
| 3 | Chityy_REVERSE_PATTERN_PARAMETER_ENGINE_v0_4.xlsx | **REAL data, owner's design — extracted whole** | research/chityy_archive/reverse_pattern_v0_4/ (14 CSVs) |
| 4 | AM07_FII_DII_OI_Impact_Analysis.xlsx | Findings useful, **one conclusion contradicts its own table**; panel not attached | findings recorded below |
| 5 | AM07_Backtest_Adaptive_IFR_FII_WithCosts_Report.xlsx | Narrative — **alpha lives in one block (2007-10)** | numbers recorded below |
| 6 | AM07_Backtest_Adaptive_IFR_FII_LOCKED_Report.xlsx | Same run, no costs — same verdict | numbers recorded below |
| 7 | AM07_FO_OI_Signals_Report.xlsx | MWPL **real** · FPI sheet **synthetic (confirmed)** · OI top-25 real | data/fo_mwpl_2026-06.csv · data/fo_oi_top_contracts_2026-06-08.csv |
| 8 | AM07_Backtest_with_India_VIX.xlsx | **Not evidence** — no trade ledger (its own register says so, F-005) | nothing |
| 9 | AM07_India_VIX_Implementation.xlsx | Proposal only — AM07 already has the evidence-earned VIX rule | recorded below |
| 10 | AM07_World_Market_Parameters.xlsx | 31-factor idea catalog, no data | recorded below |

## What was checked, file by file

### 1. India VIX history (CSV, 1,608 rows, 2018-01-01 → 2024-07-01)
Cross-checked all 1,583 overlapping days against the committed
`data/index_daily_15y.csv`: open/high/low match to ±0.01, close max
difference 0.15 on a single day. **The file is genuine — and a strict
subset of what the repo already holds (2011-06 → 2026-06).** Not
committed twice, per the "feed NOTHING twice" rule.

### 2. ASHSTOCKS flaw register (15 flaws)
This is a self-audit of a *different* build ("ASH-STOCKS" source tag), not
of AM07. It is unusually honest — it flags its own synthetic data and
non-reproducible backtests. Kept at
`research/external/ASHSTOCKS_Flaw_Register_2026-08-09.csv` because two of
its flags directly settled questions about files in this same batch
(F-003 and F-005, below). Where the same risks exist in AM07 they are
already handled: hit counters journal (journal is append-only), Mongo has
retry + spill + heal (C1/C2, 2026-08-09), stops are implemented and
tested, and the universe has a PIT story (5 snapshots + PIT-constrained
v3.1 run).

### 3. Chityy Reverse Pattern Parameter Engine v0.4
The owner's own system lineage (v0_2 trigger layer is already in
`research/chityy_archive/` and wired display-only). All 14 sheets
extracted to `research/chityy_archive/reverse_pattern_v0_4/`:
1,200-parameter locked bank, 42 adopted parameters, 16-pattern reverse
library (RP001+), scoring rules, duplicate/hash audit of 21 source files,
and P0 data-gap list. **Authenticity spot-check passed**: its sample data
sheet (186 stocks, 2026-06-03) matches the committed Upstox panel exactly
— TCS close 2241.70 after 2446.90 (the −8.4% day was real), RELIANCE
1313.20/1314.60. Its own P0 gap ("full 15-year OHLCV") is partially
filled by the repo's committed 191×12y panel. **Nothing wired** — this
lands next to the trigger layer as design material; wiring is a separate
owner order.

### 4. FII/DII Open-Interest impact analysis (PWOI 2012-01 → 2023-04)
Findings recorded (the underlying daily panel was NOT in the file, so
none of this is reproducible from the upload — directional only):
- FII index-futures net **level** vs forward 15d returns: corr **0.042**
  (weak). Net **change**: 0.024/0.032 — barely better. FII **cash**
  remains the stronger signal (which AM07 already reads via
  `signals/flows.py`).
- Most actionable combo: heavy-short OI **plus** heavy cash selling →
  mean fwd15d −0.42% vs +1.03% average (166 days).
- **Caught: the "DII cushion" conclusion contradicts its own numbers.**
  The sheet says "DII buying measurably reduces the damage of FII
  selling", but its table shows FII-heavy-sell days where DII was NOT
  buying had *better* forward returns (+3.27%, n=59) than days DII was
  buying (+1.23%, n=447). Whatever the cushion story is, that table does
  not support the sentence. Do not quote the cushion claim.
- The file's own recommendation is modest and sane: OI alone is too noisy
  to trade; at most a severity booster after cash confirmation. **Nothing
  adopted** — would need the panel + an OOS test first.

### 5–6. Adaptive IFR+FII backtests (LOCKED + WithCosts, 2007-01 → 2022-12)
Equal-weight market-internals replay, 3,315 days, next-day exposure
laddering (100/70/50/25/15%) off a damage-cluster trigger with FII cash
confirmation. Headline: +2.73pp CAGR net of a 0.10%×|Δexposure| cost
model, max DD −70.4% → −45.3%. The files honestly list their own limits
(equal-weight internals, not stock selection; no 2023–2026; volume layer
missing). **The block table is the tell**:

| Block | Baseline CAGR | Adaptive net | Alpha |
|-------|--------------|--------------|-------|
| 2007-2010 | 21.0 | 37.6 | **+16.6** |
| 2011-2014 | 7.1 | 7.5 | +0.5 |
| 2015-2018 | 10.5 | 7.5 | **−3.0** |
| 2019-2022 | 25.0 | 23.8 | **−1.2** |

The whole edge is the 2008 crash block; alpha is negative in two of the
last three blocks. The drawdown improvement is real and consistent
(−25 to −2.7pts better in every block) — this is a **drawdown-reduction
overlay, not a return edge**, on this evidence. AM07 already has the IFR
throttle and FII cash flows; the exposure-ladder wiring is NOT adopted
(one crash-block of in-sample evidence does not meet the house standard,
and no self-initiated features).

### 7. F&O OI signals report (bhavcopy 2026-06-08)
- **MWPL June 2026: real NSE limits, 211 symbols → `data/fo_mwpl_2026-06.csv`.**
- **FPI category positions: SYNTHETIC — confirmed by arithmetic.** Every
  row is exactly 30%/20%/10% of that symbol's MWPL, rounded to whole
  contracts (178 rows off only by rounding, 22 exact). This matches
  ASHSTOCKS flaw F-003 ("mechanically generated ratios — not real FPI
  data"). **Not extracted; never rank on it.**
- Top-25 stock-futures OI snapshot (one day) with buildup/unwinding labels
  → `data/fo_oi_top_contracts_2026-06-08.csv`. Real-looking (expiry
  2026-06-30 is a Tuesday, matching the NSE's Tuesday-expiry regime) but a
  single stale day — reference only. Note: it is a top-25 slice, not the
  full bhavcopy.

### 8. Backtest with India VIX (2018 → 2024-06)
+441.8% / CAGR 31.2% / 4,610 trades — **with no trade ledger and no
with/without comparison**. The ASHSTOCKS register itself marks this class
of run non-reproducible (F-005: "multipliers unproven"). It also runs
10% max positions (F-013 flags that governance change). Not evidence;
nothing extracted beyond this note.

### 9. India VIX implementation sheet
A 5-tier VIX ladder (<12 / 12-16 / 16-20 / 20-25 / ≥25) with size
multipliers, plus spike rules. AM07's VIX logic is the **evidence-earned**
v4 result (10 preregistered correction episodes: only VIX-crush re-entry
passed, +2.3pp; it is wired). This ladder was "tested" only by file #8,
which is non-reproducible. Recorded as a proposal. If the owner wants it
tested, the honest path is the same preregistered episode harness used
for v4.

### 10. World market parameters (WM001–WM031)
A 31-factor catalog. Several are already live in AM07 under other names:
cross-sectional momentum (the live signal), dual momentum (absolute-mom
gate + rank blend), relative volume (volume_expansion), analyst revisions
(live), India VIX level/change (regime), PCR (the short_interest stub's
intended replacement). The genuinely new ideas (residual momentum,
idiosyncratic/downside vol, Amihud illiquidity, gross profitability, FCF
yield, USDINR, yield-curve slope) all need data feeds the app does not
have. Catalog recorded here; no build without an owner order.

## What would unlock more (owner's call, next batch welcome)
1. The **daily FII derivatives-OI panel (2012→2023)** behind file #4 —
   with it, every OI claim becomes checkable and the OI severity-booster
   idea becomes testable.
2. The **full F&O bhavcopy** for any recent date (file #7 carries only a
   top-25 slice) — would light up several DATA_NEEDED triggers in the
   owner's trigger layer.
3. The **trade ledger** behind file #8, if it exists — without it the VIX
   ladder stays unproven.
