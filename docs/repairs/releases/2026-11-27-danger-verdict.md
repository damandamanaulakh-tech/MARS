# XR-060 — the danger layer counted only the tripwires that happened to return a Python bool

Status: **implemented and locally tested on the PR234 branch; NOT merged, NOT
deployed.** The deployment hold in
[the intraday record](2026-11-26-intraday-pipeline.md) applies. **This is a
money-path repair:** it changes which held positions the danger layer acts on.

## Owner decision

27 November: "go", against the recommendation to take XR-060 next — the
highest-priority confirmed money-path row on the open list. The fault was
first recorded in the 2026-10-14 upload review and had been parked since,
because the owner had placed their tables 1-3 in a later batch.

## The fault

`danger.evaluate_stock` decided a tripwire had fired with `result is True`.

Eight of the ten price tripwires return **`numpy.bool`**, which is never
`True` by identity. Their fires were computed and thrown away. Only
`T12W_0079` and `T12W_0096` return a real Python `bool`; the two panel-fed
tripwires (`T12W_0094`, `T12W_0095`) already wrapped their answer in
`bool()`. So **4 of 12 VERIFIED tripwires could ever reach the engine**, and
the owner's danger layer had been running at a third of its cover since it
went live on 2026-08-11.

**Why it survived every reading of this file.** Under NumPy 2 the type is
*named* `bool` — `np.bool_.__name__` is `"bool"` — and it prints as `True`.
A reader checking the return type sees `bool`, and a reader printing the
value sees `True`. Only an identity test tells them apart.

The same mistake also mislabelled the record: a fired tripwire was written
to the decision evidence as **`unavailable`** rather than `passed`, because
the state expression tested identity too. So the journal under-reported the
layer exactly as the engine did.

## Reproduced before anything was changed

`trading/tests/test_danger_verdict_2026_11_27.py` on the unrepaired code:
**20 of its 24 tests fail.** On a synthetic shock (calm uptrend, then one
-12% day on nine times the volume) four tripwires fire by value and
`evaluate_stock` returns an empty list. Measured return types on that frame:

| Returns a real `bool` | Returns `numpy.bool` |
|---|---|
| T12W_0079, T12W_0096 | T12W_0068, 0072, 0080, 0085, 0089, 0091, 0092, 0093 |

## Implemented behavior

- `danger._verdict(result)` reads an answer **by value**: `bool(result)` for
  either boolean kind, `None` for anything else. An int, a float, a string
  or an array is unknown and abstains — a stop must never tighten on a value
  nobody meant as a verdict.
- Every call site reads through it: both tripwire loops and the decision
  record. No raw trigger result is compared by identity any more
  (syntax-tree pinned, so a comment or docstring cannot satisfy the test).
- A tripwire that abstains (too few bars, or one that raised) still abstains.
  Nothing new fires on thin data.

**Also repaired, found while reading for this one:**
`triggers._cluster_fired` counted a cluster member with the same identity
test, so the owner's accumulation (`T12W_0032`) and distribution
(`T12W_0081`) cluster counts under-reported. Measured on one real-shaped
series: distribution counted **1** by identity against **3** by value. Those
two feed the owner's own gates, the selection-sequence ladder and the
/triggers screens, and are **display only** — no money path reads them
(`trading/triggers` is barred from the engine by the standing boundary
test). This half can be reverted on its own.

**Two more defects found in the adversarial review of this change, both
reproduced, both repaired here.** Each was latent while the layer was blind;
counting its fires is what made them live.

1. **The once-per-ticker-per-day guard did not hold on a real book.** It was
   read back out of `decision_journal[-300:]`, but that journal is
   BYTE-capped (`paper.JOURNAL_BYTES_CAP`, 2 MiB) and trimmed inside the save
   the danger pass itself calls. One pass on a 56-position book writes about
   112 rows — a `DANGER_*` row and a `SELL_PARTIAL` row per acted name — of
   which only ~103 survive, so **four names lost their own guard every pass
   and were half-sold again**. Measured over one day's thirteen passes: 56
   intended half-sales became **104**, and the worst position was cut from
   1,000 shares to **250**. The record now lives on the book
   (`Portfolio.danger_acted`, persisted to JSON and Mongo both ways, rolling
   over by date), where the cap cannot evict it. Re-measured after the fix:
   56 of 56, every position halved once.
2. **Thin history could act.** `evaluate_stock` called the tripwire functions
   directly with no minimum-history gate, relying on them to return None —
   which they do not. Every danger tripwire needs **70 bars**, and
   `engine._danger_pass` admits a position at **60**. Measured: at 30, 60, 65
   and 69 bars the repaired layer fired four tripwires and would have
   half-sold. It now honours each tripwire's own `_MIN_BARS` and records the
   skip as `unavailable`, which is what this module's docstring always
   promised. Re-measured: nothing fires below 70 bars, four fire at 70.

**An entry-side change, which an earlier draft of this record wrongly denied.**
`engines/ocean.py` rail R4 is a BUY veto fed by `danger.evaluate_stock`
(`engine.py:4229`). With the tripwires counted it vetoes Ocean candidates it
previously waved through — reproduced in review on a real-shaped candidate
35.2% below its 200-DMA. Ocean has produced zero entries in twelve years of
the committed panel, so the practical effect is nil, but the claim "entries
are not changed" was false and is withdrawn.

