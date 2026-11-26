# VOL-001 — two volume gates, each with its own reading and switch

Status: **implemented and locally tested on the PR234 branch; NOT merged, NOT
deployed.** The deployment hold in
[the intraday record](2026-11-26-intraday-pipeline.md) applies. **This is not
a display-only change:** Volume Gate 2 refuses a buy in the Intraday segment.

## Owner decision

26 November, on the formula table: "two volume gates shown separately" — a
5-against-20 trend, and a hard 1.5× spike "blocking Intraday only". Approved
default for the open question: Gate 2 blocks in Intraday only and is shown
without blocking elsewhere. Extending Gate 2 to other segments is a separate
test-first item.

## Before

- Volume entered a scan only as one half of the timing check
  (`timing.timing_ok`): the mean of the last 5 bars against the mean of the
  last 20, at each segment's own mark (Intraday 1.5, Short 1.3, Swing 1.2,
  Positional 1.2, Momentum 0 = off). No screen showed it apart from the
  combined "timing yes / no".
- Nothing checked the latest bar's own volume.

## Implemented behavior

- **Gate 1 — trend.** Unchanged by default: a stock below its segment's mark
  fails timing and is not bought. It now also has its own reading
  (`timing.volume_gates`) and its own switch, `volume_gate1_enabled`
  (default on). Off = still measured and shown, timing rests on the breakout
  leg alone; the decision record then stores it as advisory, not as a
  refusal.
- **Gate 2 — spike.** `timing.passes_volume_spike`: the latest bar's volume
  divided by the mean of the 20 bars **before** it; 1.5× or more passes. The
  latest bar is not part of its own average. Too few bars, a zero base or an
  unreadable value is "unknown", never a pass.
- **Where Gate 2 refuses.** Only in the segments listed in
  `volume_gate2_block_modes` (default `("intraday",)`) while
  `volume_gate2_enabled` is on. In the scan's pick loop a stock that cleared
  its score and timing but has no spike (or an unreadable one) is not picked:
  skip reason `volume_spike`, decision gate `volume_spike` recorded with the
  ratio and the mark. In every other segment the gate is a reading; no
  refusal and no decision-gate row is written.
- A "bar" is what the pass scores on: a completed 5-minute bar in Intraday,
  a daily bar in the other four segments.
- **Counts.** Every pass stores how many scored stocks pass each gate, the
  mark each needs, whether it can refuse there, and how many stocks Gate 2
  alone stopped (`scan_matrix["volume_gates"]`; for the 5-minute pass,
  `intraday_data_status["last_pass"]["volume_gates"]` and one sentence on the
  `INTRADAY_PASS` line). Each scored stock's two readings are stored in
  compact form on its scan row.
- **Screens.** Scan page: a "Volume gates" block with both gates' rule,
  count and effect; both readings under each pick's timing verdict; two piano
  lanes with their own tap pages (a miss is red only where the gate can
  refuse, amber where it is a reading, grey when off or unreadable); the
  last 5-minute pass line says how many stocks the spike gate stopped. Stock
  page: both gates on the stored daily bars with each daily segment's mark.
  Den: one line stating both rules from the live settings. Settings: two
  switches; the existing ratio field is relabelled "Volume Gate 1 mark".
- **Storage.** No per-stock journal row was added for the readings. The
  `volume_spike` decision gate is written only for an Intraday stock that
  reached it; it is deliberately not added to the list of gates every
  decision carries, so ordinary decision records do not grow
  ([RUN-20261126-01](../evidence/2026-11-26-journal-growth-inventory.md)).

Not changed: scores, weights, score floors, the breakout leg, stops, targets,
sizes, exits, segment shares, owner order paths (they do not pass through
timing), the backtest's use of `timing_ok`, and Gate 1's marks.

## What the gates read on real daily bars (context, not a test of returns)

253 stocks with a bar on 25 November 2026 (public daily candles held in the
session scratchpad, production `timing` functions):

