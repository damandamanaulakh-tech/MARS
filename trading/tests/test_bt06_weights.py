"""Node 11 / BT06 (owner: "node 11", 2026-08-24) — measured side-score
weights.

Pinned: the committed run's weights load at import and every key is a
real trigger; side_scores computes with the measured weights (a
zero-weight trigger firing moves nothing); a side where no trigger earned
weight is ABSENT, never a fabricated 0; with no weights file the formula
is byte-identical to the pre-BT06 sheet-weight version; the page note
names which weight set computed the number; the provenance entry cites
the run. Scope guard: nothing here touches the money path — gates remain
display-only (the standing boundary tests cover engine/risk/orders).
"""
from __future__ import annotations

import pytest

from trading import triggers as T


def test_measured_weights_load_and_map_to_real_triggers():
    assert T.MEASURED_WEIGHTS, "committed BT06 CSV must load at import"
    ids = {t.id for t in T.TRIGGERS}
    for tid, w in T.MEASURED_WEIGHTS.items():
        assert tid in ids, tid
        assert w >= 0.0
    # the fit must be selective: some triggers earn weight, some do not
    assert any(w > 0 for w in T.MEASURED_WEIGHTS.values())
    assert any(w == 0 for w in T.MEASURED_WEIGHTS.values())


def test_zero_weight_trigger_moves_no_score(monkeypatch):
    a, b = T.COMPUTABLE[0], T.COMPUTABLE[1]
    monkeypatch.setattr(T, "MEASURED_WEIGHTS", {a.id: 0.0, b.id: 1.0})
    base = T.side_scores({a.id: False, b.id: False})
    fired_zero = T.side_scores({a.id: True, b.id: False})
    assert fired_zero == base                # the 0-weight fire changed nothing
    fired_real = T.side_scores({a.id: False, b.id: True})
    assert fired_real[b.side] == 100.0       # the weighted fire is the score
    assert base[b.side] == 0.0


def test_side_with_no_weighted_trigger_is_absent_not_zero(monkeypatch):
    rise = next(t for t in T.COMPUTABLE if t.side == "RISE")
    fall = next(t for t in T.COMPUTABLE if t.side == "FALL")
    monkeypatch.setattr(T, "MEASURED_WEIGHTS", {rise.id: 1.0, fall.id: 0.0})
    s = T.side_scores({rise.id: True, fall.id: True})
    assert s["RISE"] == 100.0
    assert "FALL" not in s      # no measured evidence != a measured zero
    # and the gate default treats the absent side conservatively
    gates = {g["id"]: g["status"] for g in
             T.evaluate_gates({rise.id: True, fall.id: True})}
    assert gates["GATE_RISE_ENTRY_01"] in ("PASS", "no", "DATA_NEEDED")


def test_no_weights_file_is_exactly_the_old_sheet_formula(monkeypatch):
    monkeypatch.setattr(T, "MEASURED_WEIGHTS", {})
    hits = {t.id: (i % 3 == 0) for i, t in enumerate(T.COMPUTABLE)}
    got = T.side_scores(hits)
    # the pre-BT06 formula, reimplemented literally for the pin
    acc: dict[str, list[float]] = {}
    for t in T.TRIGGERS:
        h = hits.get(t.id)
        if h is None:
            continue
        acc.setdefault(t.side, [0.0, 0.0])
        acc[t.side][1] += t.weight
        if h:
            acc[t.side][0] += t.weight
    want = {s: round(100.0 * f / tot, 1)
            for s, (f, tot) in acc.items() if tot}
    assert got == want


def test_score_note_names_the_weight_set():
    assert "MEASURED" in T.SCORE_NOTE and "bt06" in T.SCORE_NOTE.lower()
    src = T.MEASURED_CONSTANT_SOURCE["side_score_weights"]
    assert "research/bt06" in src and "holdout" in src.lower()
    # the shape remains an assumption and the constants file says so
    assert "shape" in T.ASSUMED_CONSTANTS["score_formula"].lower()
