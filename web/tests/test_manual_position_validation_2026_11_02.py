"""Invalid manual inputs must not change cash, holdings, or closed trades."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from web.routes import positions as routes


@pytest.fixture
def book(monkeypatch):
    portfolio = SimpleNamespace(cash=1000.0, positions={},
                                decision_journal=[], save=Mock())
    monkeypatch.setattr(routes.auth, "current_user", lambda request: {"role": "owner"})
    monkeypatch.setattr(routes.state, "portfolio", lambda: portfolio)
    monkeypatch.setattr(routes.state, "cfg", lambda: SimpleNamespace(sector_map={}))
    return portfolio


def add(qty, price):
    return routes.add_manual_position(
        object(), ticker="TEST", qty=qty, entry_price=price,
        entry_date="2026-11-02", sector="IT", entry_notes="", tags="")


@pytest.mark.parametrize("qty,price", [
    (0, 100.0), (-1, 100.0), (1, 0.0), (1, -100.0),
    (1, float("nan")), (1, float("inf")), (1, -float("inf")),
    (10, 1e308), (10 ** 400, 1.0),
])
def test_invalid_manual_entry_preserves_book(book, qty, price):
    response = add(qty, price)
    assert response.status_code == 303
    assert "error=bad_manual_values" in response.headers["location"]
    assert book.cash == 1000.0
    assert book.positions == {}
    book.save.assert_not_called()


def test_valid_manual_entry_still_records_holding(book):
    response = add(2, 100.0)
    assert response.headers["location"] == "/positions"
    assert book.cash == 800.0
    assert book.positions["TEST.NS"].qty == 2
    assert book.positions["TEST.NS"].entry_price == 100.0
    book.save.assert_called_once()


@pytest.mark.parametrize("typed,quote", [
    ("nan", None), ("inf", None), ("-inf", None),
    ("", float("inf")), ("", float("nan")),
])
def test_nonfinite_sell_never_reaches_engine(book, monkeypatch, typed, quote):
    position = object()
    book.positions["TEST.NS"] = position
    engine = Mock()
    monkeypatch.setattr(routes.state, "engine", lambda: engine)
    monkeypatch.setattr(routes.state, "marks", lambda tickers: ({"TEST.NS": quote}, True))

    async def form():
        return {"price": typed}

    response = asyncio.run(routes.sell_position(SimpleNamespace(form=form), "TEST.NS"))
    assert "error=" in response.headers["location"]
    engine._close.assert_not_called()
    assert book.positions == {"TEST.NS": position}
    assert book.cash == 1000.0
    assert book.decision_journal == []
    book.save.assert_not_called()
