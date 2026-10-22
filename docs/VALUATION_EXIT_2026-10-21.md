# VALUATION EXIT TARGET — item 19 (2026-10-21)

**Money path.** This page is the record of the change. `docs/FINAL_SET_V1_3.md`
carries a one-line pointer here; its pinned numbers are unchanged.

## 1. Your words

Card, 2026-10-21: *"Use the valuation target as the exit target for all NEW
buys. When a judged valuation (attractive/watch) exists, the market-adjusted
valuation target replaces +20% as the exit target at entry; otherwise k×ATR
as today. MONEY PATH, untested — run the pre-registered test on the 12-year
panel first and show the result before it goes live. Also do it on open
positions."*

After the result: **"Wire A3"** and **"Exit as tested"**.

## 2. The test (pre-registered before any run)

150 names (the 191-name panel ∩ the 200 names with statements), the live
momentum entry rule, exits judged once a day on the close, real costs.
Settings fixed before the run; the verdict read on the TEST half only.
Rule: an arm is EARNED if its TEST return beats C0 and its TEST max drawdown
is not worse by more than 3 points.

| Arm | What it does | TRAIN 2021-07..2023-12 | TEST 2024-01..2026-06 | TEST max DD | Verdict |
|---|---|---|---|---|---|
| C0 | k × ATR target (live before this change) | +8.58% | **+5.46%** | −4.11% | — |
| A1 | judged valuation base at entry, fixed | +8.95% | +5.63% | −4.05% | earned |
| A2 | A1 + monthly re-mark with the Sensex / sector | +8.96% | +5.65% | −4.10% | earned |
| **A3** | **A2 + re-target held k × ATR positions once** | +9.06% | **+5.68%** | **−4.18%** | **earned — wired** |

A3 TEST detail: 916 trades, hit 37.4% (C0 35.6%), 33.0% of entries took a
valuation target, 72 held positions re-targeted later, 16 VAL_TARGET_DOWN
exits. Valuation-targeted trades: median target +24.1% over entry (k × ATR
on the same trades: +21.8%).

The repo's four out-of-sample windows (same harness):

| Window | C0 | A1 | A2 | A3 |
|---|---|---|---|---|
| 2019 | +0.02% | +0.02% | +0.02% | +0.02% |
| 2020-21 | +9.94% | +9.94% | +9.94% | +9.94% |
| 2022-23 | +6.92% | +7.29% | +7.31% | +7.40% |
| 2024-26H1 | +5.46% | +5.63% | +5.65% | +5.68% |

2019 and 2020-21 are identical in every arm: no point-in-time valuation
exists before 2023-05-15 (the statements hold FY23-FY26 profit only), so the
honest window is ~3 years, not five. The edge is small (+0.22 points of
return on the Rs 5 Cr book over 2.5 years); the drawdown is 0.07 points
deeper. Caveats as in every study: survivor panel, the harness sizes every
trade at the slab, no breadth band / VIX re-entry / IFR in the harness.

## 3. The live rule, field for field

Code: `trading/valuation_exit.py` (pure — no network, no web import, never
runs the valuation pipeline), wired in `trading/engine.py`.

