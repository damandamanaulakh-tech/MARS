"""Owner BUY CALL — /scan/buy opens a momentum position at live price."""

from fastapi.testclient import TestClient

from web import auth as auth_mod, state
from web.main import app


def _owner() -> TestClient:
    c = TestClient(app)
    c.post("/login", data={"email": "owner@sourceborn.in",
                           "password": auth_mod._SEEDED_PWD})
    return c


def test_requires_owner():
    c = TestClient(app)
    r = c.post("/scan/buy", data={"ticker": "TCS"}, follow_redirects=False)
    assert r.status_code == 303 and "owner_only" in r.headers["location"] \
        or "/login" in r.headers["location"]


def test_no_quote_redirects_with_error():
    # conftest mocks fetch_quotes to {} -> no live price available
    r = _owner().post("/scan/buy", data={"ticker": "NOQUOTE"},
                      follow_redirects=False)
    assert r.status_code == 303
    assert "no_quote" in r.headers["location"]


def test_opens_momentum_position_at_live_price(monkeypatch):
    import trading.quote_router as qr
    monkeypatch.setattr(qr, "fetch_quotes",
                        lambda tickers, upstox_client=None:
                        {t: 500.0 for t in tickers})
    p = state.portfolio()
    cash_before = p.cash
    try:
        r = _owner().post("/scan/buy", data={"ticker": "torntpharm"},
                          follow_redirects=False)
        assert r.status_code == 303 and "/positions" in r.headers["location"]
        pos = p.positions.get("TORNTPHARM.NS")
        assert pos is not None, "position not opened"
        assert pos.mode == "nifty200_momentum"
        assert abs(pos.stop_price - 500.0 * 0.88) < 0.5      # momentum -12%
        assert abs(pos.target_price - 500.0 * 1.12) < 0.5    # user's +12 (2026-07-21)
        assert p.cash < cash_before
        buys = [e for e in p.decision_journal
                if e.get("action") == "BUY"
                and e.get("ticker") == "TORNTPHARM.NS"]
        assert buys and "OWNER BUY CALL" in buys[-1].get("why", "")
    finally:
        pos = p.positions.pop("TORNTPHARM.NS", None)
        if pos is not None:
            p.cash += pos.qty * pos.entry_price
