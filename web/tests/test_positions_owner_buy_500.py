"""The /positions 500 (owner report 2026-08-21: a screenshot of
"Internal Server Error" on /positions — "not fixed").

ROOT CAUSE: _pick_detail.html line 16 read `p.entry_score if ... and
p.entry_score else p.score` — a hand-tapped 🟢BUY stores entry_score 0
(no scan score exists at a manual entry), 0 is falsy, the fallback reached
`p.score`, and POSITION rows have no `score` → jinja2 UndefinedError → the
whole page died. Shipped in the 2026-07-22 repo port; armed by the owner's
first manual buy; every replica with only scan-opened positions rendered
fine, which is why it survived every crawl until the real book was modelled.

These tests pin the fix and the register-page fault found in the same hunt.
"""
from datetime import date, datetime

from fastapi.testclient import TestClient

from trading.paper import PaperPosition
from web import auth as auth_mod, state
from web.main import app


def _client() -> TestClient:
    c = TestClient(app)
    c.post("/login", data={"email": "owner@sourceborn.in",
                           "password": auth_mod._SEEDED_PWD})
    return c


def _pos(ticker: str, **kw) -> PaperPosition:
    base = dict(qty=10, entry_price=100.0, entry_date=datetime(2026, 8, 21),
                sector="Test", peak_price=100.0, entry_score=50.0,
                reasoning="fixture")
    base.update(kw)
    return PaperPosition(ticker=ticker, **base)


def _with_positions(positions: list):
    p = state.portfolio()
    old = dict(p.positions)
    p.positions = {x.ticker: x for x in positions}
    return p, old


def test_an_owner_bought_position_with_score_zero_renders():
    """THE crash: entry_score 0 must render as an honest 0.0, not kill the
    page the owner checks prices on."""
    p, old = _with_positions([
        _pos("TORNTPHARM.NS", entry_score=0.0, tags=["owner_pick"],
             reasoning="owner buy — no scan score at entry"),
    ])
    try:
        r = _client().get("/positions")
        assert r.status_code == 200
        assert "TORNTPHARM" in r.text
    finally:
        p.positions = old


def test_the_owners_real_book_shape_renders_whole():
    """The mix that reproduced the live 500: scan buys + manual buys +
    a date-only entry_date + custom levels + an ampersand ticker + a
    zero-entry edge, all on one page."""
    p, old = _with_positions([
        _pos("HFCL.NS", qty=558, entry_price=223.9, entry_score=83.8,
             mode="nifty200_momentum"),
        _pos("TORNTPHARM.NS", entry_score=0.0, tags=["owner_pick"],
             entry_date=date(2026, 8, 5)),
        _pos("GVT&D.NS", entry_price=4050.1, entry_score=65.2),
        _pos("CONCOR.NS", entry_price=800.0, stop_price=760.0,
             target_price=900.0, custom_levels=True, tags=["owner_pick"]),
        _pos("PNB.NS", entry_price=0.0, peak_price=0.0),
    ])
    try:
        r = _client().get("/positions")
        assert r.status_code == 200
        for t in ("HFCL", "TORNTPHARM", "CONCOR", "PNB"):
            assert t in r.text
    finally:
        p.positions = old


def test_pick_detail_partial_never_reaches_an_undefined_score():
    """Source-pin: the fallback chain must end in a literal, never in an
    attribute a position row does not carry."""
    src = open("web/templates/_pick_detail.html").read()
    assert "p.entry_score is not none" in src
    assert "else 0" in src


def test_register_portfolio_items_finally_compute_on_a_live_book():
    """Found in the same hunt: register_routes called p.equity() without
    prices and read a nonexistent pos.avg_price — the except swallowed both,
    so W-028..031 never computed on a book with positions."""
    src = open("web/routes/register_routes.py").read()
    code_only = "\n".join(ln for ln in src.splitlines()
                          if not ln.strip().startswith("#"))
    assert "avg_price" not in code_only
    assert "p.equity()" not in code_only
    p, old = _with_positions([_pos("TCS.NS", entry_score=0.0)])
    try:
        r = _client().get("/register")
        assert r.status_code == 200
    finally:
        p.positions = old
