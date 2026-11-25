"""The approved six-share order must survive persisted legacy overrides."""
from copy import deepcopy

import pytest

from trading import category_budget as cb
from trading.config import Config
from web import state


def _store(monkeypatch, fail=False):
    saved = {"budget_share_momentum": .40, "budget_share_intraday": .15,
             "budget_share_short": .15, "budget_share_swing": .15,
             "budget_share_positional": .15, "category_budgets_enabled": False,
             "mode": "swing", "custom_universe": ["KEPT.NS"]}
    calls = []

    def write(values):
        calls.append(deepcopy(values))
        if not fail:
            saved.update(deepcopy(values))

    monkeypatch.setattr(state.db, "load_settings", lambda: deepcopy(saved))
    monkeypatch.setattr(state.db, "save_settings", write)
    return saved, calls


def test_order_is_one_atomic_merge_and_preserves_unrelated_settings(monkeypatch):
    saved, calls = _store(monkeypatch)
    cfg = Config()
    assert state._migrate_segment_allocation(cfg, deepcopy(saved))
    assert len(calls) == 1
    assert calls[0][cb.OWNER_ALLOCATION_MARKER] is True
    assert saved["mode"] == "swing" and saved["custom_universe"] == ["KEPT.NS"]
    assert saved["category_budgets_enabled"] is True
    for cat, fraction in cb.OWNER_SHARES.items():
        assert saved[cb.SHARE_FIELDS[cat]] == fraction
        assert cb.share(cfg, cat) == fraction
    assert cb.share_total(cfg) == pytest.approx(1)


def test_restart_retains_later_deliberate_edit_and_does_not_reapply(monkeypatch):
    saved, calls = _store(monkeypatch)
    assert state._migrate_segment_allocation(Config(), deepcopy(saved))
    # Later owner edit remains free; the marker is merged, never overwritten
    # by partial settings writers. Reconstruct exactly as bootstrap does.
    saved["budget_share_momentum"] = .28
    saved["budget_share_ocean"] = .04
    cfg = Config()
    for key, value in saved.items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)
    assert not state._migrate_segment_allocation(cfg, deepcopy(saved))
    assert len(calls) == 1
    assert cb.share(cfg, cb.MOMENTUM) == .28
    assert cb.share(cfg, cb.OCEAN) == .04


def test_failed_store_does_not_mark_complete_and_next_boot_retries(monkeypatch):
    saved, calls = _store(monkeypatch, fail=True)
    cfg = Config(budget_share_momentum=.40)
    assert not state._migrate_segment_allocation(cfg, deepcopy(saved))
    assert cb.OWNER_ALLOCATION_MARKER not in saved
    assert cb.share(cfg, cb.MOMENTUM) == .30
    assert not state._migrate_segment_allocation(Config(), deepcopy(saved))
    assert len(calls) == 2


def test_partial_acknowledgement_is_not_confirmed(monkeypatch):
    saved, _ = _store(monkeypatch, fail=True)
    monkeypatch.setattr(state.db, "save_settings", lambda values: saved.update(
        {cb.OWNER_ALLOCATION_MARKER: True}))
    assert not state._migrate_segment_allocation(Config(), deepcopy(saved))

