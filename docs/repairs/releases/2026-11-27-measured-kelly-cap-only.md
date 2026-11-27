# OWN-KELLY-01 — a losing record now shrinks the trade instead of stopping the book

**Owner's choice, 2026-11-27, from an options card: "Cap only, never block".**
Their words for the option they picked: *"The check shrinks position size when
the edge is weak but never refuses a buy outright."* They chose it after
reading the live reading on their own Risk page, which they sent as a
screenshot. The alternatives offered — leave the veto on, or switch the whole
check off — were both declined, so the measurement stays and only its
consequence changes.

## What was wrong

Nothing was broken in the arithmetic. The fault was in the loop it closed.

`measured_kelly.measure` takes the book's own closed momentum trades and
returns `block_new_entries=True` whenever the measured Kelly fraction is not
positive. `engine.scan` read that flag and **refused every new momentum
entry**. The live reading on 27 November:

| | |
|---|---|
| usable exits | **9** |
| win rate | 11% |
| payoff ratio | 0.27 |
| raw Kelly | **−1.645** |
| status | `NO_POSITIVE_EDGE` |

The estimator's own confidence ramp is `min(1, n / 300)` — it does not consider
a sample trustworthy until **300** closed trades. At nine exits the confidence
weight is 0.03. So the check refused to let the book trade on evidence it
itself rated at three percent, **and refusing to trade is what stopped the book
from producing the evidence that would have cleared it.** Nine bad exits could
hold the momentum engine shut indefinitely, because the tenth could never
happen.

That is the whole repair: the measurement was sound, the gate was self-sealing.

## What changed

* `cfg.measured_kelly_block_enabled: bool = False` — a new switch, **off by
  default**, which is the owner's decision. The check itself
  (`measured_kelly_enabled`) stays **on**: it still measures, and it still caps.
* `engine._measured_kelly_cap` returns the **minimum ticket** (the slab) on a
  `NO_POSITIVE_EDGE` reading instead of refusing. A cap of zero would be a veto
  wearing a cap's clothes, so the floor is the slab and nothing smaller.
* `engine.scan` refuses the entry only while the switch is on. With it off the
  pick is sized at the minimum and opened.
* The journal line names the policy that actually ran:
  **`MEASURED_KELLY_NO_EDGE`** with `policy: "cap_only"` when it capped,
  `MEASURED_KELLY_BLOCKED` with `policy: "block"` when it refused. A line
  called BLOCKED on a day nothing was blocked is the lying-screen class.
  `web/why_no_buy.py` and `web/market_panels.py` learned the new action, so
  "why no buy today" still accounts for the day either way.
* The /risk card prints an amber **"NEW SCAN ENTRIES CAPPED AT ₹X, NOT
  BLOCKED"** headline in cap-only mode, and the Den engines screen drops its
  two "entries are blocked" sentences unless the switch is on.
* Settings gains the row **"Measured Kelly Veto On (1/0)"** so the owner can
  put the veto back without a deploy.

## A second fault this surfaced, fixed here

`measure()` is pure and **cannot see the config switch**, yet its own `note`
ended with *"so new entries are blocked."* That string renders verbatim on
**both** the /risk card (`risk.html:130`) and the Den engines screen
(`_den_engines.html:220`). On a cap-only day the owner's own screen would have
told them entries were blocked while the book was buying.

Found by reading the real record a test run produced, not by reading the diff.
Both notes that asserted a policy (the `NO_POSITIVE_EDGE` one and the
`ALL_LOSS` one) now state the measurement and stop there — the estimator
measures, the screen says what was done about it. `block_new_entries` still
carries the machine-readable fact, so no consumer loses information.

## Money

**Entry side, momentum only** (`cfg.measured_kelly_modes =
("nifty200_momentum",)`; Ocean was already exempt, and owner BUY and the Buy
Desk were never gated). Exits, stops, targets and the danger layer are
untouched.

**Say this plainly, because it is a real change in shape:** while the record
shows no edge, every momentum pick now opens at **exactly ₹1,50,000** rather
than at its score-sized slab amount. Momentum's 30% share of the ₹5 crore book
is ₹1.5 crore, so that share now admits roughly **100 minimum-size positions
instead of a handful of large ones** — many small bets rather than few big
ones, until enough exits accumulate for the measurement to size them again. The
222-position ceiling and every other `can_open` gate still apply. A reading of
`ACTIVE` behaves exactly as before: the cap is its own measured fraction.

## Tests

`trading/tests/test_measured_kelly_cap_only_2026_11_27.py` — **12 tests, 8
verified to fail on the pre-repair code** (e6634c8, run in a worktree). The 4
that pass there do so by design: they pin behaviour this repair does *not*
change — the veto refusing exactly as before when switched on, an `ACTIVE`
reading still capping on its own fraction, the cap being unused when the veto
refuses, and a crashed estimator still never stopping a buy.

What they pin: the switch is off by default while the check stays on; the
no-edge reading no longer refuses; the owner's exact 9-trade shape now buys;
the capped entry opens at the minimum ticket; the veto switched on refuses
exactly as before; **the recorded observation still reads `state: "failed"`
while `outcome: "bought"`** — a cap is not a pass, and the record says both
facts; the journal action names the policy; the cap is the floor and never
zero; and the reading's note never claims a block.

**Three existing tests pinned the old unconditional veto and now switch it on
explicitly, each with a dated comment** —
`test_measured_kelly_wired_2026_10_02.py::test_no_measured_edge_blocks_every_scan_entry`
and `::test_rotation_never_sells_for_a_blocked_pick`, and
`test_measured_kelly_momentum_only_2026_10_05.py::test_losing_momentum_exits_still_block_the_momentum_scan`,
whose real subject is V1.3's category scoping. Their subjects are unchanged;
only the switch had to be stated.

## Not done

The check still pools nothing — it reads momentum exits only, which is V1.3 and
correct. The confidence ramp's 300-trade target is untouched: it is the number
that made the veto self-sealing, but lowering it would be a fitted constant with
no measurement behind it, and the owner's decision removed the need.
