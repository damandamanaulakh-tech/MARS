# RUN-20261125-01 / INTRA-005 — intraday pipeline and the owner's intraday rules

Status: **implemented and locally tested; published for review. NOT merged, NOT
deployed. Deployment is on hold** while live storage refuses writes (see
"Deployment hold"). Nothing in this record is a claim about the live app.

## Baseline and incident

Starts from main `dc503543f089e46641a8c625435a95894b009699` (PR233).

[RUN-20261125-01](../evidence/2026-11-25-no-new-positions.md): on 25 November
two intraday scans reconciled to zero ranked, zero picks, zero buys —
10:49 IST 1,010 capped + 82 deadline-labelled + 37 stale = 1,129, and
11:14 IST 1,014 + 82 + 1 unmapped + 36 stale = 1,133. The 37 / 36 stocks that
came back with valid 5-minute bars were all past the 360-second validity
(one 5-minute interval plus 60 seconds) when scoring began.

## Causes found in code (reproduced in isolated tests)

1. **The bars were fetched first and scored last.** `engine.scan` fetched the
   5-minute bars at the top, then did the market and book work that every
   category pass does (regime, risk marks, flows, sector heat), then scored.
   With a clock that advances 20 minutes during that work, the old order
   scores 0 stocks; fetched immediately before scoring, the same stock scores
   and buys.
2. **The intraday scorer fetched three outside inputs it gives zero weight.**
   `compute_score` called the news-sentiment, earnings-growth and
   analyst-revision readers for every stock in every category. The intraday
   preset weights all three at 0. Each can be a network call per stock.
3. **About 1,130 names shared 119 five-minute fetch slots**, so one stock was
   read roughly once in ten passes.
4. **A request that answered with nothing was reported as a deadline** when a
   different request ran out of time, so the "82 deadline" count was not all
   deadlines.
5. **The Intraday segment had no clock of its own.** The 5-minute job was
   registered only with `INTRADAY_SCHEDULER=1` (render.yaml sets `0`) and
   returned at its first line unless the whole app was in Intraday mode. The
   segment was scored only inside the 09:20 and 14:30 rotations.

## Owner decisions (26 November, defaults approved: "go with defaults")

| # | Question | Approved default | Setting |
|---|---|---|---|
| 11 | Which stocks does Intraday read? | About 100 most-traded, all read every pass | `intraday_universe_size = 100` |
| 12 | How often? | Every 5 minutes of the session, whatever the app's mode | `intraday_always_scan = True` |
| 13 | Last automatic new buy | 14:45 IST (square-off stays 15:25) | `intraday_last_entry_time = "14:45"` |
| 14 | Most intraday positions at once | 10 | `intraday_max_positions = 10` |
| 15 | Re-buy a stock stopped out the same day? | No | `intraday_rebuy_after_stop = False` |

These are policy, recorded separately from the bug fixes above. No score
floor, weight, stop, target, size, segment share or freshness rule changed.

## Implemented behavior

- **Fetch, then score.** The signal inputs (5-minute bars and each stock's
  decision record) are prepared by one step. Daily categories run it where it
  always ran. The intraday pass runs it immediately before the scoring loop.
  Completed-bar, session and 360-second freshness requirements are unchanged,
  including the final re-check before a position is booked.
- **Zero-weight inputs are not fetched.** A category that weights catalyst,
  earnings growth or analyst revision at 0 records a neutral 50 without the
  call. Short, Swing and Positional weight them and still fetch. Intraday
  coverage labels read `zero_weight_not_fetched`.
- **The intraday list.** The scan list is ranked by median rupee turnover
  (close x volume) over the last 20 stored daily bars — the measure the
  liquidity gate and the weekly rebuild already use — and the top
  `intraday_universe_size` are read on every pass. Held names take no slot
  (their exits run elsewhere). A name with no daily history only fills a slot
  the ranked names leave empty. The list never exceeds the fetch cap less the
  benchmark; `0` keeps the whole list. **Only the names fetched and scored on
  5-minute bars are narrowed**: Ocean's search, sector heat and the structural
  flags still read the whole scan list.
- **Honest fetch reasons.** A request that answered keeps its own reason; only
  requests still outstanding at the deadline are labelled a deadline.
- **Owner rules, automatic scan only.** New gates `intraday_position_cap` and
  `intraday_rebuy_after_stop` are written on each decision record. The cap is
  re-read with the live count inside the opening loop, so one pass cannot open
  past it. "Stopped out" means a closed intraday trade with reason STOP_LOSS or
  TRAILING_STOP exited the same IST day. The 14:45 time applies to automatic
  intraday buys and to rotation funding; the owner's own order paths keep the
  existing 15:25 rule. Other categories, Ocean picks and owner orders are not
  affected by any of the three rules.
