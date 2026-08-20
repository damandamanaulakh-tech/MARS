"""Selection sequence — the owner's stage ladder, made visible.

THE OWNER'S NAMED GAP (full re-audit 2026-08-12, their words: "sequence of
selections is not there"; Node 1 of the pending list): a stock should be
readable as a STAGE on one ladder —

    QUIET → COMPRESSING → ACCUMULATING → RECLAIM → ENTRY WINDOW
                                                  → (after a buy) DANGER WATCH

— instead of a bag of 112 separate trigger lights. This module turns the
trigger layer's existing per-stock reading into that single word.

WATCHLIST-ONLY, per the calibration evidence: the BT01-BT08 run measured the
rise side of the trigger book at essentially chance (best lift 1.15), so a
stage on this ladder is a READING ORDER for the owner's eyes, not an entry
signal. Nothing here opens, closes, sizes or blocks anything, and
engine.py / risk.py / orders.py never import this module (test-enforced).

STAGE RULES — taken from the owner's own sheet where it defines them:
  * COMPRESSING / ACCUMULATING use >= 2 fired in the family, because that is
    the owner's own GATE_RISE_WATCH_01 threshold (compression>=2 AND
    accumulation>=2). ENTRY WINDOW *is* that gate passing.
  * RECLAIM uses >= 1 fired — ASSUMED: their sheet defines no per-family
    reclaim threshold. One failed-breakdown/reclaim event is itself the
    signature (the family's calibrated survivors fire rarely and lead ~9d).
  * A held position is POST-ENTRY WATCH regardless of the ladder: the danger
    layer (the 12 calibrated tripwires) is what guards it from here.
  * The ladder reads top-down and the HIGHEST stage wins — a stock can be
    compressing and reclaiming at once; the later stage is the reading.

DANGER OVERLAY, separate from the stage on purpose: distribution/collapse
fires do not move a stock DOWN the ladder (the owner's ladder has no down
direction) — they set a visible warn flag instead, using the owner's own
fall-avoid arithmetic (distribution >= 3) plus any collapse tripwire. A stock
can be in ENTRY WINDOW with a warn flag; hiding either half would be lying.

Measured leads shown per stage come from the committed BT calibration
(research/bt_calibration/): reclaim survivors lead ~9d, compression /
accumulation / liquidity ~6d, collapse tripwires ~4d. Those are the VERIFIED /
SUPPORTED rows' median leads — the stage inherits its family's measured lead,
it does not have one of its own.
"""
from __future__ import annotations

from typing import Optional

from .triggers import (F_ACCUMULATION, F_COLLAPSE, F_COMPRESSION,
                       F_DISTRIBUTION, F_RECLAIM)

# Ladder order, lowest first. Index = rung height.
STAGES = ("QUIET", "COMPRESSING", "ACCUMULATING", "RECLAIM",
          "ENTRY_WINDOW", "POST_ENTRY_WATCH")

#: Per-stage display facts. `lead` = the measured median lead of the family's
#: calibrated survivors (research/bt_calibration run_2026-08-10, strict
#: labels) — "" where no survivor exists to measure.
STAGE_META = {
    "QUIET": {
        "label": "Quiet", "icon": "·",
        "means": "nothing on the ladder is firing — not scanned for entry, "
                 "just not showing a sequence yet",
        "lead": "",
    },
    "COMPRESSING": {
        "label": "Compressing", "icon": "▁",
        "means": "base forming: 2+ compression triggers fired "
                 "(the owner's own watch-gate threshold)",
        "lead": "family survivors lead ~6d (measured)",
    },
    "ACCUMULATING": {
        "label": "Accumulating", "icon": "▃",
        "means": "absorption: 2+ stealth-accumulation triggers fired",
        "lead": "family survivors lead ~6d (measured)",
    },
    "RECLAIM": {
        "label": "Reclaim", "icon": "▅",
        "means": "a failed breakdown was bought back — 1+ reclaim trigger "
                 "fired (>=1 is ASSUMED; their sheet sets no family threshold)",
        "lead": "family survivors lead ~9d (measured, the earliest warning "
                "in the book)",
    },
    "ENTRY_WINDOW": {
        "label": "Entry window", "icon": "█",
        "means": "GATE_RISE_WATCH_01 passed (compression>=2 AND "
                 "accumulation>=2 AND distribution=0) — the owner's own "
                 "watch gate. Shown, never traded: rise triggers measured "
                 "~chance in BT01-BT08",
        "lead": "",
    },
    "POST_ENTRY_WATCH": {
        "label": "Post-entry watch", "icon": "👁",
        "means": "position is OPEN — the 12 calibrated danger tripwires "
                 "guard it from here (the side of the book with measured "
                 "edge, lifts 1.5-4.1x)",
        "lead": "collapse tripwires lead ~4d (measured)",
    },
}


def stage_for(family_counts: Optional[dict],
              gates_passed: Optional[list],
              held: bool) -> dict:
    """One stock's rung on the owner's ladder, from data the trigger layer
    already computed. Pure function — no I/O, no state.

    Returns {stage, rung, warn, warn_why} where `warn` is the danger overlay
    (distribution cluster per the owner's fall-avoid arithmetic, or any
    collapse tripwire) and never changes the stage itself.
    """
    fam = family_counts or {}
    gates = set(gates_passed or [])
    comp = int(fam.get(F_COMPRESSION, 0) or 0)
    accu = int(fam.get(F_ACCUMULATION, 0) or 0)
    recl = int(fam.get(F_RECLAIM, 0) or 0)
    dist = int(fam.get(F_DISTRIBUTION, 0) or 0)
    coll = int(fam.get(F_COLLAPSE, 0) or 0)

    if held:
        stage = "POST_ENTRY_WATCH"
    elif "GATE_RISE_WATCH_01" in gates:
        stage = "ENTRY_WINDOW"
    elif recl >= 1:
        stage = "RECLAIM"
    elif accu >= 2:
        stage = "ACCUMULATING"
    elif comp >= 2:
        stage = "COMPRESSING"
    else:
        stage = "QUIET"

    warn_bits = []
    if dist >= 3:                      # the owner's GATE_FALL_AVOID_01 leg
        warn_bits.append(f"distribution cluster ({dist} fired)")
    if coll >= 1:
        warn_bits.append(f"collapse tripwire ({coll} fired)")
    return {
        "stage": stage,
        "rung": STAGES.index(stage),
        "warn": bool(warn_bits),
        "warn_why": "; ".join(warn_bits),
    }


def ladder(rows: dict, held: set) -> dict:
    """Group a trigger-watch rows dict into ladder rungs for display.

    `rows` is portfolio.trigger_watch["rows"]; `held` is the set of held
    tickers. Returns {stage: [ticker, ...]} sorted highest rung first, plus
    each row gains nothing — this reads, it does not mutate.
    """
    out: dict[str, list[str]] = {s: [] for s in STAGES}
    for t, r in (rows or {}).items():
        s = stage_for(r.get("family_counts"), r.get("gates_passed"),
                      t in held)
        out[s["stage"]].append(t)
    for names in out.values():
        names.sort()
    return out
