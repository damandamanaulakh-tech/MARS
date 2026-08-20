"""Selection sequence (owner: "stock selection sequence is still pending" —
Node 1 of the 2026-08-12 audit, their named gap).

What these tests pin: the ladder is the OWNER'S ladder in the owner's order,
its thresholds are the ones their own watch gate defines (so the two can
never disagree on screen), a held name always reads post-entry watch, the
danger overlay warns without demoting, and none of it can touch the money
path.
"""
from __future__ import annotations

from pathlib import Path

import trading.selection_sequence as S
from trading.triggers import (F_ACCUMULATION, F_COLLAPSE, F_COMPRESSION,
                              F_DISTRIBUTION, F_RECLAIM)


def _stage(fam=None, gates=None, held=False):
    return S.stage_for(fam or {}, gates or [], held)


def test_the_ladder_is_the_owners_ladder_in_their_order():
    assert S.STAGES == ("QUIET", "COMPRESSING", "ACCUMULATING", "RECLAIM",
                        "ENTRY_WINDOW", "POST_ENTRY_WATCH")
    for s in S.STAGES:
        assert s in S.STAGE_META


def test_nothing_firing_reads_quiet():
    r = _stage()
    assert r["stage"] == "QUIET" and r["rung"] == 0 and not r["warn"]


def test_each_rung_needs_its_own_evidence():
    assert _stage({F_COMPRESSION: 2})["stage"] == "COMPRESSING"
    assert _stage({F_COMPRESSION: 1})["stage"] == "QUIET"      # gate says >=2
    assert _stage({F_ACCUMULATION: 2})["stage"] == "ACCUMULATING"
    assert _stage({F_RECLAIM: 1})["stage"] == "RECLAIM"


def test_the_highest_rung_wins_when_several_fire():
    fam = {F_COMPRESSION: 3, F_ACCUMULATION: 2, F_RECLAIM: 1}
    assert _stage(fam)["stage"] == "RECLAIM"


def test_entry_window_is_exactly_the_owners_watch_gate():
    """ENTRY_WINDOW must not have its own formula — it IS GATE_RISE_WATCH_01
    passing, so the stage and the gate chip can never disagree."""
    fam = {F_COMPRESSION: 2, F_ACCUMULATION: 2}
    assert _stage(fam)["stage"] == "ACCUMULATING"      # counts alone: no
    assert _stage(fam, gates=["GATE_RISE_WATCH_01"])["stage"] == "ENTRY_WINDOW"


def test_a_held_name_is_post_entry_watch_whatever_the_triggers_say():
    r = _stage({F_COMPRESSION: 3}, gates=["GATE_RISE_WATCH_01"], held=True)
    assert r["stage"] == "POST_ENTRY_WATCH"
    assert r["rung"] == len(S.STAGES) - 1


def test_danger_overlay_warns_without_demoting():
    """The owner's ladder has no down direction: distribution/collapse set a
    visible warn flag, they never change the rung. Hiding either half of
    'entry window BUT distribution building' would be lying."""
    fam = {F_COMPRESSION: 2, F_ACCUMULATION: 2, F_DISTRIBUTION: 3}
    r = _stage(fam, gates=["GATE_RISE_WATCH_01"])
    assert r["stage"] == "ENTRY_WINDOW"
    assert r["warn"] and "distribution" in r["warn_why"]
    r2 = _stage({F_COLLAPSE: 1})
    assert r2["stage"] == "QUIET" and r2["warn"] and "collapse" in r2["warn_why"]


def test_distribution_warn_uses_the_fall_avoid_threshold_not_a_new_one():
    assert not _stage({F_DISTRIBUTION: 2})["warn"]     # gate says >=3
    assert _stage({F_DISTRIBUTION: 3})["warn"]


def test_ladder_grouping_puts_every_stock_on_exactly_one_rung():
    rows = {
        "A.NS": {"family_counts": {F_COMPRESSION: 2}, "gates_passed": []},
        "B.NS": {"family_counts": {}, "gates_passed": []},
        "C.NS": {"family_counts": {F_ACCUMULATION: 2},
                 "gates_passed": ["GATE_RISE_WATCH_01"]},
        "H.NS": {"family_counts": {}, "gates_passed": []},
    }
    lad = S.ladder(rows, held={"H.NS"})
    assert lad["COMPRESSING"] == ["A.NS"]
    assert lad["QUIET"] == ["B.NS"]
    assert lad["ENTRY_WINDOW"] == ["C.NS"]
    assert lad["POST_ENTRY_WATCH"] == ["H.NS"]
    assert sum(len(v) for v in lad.values()) == len(rows)


def test_missing_counts_read_quiet_never_crash():
    assert _stage(None, None)["stage"] == "QUIET"


def test_every_stage_says_what_it_means_and_cites_measured_leads():
    """The page prints STAGE_META verbatim; a stage without a plain-language
    meaning is a mystery chip. Leads must say 'measured' — they come from the
    committed calibration, not from hope."""
    for s, m in S.STAGE_META.items():
        assert m["means"], s
        if m["lead"]:
            assert "measured" in m["lead"], s
    assert "ASSUMED" in S.STAGE_META["RECLAIM"]["means"]   # the >=1 threshold
    assert "never" in S.STAGE_META["ENTRY_WINDOW"]["means"].lower() or \
        "never traded" in S.STAGE_META["ENTRY_WINDOW"]["means"]


def test_watchlist_only_the_money_path_never_imports_the_sequence():
    root = Path(S.__file__).resolve().parent
    for name in ("engine.py", "risk.py", "orders.py", "danger.py"):
        src = (root / name).read_text()
        assert "selection_sequence" not in src, (
            f"{name} must not import selection_sequence — the ladder is a "
            "reading order (rise side measured ~chance in BT01-BT08), "
            "never an entry signal")
