"""Owner order 2026-10-02 ("F9- write it"): the measured-Kelly reading is on
the Risk page and has a Settings switch, so the owner sees the numbers that
can block or cap the scan before a rupee moves."""
from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from trading.paper import ClosedTrade
from web import auth as auth_mod
from web import state
from web.main import app


def _t(i, ret, reason):
    return ClosedTrade(
        ticker=f"T{i}.NS", qty=100, entry_date=datetime(2026, 1, 1),
        exit_date=datetime(2026, 2, 1), entry_price=100.0,
        exit_price=100.0 * (1 + ret), gross_pnl=ret * 10_000, costs=0.0,
        net_pnl=ret * 10_000, reason=reason, sector="IT", entry_score=70.0,
        reasoning="fixture")


def _page(trades):
    p = state.portfolio()
    saved = list(p.trades)
    p.trades = list(trades)
    try:
        c = TestClient(app)
        r = c.post("/login", data={"email": "owner@sourceborn.in",
                                   "password": auth_mod._SEEDED_PWD})
        assert r.status_code == 200
        r = c.get("/risk")
        assert r.status_code == 200
        return r.text
    finally:
        p.trades = saved


def test_a_no_edge_book_says_entries_are_blocked():
    html = _page([_t(i, 0.02, "TAKE_PROFIT") for i in range(10)]
                 + [_t(50 + i, -0.05, "STOP_LOSS") for i in range(30)])
    assert "measured_kelly_marker" in html
    assert "NEW SCAN ENTRIES BLOCKED" in html


def test_an_edge_book_shows_the_cap():
    html = _page([_t(i, 0.05, "TAKE_PROFIT") for i in range(40)]
                 + [_t(50 + i, -0.05, "STOP_LOSS") for i in range(20)])
    assert "SIZE CAP ₹" in html and "BLOCKED" not in html.split(
        "measured_kelly_marker")[1].split("IFR OVERLAY")[0]


def test_a_thin_book_says_no_cap_no_block():
    html = _page([_t(i, -0.05, "OWNER_SELL") for i in range(40)])
    assert "NOT ENOUGH EXITS YET" in html


def test_settings_carries_the_switch():
    from web.routes import settings_routes
    keys = [row[0] for row in settings_routes.EDITABLE_FIELDS]
    assert "measured_kelly_enabled" in keys
