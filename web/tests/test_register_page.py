"""📇 /register — the Selection Register v4 page. Display/research only."""

from fastapi.testclient import TestClient

from web import auth as auth_mod
from web.main import app


def _client(login: bool = True) -> TestClient:
    c = TestClient(app)
    if login:
        c.post("/login", data={"email": "owner@sourceborn.in",
                               "password": auth_mod._SEEDED_PWD})
    return c


def test_page_requires_login():
    r = _client(login=False).get("/register", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_page_renders_market_items_and_scope():
    html = _client().get("/register").text
    assert "279" in html
    assert "REQUIRES_15Y_BACKTEST" in html
    assert "FII-02" in html                    # committed-flows item present
    assert "DATA_NEEDED" in html               # honesty states rendered
    assert "dbi" in html                       # the undefined-input PX rows


def test_page_lists_assumptions_and_have_map():
    html = _client().get("/register").text
    assert "Assumptions this page makes" in html
    assert "already has" in html
    assert "trading/triggers.py" in html


def test_per_stock_view_from_stored_history():
    html = _client().get("/register?ticker=TCS.NS").text
    assert "Per-stock items" in html
    assert "H-11" in html                      # CAGR row rendered
    assert "X-12" in html                      # Piotroski row rendered


def test_unknown_ticker_says_data_needed_never_fakes():
    html = _client().get("/register?ticker=NOSUCHNAME").text
    assert "DATA_NEEDED" in html
