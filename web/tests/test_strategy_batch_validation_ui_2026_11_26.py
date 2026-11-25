"""The owner page exposes batch outcomes without changing automatic evidence."""
import re

from fastapi.testclient import TestClient
import pytest

from trading.config import Config
from trading.paper import Portfolio
from web import auth, db, state
from web.main import app


@pytest.mark.parametrize("unknown", [False, True])
def test_owner_batch_results_and_recorded_losses_remain_visible_separately(monkeypatch, tmp_path, unknown):
    book = Portfolio(cfg=Config(state_dir=str(tmp_path)), cash=1000.)
    monkeypatch.setattr(state, "portfolio", lambda: book)
    monkeypatch.setattr(auth, "current_user", lambda r: {"role": "owner"})
    def forbidden(*a, **kw):
        raise AssertionError("Reading validation cannot trade or save")
    monkeypatch.setattr(state, "engine", forbidden)
    monkeypatch.setattr(book, "save", forbidden)
    run = {"id": "isolated-batch-version", "settings": {"mode": "nifty200_momentum", "starting_capital": 1000.}}
    authority = {"batch_id": "dated-owner-request", "original_path": "scan",
                 "override": {"authorized": True, "applied": False}}
    stamp = "2026-11-26T04:30:00Z"
    entry = {"kind": "entry", "path": "owner_strategy_batch", "strategy_batch": authority,
             "run": run, "book_epoch": "fixture-book", "trade_id": "owner-one",
             "position": {"qty": 2, "mode": "nifty200_momentum"}, "observed_at": stamp}
    exit_event = {"kind": "exit", "path": "owner_strategy_batch", "strategy_batch": authority,
                  "run": run, "book_epoch": "fixture-book", "trade_id": "owner-one", "observed_at": stamp,
                  "trade": {"trade_id": "owner-one", "qty": 1 if unknown else 2,
                            "mode": "nifty200_momentum", "net_pnl": None if unknown else -12.34,
                            "costs": None if unknown else 2.50,
                            "cost_evidence": {"status": "legacy_unverified"}, "reason": "STOP_LOSS"}}
    monkeypatch.setattr(db, "load_validation_events", lambda: ([entry, exit_event], True, None))
    client = TestClient(app)
    response = client.get("/validation")
    assert response.status_code == 200
    assert "Coverage incomplete" in response.text
    owner_table = re.search(r'<table id="owner-batch-validation".*?</table>', response.text, re.S).group()
    economy_table = re.search(r'<table id="observed-economics".*?</table>', response.text, re.S).group()
    assert "Momentum" in owner_table and "1 / 0" in owner_table
    assert "2026-11-26 10:00 IST" in owner_table
    assert "Unknown costs: 1 legs" in owner_table
    assert "fixture-book" in economy_table and "Unverified charges: 1 legs" in economy_table
    if unknown:
        assert "Known subtotal: Unavailable" in economy_table
        assert "Unknown net: 1 legs" in economy_table
        assert "Unknown costs: 1 legs" in economy_table
        assert "0.00" not in economy_table
    else:
        assert "-12.34" in economy_table and "2.50" in economy_table
    result = client.get("/api/validation").json()
    assert result["cohorts"] == []
    assert result["owner_batch_cohorts"][0]["automatic_strategy_evidence"] is False
    assert result["owner_batch_cohorts"][0]["performance_override_entries"] == 0
    assert result["observed_economics"][0]["net_pnl"] == (None if unknown else -12.34)
    assert result["promotion"] == "none"
