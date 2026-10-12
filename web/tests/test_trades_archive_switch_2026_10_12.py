"""Owner 2026-10-11 item 5: pre-15-September closed trades are archived, not
deleted — /trades keeps them out of the live figures and shows them only
on ?archive=1, flagged."""
from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from trading.paper import ClosedTrade
from web import auth as auth_mod, db, state
from web.main import app


def _owner():
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD})
    assert r.status_code == 200
    return c


def _trade(ticker, exit_dt, pnl):
    return ClosedTrade(ticker=ticker, qty=10, entry_date=datetime(2026, 8, 1),
                       exit_date=exit_dt, entry_price=100.0, exit_price=100.0 + pnl / 10,
                       gross_pnl=pnl, costs=0.0, net_pnl=pnl, reason="TARGET",
                       sector="IT", entry_score=70.0, reasoning="fixture")


def test_archived_trades_stay_out_of_the_live_figures_until_asked(monkeypatch):
    p = state.portfolio()
    saved = list(p.trades)
    old = _trade("OLDPRE.NS", datetime(2026, 9, 1), 500.0).to_dict()
    old[db.PRE_MARS_FLAG] = True
    monkeypatch.setattr(db, "count_pre_mars", lambda coll: 1)
    monkeypatch.setattr(db, "list_trades",
                        lambda limit=0, include_pre_mars=False: [old] if include_pre_mars else [])
    try:
        p.trades = [_trade("LIVE.NS", datetime(2026, 10, 1), -200.0)]
        c = _owner()
        body = c.get("/trades").text
        assert "pre_mars_archive_marker" in body and "Show them" in body
        assert "OLDPRE" not in body and "LIVE.NS" in body
        assert "1 trades" in body or ">1 trades" in body or "1 trades ·" in body or "1 trade" in body
        body2 = c.get("/trades?archive=1").text
        assert "OLDPRE" in body2 and "archived" in body2.lower()
        assert "Hide them" in body2
        # the live figures do not change when the archive is shown
        assert "₹-200" in body2 or "-200" in body2
    finally:
        p.trades = saved


def test_without_archived_rows_the_line_is_absent(monkeypatch):
    monkeypatch.setattr(db, "count_pre_mars", lambda coll: 0)
    body = _owner().get("/trades").text
    assert "pre_mars_archive_marker" not in body
