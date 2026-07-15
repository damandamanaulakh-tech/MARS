"""Boot + auth smoke tests for the FastAPI control panel."""

from fastapi.testclient import TestClient

from web.main import app
from web import auth as auth_mod
from web.templating import inr, inr_lakh, pct


client = TestClient(app)


def test_healthz_ok():
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    # v2: now also reports DB status
    assert "db" in body


def test_login_page_renders_without_session():
    r = client.get("/login")
    assert r.status_code == 200
    assert "Welcome back" in r.text


def test_root_redirects_to_login_when_no_session():
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert "/login" in r.headers["location"]


def test_seeded_owner_logs_in_and_sees_dashboard():
    fresh = TestClient(app)
    r = fresh.post(
        "/login",
        data={"email": "owner@sourceborn.in", "password": auth_mod._SEEDED_PWD},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/"

    r2 = fresh.get("/")
    assert r2.status_code == 200
    assert "Master Dashboard" in r2.text
    assert "SOURCEBORN" in r2.text


def test_bad_password_redirects_back_with_error():
    fresh = TestClient(app)
    r = fresh.post(
        "/login",
        data={"email": "owner@sourceborn.in", "password": "wrong"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "error=invalid" in r.headers["location"]


def test_logout_clears_session():
    fresh = TestClient(app)
    fresh.post(
        "/login",
        data={"email": "owner@sourceborn.in", "password": auth_mod._SEEDED_PWD},
    )
    r = fresh.get("/logout", follow_redirects=False)
    assert r.status_code == 303
    r2 = fresh.get("/", follow_redirects=False)
    assert r2.status_code == 303
    assert "/login" in r2.headers["location"]


def test_protected_pages_require_login():
    for path in ("/positions", "/trades", "/scan", "/settings"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303
        assert "/login" in r.headers["location"]


def test_stub_pages_render_for_logged_in_user():
    fresh = TestClient(app)
    fresh.post(
        "/login",
        data={"email": "owner@sourceborn.in", "password": auth_mod._SEEDED_PWD},
    )
    # /strategy, /backtest, /reports, /alerts are all real now.
    for path, title in (
        ("/strategy", "Strategy Weights"),
        ("/backtest", "Backtest Lab"),
        ("/reports", "Reports"),
        ("/alerts", "Alerts"),
    ):
        r = fresh.get(path)
        assert r.status_code == 200
        assert title in r.text


def test_settings_renders_editable_fields():
    fresh = TestClient(app)
    fresh.post(
        "/login",
        data={"email": "owner@sourceborn.in", "password": auth_mod._SEEDED_PWD},
    )
    r = fresh.get("/settings")
    assert r.status_code == 200
    assert "Strategy Settings" in r.text
    assert "Starting Capital" in r.text
    assert "Stop Loss" in r.text


def test_inr_indian_grouping():
    assert inr(2_500_000) == "25,00,000"
    assert inr(0) == "0"
    assert inr(1234) == "1,234"
    assert inr(123) == "123"
    assert inr(12_345_678) == "1,23,45,678"
    assert inr(-100_000) == "-1,00,000"
    assert inr(1500, digits=2) == "1,500.00"


def test_inr_lakh_compaction():
    assert inr_lakh(2_500_000) == "25.00 L"
    assert inr_lakh(12_345_678) == "1.23 Cr"
    assert inr_lakh(5000) == "5.00 K"
    assert inr_lakh(-500_000) == "-5.00 L"
    assert inr_lakh(50) == "50"


def test_pct_formatting():
    assert pct(0.05) == "+0.05%"
    assert pct(-1.234) == "-1.23%"
    assert pct(None) == "—"
