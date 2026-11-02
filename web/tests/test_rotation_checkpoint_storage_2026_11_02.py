"""A rotation checkpoint needs the actual storage acknowledgement."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from trading.config import Config
from trading.paper import Portfolio
from web import db, state, rotation_progress as progress


@pytest.mark.parametrize("acknowledged", [True, False])
def test_mongo_acknowledgement_is_returned(monkeypatch, acknowledged):
    collection = Mock()
    collection.replace_one.return_value = SimpleNamespace(acknowledged=acknowledged)
    monkeypatch.setattr(db, "_CONNECTED", True)
    monkeypatch.setattr(db, "_DB", SimpleNamespace(portfolio=collection))
    monkeypatch.setattr(db, "LAST_WRITE_ERROR", None)
    monkeypatch.setattr(db, "LAST_WRITE_ERROR_AT", None)
    assert db.save_portfolio({"cash": 100}) is acknowledged
    assert bool(db.LAST_WRITE_ERROR) is not acknowledged


def test_mongo_exception_is_not_success(monkeypatch):
    collection = Mock()
    collection.replace_one.side_effect = RuntimeError("write refused")
    monkeypatch.setattr(db, "_CONNECTED", True)
    monkeypatch.setattr(db, "_DB", SimpleNamespace(portfolio=collection))
    monkeypatch.setattr(db, "LAST_WRITE_ERROR", None)
    monkeypatch.setattr(db, "LAST_WRITE_ERROR_AT", None)
    assert db.save_portfolio({"cash": 100}) is False
    assert "write refused" in db.LAST_WRITE_ERROR


@pytest.mark.parametrize("expected", [False, True])
def test_mirror_distinguishes_local_only_from_disconnected_mongo(tmp_path, monkeypatch, expected):
    state._patch_portfolio_save_to_mirror_mongo()
    monkeypatch.setattr(db, "is_connected", lambda: False)
    monkeypatch.setattr(db, "mongo_expected", lambda: expected)
    p = Portfolio.fresh(Config(state_dir=str(tmp_path)))
    assert p.save() is (not expected)


def test_checkpoint_and_book_roundtrip_together(tmp_path, monkeypatch):
    state._patch_portfolio_save_to_mirror_mongo()
    monkeypatch.setattr(db, "is_connected", lambda: True)
    stored = {}
    def save(doc):
        stored.update(deepcopy(doc))
        return True
    monkeypatch.setattr(db, "save_portfolio", save)
    cfg = Config(state_dir=str(tmp_path))
    p = Portfolio.fresh(cfg)
    p.cash = 1234.0
    rot = progress.begin(p, "09:20", "2026-11-02", ["nifty200_momentum"], True)
    rot["passes"]["nifty200_momentum"] = {"completed": True, "live": True, "opened": 1}
    assert progress.checkpoint(p, rot)
    q = Portfolio.fresh(cfg)
    state._hydrate_portfolio_from_mongo(q, stored)
    assert q.cash == 1234.0
    assert q.rotation_report == p.rotation_report
    assert Portfolio.load_or_fresh(cfg).rotation_report == p.rotation_report


def test_mirror_propagates_refused_mongo_checkpoint(tmp_path, monkeypatch):
    state._patch_portfolio_save_to_mirror_mongo()
    monkeypatch.setattr(db, "is_connected", lambda: True)
    monkeypatch.setattr(db, "save_portfolio", lambda doc: False)
    p = Portfolio.fresh(Config(state_dir=str(tmp_path)))
    rot = progress.begin(p, "09:20", "2026-11-02", ["nifty200_momentum"], True)
    assert progress.checkpoint(p, rot) is False
    assert p.rotation_report["daily"]["status"] == "save_failed"
