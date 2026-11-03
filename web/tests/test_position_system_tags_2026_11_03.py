"""Editing a journal note cannot change strategy allocation or exit identity."""
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from trading import category_budget as cb
from trading.config import Config
from trading.exits import check_exit
from trading.paper import PaperPosition
from web.routes import positions as routes


@pytest.fixture
def book(monkeypatch):
    pos = PaperPosition(
        ticker="TEST.NS", qty=10, entry_price=100.0,
        entry_date=datetime(2026, 11, 3), sector="IT", peak_price=100.0,
        entry_score=70.0, reasoning="fixture", mode="swing",
        stop_price=90.0, target_price=120.0, entry_notes="old note")
    portfolio = SimpleNamespace(cash=10_000.0, positions={pos.ticker: pos}, save=Mock())
    monkeypatch.setattr(routes.state, "portfolio", lambda: portfolio)
    monkeypatch.setattr(routes.auth, "current_user", lambda request: {"role": "owner"})
    return portfolio, pos


def _edit(tags="", note="updated note"):
    return routes.update_notes(object(), "TEST.NS", entry_notes=note, tags=tags)


def _exit(pos):
    cfg = Config()
    cfg.disable_exits = False
    cfg.score_decay_exit = 45
    return check_exit(pos, 100.0, 0, cfg, now=datetime(2026, 11, 3, 6))


@pytest.mark.parametrize("forged", ["ocean", "owner_pick", "buy_desk", "manual_import", "seed_june_2026", "real_holding", "OCEAN"])
def test_notes_cannot_invent_reserved_identity_or_move_budget(book, forged):
    p, pos = book
    pos.tags = ["old_user_tag"]
    before = cb.used(p.positions.values(), "swing")
    assert _edit(f"{forged},research").headers["location"] == "/positions"
    assert pos.tags == ["research"]
    assert cb.category_of(pos) == "swing"
    assert cb.used(p.positions.values(), "swing") == before == 1000
    assert not cb.is_owner(pos)
    assert _exit(pos) == (True, "SCORE_DECAY")
    assert pos.entry_notes == "updated note"
    assert p.cash == 10_000 and pos.mode == "swing"
    p.save.assert_called_once()


@pytest.mark.parametrize("source", [["owner_pick", "buy_desk"], ["ocean"], ["manual_import", "real_holding"], ["seed_june_2026", "real_holding"]])
def test_notes_cannot_remove_original_system_tags_or_change_exits(book, source):
    p, pos = book
    pos.tags = source + ["old_user_tag"]
    category_before, exit_before = cb.category_of(pos), _exit(pos)
    assert _edit("new_user_tag").status_code == 303
    assert pos.tags == source + ["new_user_tag"]
    assert cb.category_of(pos) == category_before
    assert _exit(pos) == exit_before
    assert pos.stop_price == 90 and pos.target_price == 120
    assert pos.reasoning == "fixture" and pos.mode == "swing"


def test_notes_empty_user_tags_keep_source_and_edit_notes(book):
    p, pos = book
    pos.tags = ["owner_pick", "research"]
    _edit("", "  new research  ")
    assert pos.tags == ["owner_pick"] and pos.entry_notes == "new research"
    assert cb.is_owner(pos)
    assert _exit(pos) == (False, "")


def test_notes_wait_for_book_lock_and_do_nothing_when_busy(book, monkeypatch):
    from trading import modes
    p, pos = book
    pos.tags = ["owner_pick", "research"]

    @contextmanager
    def busy(timeout=None):
        raise modes.ModeBusy("fixture busy")
        yield

    monkeypatch.setattr(modes, "holding_mode_lock", busy)
    response = _edit("ocean", "changed")
    assert response.headers["location"] == "/positions?error=scan_busy"
    assert pos.entry_notes == "old note" and pos.tags == ["owner_pick", "research"]
    p.save.assert_not_called()
