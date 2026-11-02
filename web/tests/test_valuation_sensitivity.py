"""Scenario arithmetic, saved-data failures and read-only authenticated UI."""
import copy
import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trading.valuation.sensitivity import calculate
from web import auth, db, state, valuation_exit_feed
from web.valuation_sensitivity import build
from web.main import app

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "research/valuation/five_stock_skeleton_2026_11_02"
INPUTS = json.loads((FIXTURE / "inputs.json").read_text())
RESULTS = json.loads((FIXTURE / "results.json").read_text())


@pytest.mark.parametrize("expected", RESULTS, ids=lambda r: r["ticker"] + "-" + r["scenario"])
def test_all_fifteen_reviewed_scenarios_match(expected):
    stock = next(s for s in INPUTS["stocks"] if s["ticker"] == expected["ticker"])
    scenario = next(s for s in INPUTS["scenarios"] if s["name"] == expected["scenario"])
    r = calculate(base=stock["baseline_target"], beta=stock["beta"],
        market_pct=scenario["market_pct"], sector_pct=scenario["sector_pct"].get(stock["sector_index"]),
        sector_fallback=stock["sector_fallback"], fpi_cr=scenario["fpi_5d_cr"], dii_cr=scenario["dii_5d_cr"])
    for key in ("adjusted_target", "illustrative_entry_ceiling", "market_impact_rs", "sector_impact_rs", "total_margin_pct"):
        assert r[key] == pytest.approx(expected[key])
    assert r["base"] + r["market_impact_rs"] + r["sector_impact_rs"] == pytest.approx(r["adjusted_target"])


def test_dii_changes_discount_not_value_and_positive_flows_do_not_discount_risk_away():
    common = dict(base=100, beta=1, market_pct=-5, sector_pct=-8, fpi_cr=-12000)
    rows = [calculate(**common, dii_cr=d) for d in (0, 8000, 12000, 20000)]
    assert [r["total_margin_pct"] for r in rows] == [15, 12, 10, 10]
    assert all(r["adjusted_target"] == pytest.approx(92.15) for r in rows)
    assert rows[1]["illustrative_entry_ceiling"] == pytest.approx(81.092)


def test_direction_reversal_is_explicit_and_fallback_does_not_apply_sector_twice():
    r = calculate(base=100, beta=.396, market_pct=-5, sector_pct=-2)
    assert r["adjusted_target"] > 100
    assert "Calibration warning" in r["warnings"][0]
    fallback = calculate(base=100, beta=1, market_pct=-5, sector_pct=-80, sector_fallback=True)
    assert fallback["adjusted_target"] == 95
    assert fallback["sector_impact_rs"] == 0
    assert "No independent sector" in fallback["warnings"][0]


@pytest.mark.parametrize("key", ["base", "beta", "market_pct", "sector_pct", "sector_coefficient", "fpi_cr", "dii_cr", "base_margin_pct", "max_extra_pp", "flow_scale_cr"])
@pytest.mark.parametrize("bad", ["nan", "inf", "-inf"])
def test_nonfinite_inputs_are_never_valuations(key, bad):
    args = dict(base=100, beta=1, market_pct=-5, sector_pct=-8)
    args[key] = bad
    with pytest.raises(ValueError):
        calculate(**args)


@pytest.mark.parametrize("extra", [{"flow_scale_cr": 0}, {"base_margin_pct": 90, "max_extra_pp": 10}, {"beta": 5, "market_pct": -90}, {"sector_coefficient": 5, "sector_pct": -90}])
def test_invalid_boundaries_refuse_results(extra):
    args = dict(base=100, beta=1, market_pct=-5, sector_pct=-8)
    args.update(extra)
    with pytest.raises(ValueError):
        calculate(**args)


@pytest.fixture
def document():
    return {"built": "2026-11-02T11:45:56", "rows": {
        s["ticker"]: {"base_target": s["baseline_target"], "beta": s["beta"],
          "beta_n": 252, "asof": "2026-10-30", "range_status": "OK",
          "sector_index": s["sector_index"]} for s in INPUTS["stocks"]}}