Not changed: the tripwire formulas, their calibrated verdicts, the VERIFIED
list, `TIGHTEN_PCT`, the leash floor, the half-exit rule, the alert-only
window, scores, momentum/intraday/short/swing/positional entries, sizing or
targets.

## What it changes for the money

The engine acts on a held name only (`engine._danger_pass`):

- **1 tripwire → the stop becomes a trailing leash off the live price**,
  whose width is floored at the wider of 2.5%, the position's own category
  stop and its entry-stop width (the 2026-09-16 and 2026-10-04 owner rules).
  So it ratchets a winner's stop up and **cannot be tighter than the risk
  already accepted at entry**.
- **2+ tripwires → half the position is sold** (a real closed trade; a full
  close when the quantity is 1).
- Alerts only during the first `ALERT_DAYS`; once per ticker per day; no
  action without a live quote and an open session.

### Measured, 251 real names at the 26 November close

Live tokenless daily candles for the owner's 172-name list plus the
committed 191-stock panel (251 distinct names, every one read). The same
histories scored twice: once under the old identity rule, once under the
repair.

| | Before | After |
|---|---|---|
| Names with 1+ fire — **stop becomes a trailing leash** | 1 | **7** |
| Names with 2+ fires — **half the position sold** | 0 | **1** |
| Total fires counted | 1 | 8 |

Which tripwires fired, after: Volatility Tripwire 4, Bearish Wide Range
Close 2, Crash Candle After Failed Breakout 1, ATR Shock + Close Low 1. The
one name that would now be half-sold and would not have been before:
**ZYDUSLIFE**.

So on a calm day (market state BULL_EXTENSION) this is **2.8% of names given
a trailing stop and 0.4% half-sold**, against 0.4% and none before.

**On a bad day it is far larger, and that is the point of the repair.** The
2026-10-15 reading of the same layer on 220 names recorded 141 names firing
a price tripwire, 192 fires and 31 names with two or more — of which the
engine counted 12. Scaled to that day this repair moves roughly 5% of held
names being acted on to roughly 64%, with about 14% half-sold instead of
almost none. A one-day reading is not a forecast; both numbers are given so
the range is visible rather than implied.

## Validation

- `trading/tests/test_danger_guard_2026_11_27.py` (4): one action per ticker
  per day on a 56-position book across three passes; the guard does not
  depend on the byte-capped journal (the fixture is asserted to overflow it);
  the record survives a restart; a new day re-arms. **All four fail before
  the guard repair.**
- `trading/tests/test_cluster_count_2026_11_27.py` (4): the cluster count
  equals what its members really say; the members are shown to answer with
  NumPy so it is not a corner case; an abstaining, array-returning or
  integer-returning member is not counted; a member below its own minimum
  history is skipped. The first fails before the repair.
- `trading/tests/test_danger_verdict_2026_11_27.py` (28): the shock is
  counted; the NumPy majority is proven to be the common case and the
  name-collision trap pinned; both boolean kinds read by value; ten
  non-boolean values each abstain; an abstaining tripwire never becomes a
  fire; a quiet stock fires nothing; a NumPy fire is recorded as `passed`
  rather than `unavailable`; the two panel tripwires keep their `bool()`
  real `bool` (asserted on the VALUE, not on their source text); thin
  history at 30, 60 and 69 bars fires nothing and is recorded `unavailable`;
  and a syntax-tree pin that every tripwire answer is read through
  `_verdict` — name-independent, and verified to FAIL when the wrap is
  removed.

  **Three of these were weak in the first cut and the review caught them**:
  the abstention test compared the layer with the functions it calls (true by
  construction, and it ran at 30 bars where the layer really did half-sell);
  the panel-pair test asserted on source text a comment would satisfy; and
  the source pin named a variable the repair had already renamed, so it
  guarded nothing.
- `web/tests/test_danger_blind_cleared_2026_11_27.py` (5): nothing reads as
  blind and all ten price tripwires count; a tripwire returning something
  that is not a verdict IS still reported blind (so the probe is not
  hard-wired to pass); an abstention is not called blindness; the Den stops
  printing the red warning; and it comes back if the layer ever goes blind
  again.
- **On the code before the repair, 20 of those 24 tests fail.** The 4 that
  pass pin behaviour that must not change.
- Existing danger and trigger suites: 98 passed, unchanged.
- Full local suite: running at this commit from a clean state directory; the
  total is recorded in the next commit.

## Limitations and open items

- Not observed live. Acceptance is a normal session on a deployed revision
  whose journal shows a DANGER action with its tripwire named; no position
  will be induced into one.
- The tripwires' calibrated lifts (1.6-4.1x, strict labels for ten of them,
  main labels for the two panel-fed ones) were measured on their **formulas**,
  not on this engine's tighten/half-exit response. This repair makes the
  layer behave as the 2026-08-11 approval described; it does not re-earn that
  approval.
- `TIGHTEN_PCT` (-2.5%) remains an ASSUMED number, as recorded since
  2026-09-16. It binds only where it is wider than the position's own stop.
- The cluster-count half changes two display triggers. Their gates were never
  calibrated as gates, and the owner's own rule keeps them off the money path.
