"""Marketing copy must match the code (owner-approved truth pass, 2026-07-27).

An external review graded the app by its public pages and correctly caught
them overpromising: "ATR-based stops" (stops are fixed %), "Upstox-ready live
mode" (no order path exists), "500+ universe" (it's 2,393), a correlation
threshold and cash-reserve ladder the code contradicts, and a sector
blacklist that is empty by default. These tests pin every page claim that an
earlier version got wrong.
"""

from fastapi.testclient import TestClient

from web import auth as auth_mod
from web.main import app


def _login_page() -> str:
    r = TestClient(app).get("/login")
    assert r.status_code == 200
    return r.text


def _dashboard() -> str:
    c = TestClient(app)
    c.post("/login", data={"email": "owner@sourceborn.in",
                           "password": auth_mod._SEEDED_PWD})
    r = c.get("/", follow_redirects=True)
    assert r.status_code == 200
    return r.text


def test_login_page_no_false_claims():
    page = _login_page()
    assert "ATR" not in page                 # stops are fixed %, not ATR
    assert "Upstox-ready live mode" not in page
    assert "500+" not in page                # universe is 2,393
    assert "paper" in page.lower()           # says what it actually is


def test_dashboard_panel_matches_code_values():
    page = _dashboard()
    # correlation gate: engine.py uses threshold=0.85 (tightened from 0.70)
    assert "correlation &gt; 0.85" in page
    assert "correlation &gt; 0.7," not in page
    # cash-reserve ladder: risk_v2.DYNAMIC_CASH_RESERVE is 0/0/20/40
    assert "0% bull" in page and "20% correction" in page
    assert "20% bull" not in page             # the old fictional ladder
    assert "vol-adjusted sizing" not in page  # imported but never called
    assert "skips Metal" not in page          # blacklist is empty by default
