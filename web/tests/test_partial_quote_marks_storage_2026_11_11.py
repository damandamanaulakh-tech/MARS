"""AUD-002: persisted source evidence and read-only Risk-page disclosure."""
from copy import deepcopy
from datetime import datetime

from trading.config import Config
from trading.engine import TradingEngine
from trading.paper import PaperPosition, Portfolio
from web import db, state
from web.tests.test_category_budgets_page_2026_10_04 import _client


def marked_book(tmp_path):
    cfg = Config(state_dir=str(tmp_path), starting_capital=5_000_000.)
    p = Portfolio.fresh(cfg)
    p.cash = 4_900_000.
    p.positions["BBB.NS"] = PaperPosition(
        ticker="BBB.NS", qty=1000, entry_price=100., entry_date=datetime(2026, 11, 9),
        sector="Test", peak_price=100., entry_score=75., reasoning="fixture")
    e = TradingEngine(cfg, p)
    e._freshest_marks({"BBB.NS": 300.}, {})
    return e


def test_mongo_mirror_and_hydrate_retain_value_and_evidence(tmp_path, monkeypatch):
    state._patch_portfolio_save_to_mirror_mongo()
    monkeypatch.setattr(db, "is_connected", lambda: True)
    stored = {}

    def save(doc):
        stored.update(deepcopy(doc))
        return True

    monkeypatch.setattr(db, "save_portfolio", save)
    e = marked_book(tmp_path)
    assert e.portfolio.save() is True
    restored = Portfolio.fresh(e.cfg)
    state._hydrate_portfolio_from_mongo(restored, stored)
    assert restored.equity({}) == 5_200_000.
    assert restored.mark_snapshot == e.portfolio.mark_snapshot
    assert restored.positions["BBB.NS"].last_mark == e.portfolio.positions["BBB.NS"].last_mark


def test_legacy_mongo_has_no_invented_observation(tmp_path):
    e = marked_book(tmp_path)
    raw = e.portfolio.positions["BBB.NS"].to_dict()
    raw.pop("last_mark")
    state._hydrate_portfolio_from_mongo(e.portfolio, {"positions": {"BBB.NS": raw}})
    assert e.portfolio.mark_snapshot == {}
    assert e.portfolio.positions["BBB.NS"].last_mark == {}


def test_risk_page_shows_retained_value_time_and_unknown_provider_age_without_mutation(tmp_path, monkeypatch):
    e = marked_book(tmp_path)
    monkeypatch.setattr(state, "engine", lambda: e)
    monkeypatch.setattr(state, "cfg", lambda: e.cfg)
    before = deepcopy(e.portfolio.positions["BBB.NS"].to_dict())
    journal = deepcopy(e.portfolio.decision_journal)
    snapshot = deepcopy(e.drawdown_gov.snapshot())
    response = _client().get("/risk")
    assert response.status_code == 200
    assert "₹52,00,000" in response.text
    assert "quote response" in response.text
    assert "provider quote age is not yet verified" in response.text
    assert e.portfolio.positions["BBB.NS"].last_mark["received_at"] in response.text
    assert e.portfolio.positions["BBB.NS"].to_dict() == before
    assert e.portfolio.decision_journal == journal
    assert e.drawdown_gov.snapshot() == snapshot


def test_risk_page_labels_unknown_entry_estimate(tmp_path, monkeypatch):
    e = marked_book(tmp_path)
    e.portfolio.positions["BBB.NS"].last_mark = {}
    monkeypatch.setattr(state, "engine", lambda: e)
    monkeypatch.setattr(state, "cfg", lambda: e.cfg)
    response = _client().get("/risk")
    assert response.status_code == 200
    assert "Entry-only estimates: BBB.NS" in response.text
    assert "Automatic scan entries wait" in response.text
    assert "₹50,00,000" in response.text
