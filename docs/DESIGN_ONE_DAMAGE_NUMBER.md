# One damage number — design note (Node 9)

Node 9 of the 2026-08-12 audit says: *"Consolidate IFR / danger layer / FII
stacks onto the same internals series so the app has a single fracture
gauge with three consumers. Design note first."* This is that note. **No
code changes with this document** — it exists so the owner can approve or
reject a concrete design instead of a slogan.

## The overlap, precisely

Three systems each measure "how damaged is the tape" with their own
arithmetic:

| system | series it reads | what it does with it | money path? |
|---|---|---|---|
| IFR throttle | own damage-cluster count over the scan panel (engine) | halves NEW entry size for 22 sessions after a fire | YES — sizes entries |
| Danger layer | 12 calibrated tripwires on each HELD name's own bars (+ index/sector panels for 0094/0095) | tightens stop / exits half | YES — exits |
| FII-08/09 stacks (/register) | `tail_down3_5d` vs the 0.213 threshold from the owner's own internals file, AND-ed with FII legs | display only | no |

The overlap is real but narrower than it looks: IFR and FII-08/09 both use
a *panel-tail-damage* idea, while the danger layer is mostly *per-stock*
signatures. The three do not disagree today — they just each define
"damage" privately, so when one fires and another stays quiet, nobody can
say which is right.

## The proposal

1. **One canonical series**: daily `tail_down3_5d` (share of panel members
   whose worst 3-day return in the window ≤ −3%, 5-day mean) computed once
   per day over the scan panel — the exact formula recovered from the
   owner's `market_internals_1996_2024.csv` (verified 6,808/7,162 rows) —
   stored on the portfolio like `breadth_store` (JSON+Mongo, asof-dated).
2. **Three consumers, unchanged behaviour on day one**:
   * FII-08/09 read the stored number instead of recomputing it (display —
     zero risk).
   * IFR keeps its own fire rule but *journals* the canonical number next
     to its own each time it fires, so 60 days of live data show whether
     the two agree.
   * The danger layer changes **nothing** — its 12 tripwires are
     calibration-VERIFIED per-stock rules; welding them to a panel gauge
     would discard measured evidence for tidiness.
3. **Only after the 60-day window**, and only on the owner's word: if the
   journal shows IFR's private cluster and the canonical series fire
   together, retire the private computation and let IFR read the stored
   number. If they diverge, the divergence itself is the finding.

## What this changes for the owner's money

Nothing at step 1–2. Step 3 could change *when* the IFR half-sizing
triggers — which is exactly why it waits for live evidence and an explicit
order.

## Cost

Step 1–2 is roughly one short session (a pure function + a store field +
two read sites + tests). Step 3 is a one-line switch once ordered.

**Decision needed from the owner**: "go" on steps 1–2, or leave the three
gauges separate. Nothing proceeds without it.