- **The 5-minute pass is a side pass** (`engine.scan(side_pass=True)`), not a
  sixth copy of the rotation. It runs the same selection and the same gates
  and may open Intraday positions. It does not:
  - overwrite the Scan page / dashboard record of the last category pass
    (`last_scan`, `last_scan_live`, `last_scan_picks`, `last_scan_skipped`,
    `last_scan_opened`, `scan_matrix`, `scan_history`, shadow book);
  - run Ocean or rotation selling — both keep their 09:20 and 14:30 slots;
  - write one "not selected" record per stock per pass.
- **Journal volume ([RUN-20261126-01](../evidence/2026-11-26-journal-growth-inventory.md)).**
  Scan decisions are already 93.74% of the journal. A side pass writes one
  `INTRADAY_PASS` summary line (asked / usable / scored / cleared / bought and
  the count of stocks per not-bought reason) plus a full decision record for a
  stock that cleared its score and was bought or refused. The same refusal for
  the same stock is recorded once per IST day per process, not every five
  minutes. An ordinary pass is unchanged and still records every stock.
- **Scheduling.** The job is registered on the always-on daily track. A tick
  does nothing outside 09:15–15:30 IST, after the last-buy time, while a
  category rotation holds its lock, while any other book work holds the mode
  lock (it skips; it never waits), or while the portfolio save is not
  confirmed. The owner's mode and preset are restored in `finally`. The legacy
  path (`INTRADAY_SCHEDULER=1` and mode Intraday: refresh + full scan) is
  unchanged. No exit check was added or removed: the 30-minute pass, the 15:25
  square-off and the one-minute deadline recovery keep their clocks.
- **Stage timing** is stored on the intraday status (`before_fetch_s`,
  `fetch_s`, `scored_after_fetch_s`, `scored`) and shown on the Scan page with
  the list rule and the last 5-minute pass.

## Validation

- Focused, new: 59 tests — 39 in
  `trading/tests/test_intraday_pipeline_2026_11_26.py` (33) and
  `trading/tests/test_zero_weight_inputs_not_fetched_2026_11_26.py` (6), and 20
  in `web/tests/test_intraday_five_minute_pass_2026_11_26.py`. All pass.
- Against baseline `dc50354` the same three files give **49 failed, 10
  passed**. The 10 that pass there pin behavior that must not change (an
  ordinary pass still records every stock and the screens; a pick before 14:45
  still buys; the Render config needs no new setting; every stored status
  shape still renders).
- Existing intraday and scheduler tests (196 selected by name) pass.
- Full suite: **running at the time of the first commit of this batch; its
  actual totals are recorded in a follow-up commit.** No claim is made here.
- Side-pass cost control added after review of the first cut: the daily
  panel, FII flows and the market regime (all one-figure-a-day inputs) are
  loaded once and reused for 30 minutes by the side pass only, and the pass
  waits for the day's first completed 5-minute bar (09:20 IST). An ordinary
  pass reads everything fresh, as before.

## Limitations and open items

- **Not observed live.** Runtime acceptance for RUN-20261125-01 stays open
  until a normal session shows scored intraday stocks on the deployed
  revision. No trade will be induced to prove it.
- **Storage.** A side pass still adds roughly 66 summary lines a day plus the
  decisions that mattered. Permanent journal retention (RUN-20261126-01) is
  not implemented here.
- **Stops for intraday positions** are still checked on the 30-minute pass.
  A 5-minute stop check for intraday positions only was not requested and is
  not built.
- **Five-minute strategy returns are not validated** by the daily-history
  studies; this repair makes the segment able to score, it is not evidence of
  an edge.
- The five settings are code defaults. No Settings-page row was added.
- XR-060 (danger layer counts 2 of 12 tripwires) is fixed and tested in a
  separate parked change at the owner's direction and is **not** in this batch.

## Deployment hold

Live `/healthz` at 2026-11-26 13:43 UTC: database `degraded: true`,
`write_error` "you are over your space quota, using 513 MB of 512 MB. Writes
are blocked on your cluster", `portfolio_save_confirmed_at`
2026-11-26T08:53:56 UTC, `entry_block_reason: portfolio_save_failed`.
Every merge to main restarts the process, and a restart would reload the
08:53:56 UTC copy of the book. Per WORKFLOW.md this change is delivered to the
repository with an explicit deployment hold: do not merge until an
acknowledged save is observed and the owner approves.