| Study (harness `bt_item19.py`) | Live |
|---|---|
| judged = verdict ∈ {attractive, watch} AND range status OK AND base > 0 | `target_from_record`: same three tests on the stored record (`verdict`, `range_status`, `base_target`) |
| base > fill × 1.05 (MARGIN) | `VALUATION_EXIT_MARGIN = 1.05`, strictly above |
| latest month-end valuation dated ≤ the decision day | the latest stored record, built nightly; used only while **≤ 35 days old** (`RECORD_MAX_AGE_DAYS`, ASSUMED: a month-end valuation is up to ~35 days old when used) |
| default assumptions | the precompute always uses the defaults; a record marked `drafted` is never used — a Den §13 draft never moves a target |
| T_m = T_entry × (1 + β × Sensex move) × (1 + (sector move − Sensex move)) | `remarked_target`, same formula |
| moves from the anchor day's close (fill day / re-target day) to the month-end close | same: the re-mark reads the anchor day's stored close (falls back to the reading taken at the buy if that day is missing) |
| β = OLS on the Sensex over 252 sessions; < 120 pairs → 1, labelled | β from the record (same OLS, 252 sessions, as of the record's closes); none → 1, journaled `beta_source: fallback_1` |
| sector index per SECTOR_INDEX_FOR / TICKER_INDEX_OVERRIDE; unmapped → Nifty 50 | same map — the one the study read (`valuation_view.bench_for_tested`: your realty overrides → the ten app labels that had an index before 2026-10-21 → Nifty 50). NOT the batch-3 remap the Den §11/§12 screens use (`bench_for`), see §11 below; an index with no stored close for those days → Nifty 50, labelled in the re-mark |
| re-marked target ≤ that month-end close → exit, VAL_TARGET_DOWN | `exit_due` set by the re-mark; the exit fires at the next in-session pass |
| A3: first month-end ≥ entry with a judged record and base_m > ENTRY × 1.05 → re-target once, legs from that day | first **in-session** pass a qualifying record exists; base > **entry** × 1.05; once (`retargeted`); legs from that day — and only while that pass's price is inside the envelope a same-day judged verdict implies: **at most base × 1.10 and at most the range high** (`RETARGET_PRICE_CEILING`, `retarget_price_ok`). The study's record was valued at the very close it re-targeted on, so a judged verdict meant exactly that; a live record is from an earlier close, so the price is checked. No live quote = no re-target that pass |
| stop / trail / max-hold / decay unchanged | unchanged — the valuation target IS `pos.target_price`, so TAKE_PROFIT fires through `exits.check_exit` as before; the stop keeps its priority over VAL_TARGET_DOWN; buy-and-hold (`disable_exits`) holds a VAL_TARGET_DOWN like every other target exit |

Applies to **every new buy through `engine._open`**: the scan, Ocean, your
BUY button, the Buy Desk and limit fills — except **intraday** (squared off
the same day; also never re-targeted). A target **you type on the order
ticket always wins**: the position's source becomes `owner` and it is never
re-targeted or re-marked. Held positions with hand-set levels
(`custom_levels`, which the danger layer also sets when it tightens a stop)
are not re-targeted.

**Short / swing / positional buys** follow the same rule because your order
said "all NEW buys". **The study measured momentum only** — for those three
categories this is your order, not a measurement.

## 4. Three live-vs-test differences

1. **Daily records, not month-end valuations.** The study valued every name
   at each month-end and an entry used the latest one before its decision
   day. Live, the records are rebuilt every evening (16:50 IST) — a buy reads
   last evening's record, which is fresher than anything the study used. The
   35-day age limit keeps a record no older than the study's.
2. **Re-target on the first pass a judged record exists, not at month-end.**
   A held k × ATR position is re-targeted on the first in-session 30-minute
   pass after a qualifying record appears (on the first pass after this
   deploy for the names that already qualify). If the base sits at or below
   the live price it takes profit on that pass (TAKE_PROFIT; the study
   labelled those VAL_TARGET_DOWN at the month-end close). **So on the first
   in-session pass after the first records are built, a held name whose base
   is above its entry × 1.05 and whose price is between the base and base ×
   1.10 (and not above the range high) is SOLD at that pass** — the tested
   rule, said here so it is not a surprise. In the study this was 12 of the
   72 TEST re-targets (TRAIN 7 of 61), sold 0.7%-9.3% above their base,
   median +10.7% on the trade. A price already above base × 1.10 (or the
   range high) is NOT re-targeted — at that price the verdict would read
   unattractive, and the study never re-targeted one; it keeps k × ATR and
   is looked at again on every pass. A re-target is never set on a
   closed-market pass (night refresh, 15:35): the target and the sale must
   read the same price, as they did in the study.
3. **VAL_TARGET_DOWN exits at the next in-session pass, at that pass's
   price.** The study exited AT the month-end close. Live, the month is
   re-marked on the first pass of the new month (the month-end close is
   only known after the close) and the exit fires at that pass's live price
   — outside 09:15-15:30 IST it is journaled EXIT_DUE_MARKET_CLOSED and
   fires on the next in-session pass, like every other due exit.

Smaller ones, said once: the month-end close the re-mark compares with is
the stock's stored daily close on or before the month-end. The Sensex close
is often stored the same evening (15:35) while the stock's own close lands
with the next morning's top-up, so the re-mark WAITS for the stock's close
(VALUATION_REMARK_WAITING, retried every pass) for up to
`REMARK_CLOSE_WAIT_DAYS` = 5 calendar days (ASSUMED) — judging on the next
morning's price instead would lose a tested exit whenever the price dipped
under the re-marked target after the month-end close; only past those days
is the pass's own price used, and the journal says which (`close_source`); if the app is down for more than a month, only the
latest closed month is re-marked (the formula is cumulative from the anchor,
so the target is the same — only the in-between VAL_TARGET_DOWN checks are
lost); a month whose Sensex month-end close is not stored yet waits for the
next pass (VALUATION_REMARK_WAITING, once a day).

## 5. The switch

Den §9 / Settings, Risk group: **"Valuation exit target (1/0)"**,
`cfg.valuation_exit_enabled`, default **1** (your order).
0 = no new valuation targets, no re-targets, no re-marks and no
VAL_TARGET_DOWN exits; positions already on a valuation target **keep the
target they have** (nothing on a held name changes silently).

The save itself had to be fixed for this switch to work: the §9 form
always posts the universe box as shown, and the live list has held 1,205
names (above the 1,200 cap) since the 09-30 rebuild, so EVERY save was
refused `universe_too_big` (upload review ROOT-ROUTE-07). A box that comes
back unchanged is now not checked; an edited list still is. To switch this
off, use the switch — never revert the code: a position saved with
`val_target` cannot be read by code from before this change.

## 6. The records (web side)

`web/scheduler.run_valuation_precompute(where)`:

* scope = held names, then the momentum shortlist at or above its floor by
  score, then the last scan picks, then your Den §11 targets — unique,
  capped at 120, held first;
* each name = the Den §12 chain row built the tested way
  (`valuation_chain_view.row_for(..., tested_rule=True)`, the stored peer
  base, closes in one query per 25 names, default assumptions — §11 below)
  reduced to the record `{ticker, asof, verdict, range_status, base_target,
  low, high, family, method, sector_index, beta, beta_n, version, drafted,
  price, rule, tested_rule, note}`;
* time budget `SB_VALUATION_PRECOMPUTE_BUDGET_S` (default 120 s) — names past
  it keep their previous record (or none), counted;
* never while a scan rotation holds the lock, never above the Den's memory
  ceiling (380 MiB) — checked at the start AND again before every chunk of
  25 names, so a rotation that starts or memory that climbs mid-run stops it
  (the rest keep their previous record) — journaled
  VALUATION_PRECOMPUTE_SKIPPED with the reason;
* never at the same time as the 16:45 register precompute (it peaks ~350 MiB
  at 16:45-16:55 on the live box): the 16:50 job, the weekly block and the
  catch-up WAIT for it (up to 20 min, `VALUATION_WAIT_FOR_REGISTER_S`), the
  owner's button does not wait (skipped, reason `register`); and never while
  a Den §12 open is computing rows (waits up to 30 s, then skipped, reason
  `den`) — the evening jobs run one after another, never stacked (the
  register precompute itself waits at most 15 min for the lock, then runs as
  before, so a stuck holder can never stall the weekly block);
* stored in settings doc `valuation_records` + the in-process copy; the
  journal says `stored: true` only when the stored doc reads back with this
  build's stamp (`db.save_settings` logs a refused write and returns, so "no
  error" is not proof);
* runs: **16:50 IST mon-fri** (after the 16:45 register precompute), inside
  the weekly block after the register precompute, a **boot catch-up** when
  the records are older than one trading day — only at or after 15:45 IST on
  a trading day, or on a holiday, so **the 09:20 and 14:30 chains get no new
  load** — and the owner's Den §12 button **REBUILD VALUATION RECORDS NOW**
  (`POST /den/valuation/precompute`, owner only).

Readers injected at boot (`web/state.py`, the flows pattern — trading/ never
imports web): records from `web/valuation_exit_feed.records_for`; Sensex /
Nifty from the committed index file + the stored tail, sector closes from
the stored `sector_index` rows (`valuation_exit_feed.level`). A missing
reader means k × ATR — nothing raises.

## 7. Journal lines

| Line | When |
|---|---|
| BUY `target` {price, source "valuation", base, asof, verdict, family, k_atr_target, k_atr_source} | a buy took the valuation base |
| BUY `target` {…, k_atr_target, valuation: reason} | a buy kept k × ATR (reason: no_record / stale:N / not_judged:… / below_margin / disabled / intraday / no_reader) |
| VALUATION_RETARGET {ticker, old_target, new_target, asof, verdict, family} | A3, once per position |
| VALUATION_REMARK {ticker, month, month_end, old_target, new_target, sensex_move_pct, sector_move_pct, sector_leg, beta, beta_source, close, close_source, exit_due} | once per position per closed month |
| VALUATION_REMARK_WAITING | the Sensex month-end close, or the stock's own month-end close (up to 5 days), is not stored yet (once a day) |
| SELL exit_reason VAL_TARGET_DOWN / EXIT_DUE_MARKET_CLOSED rule VAL_TARGET_DOWN | the exit |
| VALUATION_PRECOMPUTE {where, n, n_judged, skipped, elapsed_s, stopped, stored} · _SKIPPED {reason: busy / memory / register / den} · _FAILED | the records job |
| VALUATION_PASS_FAILED | the re-target / re-mark of one position raised (its target untouched; the others still run), once a day |

VAL_TARGET_DOWN counts as a strategy exit in the measured-Kelly check and
the loss halt (an unknown reason would have dropped it from the record).

## 8. Persistence

`PaperPosition.val_target` (dict; `{}` on positions opened before this) —
JSON save and the Mongo mirror both go through `to_dict` / `from_dict`, so
it survives a Render restart (tested on a modelled Mongo doc, both ways).
The boot retag keeps a valuation target even on a blank legacy tag.

## 9. Screens

/positions: a chip under every target — **VAL base ₹x · asof d · verdict**
(plus the last re-mark, and VAL_TARGET_DOWN when due) or **k×ATR** with the
reason on hover; the legend explains it. Dashboard Trade Horizons: one line.
Den: §12 records block + button; §9 switch; sequence stage 21 "exists".
/risk: nothing. Backtest: unchanged (one category, no valuation) — its
warning now says the valuation exit target is not modelled.

## 10. Not built

**Open, for your word (not changed):** the measured-Kelly check reads each
closed trade's return clipped at ±20% (`measured_kelly.live_clip_pct`, the
momentum preset target). A valuation target exit above +20% (the TEST
median target was +24%, p90 +144%) therefore counts as +20% in that
record, which understates the winners the check sees. k × ATR targets
above +20% (its band reaches +60%) already had this. Raising the clip is a
change to a money gate — not made without your order.

