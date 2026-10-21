"""Owner order 2026-10-21: the owner bucket on the screens. /risk shows an
"owner (outside the shares)" row with entry cost and count; the Buy Desk
card and the BUY ticket say what they open counts under owner, outside the
five shares; the dashboard fund table carries the Owner segment."""
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


def test_the_risk_page_shows_the_owner_row_outside_the_shares():
    book = {"BD1.NS": _pos("BD1.NS", 10_00_000.0, tags=["owner_pick", "buy_desk"]),
            "BD2.NS": _pos("BD2.NS", 5_00_000.0, tags=["owner_pick"]),
            "MOM.NS": _pos("MOM.NS", 3_00_000.0)}
    with _Book(book):
        html = _client().get("/risk").text
    assert "category_owner_marker" in html
    card = html.split("category_budget_marker")[1].split("IFR OVERLAY")[0]
    row = card.split("category_owner_marker")[1].split("</tr>")[0]
    assert "Owner" in row and "outside the shares" in row
    assert "₹15,00,000" in row and ">2<" in row            # cost + count
    # the owner fills no longer sit in momentum's row
    mom = card.split("Momentum")[1].split("</tr>")[0]
    assert "₹3,00,000" in mom and ">1<" in mom
    assert "counts under <strong>owner</strong>" in card
    assert "counts as momentum" not in card


def test_the_buy_desk_card_and_the_ticket_say_owner():
    c = _client()
    html = c.get("/scan").text
    assert html.count("counts under owner, outside the five shares") >= 2
    ticket = html.split("Place paper order")[1].split("</form>")[0]
    assert "counts under owner" in ticket


def test_the_dashboard_fund_table_carries_the_owner_segment():
    book = {"BD1.NS": _pos("BD1.NS", 10_00_000.0, tags=["owner_pick", "buy_desk"])}
    with _Book(book):
        html = _client().get("/").text
    assert 'data-fund-segment="owner"' in html
    seg = html.split('data-fund-segment="owner"')[1].split("</tr>")[0]
    assert "outside the five shares" in seg and "₹10,00,000" in seg
    mom = html.split('data-fund-segment="nifty200_momentum"')[1].split("</tr>")[0]
    assert "₹10,00,000" not in mom


def test_a_viewer_still_sees_no_risk_page(monkeypatch):
    monkeypatch.setitem(auth_mod.SEEDED_USERS, "viewer@sourceborn.in",
                        {"role": "viewer", "password": "v-test", "name": "Friend"})
    c = TestClient(app)
    assert c.post("/login", data={"email": "viewer@sourceborn.in",
                                  "password": "v-test"}).status_code == 200
    assert c.get("/risk").status_code == 403


def test_no_screen_claims_owner_buys_obey_the_deploy_cap():
    """Review fix 2026-10-21: the owner's BUY button, limit fills and the Buy
    Desk call no risk.can_open (before or after this order) — only cash is
    checked. The first build printed "Owner buys still obey the deploy cap
    and the heat cap" on /risk and "the deploy cap and heat still apply" on
    the Buy Desk; both were false. What IS true is printed instead: their
    positions count in the cap every scan entry is judged against."""
    book = {"BD1.NS": _pos("BD1.NS", 10_00_000.0, tags=["owner_pick", "buy_desk"])}
    c = _client()
    with _Book(book):
        risk = c.get("/risk").text
    card = risk.split("category_budget_marker")[1].split("IFR OVERLAY")[0]
    assert "still obey the deploy cap" not in card
    assert "cash only" in card
    assert "counts in the deploy cap and the heat every scan entry" in card
    scan = c.get("/scan").text
    assert "the deploy cap and heat still apply" not in scan