| Reading | 25 Nov | Last 60 sessions (15,180 stock-days) |
|---|---|---|
| Gate 1 trend ≥ 1.2× | 54 of 253 (21%) | 24.7% |
| Gate 1 trend ≥ 1.3× | 43 of 253 (17%) | 18.3% |
| Gate 2 spike ≥ 1.5× | 44 of 253 (17%) | 15.8% |

No 5-minute history is stored, so the same count for Intraday bars is not
available. **Gate 2 has not been tested against returns in any segment.**

## Validation

- `trading/tests/test_volume_gates_2026_11_26.py` (25): the spike formula
  and its unknown cases; each segment's rule; both switches; Gate 1's
  unchanged default and its advisory record when off; an Intraday pick
  without a spike is refused and with one is bought; the gate off buys;
  the 5-minute summary and its single decision record; a daily segment is
  never refused and the readings survive the saved book; a failed reading
  costs the columns in a daily pass and refuses in Intraday; no size, stop,
  target, exit, score or order module reads the gate (syntax-tree check).
- `web/tests/test_volume_gates_page_2026_11_26.py` (16): cell colours;
  piano lanes; records stored before the gates; both tap pages; the Scan
  block in Short, Intraday and Momentum; per-pick readings; the 5-minute
  line; the stock page; the Den line; the two switches through a real
  Settings save.
- On the previous branch commit `9f7439c` the same two files give **39
  failed, 2 passed** (the 2 pin behavior that must not change).
- Local render with real 25 November volume readings: Scan block at 1280 and
  390 px, the pick rows, the spike lane and its tap page.
- One existing fixture was updated, not weakened:
  `test_scan_crossing_deadline_refuses_intraday_entry_before_rotation` stubs
  the timing check to isolate the entry deadline and supplied flat-volume
  bars; it now supplies a passing volume reading the same way. It was the
  only failure in the first full run with the gates (3,925 passed, 1 failed).
- Full local suite on commit `2e5198d` (the gates, that fixture and the
  pre-rise label) from a clean state directory: **3,951 passed, 0 failed, 0
  errors, 0 skipped** (695 s; junit report read back). The branch head
  `8f74b78`, which adds the VIX switch, gives **3,983 passed, 0 failed, 0
  errors, 0 skipped** (655 s).
- CI on the gates commit `aa1454b`, before the fixture update: 3,925 passed,
  1 failed — the same single test. CI on `2e5198d` completed with nothing
  failed.

## Limitations and open items

- Not observed live. Gate 2's effect on Intraday buying will be visible only
  after a normal session on a deployed revision; no trade will be induced.
- Intraday already needs a score of 72, a close within 0.5% of the 20-bar
  high and Gate 1 at 1.5×. Gate 2 makes it stricter still; the 5-minute
  summary counts what it stops so the cost is visible.
- The 1.5 and the 20 are the owner's numbers, not measured ones.
- Extending Gate 2 to Short, Swing, Positional or Momentum is not built.
  **It was measured on 2026-11-27 and NOT SUPPORTED** — plan committed
  first, `research/volume_gate2_study/` (PLAN.md, summary.md,
  run_2026-11-27 with its identity sidecar), `scripts/volume_gate2_study.py`.
  On the 11,055 Momentum picks of the committed 191 x 12-year panel (the
  same population as the VIX test), a buy the gate passes returns **+1.659%
  against +2.385% for one it refuses in the first half** (to 2020-06-30) and
  +4.555% against +3.958% in the second: the sign flips, so the whole-period
  +0.152 points comes from the second half alone. The ratio buckets have
  opposite shapes in the two halves (the biggest spikes are the WORST bucket
  before 2020 at +0.700%, among the best after at +4.970%), and the gate
  helps in 6 of 12 calendar years. At the owner's 1.5 mark it would refuse
  **76% of Momentum buys**. The marks 1.2 / 1.3 / 1.5 / 2.0 are in the run
  file but deliberately not used to pick a better one — 2.0 shows the same
  flip, wider. `volume_gate2_block_modes` stays `("intraday",)` as shipped;
  the owner decides. The vectorised ratio was proved equal to the production
  `passes_volume_spike` on 382 real (ticker, date) pairs before use.
  Intraday itself still cannot be measured: no 5-minute history is stored.