* the global sector leg (Indian NSE indices only, your 2026-10-12 pick);
* CPI / repo / GDP / Brent feeds (the chain counts them 0, listed DATA_NEEDED);
* category-specific measurement for short / swing / positional (§3);
* the valuation exit in the backtest;
* names without full statements (~1,000 of the scan list) read
  "insufficient" and keep k × ATR until the statements batch fetch you have
  not yet approved runs.

## 11. 2026-10-21 (batch 3 review) — the record keeps the tested definitions

The same evening, the valuation screens changed three definitions (batch 3:
the market split into its own legs, the currency through EPS, a wider
sector-index map). A review found that the exit record was built from the
same Den row, so the exit target would have moved off the rule the test
measured without anyone deciding it. Fixed: **the record is valued exactly
the way the pre-registered study valued it**; the Den screens keep the new
definitions.

| | The exit record (money path) | The Den §11 / §12 screens (display) |
|---|---|---|
| Market rows of the P/E bridge | read to the valuation day — the newest stored close, as the study did | read to the selection date (the time of action); the market since then is its own leg |
| Rupee row E101 | live for IT-services routes (USD/INR now vs the base year end) | counted 0; the currency leg runs through EPS for every sector |
| Sector index | your realty overrides → the ten app labels that had an index before 2026-10-21 → Nifty 50 | the batch-3 map: also a name's Nifty-500 industry when it has no app label, and the Chemicals label — 191 names in the universe read a different index |
| A date you type in the inputs table, or a §11 push | does not reach it | sets the selection date |

How: `row_for(tested_rule=True)` (the nightly precompute always asks for
it) → `pipeline.evaluate(e101_it_rupee=True)`, the batch-2 branch word for
word; the record's sector index comes from `valuation_view.bench_for_tested`.
Every record now carries `rule: "tested-2026-10-21"` and `tested_rule`
(True from the precompute), and its note says "valued as the pre-registered
study valued it". Checked: with the flag the pipeline gives the same range,
cases and verdict as the batch-2 code on 28 test cases; the tested sector
map equals the old one for all 2,680 names in the universe file.

What it means for your money: nothing changes from what was tested and
wired on 2026-10-21 — the exit target is the one the study measured. Without
this fix, 25 of the names that can carry a judged record today (they have
full statements — e.g. SRF, PIIND, UPL, LTM, HINDPETRO, HINDZINC) would
have changed their sector leg from Nifty 50 to an industry index, IT-services
names would have lost the rupee row from their base, and a date typed on the
Den would have moved a live exit target.

Moving the record onto the Den's batch-3 definitions is a new rule: it
needs the pre-registered test re-run on them (the study record
STUDY_item19_valuation_exits.md, the same harness `bt_item19.py`) and your
word. Not done.
