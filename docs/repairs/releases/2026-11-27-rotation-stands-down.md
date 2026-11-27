# XR-052 — rotation no longer sells a winner for a buy that cannot happen

**Owner's choice, 2026-11-27, from an options card: "Only sell when the new buy
can actually go through."** (Capping it at one sale per scan was offered as a
stronger option and leaving it alone as the third; the recommended middle was
taken.)

## What was wrong

The entry decision runs in two passes, exactly as the 2026-10-02 S-4 repair
describes: a **gate pass** that fills `cleared`, then **rotation**, then the
**sizing and opening pass** whose single `can_open` call is the only place the
kill switch, the sector caps, the minimum ticket and the rest are applied.

So rotation decided **before** those gates ran. Across the gate pass and the
rotation call, the strings `kill_switch` / `can_open` / `sector_cap` / `heat`
appear exactly **once**, in a comment. `_rotation_exits` never called
`can_open`. The kill switch is the sharpest case: it is tripped ~560 lines
earlier, at the top of `scan`, and the gate pass never reads it.

Reproduced three ways with the real engine, offline — a near-target holding
bought at 1,000 with a 1,200 target, quoted 1,190, and cash short of a ticket:

| day | rotation sold | `can_open` then said |
|---|---|---|
| kill switch tripped | `NEAR.NS` | `kill_switch_active` |
| 12 IT names held | `NEAR.NS` | `sector_cap_reached` |
| ticket under the minimum | `NEAR.NS` | `below_min_value_125000` |

Each time: winner gone, round trip of costs booked, last stretch to target
given up, cash idle, nothing bought. It bites hardest exactly when it should
not — **a kill-switch day is a day the book has already lost 2%, and that is
the day the engine would sell a winner for nothing.**

**Two of the row's five claims do not hold, and are recorded as not holding
rather than inflated:**

* **Position ceiling** — not reachable in the single-pick case, because the
  rotation sale itself frees the slot it needs. Measured: 6 held against a
  ceiling of 6; after the sale 5 < 6 passes.
* **Portfolio heat** — arithmetically unreachable under live settings.
  `max_portfolio_heat_pct` is 0.25 while the widest stop any entry can carry is
  `ATR_STOP_CAP` 0.15, and deployed capital is bounded by the deploy cap. The
  worst case that could be built — eight wide-stop holdings plus a winner whose
  stop has trailed above entry, so selling it frees no heat budget — reached
  **12.6% of a 25% cap**.

**Category budget** is already guarded: `_rotation_exits` does its own share
arithmetic, so `category_budget_full` is mostly pre-empted there.

## What changed

`trading/risk.blocked_regardless_of_cash(...)` — a read-only test of the gates
no amount of freed cash changes: the kill switch, a zero-sized pick, a share
dearer than the whole ticket, the sector count cap and the sector capital cap.
`_rotation_exits` runs it over every fresh pick and drops the ones that are
blocked anyway; if none survives it journals **`ROTATION_STOOD_DOWN`** naming
the reasons and sells nothing.

**The order is deliberately NOT changed.**
`trading/tests/test_rotation_exec.py` asserts `rot < src.index("can_open(")`
on purpose — the freed cash has to exist when `can_open` counts it. Reordering
would break the feature and that test.

**The design subtlety that is the whole content of the work:** a naive dry run
refuses nearly every pick for `no_headroom` / `qty_zero`, because there is no
cash yet — which is precisely what rotation exists to fix — so rotation would
never fire at all. The helper therefore tests **only** the non-cash gates, and
a source test asserts it never reads cash. Cash, headroom, quantity, costs and
the minimum round-up are left to `can_open`.

**And `freeing`:** the sector gates are judged with the rotation candidates
treated as already sold, because selling a **same-sector** holding really does
free a sector slot. Without that, the helper would stand rotation down in the
one case where selling genuinely helps. Selling another sector's holding does
not free this one, and that is tested both ways.

**A guard that breaks the feature it guards is worse than no guard:** a failure
inside the read is swallowed and the rotation proceeds as before, with a test
for it.

## Money

**Exit side, with an entry-side consequence.** The unit of damage was one
near-target winner per affected scan — a position 90%+ of the way to its target
sold, its costs booked, its last stretch given up, and ₹1.5 lakh to ₹25 lakh of
freed cash left idle. It never created a loss; it destroyed upside and churned
costs. Frequency was bounded by rotation's own triggers (cash short for the
cleared picks, or cash under 5% of the book), so occasional, not daily.

**Cost of the repair, stated:** on rare mixed days rotation does slightly less
— if every pick is blocked anyway, nothing is sold. That is the point.

## Tests

`trading/tests/test_rotation_stands_down_2026_11_27.py` — 12 tests, **all 12
verified to fail on the pre-repair code.** Each non-cash gate is named; a clean
pick is not blocked; a sector slot freed by the sale **is** counted and another
sector's is not; the gates rotation exists to fix are **not** named (naming them
would stop it firing at all); a source test that the helper never reads cash;
the engine sells nothing and books no trade on a kill-switch day and journals
`ROTATION_STOOD_DOWN`; and a read failure never stops a rotation.
