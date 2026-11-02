"""Persist the liquidation obligation and holdings in the same Mongo document."""
from copy import deepcopy

from trading.engine import TradingEngine
from trading.paper import Portfolio
from trading.tests.test_drawdown_recovery_2026_11_02 import book, cut
from web import db, state


def test_partial_liquidation_mongo_roundtrip_and_resume(tmp_path, monkeypatch):
    state._patch_portfolio_save_to_mirror_mongo()
    monkeypatch.setattr(db, "is_connected", lambda: True)
    stored = {}

    def save(doc):
        stored.update(deepcopy(doc))
        return True

    monkeypatch.setattr(db, "save_portfolio", save)
    e = book(tmp_path, 2, .25)
    cut(e, {"H0.NS": 100.})
    assert e.portfolio.save() is True
    restored = Portfolio.fresh(e.cfg)
    state._hydrate_portfolio_from_mongo(restored, stored)
    assert restored.dd_cut_pending == e.portfolio.dd_cut_pending
    assert set(restored.positions) == {"H1.NS"}
    fresh = TradingEngine(e.cfg, restored)
    assert cut(fresh, {"H1.NS": 100.}) == ["H1.NS"]
    assert fresh.portfolio.save() is True
    assert stored["dd_cut_pending"] == {}
    assert stored["positions"] == {}
    assert len(stored["trades"]) == 2


def test_old_mongo_document_does_not_invent_liquidation(tmp_path):
    e = book(tmp_path)
    cut(e, {"H0.NS": 100.})
    state._hydrate_portfolio_from_mongo(e.portfolio, {"cash": 1000.})
    assert e.portfolio.dd_cut_pending == {}