def test_missing_and_stale_records_are_not_silently_replaced(document):
    document["rows"]["COFORGE.NS"]["asof"] = "2026-09-01"
    del document["rows"]["DRREDDY.NS"]
    document["rows"]["TVSMOTOR.NS"]["beta_n"] = 50
    document["rows"]["ICICIBANK.NS"]["asof"] = "2027-01-01"
    result = build({}, document, today=date(2026, 11, 2))
    assert "Older baseline" in result["rows"][0]["notes"][0]
    assert result["rows"][0]["result"]
    for i in (1, 2, 3):
        assert result["rows"][i]["result"] is None
        assert "DATA NEEDED" in result["rows"][i]["error"]


def test_ticker_limits_unknown_preset_and_bad_sector(document):
    assert build({"tickers": ""}, document)["errors"]
    assert build({"tickers": ",".join("X" + str(i) for i in range(21))}, document)["errors"]
    assert build({"preset": "unknown"}, document)["errors"]
    result = build({"tickers": "COFORGE", "sector_COFORGE.NS": "nan"}, document)
    assert result["rows"][0]["result"] is None
    assert "sector move" in result["rows"][0]["error"]


def test_login_required_before_reading_records(monkeypatch):
    monkeypatch.setattr(auth, "current_user", lambda request: None)
    monkeypatch.setattr(valuation_exit_feed, "current_doc", lambda: pytest.fail("unauthenticated data read"))
    response = TestClient(app).get("/den/valuation/scenarios", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


@pytest.mark.parametrize("role", ["owner", "viewer"])
def test_authenticated_page_runs_from_saved_records_without_writes(monkeypatch, document, role):
    before = copy.deepcopy(document)
    monkeypatch.setattr(auth, "current_user", lambda request: {"email": "reader@example.com", "role": role, "name": "Reader"})
    monkeypatch.setattr(valuation_exit_feed, "current_doc", lambda: document)
    def forbidden(*args, **kwargs):
        pytest.fail("scenario attempted to write or access the trading engine")
    monkeypatch.setattr(db, "save_settings", forbidden)
    monkeypatch.setattr(valuation_exit_feed, "store", forbidden)
    monkeypatch.setattr(state, "engine", forbidden)
    c = TestClient(app)
    response = c.get("/den/valuation/scenarios")
    if role == "viewer":
        assert response.status_code == 403
        assert "1,455.53" not in response.text
        assert document == before
        return
    assert response.status_code == 200
    assert "1,455.53" in response.text and "1,280.86" in response.text
    assert "Calibration warning" in response.text
    assert "Market fallback" in response.text
    assert "2026-10-30" in response.text
    changed = c.get("/den/valuation/scenarios", params={"tickers": "COFORGE", "dii": 12000})
    assert changed.status_code == 200
    assert "1,309.97" in changed.text
    assert document == before


def test_bad_shared_input_suppresses_all_results_and_is_escaped(monkeypatch, document):
    monkeypatch.setattr(auth, "current_user", lambda request: {"email": "reader@example.com", "role": "owner"})
    monkeypatch.setattr(valuation_exit_feed, "current_doc", lambda: document)
    response = TestClient(app).get("/den/valuation/scenarios", params={"fpi": '<script>alert(1)</script>'})
    assert response.status_code == 422
    assert "Correct these inputs" in response.text
    assert "1,455.53" not in response.text
    assert "<script>alert(1)</script>" not in response.text


def test_empty_store_keeps_inputs_and_names_visible(monkeypatch):
    monkeypatch.setattr(auth, "current_user", lambda request: {"email": "reader@example.com", "role": "owner"})
    monkeypatch.setattr(valuation_exit_feed, "current_doc", lambda: {})
    response = TestClient(app).get("/den/valuation/scenarios")
    assert response.status_code == 200
    assert "DATA NEEDED" in response.text and "COFORGE.NS" in response.text
    assert "not available" in response.text


def test_existing_valuation_screen_links_to_scenarios():
    text = (ROOT / "web/templates/_den_valuation_chain.html").read_text()
    assert 'href="/den/valuation/scenarios"' in text
