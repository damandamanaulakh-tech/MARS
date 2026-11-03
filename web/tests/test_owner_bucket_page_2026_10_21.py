"""The 2026-11-03 policy replaces the separate owner allocation display.
Owner source markers remain; strategy allocation and P&L are combined.
"""
from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from trading.paper import PaperPosition
from web import auth as auth_mod
from web import state
from web.main import app


def _client():
    c = TestClient(app)
    r = c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD})
    assert r.status_code == 200
    return c


def _pos(t, rupees, mode="nifty200_momentum", tags=None):
    return PaperPosition(
        ticker=t, qty=int(rupees // 100), entry_price=100.0,
        entry_date=datetime(2026, 9, 24), sector="IT", peak_price=100.0,
        entry_score=70.0, reasoning="fixture", mode=mode,
        stop_price=95.0, target_price=120.0, tags=list(tags or []))


class _Book:
    def __init__(self, positions):
        self.positions = positions

    def __enter__(self):
        self.p = state.portfolio()
        self.saved = dict(self.p.positions)
        self.p.positions = self.positions
        return self.p

    def __exit__(self, *a):
        self.p.positions = self.saved


def test_the_risk_page_counts_owner_entries_under_their_segments():
    book = {"BD1.NS": _pos("BD1.NS", 10_00_000.0, tags=["owner_pick", "buy_desk"]),
            "BD2.NS": _pos("BD2.NS", 5_00_000.0, mode="swing", tags=["owner_pick"]),
            "MOM.NS": _pos("MOM.NS", 3_00_000.0)}
    with _Book(book):
        html = _client().get("/risk").text
    assert "category_owner_marker" not in html
    card = html.split("category_budget_marker")[1].split("IFR OVERLAY")[0]
    mom = card.split('data-category-budget-row="nifty200_momentum"')[1].split("</tr>")[0]
    swing = card.split('data-category-budget-row="swing"')[1].split("</tr>")[0]
    assert "₹13,00,000" in mom and ">2<" in mom
    assert "₹5,00,000" in swing and ">1<" in swing
    assert "stored strategy segment" in card


def test_the_buy_desk_card_and_the_ticket_describe_strategy_allocation():
    html = _client().get("/scan").text
    assert "counts under owner, outside the five shares" not in html
    assert 'name="mode"' in html
    assert "Momentum" in html


def test_the_dashboard_fund_table_combines_owner_and_auto_in_strategy():
    book = {"BD1.NS": _pos("BD1.NS", 10_00_000.0, tags=["owner_pick", "buy_desk"])}
    with _Book(book):
        html = _client().get("/").text
    assert 'data-fund-segment="owner"' not in html
    mom = html.split('data-fund-segment="nifty200_momentum"')[1].split("</tr>")[0]
    assert "₹10,00,000" in mom


def test_open_position_segment_and_owner_source_are_distinct():
    book = {"BD1.NS": _pos("BD1.NS", 10_00_000.0, mode="short", tags=["owner_pick"]),
            "LEGACY.NS": _pos("LEGACY.NS", 1_00_000.0, mode="", tags=["owner_pick"])}
    with _Book(book):
        html = _client().get("/positions").text
    assert "Short" in html and "Momentum" in html
    assert "OWNER PICK" in html
    assert "do not create separate fund allocations" in html
    assert 'name="mode"' in html and 'value="positional"' in html


def test_a_viewer_still_sees_no_risk_page(monkeypatch):
    monkeypatch.setitem(auth_mod.SEEDED_USERS, "viewer@sourceborn.in",
                        {"role": "viewer", "password": "v-test", "name": "Friend"})
    c = TestClient(app)
    assert c.post("/login", data={"email": "viewer@sourceborn.in",
                                  "password": "v-test"}).status_code == 200
    assert c.get("/risk").status_code == 403


def test_allocation_help_distinguishes_share_checks_from_other_scan_gates():
    html = _client().get("/risk").text
    card = html.split("category_budget_marker")[1].split("IFR OVERLAY")[0]
    assert "cash only" not in card
    assert "selected segment’s cash share" in card
    assert "deploy cap and heat used for automatic entries" in card


def test_legacy_overfull_owner_allocation_is_visible_without_a_sale():
    book = {"BIG.NS": _pos("BIG.NS", 4_00_00_000.0, tags=["owner_pick"])}
    with _Book(book) as p:
        before_cash = p.cash
        html = _client().get("/risk").text
        dashboard = _client().get("/").text
        assert p.positions == book and p.cash == before_cash
    assert "over limit; existing holdings retained" in html
    assert "over limit · holdings retained" in dashboard
